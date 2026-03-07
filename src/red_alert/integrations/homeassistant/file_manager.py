import csv
import json
import os
from io import StringIO

from red_alert.core.i18n import get_translator
from red_alert.core.utils import parse_datetime_str


class FileManager:
    def __init__(self, paths: dict, save_enabled: bool, day_names_map: dict, timer_duration, logger, language: str = 'en'):
        self._paths = paths
        self._save_enabled = save_enabled
        self._day_names = day_names_map
        self._timer_duration = timer_duration
        self._log = logger
        self._last_saved_alert_id = None
        self._ = get_translator(language)

    def get_from_json(self):
        """Load the last alert state from the JSON backup file."""
        path = self._paths.get('json_backup')
        if not self._save_enabled or not path or not os.path.exists(path):
            return None
        try:
            with open(path, encoding='utf-8-sig') as f:
                data = json.load(f)
            if isinstance(data, dict) and ('id' in data or 'title' in data):
                return data
            self._log(f'JSON backup content invalid or empty: {path}', level='WARNING')
        except json.JSONDecodeError as e:
            self._log(f'Error decoding JSON backup {path}: {e}', level='ERROR')
        except Exception as e:
            self._log(f'Error reading JSON backup {path}: {e}', level='ERROR')
        return None

    def create_csv_header_if_needed(self):
        """Create the CSV history file with a header row if it doesn't exist or is empty."""
        path = self._paths.get('csv')
        if not self._save_enabled or not path:
            return
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if not os.path.exists(path) or os.path.getsize(path) == 0:
                with open(path, 'w', encoding='utf-8-sig', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow(['ID', 'DAY', 'DATE', 'TIME', 'TITLE', 'COUNT', 'AREAS', 'CITIES', 'DESC', 'ALERTS_IN_SEQUENCE'])
                self._log(f'Created/ensured CSV header in: {path}')
        except PermissionError as e:
            self._log(f'Permission error creating/writing CSV header: {path} - {e}', level='ERROR')
        except Exception as e:
            self._log(f'Error creating/checking CSV header: {path} - {e}', level='ERROR')

    def save_json_backup(self, data):
        """Save the current alert state to the JSON backup file."""
        path = self._paths.get('json_backup')
        if not self._save_enabled or not path:
            return
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8-sig') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except PermissionError as e:
            self._log(f'Permission error writing JSON backup to {path}: {e}', level='ERROR')
        except TypeError as e:
            self._log(f'Error writing JSON backup to {path}: Data not JSON serializable - {e}', level='ERROR')
        except Exception as e:
            self._log(f'Error writing JSON backup to {path}: {e}', level='ERROR')

    def save_history_files(self, attrs):
        """Save the summary of the completed alert window to TXT and CSV files."""
        _ = self._
        if not self._save_enabled or not attrs:
            return

        alert_id = attrs.get('id', 0)
        if alert_id == self._last_saved_alert_id and alert_id != 0:
            return

        txt_p, csv_p = self._paths.get('txt_history'), self._paths.get('csv')
        if not txt_p or not csv_p:
            self._log('History file saving skipped (TXT or CSV path missing).', level='WARNING')
            return

        fmt_time, fmt_date, day_name_he = _('Error processing time'), _('Error processing time'), _('Error processing time')
        try:
            last_update_str = attrs.get('last_changed')
            last_update_dt = parse_datetime_str(last_update_str) or __import__('datetime').datetime.now()
            event_dt = last_update_dt

            fmt_time = event_dt.strftime('%H:%M:%S')
            fmt_date = event_dt.strftime('%d/%m/%Y')
            day_name_en = event_dt.strftime('%A')
            day_name_he = self._day_names.get(day_name_en, day_name_en)
            date_str = f'\n{day_name_he}, {fmt_date}, {fmt_time}'
        except Exception as e:
            self._log(f'Error processing time for history file context: {e}', level='ERROR')
            date_str = f'\n{_("Error processing time")}'

        # --- Save to TXT ---
        try:
            os.makedirs(os.path.dirname(txt_p), exist_ok=True)
            with open(txt_p, 'a', encoding='utf-8-sig') as f:
                f.write(date_str + '\n')
                message_to_write = attrs.get('full_message_str', attrs.get('alert_alt', attrs.get('text_status', _('No details'))))
                f.write(message_to_write + '\n')
        except PermissionError as e:
            self._log(f'Permission error writing TXT history to {txt_p}: {e}', level='ERROR')
        except Exception as e:
            self._log(f'Error writing TXT history to {txt_p}: {e}', level='ERROR')

        # --- Save to CSV ---
        try:
            self.create_csv_header_if_needed()

            csv_data = [
                str(alert_id),
                day_name_he,
                fmt_date,
                fmt_time,
                attrs.get('prev_title', 'N/A'),
                attrs.get('prev_data_count', 0),
                attrs.get('prev_areas_alert_str', ''),
                attrs.get('prev_alerts_cities_str', ''),
                attrs.get('prev_desc', ''),
                attrs.get('prev_alerts_count', 0),
            ]

            output = StringIO()
            writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)
            writer.writerow(csv_data)
            line = output.getvalue().strip()
            output.close()

            os.makedirs(os.path.dirname(csv_p), exist_ok=True)
            with open(csv_p, 'a', encoding='utf-8-sig', newline='') as f:
                f.write(line + '\n')

            self._last_saved_alert_id = alert_id

        except PermissionError as e:
            self._log(f'Permission error writing CSV history to {csv_p}: {e}', level='ERROR')
        except Exception as e:
            self._log(f'Error writing CSV history to {csv_p}: {e}', level='ERROR', exc_info=True)

    def clear_last_saved_id(self):
        """Reset the tracker for the last saved alert ID."""
        self._last_saved_alert_id = None

    def save_geojson_file(self, geojson_data, path):
        """Save the provided GeoJSON data structure to the specified file path."""
        if not self._save_enabled:
            return
        if not path:
            self._log('Skipping GeoJSON save: Path is missing.', level='WARNING')
            return
        if not isinstance(geojson_data, dict) or 'features' not in geojson_data:
            self._log(f'Skipping GeoJSON save to {path}: Invalid data structure.', level='WARNING')
            return

        num_features = len(geojson_data.get('features', []))
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8-sig') as f:
                json.dump(geojson_data, f, ensure_ascii=False, indent=2)

            log_level = 'DEBUG'
            if 'latest' in path and num_features > 0:
                log_level = 'INFO'
            elif 'latest' in path and num_features == 0:
                log_level = 'DEBUG'

            if num_features > 0 or '24h' in path:
                self._log(f'Successfully wrote GeoJSON ({num_features} features) to: {path}', level=log_level)

        except PermissionError as e:
            self._log(f'PERMISSION ERROR writing GeoJSON to {path}: {e}. Check permissions.', level='ERROR')
        except TypeError as e:
            self._log(f'Error writing GeoJSON to {path}: Data not JSON serializable - {e}', level='ERROR')
        except Exception as e:
            self._log(f'Error writing GeoJSON to {path}: {e}', level='ERROR', exc_info=True)
