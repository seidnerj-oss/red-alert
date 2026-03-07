import functools
import json
import os

from red_alert.core.utils import check_bom, standardize_name


class CityDataManager:
    """Manages city geographic data (coordinates, areas) sourced from ICBS (Israel Central Bureau of Statistics)."""

    def __init__(self, file_path: str, github_url: str, api_client, logger):
        self._local_file_path = file_path
        self._github_url = github_url
        self._api_client = api_client
        self._log = logger
        self._city_data = None
        self._city_details_map: dict[str, dict] = {}

    async def load_data(self, force_download=False):
        """Load city data, preferring local file unless forced or missing/invalid."""
        loaded = None
        if not force_download and os.path.exists(self._local_file_path):
            try:
                with open(self._local_file_path, encoding='utf-8-sig') as f:
                    loaded = json.load(f)
                if not loaded or 'areas' not in loaded:
                    self._log('Local city data invalid or empty. Will attempt download.', level='WARNING')
                    loaded = None
            except (json.JSONDecodeError, OSError, Exception) as e:
                self._log(f"Error reading local city data file '{self._local_file_path}': {e}. Will attempt download.", level='WARNING')
                loaded = None

        if loaded is None:
            self._log('Downloading city data from GitHub.')
            text = await self._api_client.download_file(self._github_url)
            if text:
                try:
                    text = check_bom(text)
                    loaded = json.loads(text)
                    if loaded and 'areas' in loaded:
                        try:
                            os.makedirs(os.path.dirname(self._local_file_path), exist_ok=True)
                            with open(self._local_file_path, 'w', encoding='utf-8-sig') as f:
                                json.dump(loaded, f, ensure_ascii=False, indent=2)
                            self._log('City data downloaded and saved locally.')
                        except Exception as e:
                            self._log(f"Error saving city data locally to '{self._local_file_path}': {e}", level='ERROR')
                    else:
                        self._log("Downloaded city data is invalid (missing 'areas' key).", level='ERROR')
                        loaded = None
                except json.JSONDecodeError as e:
                    self._log(f"Invalid city data JSON downloaded from '{self._github_url}': {e}", level='ERROR')
                    loaded = None
            else:
                self._log('Failed to download city data.', level='ERROR')

        if loaded and self._process_city_data(loaded):
            self._build_city_details_map()
            return True

        self._log('CRITICAL: Failed to load city data from both local file and download.', level='CRITICAL')
        self._city_data = None
        self._city_details_map = {}
        return False

    def _process_city_data(self, raw_data):
        """Process raw city data into the internal structure."""
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
        """Get city details (original name, coords, area) from the map using the standardized name."""
        if not isinstance(standardized_name, str) or not standardized_name:
            return None
        return self._city_details_map.get(standardized_name)
