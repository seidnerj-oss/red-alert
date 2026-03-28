import asyncio
import functools
import json
import math
import os

from red_alert.core.utils import standardize_name

# Default path to the city data cache file (generated from HFC sources, with a
# committed fallback copy in the repo for when both HFC APIs are unavailable)
_DEFAULT_CITY_DATA_PATH = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'data', 'city_data.json')

CITY_DATA_REFRESH_INTERVAL = 86400  # 24 hours


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great-circle distance between two points in kilometers."""
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def find_cities_near(
    latitude: float,
    longitude: float,
    radius_km: float = 5.0,
    city_data_path: str | None = None,
) -> list[str]:
    """Find city names within a radius of the given coordinates.

    Reads city_data.json directly (no CityDataManager or async required).
    Returns original (non-standardized) city names sorted by distance.
    """
    path = city_data_path or _DEFAULT_CITY_DATA_PATH
    try:
        with open(path, encoding='utf-8-sig') as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

    results: list[tuple[float, str]] = []
    for cities in data.get('areas', {}).values():
        if not isinstance(cities, dict):
            continue
        for city_name, details in cities.items():
            if not isinstance(details, dict):
                continue
            lat = details.get('lat')
            lon = details.get('long')
            if lat is None or lon is None:
                continue
            try:
                dist = _haversine_km(latitude, longitude, float(lat), float(lon))
            except (ValueError, TypeError):
                continue
            if dist <= radius_km:
                results.append((dist, city_name))

    results.sort(key=lambda x: x[0])
    return [name for _, name in results]


class CityDataManager:
    """Manages city geographic data from HFC (Home Front Command) sources.

    Primary source: HFC GetDistricts endpoint - city names, area groupings, shelter times (migun_time).
    Coordinate source: HFC Meser Hadash polygon API - per-city polygon boundaries, from which
    centroids are computed for lat/long coordinates.

    When both sources are available, the result is saved to disk as city_data.json. On subsequent
    loads, the cached file is used as a fallback if the APIs are unavailable. A committed copy in
    the repo (data/city_data.json) serves as the ultimate fallback.
    """

    def __init__(self, file_path: str, github_url: str, api_client, logger):
        self._local_file_path = file_path
        self._github_url = github_url
        self._api_client = api_client
        self._log = logger
        self._city_data = None
        self._city_details_map: dict[str, dict] = {}

    async def load_data(self, force_download=False):
        """Load city data from HFC districts + polygon centroids, with cached fallback.

        1. Fetch HFC districts (city names, areas, shelter times)
        2. Fetch HFC polygon data and compute centroids for coordinates
        3. Save result to disk as cache
        4. Fall back to cached file if APIs are unavailable
        """
        hfc_loaded = False
        try:
            hfc_districts = await self._api_client.get_districts()
            if hfc_districts:
                hfc_loaded = self._process_hfc_districts(hfc_districts)
        except Exception as e:
            self._log(f'Error loading HFC districts: {e}', level='WARNING')

        if hfc_loaded:
            await self._overlay_polygon_centroids()
            self._save_cache()
            self._build_city_details_map()
            return True

        # Fallback: load from cached file (generated or committed)
        cached = self._load_cached_file(force_download)
        if cached is not None and self._process_cached_data(cached):
            self._log('Using cached city data (HFC districts unavailable).', level='WARNING')
            self._build_city_details_map()
            return True

        self._log('CRITICAL: Failed to load city data from HFC and cache.', level='CRITICAL')
        self._city_data = None
        self._city_details_map = {}
        return False

    async def refresh(self) -> bool:
        """Re-fetch city data from HFC sources and update cache.

        Called periodically (every 24h) to keep city data current.
        Only updates if the fetch succeeds - keeps existing data on failure.
        """
        try:
            hfc_districts = await self._api_client.get_districts()
            if not hfc_districts:
                self._log('City data refresh: HFC districts unavailable.', level='WARNING')
                return False
            if not self._process_hfc_districts(hfc_districts):
                return False
            await self._overlay_polygon_centroids()
            self._save_cache()
            self.get_city_details.cache_clear()
            self._build_city_details_map()
            self._log('City data refreshed successfully.')
            return True
        except Exception as e:
            self._log(f'City data refresh failed: {e}', level='WARNING')
            return False

    def _load_cached_file(self, force_download):
        """Load city data from local cached file. Returns raw data dict or None."""
        if force_download or not os.path.exists(self._local_file_path):
            return None
        try:
            with open(self._local_file_path, encoding='utf-8-sig') as f:
                loaded = json.load(f)
            if loaded and 'areas' in loaded:
                return loaded
            self._log('Local city data invalid or empty.', level='WARNING')
        except (json.JSONDecodeError, OSError, Exception) as e:
            self._log(f"Error reading local city data file '{self._local_file_path}': {e}.", level='WARNING')
        return None

    def _save_cache(self):
        """Save current city data to disk for fallback use."""
        if not self._city_data or 'areas' not in self._city_data:
            return
        try:
            os.makedirs(os.path.dirname(self._local_file_path), exist_ok=True)
            with open(self._local_file_path, 'w', encoding='utf-8') as f:
                json.dump(self._city_data, f, ensure_ascii=False, indent=2)
            self._log(f'City data cached to {self._local_file_path}.')
        except Exception as e:
            self._log(f'Error saving city data cache: {e}', level='WARNING')

    def _process_hfc_districts(self, districts_list):
        """Process HFC GetDistricts response into internal city data structure.

        Input format (list of dicts):
            {"label": "כפר סבא", "label_he": "כפר סבא", "value": "...", "id": "840",
             "areaid": 27, "areaname": "שרון", "migun_time": 90}

        Internal format:
            {"areas": {"שרון": {"כפר סבא": {"original_name": "כפר סבא", "migun_time": 90}}}}
        """
        if not isinstance(districts_list, list):
            self._log('HFC districts data is not a list.', level='WARNING')
            return False

        proc = {'areas': {}}
        processed = 0
        skipped = 0

        for entry in districts_list:
            if not isinstance(entry, dict):
                skipped += 1
                continue

            city_name = entry.get('label_he') or entry.get('label', '')
            area_name = entry.get('areaname', '')
            migun_time = entry.get('migun_time')

            if not city_name or not area_name:
                skipped += 1
                continue

            std = standardize_name(city_name)
            if not std:
                skipped += 1
                continue

            area_dict = proc['areas'].setdefault(area_name, {})
            city_entry = {'original_name': city_name}

            if migun_time is not None:
                try:
                    city_entry['migun_time'] = int(migun_time)
                except (ValueError, TypeError):
                    pass

            if std in area_dict:
                self._log(
                    f"HFC districts: Duplicate city '{city_name}' (std: '{std}') in area '{area_name}'. Overwriting.",
                    level='DEBUG',
                )

            area_dict[std] = city_entry
            processed += 1

        if processed == 0:
            self._log('HFC districts: No valid entries processed.', level='WARNING')
            return False

        self._city_data = proc
        self._log(f'HFC districts: Processed {processed} cities across {len(proc["areas"])} areas.')
        if skipped > 0:
            self._log(f'HFC districts: Skipped {skipped} invalid entries.', level='DEBUG')
        return True

    async def _overlay_polygon_centroids(self):
        """Fetch HFC polygon data and overlay centroid coordinates onto city data.

        Fetches the segments list from the Meser Hadash backend, matches segments
        to districts by standardized name, fetches each segment's polygon in
        parallel (with concurrency limit), and computes the centroid lat/long.
        """
        from red_alert.core.polygon_data import POLYGON_URL_TEMPLATE, SEGMENTS_URL

        if not self._city_data or 'areas' not in self._city_data:
            return

        try:
            resp = await self._api_client._client.get(SEGMENTS_URL)
            resp.raise_for_status()
            seg_data = resp.json()
        except Exception as e:
            self._log(f'Failed to fetch segments for coordinate overlay: {e}', level='WARNING')
            return

        if isinstance(seg_data, dict) and 'segments' in seg_data:
            segments = seg_data['segments']
            if isinstance(segments, dict):
                segments = list(segments.values())
        elif isinstance(seg_data, list):
            segments = seg_data
        else:
            self._log('Unexpected segments response format.', level='WARNING')
            return

        seg_by_name: dict[str, dict] = {}
        for seg in segments:
            name = standardize_name(seg.get('cityName', seg.get('name', '')))
            if name and ('id' in seg or 'segmentId' in seg):
                seg_by_name[name] = seg

        flat_cities: dict[str, dict] = {}
        for area, cities in self._city_data['areas'].items():
            if isinstance(cities, dict):
                for std, entry in cities.items():
                    flat_cities[std] = entry

        sem = asyncio.Semaphore(20)
        centroids: dict[str, tuple[float, float]] = {}

        async def fetch_centroid(seg_id: str, std_name: str) -> None:
            async with sem:
                try:
                    url = POLYGON_URL_TEMPLATE.format(segment_id=seg_id)
                    resp = await self._api_client._client.get(url)
                    if resp.status_code != 200:
                        return
                    data = resp.json()
                    rings = data.get('polygonPointList', [])
                    if rings and isinstance(rings[0], list) and rings[0]:
                        coords = rings[0]
                        lats = [p[0] for p in coords]
                        lons = [p[1] for p in coords]
                        centroids[std_name] = (round(sum(lats) / len(lats), 5), round(sum(lons) / len(lons), 5))
                except Exception:
                    pass

        tasks = []
        for std_name in flat_cities:
            if std_name in seg_by_name:
                seg = seg_by_name[std_name]
                seg_id = seg.get('segmentId') or seg.get('id')
                if seg_id:
                    tasks.append(fetch_centroid(str(seg_id), std_name))

        if tasks:
            self._log(f'Fetching {len(tasks)} polygon centroids for coordinate overlay...')
            await asyncio.gather(*tasks)

        overlaid = 0
        for area, cities in self._city_data['areas'].items():
            if not isinstance(cities, dict):
                continue
            for std, entry in cities.items():
                if std in centroids:
                    entry['lat'] = centroids[std][0]
                    entry['long'] = centroids[std][1]
                    overlaid += 1

        total = sum(len(c) for c in self._city_data['areas'].values() if isinstance(c, dict))
        self._log(f'Polygon centroid overlay: {overlaid}/{total} cities with coordinates.')

    def _process_cached_data(self, raw_data):
        """Process cached city data file into internal structure."""
        if not raw_data or 'areas' not in raw_data:
            self._log("City data missing 'areas' key.", level='ERROR')
            return False
        proc = {'areas': {}}
        processed = 0
        for area, cities in raw_data['areas'].items():
            if not isinstance(cities, dict):
                continue
            std_cities = {}
            for city, details in cities.items():
                if not isinstance(details, dict):
                    continue
                std = standardize_name(city)
                if not std:
                    continue

                entry = {'original_name': details.get('original_name', city)}
                for key in ('lat', 'long', 'migun_time'):
                    if key in details:
                        entry[key] = details[key]

                std_cities[std] = entry
                processed += 1
            proc['areas'][area] = std_cities
        if processed == 0:
            return False
        self._city_data = proc
        self._log(f'Loaded {processed} cities from cache.')
        return True

    def _build_city_details_map(self):
        """Build the flat map for quick standardized name lookups."""
        self._city_details_map = {}
        if self._city_data and 'areas' in self._city_data:
            entries_built = 0
            duplicates = {}
            for area, cities in self._city_data['areas'].items():
                if isinstance(cities, dict):
                    for std, details in cities.items():
                        if std in self._city_details_map:
                            if std not in duplicates:
                                duplicates[std] = [self._city_details_map[std]['area']]
                            duplicates[std].append(area)
                            self._log(
                                f"City data map build: Duplicate std name '{std}' found in areas: {duplicates[std]}. Using entry from area '{area}'.",
                                level='WARNING',
                            )
                        self._city_details_map[std] = {**details, 'area': area}
                        entries_built += 1
                else:
                    self._log(f"City data map build: Area '{area}' has unexpected data type {type(cities)}. Skipping.", level='WARNING')

            if entries_built == 0:
                self._log('City data map build: No valid city entries found to build map.', level='ERROR')
            if duplicates:
                self._log(f'City data map build: Found {len(duplicates)} standardized names duplicated across multiple areas.', level='WARNING')
        else:
            self._log('No city data available to build map.', level='ERROR')

    @functools.lru_cache(maxsize=512)
    def get_city_details(self, standardized_name: str):
        """Get city details (original name, coords, area, migun_time) from the map using the standardized name."""
        if not isinstance(standardized_name, str) or not standardized_name:
            return None
        return self._city_details_map.get(standardized_name)
