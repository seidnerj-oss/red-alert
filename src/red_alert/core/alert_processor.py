import re

from red_alert.core.constants import DEFAULT_UNKNOWN_AREA
from red_alert.core.i18n import get_translator


class AlertProcessor:
    def __init__(self, city_data_manager, icons_emojis_map: dict, logger, language: str = 'en'):
        self._city_data = city_data_manager
        self._icons = icons_emojis_map
        self._log = logger
        self._ = get_translator(language)
        self.max_msg_len = 700
        self.max_attr_len = 160000
        self.max_input_len = 255

    def extract_duration_from_desc(self, descr: str) -> int:
        """Extract alert duration in seconds from description text."""
        if not isinstance(descr, str):
            return 0
        m = re.search(r'(\d+)\s+(דקות|דקה)', descr)
        if m:
            try:
                minutes = int(m.group(1))
                return minutes * 60
            except ValueError:
                self._log(f"Could not parse number from duration string: '{m.group(1)}'", level='WARNING')
        return 0

    def _check_len(self, text: str, count: int, areas: str, max_len: int, context: str = 'message') -> str:
        """Truncate text if it exceeds max_len, adding a notice."""
        if not isinstance(text, str):
            return ''
        try:
            text_len = len(text)
            if text_len > max_len:
                _ = self._
                small_text = _('Widespread attack on {count} cities in: {areas}').format(count=count, areas=areas)
                return small_text
        except Exception as e:
            self._log(f'Error during _check_len for {context}: {e}', level='ERROR')
        return text

    def process_alert_window_data(self, category, title, description, window_std_cities, window_alerts_grouped):
        """Process the accumulated data for the current alert window to generate state attributes."""
        _ = self._
        log_prefix = '[Alert Processor]'

        # --- 1. Basic processing based on LATEST alert info ---
        icon, emoji = self._icons.get(category, ('mdi:alert', '❗'))
        duration = self.extract_duration_from_desc(description)

        # --- 2. Handle Empty Input ---
        if not window_std_cities:
            self._log(f'{log_prefix} Called with empty overall city set (window_std_cities). Returning default structure.', level='WARNING')
            input_text_state = title[: self.max_input_len] if title else _('No alerts')
            return {
                'areas_alert_str': '',
                'cities_list_sorted': [],
                'data_count': 0,
                'alerts_cities_str': '',
                'icon_alert': icon,
                'icon_emoji': emoji,
                'duration': duration,
                'text_wa_grouped': f'{emoji} *{title}*\n_{description}_',
                'text_tg_grouped': f'{emoji} **{title}**\n__{description}__',
                'text_status': title,
                'full_message_str': title,
                'alert_txt': title,
                'full_message_list': [],
                'input_text_state': input_text_state,
            }

        # --- 3. Process OVERALL accumulated cities ---
        overall_areas_set = set()
        overall_orig_cities_set = set()
        cities_by_area_overall = {}
        unknown_cities_logged_overall = set()
        for std in window_std_cities:
            det = self._city_data.get_city_details(std)
            area = DEFAULT_UNKNOWN_AREA
            name = std
            if det:
                area = det.get('area', DEFAULT_UNKNOWN_AREA)
                name = det.get('original_name', std)
            elif std not in unknown_cities_logged_overall:
                self._log(f"{log_prefix} Overall Processing: City '{std}' not found in city data. Using Area='{area}'.", level='WARNING')
                unknown_cities_logged_overall.add(std)
            overall_areas_set.add(area)
            overall_orig_cities_set.add(name)
            cities_by_area_overall.setdefault(area, set()).add(name)

        overall_areas_list_sorted = sorted(list(overall_areas_set))
        overall_areas_str = ', '.join(overall_areas_list_sorted) if overall_areas_list_sorted else DEFAULT_UNKNOWN_AREA
        overall_cities_list_sorted = sorted(list(overall_orig_cities_set))
        overall_count = len(overall_cities_list_sorted)
        overall_cities_str = ', '.join(overall_cities_list_sorted)

        # --- 4. Generate Standard Message Components ---
        full_overall_lines = []
        for area, names_set in sorted(cities_by_area_overall.items()):
            sorted_cities_str_area = ', '.join(sorted(list(names_set)))
            full_overall_lines.append(f'{area}: {sorted_cities_str_area}')
        status_str_raw = f'{title} - {overall_areas_str}: {overall_cities_str}'
        full_message_str_raw = title + '\n * ' + '\n * '.join(full_overall_lines)
        alert_txt_basic = ' * '.join(full_overall_lines)

        # --- 5. Generate Grouped WhatsApp and Telegram Messages ---
        wa_grouped_lines = []
        tg_grouped_lines = []
        num_alert_types_in_window = len(window_alerts_grouped)

        if num_alert_types_in_window > 1:
            header = _('Active alerts ({count} types)').format(count=num_alert_types_in_window)
            wa_grouped_lines.append(f'{emoji} *{header}*')
            tg_grouped_lines.append(f'{emoji} **{header}**')
        elif num_alert_types_in_window == 1:
            single_title = next(iter(window_alerts_grouped.keys()))
            wa_grouped_lines.append(f'{emoji} *{single_title}*')
            tg_grouped_lines.append(f'{emoji} **{single_title}**')
        else:
            self._log(f'{log_prefix} Grouped data empty despite overall cities present. Using latest title for header.', level='WARNING')
            wa_grouped_lines.append(f'{emoji} *{title}*')
            tg_grouped_lines.append(f'{emoji} **{title}**')

        for alert_title_group, areas_dict in sorted(window_alerts_grouped.items()):
            if num_alert_types_in_window > 1:
                wa_grouped_lines.append(f'\n🚨 *{alert_title_group}*')
                tg_grouped_lines.append(f'\n🚨 **{alert_title_group}**')
            for area, cities_set in sorted(areas_dict.items()):
                if not cities_set:
                    continue
                sorted_cities_str_group = ', '.join(sorted(list(cities_set)))
                wa_grouped_lines.append(f'> {area}\n{sorted_cities_str_group}')
                tg_grouped_lines.append(f'**__{area}__** - {sorted_cities_str_group}')

        if description:
            wa_grouped_lines.append(f'\n_{description}_')
            tg_grouped_lines.append(f'\n__{description}__')

        text_wa_grouped_raw = '\n'.join(wa_grouped_lines)
        text_tg_grouped_raw = '\n'.join(tg_grouped_lines)

        # --- 6. Truncate Results if Needed ---
        text_wa_grouped_checked = self._check_len(text_wa_grouped_raw, overall_count, overall_areas_str, self.max_msg_len, 'Grouped WhatsApp Msg')
        text_tg_grouped_checked = self._check_len(text_tg_grouped_raw, overall_count, overall_areas_str, self.max_msg_len, 'Grouped Telegram Msg')
        status_checked = self._check_len(status_str_raw, overall_count, overall_areas_str, self.max_attr_len, 'Status Attribute')
        full_message_str_checked = self._check_len(
            full_message_str_raw, overall_count, overall_areas_str, self.max_attr_len, 'Full Message Attribute'
        )
        overall_cities_str_checked = self._check_len(
            overall_cities_str, overall_count, overall_areas_str, self.max_attr_len, 'Cities String Attribute'
        )
        input_state = self._check_len(status_str_raw, overall_count, overall_areas_str, self.max_input_len, 'Input Text State')[: self.max_input_len]

        # --- 7. Return Final Attributes Dictionary ---
        return {
            'areas_alert_str': overall_areas_str,
            'cities_list_sorted': overall_cities_list_sorted,
            'data_count': overall_count,
            'alerts_cities_str': overall_cities_str_checked,
            'icon_alert': icon,
            'icon_emoji': emoji,
            'duration': duration,
            'text_wa_grouped': text_wa_grouped_checked,
            'text_tg_grouped': text_tg_grouped_checked,
            'text_status': status_checked,
            'full_message_str': full_message_str_checked,
            'alert_txt': alert_txt_basic,
            'full_message_list': full_overall_lines,
            'input_text_state': input_state,
        }
