from datetime import datetime
from typing import Any

from red_alert.core.constants import ICONS_AND_EMOJIS
from red_alert.core.i18n import get_translator
from red_alert.core.utils import parse_datetime_str, standardize_name


def generate_geojson_data(attributes, duration, city_data_manager, logger, language: str = 'en'):
    """Generate the GeoJSON structure (FeatureCollection)."""
    _ = get_translator(language)
    geo: dict[str, Any] = {'type': 'FeatureCollection', 'features': []}
    attrs = attributes or {}
    locations: dict[str, Any] = {}
    unknown_cities_logged = set()

    if duration == 'latest':
        cities_to_process = attrs.get('cities', [])
        alert_title = attrs.get('title', _('No alerts'))
        timestamp_str = attrs.get('last_changed', datetime.now().isoformat(timespec='microseconds'))
        category = attrs.get('cat', 0)
        description = attrs.get('desc', '')

        if not cities_to_process:
            return geo

        for city_display_name in cities_to_process:
            if not isinstance(city_display_name, str) or not city_display_name.strip():
                continue
            std = standardize_name(city_display_name)
            if not std:
                continue
            det = city_data_manager.get_city_details(std)

            if det and 'lat' in det and 'long' in det:
                try:
                    lat, lon = float(det['lat']), float(det['long'])
                    key = f'{lat},{lon}'
                    if key not in locations:
                        locations[key] = {'coords': [lon, lat], 'cities': set()}
                    locations[key]['cities'].add(city_display_name)
                except (ValueError, TypeError) as e:
                    if std not in unknown_cities_logged:
                        logger(f"GeoJSON ({duration}): Invalid coords for '{city_display_name}': {e}", level='WARNING')
                        unknown_cities_logged.add(std)
            elif std not in unknown_cities_logged:
                reason = 'Not found in city data' if not det else 'Missing coords'
                logger(f"GeoJSON ({duration}): SKIP city '{city_display_name}' (std: '{std}'). Reason: {reason}.", level='DEBUG')
                unknown_cities_logged.add(std)

        if locations:
            icon_mdi, emoji = ICONS_AND_EMOJIS.get(category, ('mdi:alert', '❗'))
            for key, loc_data in locations.items():
                city_names_at_point = sorted(list(loc_data['cities']))
                props = {
                    'name': ', '.join(city_names_at_point),
                    'icon': icon_mdi,
                    'label': emoji,
                    'description': f'{alert_title}\n{description}\n({timestamp_str})',
                    'alert_type': alert_title,
                    'timestamp': timestamp_str,
                    'category': category,
                }
                geo['features'].append(
                    {
                        'type': 'Feature',
                        'geometry': {'type': 'Point', 'coordinates': loc_data['coords']},
                        'properties': props,
                    }
                )

    elif duration == 'history':
        history_list = attrs.get('last_24h_alerts', [])
        if not history_list:
            return geo

        for alert in history_list:
            if not isinstance(alert, dict):
                continue
            city_display_name = alert.get('city')
            if not city_display_name or not isinstance(city_display_name, str):
                continue

            std = standardize_name(city_display_name)
            if not std:
                continue
            det = city_data_manager.get_city_details(std)

            if det and 'lat' in det and 'long' in det:
                try:
                    lat, lon = float(det['lat']), float(det['long'])
                    key = f'{lat},{lon}'
                    if key not in locations:
                        locations[key] = {'coords': [lon, lat], 'cities': set(), 'details': []}
                    locations[key]['details'].append(alert)
                    locations[key]['cities'].add(city_display_name)
                except (ValueError, TypeError) as e:
                    if std not in unknown_cities_logged:
                        logger(f"GeoJSON ({duration}): Invalid hist coords for '{city_display_name}': {e}", level='WARNING')
                        unknown_cities_logged.add(std)
            elif std not in unknown_cities_logged:
                reason = 'Not found in city data' if not det else 'Missing coords'
                logger(f"GeoJSON ({duration}): SKIP hist city '{city_display_name}' (std: '{std}'). Reason: {reason}.", level='DEBUG')
                unknown_cities_logged.add(std)

        if locations:
            icon_mdi, emoji = ('mdi:history', '📜')
            for key, loc_data in locations.items():
                if not loc_data.get('details'):
                    continue

                try:
                    latest_alert_at_loc = max(
                        loc_data['details'],
                        key=lambda x: parse_datetime_str(x.get('time', '')) or datetime.min,
                    )
                except (ValueError, TypeError) as max_err:
                    logger(f'GeoJSON ({duration}): Error finding latest alert time for location {key}: {max_err}', level='WARNING')
                    continue

                city_names_at_point = sorted(list(loc_data['cities']))
                alert_time_str = latest_alert_at_loc.get('time', 'N/A')
                alert_count = len(loc_data['details'])
                desc = (
                    f'{latest_alert_at_loc.get("title", _("Historical alert"))}\n'
                    f'{", ".join(city_names_at_point)}\n'
                    f'{_("Last time: {time}").format(time=alert_time_str)}\n'
                    f'{_("Total: {count} events").format(count=alert_count)}'
                )

                props = {
                    'name': ', '.join(city_names_at_point),
                    'area': latest_alert_at_loc.get('area', ''),
                    'icon': icon_mdi,
                    'label': emoji,
                    'description': desc,
                    'alert_count_at_location': alert_count,
                    'latest_alert_time': alert_time_str,
                }
                geo['features'].append(
                    {
                        'type': 'Feature',
                        'geometry': {'type': 'Point', 'coordinates': loc_data['coords']},
                        'properties': props,
                    }
                )
    else:
        logger(f"GeoJSON: Unknown duration type '{duration}'.", level='WARNING')

    return geo
