"""Polygon-based city lookup using HFC Meser Hadash app backend.

Fetches per-city polygon boundaries from the HFC mobile app backend and provides
point-in-polygon matching to resolve geographic coordinates to city names.

Data source (geo-blocked outside Israel):
    Segments: https://dist-android.meser-hadash.org.il/smart-dist/services/anonymous/segments/android
    Polygon:  https://services.meser-hadash.org.il/smart-dist/services/anonymous/polygon/id/android
"""

import json
import os

import httpx

SEGMENTS_URL = 'https://dist-android.meser-hadash.org.il/smart-dist/services/anonymous/segments/android?instance=1544803905&locale=iw_IL'
POLYGON_URL_TEMPLATE = 'https://services.meser-hadash.org.il/smart-dist/services/anonymous/polygon/id/android?instance=1544803905&id={segment_id}'


def _point_in_polygon(lat: float, lon: float, polygon: list[list[float]]) -> bool:
    """Ray casting algorithm for point-in-polygon test.

    Args:
        lat: Latitude of the point.
        lon: Longitude of the point.
        polygon: List of [lat, lon] coordinate pairs forming a closed polygon.

    Returns True if the point is inside the polygon.
    """
    n = len(polygon)
    if n < 3:
        return False

    inside = False
    j = n - 1
    for i in range(n):
        lat_i, lon_i = polygon[i][0], polygon[i][1]
        lat_j, lon_j = polygon[j][0], polygon[j][1]

        if ((lat_i > lat) != (lat_j > lat)) and (lon < (lon_j - lon_i) * (lat - lat_i) / (lat_j - lat_i) + lon_i):
            inside = not inside
        j = i

    return inside


class PolygonDataManager:
    """Manages city polygon data from HFC Meser Hadash app backend.

    Fetches per-city polygon boundaries and provides point-in-polygon lookup
    to resolve geographic coordinates to city names. Results are cached to a
    local JSON file for offline use.
    """

    def __init__(self, http_client: httpx.AsyncClient, cache_path: str, logger):
        self._client = http_client
        self._cache_path = cache_path
        self._log = logger
        self._polygons: dict[str, list[list[list[float]]]] = {}

    @property
    def is_loaded(self) -> bool:
        return bool(self._polygons)

    async def load(self) -> bool:
        """Load polygon data: fetch from API, fall back to cache on failure."""
        if await self._fetch_from_api():
            self._save_cache()
            return True

        self._log('API fetch failed, attempting to load polygon data from cache.', level='WARNING')
        if self._load_cache():
            return True

        self._log('No polygon data available (API and cache both failed).', level='WARNING')
        return False

    async def refresh(self) -> bool:
        """Re-fetch polygon data from API and update cache."""
        if await self._fetch_from_api():
            self._save_cache()
            return True
        self._log('Polygon data refresh failed.', level='WARNING')
        return False

    def find_cities_at_point(self, lat: float, lon: float) -> list[str]:
        """Find all city names whose polygon contains the given point.

        Returns city names (original Hebrew names from HFC) sorted alphabetically.
        """
        results = []
        for city_name, polygon_rings in self._polygons.items():
            for ring in polygon_rings:
                if _point_in_polygon(lat, lon, ring):
                    results.append(city_name)
                    break
        results.sort()
        return results

    async def _fetch_from_api(self) -> bool:
        """Fetch segments list then each segment's polygon from the HFC backend."""
        try:
            segments = await self._fetch_segments()
            if not segments:
                return False

            polygons: dict[str, list[list[list[float]]]] = {}
            fetched = 0
            failed = 0

            for segment in segments:
                segment_id = segment.get('segmentId') or segment.get('id')
                name = segment.get('name') or segment.get('cityName', '')
                if not segment_id or not name:
                    continue

                polygon_rings = await self._fetch_polygon(segment_id)
                if polygon_rings:
                    polygons[name] = polygon_rings
                    fetched += 1
                else:
                    failed += 1

            if fetched == 0:
                self._log('No polygons fetched from API.', level='WARNING')
                return False

            self._polygons = polygons
            self._log(f'Loaded {fetched} city polygons from API ({failed} failed).')
            return True
        except Exception as e:
            self._log(f'Error fetching polygon data from API: {e}', level='WARNING')
            return False

    async def _fetch_segments(self) -> list[dict] | None:
        """Fetch the segments list from the HFC backend."""
        try:
            resp = await self._client.get(SEGMENTS_URL)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                self._log(f'Fetched {len(data)} segments from HFC backend.')
                return data
            self._log('Segments response is not a list.', level='WARNING')
            return None
        except httpx.HTTPStatusError as e:
            self._log(f'HTTP error fetching segments: {e.response.status_code}', level='WARNING')
        except httpx.TransportError as e:
            self._log(f'Network error fetching segments: {e}', level='WARNING')
        except Exception as e:
            self._log(f'Error fetching segments: {e}', level='WARNING')
        return None

    async def _fetch_polygon(self, segment_id) -> list[list[list[float]]] | None:
        """Fetch polygon data for a single segment."""
        url = POLYGON_URL_TEMPLATE.format(segment_id=segment_id)
        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
            data = resp.json()
            points = data.get('polygonPointList')
            if isinstance(points, list) and points:
                return points
            return None
        except Exception:
            return None

    def _save_cache(self):
        """Save current polygon data to the cache file."""
        try:
            os.makedirs(os.path.dirname(self._cache_path), exist_ok=True)
            with open(self._cache_path, 'w', encoding='utf-8') as f:
                json.dump(self._polygons, f, ensure_ascii=False)
            self._log(f'Polygon data cached to {self._cache_path}.')
        except Exception as e:
            self._log(f'Error saving polygon cache: {e}', level='WARNING')

    def _load_cache(self) -> bool:
        """Load polygon data from the cache file."""
        try:
            with open(self._cache_path, encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict) and data:
                self._polygons = data
                self._log(f'Loaded {len(data)} city polygons from cache.')
                return True
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
        return False
