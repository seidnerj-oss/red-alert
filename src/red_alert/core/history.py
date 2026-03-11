from datetime import datetime, timedelta

from red_alert.core.constants import DEFAULT_UNKNOWN_AREA
from red_alert.core.i18n import get_translator
from red_alert.core.utils import parse_datetime_str, standardize_name


class HistoryManager:
    def __init__(self, hours_to_show, city_data_manager, logger, timer_duration_seconds, language: str = 'en'):
        """Initialize the HistoryManager."""
        self._ = get_translator(language)

        if not isinstance(timer_duration_seconds, (int, float)) or timer_duration_seconds <= 0:
            logger(f'Invalid timer_duration_seconds ({timer_duration_seconds}), using default 120.', level='WARNING')
            timer_duration_seconds = 120
        if not isinstance(hours_to_show, (int, float)) or hours_to_show <= 0:
            logger(f'Invalid hours_to_show ({hours_to_show}), using default 4.', level='WARNING')
            hours_to_show = 4

        self._hours_to_show = hours_to_show
        self._city_data = city_data_manager
        self._log = logger
        self._timer_duration_seconds = timer_duration_seconds
        self._history_list: list[dict] = []
        self._added_in_current_poll: set[tuple] = set()
        self._max_history_events = 2000

    def clear_poll_tracker(self):
        """Clear the set tracking entries added during the last poll cycle."""
        self._added_in_current_poll.clear()

    async def load_initial_history(self, api_client):
        """Load initial history data from the API."""
        _ = self._
        data = await api_client.get_alert_history()
        if not isinstance(data, list):
            self._history_list = []
            self._log(
                'Failed to load initial history or history was empty/invalid.\n'
                'If no alerts in the past 24 hours that is normal and you can ignore this message',
                level='WARNING',
            )
            return

        now = datetime.now()
        cutoff = now - timedelta(hours=self._hours_to_show)
        temp_hist = []
        unknown_cities_logged = set()
        loaded_count = 0
        parse_errors = 0

        for e in data:
            loaded_count += 1
            if not isinstance(e, dict):
                continue
            alert_date_str = e.get('alertDate')
            if not isinstance(alert_date_str, str):
                continue
            t = parse_datetime_str(alert_date_str)

            if not isinstance(t, datetime):
                if alert_date_str:
                    parse_errors += 1
                continue
            if t < cutoff:
                continue

            city_raw = e.get('data', _('Unknown'))
            std = standardize_name(city_raw)
            det = self._city_data.get_city_details(std)
            area = det['area'] if det else DEFAULT_UNKNOWN_AREA
            orig_name = det['original_name'] if det else city_raw

            if not det and std and std not in unknown_cities_logged:
                self._log(f"Initial History Load: City '{std}' (raw: '{city_raw}') not found. Area='{area}'.", level='DEBUG')
                unknown_cities_logged.add(std)

            temp_hist.append(
                {
                    'title': e.get('title', _('Unknown')),
                    'city': orig_name,
                    'area': area,
                    'time': t,
                }
            )

        if parse_errors > 0:
            self._log(f'Initial History Load: Encountered {parse_errors} entries with unparseable dates.', level='WARNING')

        temp_hist.sort(key=lambda x: x.get('time', datetime.min), reverse=True)
        self._history_list = temp_hist
        cities_in_period_raw = set(a['city'] for a in self._history_list)

        self._log(
            f'Initial history: Processed {loaded_count} raw alerts, '
            f'kept {len(self._history_list)} within {self._hours_to_show}h ({len(cities_in_period_raw)} unique cities).'
        )

    def update_history(self, title: str, std_payload_cities: set):
        """Update the history list with new alerts from the current payload."""
        now = datetime.now()
        unknown_cities_logged = set()
        added_count_this_call = 0

        if not std_payload_cities:
            return

        for std in std_payload_cities:
            if not std:
                continue
            det = self._city_data.get_city_details(std)
            area = DEFAULT_UNKNOWN_AREA
            orig_city_name = std
            if det:
                area = det.get('area', DEFAULT_UNKNOWN_AREA)
                orig_city_name = det.get('original_name', std)
            elif std not in unknown_cities_logged:
                self._log(f"History Add: City '{std}' not found. Using Area='{area}'.", level='WARNING')
                unknown_cities_logged.add(std)

            history_key = (title, std, area)

            if history_key not in self._added_in_current_poll:
                self._history_list.append(
                    {
                        'title': title,
                        'city': orig_city_name,
                        'area': area,
                        'time': now,
                    }
                )
                self._added_in_current_poll.add(history_key)
                added_count_this_call += 1

        if added_count_this_call > 0:
            self._history_list.sort(key=lambda x: x.get('time', datetime.min), reverse=True)

    def restructure_alerts(self, alerts_list: list) -> dict:
        """Group alerts by title, then area, including city and time (HH:MM:SS)."""
        _ = self._
        structured_data: dict[str, dict] = {}
        if not alerts_list:
            return structured_data

        for alert in alerts_list:
            if not isinstance(alert, dict):
                self._log(f'Restructure: Skipping non-dict item: {type(alert)}', level='WARNING')
                continue

            title = alert.get('title', _('Unknown'))
            area = alert.get('area', DEFAULT_UNKNOWN_AREA)
            city = alert.get('city', _('Unknown'))
            time_str = alert.get('time', '')

            time_display = '??:??:??'
            if isinstance(time_str, str) and ' ' in time_str and ':' in time_str:
                try:
                    time_display = time_str.split(' ')[1]
                except IndexError:
                    self._log(f"Restructure: Could not split time from string '{time_str}' for '{city}'. Using default.", level='DEBUG')
            elif isinstance(time_str, str) and time_str:
                self._log(f"Restructure: Unexpected time string format '{time_str}' for '{city}'. Using default.", level='DEBUG')

            area_dict = structured_data.setdefault(title, {})
            city_list_in_area = area_dict.setdefault(area, [])
            city_list_in_area.append({'city': city, 'time': time_display})

        for title_group in structured_data.values():
            for area_group in title_group.values():
                area_group.sort(key=lambda x: x.get('city', ''))

        return structured_data

    def get_history_attributes(self) -> dict:
        """Generate attributes for history sensors."""
        _ = self._

        # === Step 1: Pruning based on 'hours_to_show' ===
        now = datetime.now()
        cutoff = now - timedelta(hours=self._hours_to_show)
        pruned_history_list = [a for a in self._history_list if isinstance(a.get('time'), datetime) and a['time'] >= cutoff]

        # === Step 2: Aggregate alerts into event blocks and merge titles ===
        city_event_blocks = {}
        merge_window = timedelta(minutes=50)

        for alert in pruned_history_list:
            if not all(k in alert for k in ['city', 'time']) or not isinstance(alert.get('time'), datetime):
                self._log(f'Merge Logic: Skipping malformed history entry: {alert}', level='WARNING')
                continue

            city_name = alert['city']
            alert_time = alert['time']

            if city_name not in city_event_blocks:
                city_event_blocks[city_name] = [[alert]]
                continue

            latest_time_in_last_block = city_event_blocks[city_name][-1][0]['time']

            if latest_time_in_last_block - alert_time < merge_window:
                city_event_blocks[city_name][-1].append(alert)
            else:
                city_event_blocks[city_name].append([alert])

        merged_history_with_dt = []
        for city, blocks in city_event_blocks.items():
            for block in blocks:
                if not block:
                    continue

                latest_alert_in_block = block[0]
                all_titles_in_block = set()
                for alert_in_block in block:
                    original_title = alert_in_block.get('title', _('Unknown'))
                    translated_title = _('Pre-alerts') if original_title == 'בדקות הקרובות צפויות להתקבל התרעות באזורך' else original_title
                    all_titles_in_block.add(translated_title)

                final_title = ' & '.join(sorted(list(all_titles_in_block)))
                merged_alert = {
                    'title': final_title,
                    'city': latest_alert_in_block['city'],
                    'area': latest_alert_in_block['area'],
                    'time': latest_alert_in_block['time'],
                }
                merged_history_with_dt.append(merged_alert)

        merged_history_with_dt.sort(key=lambda x: x.get('time', datetime.min), reverse=True)

        # === Step 3: Limit the number of events ===
        original_count = len(merged_history_with_dt)
        if original_count > self._max_history_events:
            self._log(
                f'History contains {original_count} alert events, which is over the limit. Truncating to the newest {self._max_history_events}.',
                level='DEBUG',
            )
            merged_history_with_dt = merged_history_with_dt[: self._max_history_events]

        # === Step 4: Format for attributes ===
        final_history_list_for_ha = []
        final_cities_set = set()

        for a in merged_history_with_dt:
            time_str = 'N/A'
            try:
                time_str = a['time'].strftime('%Y-%m-%d %H:%M:%S')
            except (AttributeError, Exception) as e:
                self._log(f'History Formatting: Error formatting time {a.get("time")}: {e}', level='WARNING')
                time_str = str(a.get('time', 'N/A'))

            city_name = a.get('city', _('Unknown'))
            final_history_list_for_ha.append(
                {
                    'title': a.get('title', _('Unknown')),
                    'city': city_name,
                    'area': a.get('area', DEFAULT_UNKNOWN_AREA),
                    'time': time_str,
                }
            )
            final_cities_set.add(city_name)

        # === Step 5: Restructure ===
        final_grouped_structure = self.restructure_alerts(final_history_list_for_ha)

        # === Step 6: Return ===
        return {
            'cities_past_24h': sorted(list(final_cities_set)),
            'last_24h_alerts': final_history_list_for_ha,
            'last_24h_alerts_group': final_grouped_structure,
        }
