import functools
import json
import math
import os

from red_alert.core.utils import check_bom, standardize_name

# Default path to the ICBS city data cache file (fetched at runtime, not committed)
_DEFAULT_CITY_DATA_PATH = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'data', 'city_data.json')


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
    """Manages city geographic data from HFC (Home Front Command) and ICBS (Israel Central Bureau of Statistics).

    Primary source: HFC GetDistricts endpoint - authoritative area groupings and shelter times (migun_time).
    Secondary source: Static ICBS city_data.json - lat/long coordinates for GeoJSON mapping.

    The HFC area groupings (33 areas like "שרון", "דן") differ from the ICBS groupings
    (31 areas like "גוש דן", "הכרמל"). The HFC groupings match the live alert system,
    making them the preferred source. ICBS data is used only for lat/long coordinates
    needed by the Home Assistant GeoJSON integration.
    """

    def __init__(self, file_path: str, github_url: str, api_client, logger):
        self._local_file_path = file_path
        self._github_url = github_url
        self._api_client = api_client
        self._log = logger
        self._city_data = None
        self._city_details_map: dict[str, dict] = {}

    async def load_data(self, force_download=False):
        """Load city data. Prefers HFC districts (authoritative areas + shelter times)
        with ICBS coordinates overlay. Falls back to ICBS-only if HFC is unavailable."""
        # Step 1: Try HFC districts as primary source
        hfc_loaded = False
        try:
            hfc_districts = await self._api_client.get_districts()
            if hfc_districts:
                hfc_loaded = self._process_hfc_districts(hfc_districts)
        except Exception as e:
            self._log(f'Error loading HFC districts: {e}', level='WARNING')

        # Step 2: Load ICBS data (for coordinates overlay, or as full fallback)
        icbs_data = self._load_icbs_file(force_download)
        if icbs_data is None:
            icbs_data = await self._download_icbs_file()

        if hfc_loaded:
            if icbs_data:
                self._overlay_icbs_coordinates(icbs_data)
            self._build_city_details_map()
            return True

        # Fallback to ICBS-only
        if icbs_data is not None and self._process_icbs_data(icbs_data):
            self._log('Using ICBS city data as fallback (HFC districts unavailable).', level='WARNING')
            self._build_city_details_map()
            return True

        self._log('CRITICAL: Failed to load city data from both HFC districts and ICBS file.', level='CRITICAL')
        self._city_data = None
        self._city_details_map = {}
        return False

    def _load_icbs_file(self, force_download):
        """Load ICBS city data from local file. Returns raw data dict or None."""
        if force_download or not os.path.exists(self._local_file_path):
            return None
        try:
            with open(self._local_file_path, encoding='utf-8-sig') as f:
                loaded = json.load(f)
            if loaded and 'areas' in loaded:
                return loaded
            self._log('Local city data invalid or empty. Will attempt download.', level='WARNING')
        except (json.JSONDecodeError, OSError, Exception) as e:
            self._log(f"Error reading local city data file '{self._local_file_path}': {e}. Will attempt download.", level='WARNING')
        return None

    async def _download_icbs_file(self):
        """Download ICBS city data from GitHub. Returns raw data dict or None."""
        if not self._github_url:
            return None
        self._log('Downloading city data from GitHub.')
        text = await self._api_client.download_file(self._github_url)
        if not text:
            self._log('Failed to download city data.', level='ERROR')
            return None
        try:
            text = check_bom(text)
            loaded = json.loads(text)
            if not loaded or 'areas' not in loaded:
                self._log("Downloaded city data is invalid (missing 'areas' key).", level='ERROR')
                return None
            try:
                os.makedirs(os.path.dirname(self._local_file_path), exist_ok=True)
                with open(self._local_file_path, 'w', encoding='utf-8-sig') as f:
                    json.dump(loaded, f, ensure_ascii=False, indent=2)
                self._log('City data downloaded and saved locally.')
            except Exception as e:
                self._log(f"Error saving city data locally to '{self._local_file_path}': {e}", level='ERROR')
            return loaded
        except json.JSONDecodeError as e:
            self._log(f"Invalid city data JSON downloaded from '{self._github_url}': {e}", level='ERROR')
        return None

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

    def _overlay_icbs_coordinates(self, icbs_data):
        """Overlay lat/long coordinates from ICBS data onto HFC-sourced city data.

        Matches cities by standardized name. Only adds coordinates, does not
        change area assignments or other HFC-sourced fields.
        """
        if not self._city_data or 'areas' not in self._city_data:
            return

        # Build a flat lookup from ICBS data: std_name -> {lat, long}
        icbs_coords = {}
        for area, cities in icbs_data.get('areas', {}).items():
            if not isinstance(cities, dict):
                continue
            for city_name, details in cities.items():
                if not isinstance(details, dict):
                    continue
                lat = details.get('lat')
                lon = details.get('long')
                if lat is not None and lon is not None:
                    try:
                        std = standardize_name(city_name)
                        if std:
                            icbs_coords[std] = {'lat': float(lat), 'long': float(lon)}
                    except (ValueError, TypeError):
                        pass

        # Overlay onto HFC data
        overlaid = 0
        for area, cities in self._city_data['areas'].items():
            if not isinstance(cities, dict):
                continue
            for std, details in cities.items():
                coords = icbs_coords.get(std)
                if coords:
                    details['lat'] = coords['lat']
                    details['long'] = coords['long']
                    overlaid += 1

        self._log(f'ICBS coordinate overlay: Added coordinates to {overlaid} cities.')

    def _process_icbs_data(self, raw_data):
        """Process raw ICBS city data into the internal structure. Used as fallback when HFC is unavailable."""
        if not raw_data or 'areas' not in raw_data:
            self._log("City data missing 'areas' key during processing.", level='ERROR')
            return False
        proc = {'areas': {}}
        expected_keys_count = 0
        processed_keys_count = 0
        for area, cities in raw_data['areas'].items():
            if isinstance(cities, dict):
                std_cities = {}
                for city, details in cities.items():
                    if not isinstance(details, dict):
                        self._log(
                            f"City data processing: Expected dict for city details of '{city}' in area '{area}', got {type(details)}. Skipping city.",
                            level='WARNING',
                        )
                        continue
                    expected_keys_count += 1
                    std = standardize_name(city)
                    if not std:
                        self._log(f"City data processing: City '{city}' resulted in empty standardized name. Skipping.", level='WARNING')
                        continue

                    entry = {'original_name': city}
                    lat = details.get('lat')
                    lon = details.get('long')
                    try:
                        if lat is not None and lon is not None:
                            entry['lat'] = float(lat)
                            entry['long'] = float(lon)
                        elif lat is not None or lon is not None:
                            self._log(
                                f"City data processing: City '{city}' has partial coordinates (lat: {lat}, long: {lon}). Skipping coords.",
                                level='DEBUG',
                            )
                    except (ValueError, TypeError):
                        self._log(
                            f"City data processing: Invalid coordinate types for city '{city}' (lat: {lat}, long: {lon}). Skipping coords.",
                            level='WARNING',
                        )

                    if std in std_cities:
                        self._log(
                            f"City data processing: Duplicate standardized name '{std}' found in area '{area}'. "
                            f"Original names: '{std_cities[std]['original_name']}', '{city}'. Overwriting.",
                            level='WARNING',
                        )

                    std_cities[std] = entry
                    processed_keys_count += 1
                proc['areas'][area] = std_cities
            else:
                self._log(f"City data processing: Expected dict for area '{area}', got {type(cities)}. Skipping area.", level='WARNING')
                proc['areas'][area] = {}
        self._city_data = proc
        if expected_keys_count != processed_keys_count:
            self._log(
                f'City data processing: Mismatch - attempted {expected_keys_count} city entries, successfully processed {processed_keys_count}.',
                level='WARNING',
            )
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
