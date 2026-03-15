"""
red-alert - AppDaemon App for Home Assistant
=============================================

Monitors the official Israeli Home Front Command API for all alert types
(missile fire, hostile aircraft intrusion, earthquakes, tsunamis, terrorist
infiltration, hazardous materials, and more) and makes the information
available in Home Assistant.

**Example `apps.yaml` Configuration:**
```yaml
red_alert:
  module: red_alert
  class: RedAlert

  # --- Core Settings ---
  interval: 5
  timer: 120
  sensor_name: "red_alert"
  language: "en"              # "en" (default) or "he"

  # --- History & Saving ---
  save_to_file: True
  hours_to_show: 4

  # --- Optional Features ---
  mqtt: False
  event: True

  # --- Location Specific ---
  areas_of_interest:
     - "תל אביב - מרכז העיר"
```
"""

import asyncio
import atexit
import json
import os
import random
import time

import httpx
from appdaemon.plugins.hass.hassapi import Hass
from datetime import datetime

from red_alert.core.alert_processor import AlertProcessor
from red_alert.integrations.inputs.hfc.api_client import HomeFrontCommandApiClient
from red_alert.core.constants import DAY_NAMES, DEFAULT_UNKNOWN_AREA, ICONS_AND_EMOJIS
from red_alert.core.state import ALL_CLEAR_CATEGORY, PRE_ALERT_CATEGORY, PRE_ALERT_TITLE_PHRASES
from red_alert.core.history import HistoryManager
from red_alert.core.i18n import get_translator
from red_alert.core.city_data import CityDataManager
from red_alert.core.utils import parse_datetime_str, standardize_name
from red_alert.integrations.outputs.homeassistant.file_manager import FileManager
from red_alert.integrations.outputs.homeassistant.geojson import generate_geojson_data

# Singleton guard: prevents double-initialisation
_IS_RUNNING = False

# Determine the project root directory (for data files)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))))


class RedAlert(Hass):
    async def initialize(self):
        """Initialize the AppDaemon application."""
        self.log('--------------------------------------------------')
        self.log('       Initializing Red Alert App')
        self.log('--------------------------------------------------')
        global _IS_RUNNING
        if _IS_RUNNING:
            self.log('red-alert is already running - skipping duplicate initialize.', level='WARNING')
            return
        _IS_RUNNING = True
        atexit.register(self._cleanup_on_exit)

        # --- Configuration Loading & Validation ---
        self.interval = self.args.get('interval', 1)
        self.timer_duration = self.args.get('timer', 120)
        self.save_to_file = self.args.get('save_to_file', self.args.get('save_2_file', True))
        self.sensor_name = self.args.get('sensor_name', 'red_alert')
        self.areas_of_interest_config = self.args.get('areas_of_interest', [])
        self.areas_of_interest_config.append('ברחבי הארץ')
        self.hours_to_show = self.args.get('hours_to_show', 1)
        self.mqtt_topic = self.args.get('mqtt', False)
        self.ha_event = self.args.get('event', True)
        self.language = self.args.get('language', 'en')

        # Initialize translator
        self._ = get_translator(self.language)

        # Validate config types
        if not isinstance(self.interval, (int, float)) or self.interval < 1:
            self.log(f'Invalid interval ({self.interval}), must be >= 1. Using default 1s.', level='WARNING')
            self.interval = 1
        if not isinstance(self.timer_duration, (int, float)) or self.timer_duration <= 0:
            self.log(f'Invalid timer ({self.timer_duration}), must be > 0. Using default 120s.', level='WARNING')
            self.timer_duration = 120
        if not isinstance(self.hours_to_show, (int, float)) or self.hours_to_show <= 0:
            self.log(f'Invalid hours_to_show ({self.hours_to_show}), must be > 0. Using default 4h.', level='WARNING')
            self.hours_to_show = 4
        if not isinstance(self.sensor_name, str) or not self.sensor_name.strip():
            self.log("Invalid sensor_name, using default 'red_alert'.", level='WARNING')
            self.sensor_name = 'red_alert'
        if not isinstance(self.areas_of_interest_config, list):
            self.log(f'Invalid areas_of_interest format (should be a list), got {type(self.areas_of_interest_config)}. Ignoring.', level='WARNING')
            self.areas_of_interest_config = []

        self.log(
            f'Config: Interval={self.interval}s, Timer={self.timer_duration}s, '
            f'SaveFiles={self.save_to_file}, HistoryHours={self.hours_to_show}, '
            f'MQTT={self.mqtt_topic}, Event={self.ha_event}, Language={self.language}'
        )

        # --- Entity ID Setup ---
        base = self.sensor_name
        self.main_sensor = f'binary_sensor.{base}'
        self.city_sensor = f'binary_sensor.{base}_city'
        self.main_sensor_pre_alert = f'binary_sensor.{base}_pre_alert'
        self.city_sensor_pre_alert = f'binary_sensor.{base}_city_pre_alert'
        self.main_sensor_active_alert = f'binary_sensor.{base}_active_alert'
        self.city_sensor_active_alert = f'binary_sensor.{base}_city_active_alert'
        self.main_text = f'input_text.{base}'
        self.activate_alert = f'input_boolean.{base}_test'
        self.history_cities_sensor = f'sensor.{base}_history_cities'
        self.history_list_sensor = f'sensor.{base}_history_list'
        self.history_group_sensor = f'sensor.{base}_history_group'

        # --- File Path Setup ---
        www_base = self._get_www_path()
        city_data_local_path = os.path.join(_PROJECT_ROOT, 'data', 'city_data.json')
        if www_base:
            self.file_paths = {
                'txt_history': os.path.join(www_base, f'{base}_history.txt'),
                'csv': os.path.join(www_base, f'{base}_history.csv'),
                'json_backup': os.path.join(www_base, f'{base}_history.json'),
                'geojson_latest': os.path.join(www_base, f'{base}_latest.geojson'),
                'geojson_history': os.path.join(www_base, f'{base}_24h.geojson'),
                'city_data_local': city_data_local_path,
            }
            self._verify_www_writeable(www_base)
        else:
            self.log('Could not determine www path. File saving features will be disabled.', level='ERROR')
            self.save_to_file = False
            self.file_paths = {'city_data_local': city_data_local_path}

        # --- HTTP Session Setup ---
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36 AppDaemon/red-alert',
            'Referer': 'https://www.oref.org.il/',
            'X-Requested-With': 'XMLHttpRequest',
            'Accept': 'application/json',
            'Accept-Encoding': 'gzip, deflate, br',
            'Accept-Language': 'he,en;q=0.9',
            'Connection': 'keep-alive',
            'Pragma': 'no-cache',
            'Cache-Control': 'no-cache',
        }
        self.session = httpx.AsyncClient(headers=headers, timeout=15.0)

        api_urls = {
            'live': 'https://www.oref.org.il/WarningMessages/alert/alerts.json',
            'history': 'https://www.oref.org.il/WarningMessages/alert/History/AlertsHistory.json',
            'city_data_github': None,
        }
        self.api_client = HomeFrontCommandApiClient(self.session, api_urls, self.log)

        # --- State Variables ---
        self.alert_sequence_count = 0
        self.no_active_alerts_polls = 0
        self.last_alert_time = None
        self.last_processed_alert_id = None
        self.window_alerts_grouped = {}
        self.prev_alert_final_attributes = None
        self.cities_past_window_std = set()
        self.test_alert_cycle_flag = 0
        self.test_alert_start_time = 0
        self._poll_running = False
        self._terminate_event = asyncio.Event()
        self.last_active_payload_details = None
        self.last_history_attributes_cache = None

        # --- Helper Class Instantiation ---
        self.city_data_manager = CityDataManager(self.file_paths['city_data_local'], api_urls['city_data_github'], self.api_client, self.log)
        self.alert_processor = AlertProcessor(self.city_data_manager, ICONS_AND_EMOJIS, self.log, self.language)
        self.history_manager = HistoryManager(self.hours_to_show, self.city_data_manager, self.log, self.timer_duration, self.language)
        self.file_manager = FileManager(self.file_paths, self.save_to_file, DAY_NAMES, self.timer_duration, self.log, self.language)

        # --- Initial State Setup ---
        try:
            await self.set_state(self.main_sensor, state='off', attributes={'script_status': 'initializing', 'timestamp': datetime.now().isoformat()})
        except Exception as e:
            self.log(f'Error setting initial sensor state during init: {e}', level='WARNING')

        # --- Critical Dependency: Load City Data ---
        if not await self.city_data_manager.load_data():
            self.log('FATAL: City data load failed. Cannot map cities to areas. Aborting initialization.', level='CRITICAL')
            error_attrs = {'error': 'City data failed to load', 'status': 'error', 'timestamp': datetime.now().isoformat()}
            try:
                await self.set_state(self.main_sensor, state='unavailable', attributes=error_attrs)
            except Exception as e_set:
                self.log(f'Error setting sensor to unavailable state after city data failure: {e_set}', level='ERROR')
            _IS_RUNNING = False
            await self.terminate()
            return

        # --- Validate Configured City Names ---
        self._validate_configured_cities()

        # --- Initialize HA Entities and Load Initial Data ---
        await self._initialize_ha_sensors()
        await self._load_initial_data()

        # --- Register Test Boolean Listener ---
        try:
            self.listen_state(self._test_boolean_callback, self.activate_alert, new='on')
            self.log(f'Listening for test activation on {self.activate_alert}', level='INFO')
        except Exception as e:
            self.log(f'Error setting up listener for {self.activate_alert}: {e}', level='ERROR')

        # --- Start Polling Loop ---
        self.log('Scheduling first API poll.')
        self.run_in(self._poll_alerts_callback_sync, 5)

        # Update sensor status to running
        running_attrs = {'script_status': 'running', 'timestamp': datetime.now().isoformat()}
        try:
            current_main_state = await self.get_state(self.main_sensor, attribute='all')
            if current_main_state and 'attributes' in current_main_state:
                base_attrs = current_main_state.get('attributes', {})
                if current_main_state.get('state', 'off') == 'off':
                    merged_attrs = {**base_attrs, **running_attrs}
                else:
                    merged_attrs = {**base_attrs, 'script_status': 'running'}
                await self.set_state(self.main_sensor, state=current_main_state.get('state', 'off'), attributes=merged_attrs)
            else:
                await self.set_state(self.main_sensor, state='off', attributes=running_attrs)
        except Exception as e:
            self.log(f'Error setting running status attribute: {e}', level='WARNING')

        self.log('--------------------------------------------------')
        self.log('  Initialization Complete. Monitoring Red Alerts.')
        self.log('--------------------------------------------------')

    def _get_www_path(self):
        """Try to determine the Home Assistant www path."""
        ha_config_dir_options = ['/homeassistant', '/config', '/usr/share/hassio/homeassistant', '/root/config']
        for d in ha_config_dir_options:
            www_path = os.path.join(d, 'www')
            if os.path.isdir(www_path):
                return www_path

        ha_config_dir = getattr(self, 'config_dir', None)
        if ha_config_dir and os.path.isdir(os.path.join(ha_config_dir, 'www')):
            self.log(f'Using www path from HA config dir: {os.path.join(ha_config_dir, "www")}', level='INFO')
            return os.path.join(ha_config_dir, 'www')

        ad_config_dir = getattr(self, 'config_dir', _PROJECT_ROOT)
        potential_ha_config = os.path.dirname(ad_config_dir)
        www_path_guess = os.path.join(potential_ha_config, 'www')
        if os.path.isdir(www_path_guess):
            self.log(f'Using guessed www path relative to AppDaemon config: {www_path_guess}', level='WARNING')
            return www_path_guess

        self.log('Could not reliably determine www path.', level='ERROR')
        return None

    def _verify_www_writeable(self, www_base):
        """Check if the www directory is writeable."""
        if not self.save_to_file:
            return
        try:
            os.makedirs(www_base, exist_ok=True)
            test_file = os.path.join(www_base, f'.{self.sensor_name}_write_test_{random.randint(1000, 9999)}')
            with open(test_file, 'w') as f:
                f.write('test')
            os.remove(test_file)
        except PermissionError as e:
            self.log(f"PERMISSION ERROR creating/writing to www directory '{www_base}': {e}. Disabling file saving.", level='ERROR')
            self.save_to_file = False
        except OSError as e:
            self.log(f"OS ERROR accessing www directory '{www_base}': {e}. Disabling file saving.", level='ERROR')
            self.save_to_file = False
        except Exception as e:
            self.log(f"Unexpected error verifying write access to www directory '{www_base}': {e}. Disabling file saving.", level='ERROR')
            self.save_to_file = False

    def _validate_configured_cities(self):
        """Validate cities from config against loaded city data."""
        self.areas_of_interest_std = set()
        if not self.areas_of_interest_config:
            self.log('No areas_of_interest provided in configuration.', level='INFO')
            return

        found_all = True
        processed_count = 0
        invalid_entries = 0
        for city_config_raw in self.areas_of_interest_config:
            if not isinstance(city_config_raw, str) or not city_config_raw.strip():
                self.log(f"Config WARNING: Invalid/empty value found in areas_of_interest: '{city_config_raw}'. Skipping.", level='WARNING')
                invalid_entries += 1
                continue

            processed_count += 1
            city_config_std = standardize_name(city_config_raw)
            if not city_config_std:
                self.log(f"Config WARNING: City '{city_config_raw}' resulted in empty standardized name. Skipping.", level='WARNING')
                invalid_entries += 1
                continue

            self.areas_of_interest_std.add(city_config_std)
            details = self.city_data_manager.get_city_details(city_config_std)
            if details is None:
                self.log(
                    f"Config WARNING: City '{city_config_raw}' (standardized: '{city_config_std}') "
                    f"not found in city data. The '{self.city_sensor}' may not trigger correctly for this entry.",
                    level='WARNING',
                )
                found_all = False

        valid_count = processed_count - invalid_entries
        if valid_count == 0 and processed_count > 0:
            self.log('No valid areas_of_interest found after processing configuration entries.', level='WARNING')
        elif found_all and valid_count > 0:
            self.log(f'All {valid_count} configured areas_of_interest validated successfully.', level='INFO')
        elif valid_count > 0:
            self.log(
                f'Configured areas_of_interest validation complete. {len(self.areas_of_interest_std)} unique valid names processed. Some warnings issued.',
                level='WARNING',
            )

    def _poll_alerts_callback_sync(self, kwargs):
        """Callback trampoline to run the async poll function. Prevents overlapping runs."""
        if self._poll_running:
            return
        self._poll_running = True
        self.create_task(self._poll_and_schedule_next())

    async def _poll_and_schedule_next(self):
        """Run the poll logic and schedule the next poll."""
        try:
            if self._terminate_event.is_set():
                self.log('Termination signal received, skipping poll.', level='INFO')
                return
            await self.poll_alerts()
        except Exception as e:
            self.log(f'CRITICAL ERROR during poll_alerts execution: {e.__class__.__name__} - {e}', level='CRITICAL', exc_info=True)
            try:
                await self.set_state(
                    self.main_sensor,
                    attributes={'script_status': 'error', 'last_error': f'{e.__class__.__name__}: {e}', 'timestamp': datetime.now().isoformat()},
                )
            except Exception as set_err:
                self.log(f'Error setting error status on sensor: {set_err}', level='ERROR')
        finally:
            self._poll_running = False
            if not self._terminate_event.is_set():
                self.run_in(self._poll_alerts_callback_sync, self.interval)
            else:
                self.log('Termination signal received after poll, not scheduling next.', level='INFO')

    def terminate(self):
        """Synchronous callback invoked by AppDaemon when shutting down."""
        self.log('AppDaemon shutdown detected: scheduling async termination...', level='INFO')
        if hasattr(self, '_terminate_event'):
            try:
                self._terminate_event.set()
            except Exception:
                pass
        self.create_task(self._async_terminate())

    async def _async_terminate(self):
        """Gracefully shut down."""
        self.log('--------------------------------------------------')
        self.log('Async Terminate: cleaning up Red Alert App')
        self.log('--------------------------------------------------')
        global _IS_RUNNING

        if not _IS_RUNNING:
            return

        _IS_RUNNING = False
        if hasattr(self, '_terminate_event'):
            self._terminate_event.set()

        await asyncio.sleep(0)

        term_attrs = {'script_status': 'terminated', 'timestamp': datetime.now().isoformat()}
        tasks = []
        for entity in (
            self.main_sensor,
            self.city_sensor,
            self.main_sensor_pre_alert,
            self.city_sensor_pre_alert,
            self.main_sensor_active_alert,
            self.city_sensor_active_alert,
        ):
            try:
                if await self.entity_exists(entity):
                    tasks.append(self.set_state(entity, state='off', attributes=term_attrs))
            except Exception as e:
                self.log(f'Error checking/setting {entity} on terminate: {e}', level='WARNING')

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        client = getattr(self, 'session', None)
        if client and not client.is_closed:
            try:
                await client.aclose()
            except Exception as e:
                self.log(f'Error closing HTTP client: {e}', level='WARNING')

        self.log('Red Alert App shutdown complete.')
        self.log('--------------------------------------------------')

    def _cleanup_on_exit(self):
        """Synchronous cleanup function called by atexit."""
        global _IS_RUNNING
        if not _IS_RUNNING:
            return

        log_func = getattr(self, 'log', print)
        log_func('atexit: Script was running, attempting final cleanup steps.', level='INFO')
        _IS_RUNNING = False

        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                loop.call_soon_threadsafe(self._terminate_event.set)
                loop.call_soon_threadsafe(asyncio.create_task, self.terminate())
            else:
                try:
                    asyncio.run(self.terminate())
                except Exception as e2:
                    log_func(f'atexit: Error running terminate() directly: {e2}', level='WARNING')
        except Exception as e:
            log_func(f'atexit: Error accessing/signalling loop: {e}', level='WARNING')

    def _is_iso_format(self, ds: str) -> str:
        """Parse a datetime string and return it in ISO format with microseconds."""
        dt = parse_datetime_str(ds)
        now_fallback = datetime.now().isoformat(timespec='microseconds')
        if dt:
            try:
                return dt.isoformat(timespec='microseconds')
            except Exception as e:
                self.log(f"Error formatting datetime '{dt}' to ISO: {e}. Falling back to current time.", level='WARNING')
                return now_fallback
        else:
            return now_fallback

    async def _initialize_ha_sensors(self):
        """Ensure required HA entities exist with default states/attributes."""
        _ = self._
        now_iso = datetime.now().isoformat(timespec='microseconds')

        idle_attrs = {
            'active_now': False,
            'special_update': False,
            'id': 0,
            'cat': 0,
            'title': _('No alerts'),
            'desc': _('Loading...'),
            'areas': '',
            'cities': [],
            'data': '',
            'data_count': 0,
            'duration': 0,
            'icon': 'mdi:timer-sand',
            'emoji': '⏳',
            'alerts_count': 0,
            'last_changed': now_iso,
            'my_cities': sorted(list(set(self.areas_of_interest_config))),
            'prev_cat': 0,
            'prev_title': '',
            'prev_desc': '',
            'prev_areas': '',
            'prev_cities': [],
            'prev_data': '',
            'prev_data_count': 0,
            'prev_duration': 0,
            'prev_last_changed': now_iso,
            'prev_alerts_count': 0,
            'alert_wa': '',
            'alert_tg': '',
            'script_status': 'initializing',
        }
        history_default_attrs = {
            'cities_past_24h': [],
            'last_24h_alerts': [],
            'last_24h_alerts_group': {},
            'script_status': 'initializing',
        }

        sensors_to_init = [
            (self.main_sensor, 'off', idle_attrs),
            (self.city_sensor, 'off', idle_attrs.copy()),
            (self.main_sensor_pre_alert, 'off', idle_attrs.copy()),
            (self.city_sensor_pre_alert, 'off', idle_attrs.copy()),
            (self.main_sensor_active_alert, 'off', idle_attrs.copy()),
            (self.city_sensor_active_alert, 'off', idle_attrs.copy()),
            (self.history_cities_sensor, '0', history_default_attrs.copy()),
            (self.history_list_sensor, '0', history_default_attrs.copy()),
            (self.history_group_sensor, '0', history_default_attrs.copy()),
        ]

        init_tasks = []
        for entity_id, state, attrs in sensors_to_init:
            try:
                init_tasks.append(self.set_state(entity_id, state=state, attributes=attrs))
            except Exception as e:
                self.log(f'Error preparing init task for entity {entity_id}: {e}', level='WARNING', exc_info=True)

        if init_tasks:
            results = await asyncio.gather(*init_tasks, return_exceptions=True)
            for i, res in enumerate(results):
                if isinstance(res, Exception):
                    self.log(f'Error initializing entity task {i}: {res}', level='ERROR')

        # Initialize input_text and test boolean
        try:
            text_attrs = {'min': 0, 'max': 255, 'mode': 'text', 'friendly_name': f'{self.sensor_name} Summary', 'icon': 'mdi:timer-sand'}
            text_entity_exists = await self.entity_exists(self.main_text)
            if not text_entity_exists:
                self.log(f'Entity {self.main_text} not found. Creating.', level='INFO')
                await self.set_state(self.main_text, state=_('Loading...'), attributes=text_attrs)

            bool_attrs = {'friendly_name': f'{self.sensor_name} Test Trigger'}
            await self.set_state(self.activate_alert, state='off', attributes=bool_attrs)
        except Exception as e:
            self.log(f'Error checking/initializing input/boolean entities: {e}', level='WARNING', exc_info=True)

        self.log('HA sensor entities initialization check complete.')

    async def _load_initial_data(self):
        """Load history, get backup, set initial off states, save initial files."""
        _ = self._
        await self.history_manager.load_initial_history(self.api_client)
        history_attrs = self.history_manager.get_history_attributes()

        backup = self.file_manager.get_from_json()
        if backup:
            prev_attrs_formatted = self._format_backup_data_as_prev(backup)
        else:
            prev_attrs_formatted = {
                'prev_cat': 0,
                'prev_special_update': False,
                'prev_title': '',
                'prev_desc': '',
                'prev_areas': '',
                'prev_cities': [],
                'prev_data': '',
                'prev_data_count': 0,
                'prev_duration': 0,
                'prev_last_changed': datetime.now().isoformat(timespec='microseconds'),
                'prev_alerts_count': 0,
            }

        now_iso = datetime.now().isoformat(timespec='microseconds')
        initial_state_attrs = {
            'active_now': False,
            'special_update': False,
            'id': 0,
            'cat': 0,
            'title': _('No alerts'),
            'desc': _('Routine'),
            'areas': '',
            'cities': [],
            'data': '',
            'data_count': 0,
            'duration': 0,
            'icon': 'mdi:check-circle-outline',
            'emoji': '✅',
            'alerts_count': 0,
            'last_changed': now_iso,
            'my_cities': sorted(list(set(self.areas_of_interest_config))),
            **prev_attrs_formatted,
            'script_status': 'running',
        }

        try:
            tasks = [
                self.set_state(self.main_sensor, state='off', attributes=initial_state_attrs),
                self.set_state(self.city_sensor, state='off', attributes=initial_state_attrs.copy()),
                self.set_state(self.main_sensor_pre_alert, state='off', attributes=initial_state_attrs.copy()),
                self.set_state(self.city_sensor_pre_alert, state='off', attributes=initial_state_attrs.copy()),
                self.set_state(self.main_sensor_active_alert, state='off', attributes=initial_state_attrs.copy()),
                self.set_state(self.city_sensor_active_alert, state='off', attributes=initial_state_attrs.copy()),
            ]
            await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            self.log(f"Error setting initial 'off' states: {e}", level='WARNING', exc_info=True)

        try:
            count_cities = len(history_attrs.get('cities_past_24h', []))
            count_alerts = len(history_attrs.get('last_24h_alerts', []))

            tasks = [
                self.set_state(
                    self.history_cities_sensor,
                    state=str(count_cities),
                    attributes={'cities_past_24h': history_attrs['cities_past_24h'], 'script_status': 'running'},
                ),
                self.set_state(
                    self.history_list_sensor,
                    state=str(count_alerts),
                    attributes={'last_24h_alerts': history_attrs['last_24h_alerts'], 'script_status': 'running'},
                ),
                self.set_state(
                    self.history_group_sensor,
                    state=str(count_alerts),
                    attributes={'last_24h_alerts_group': history_attrs['last_24h_alerts_group'], 'script_status': 'running'},
                ),
            ]
            await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            self.log(f'Error setting dedicated history sensor states in _load_initial_data: {e}', level='WARNING', exc_info=True)

        if self.save_to_file:
            try:
                initial_latest_attrs = {
                    'title': _('No alerts'),
                    'desc': _('Routine'),
                    'cat': 0,
                    'cities': [],
                    'last_changed': datetime.now().isoformat(timespec='microseconds'),
                }
                await self._save_latest_geojson(initial_latest_attrs)
                await self._save_history_geojson(history_attrs)
                self.file_manager.create_csv_header_if_needed()
            except Exception as file_err:
                self.log(f'Error during initial file creation: {file_err}', level='ERROR', exc_info=True)

        self.log('Initial data loading and state setting complete.')

    async def _process_active_alert(self, data, is_test=False):
        """Process incoming alert data (real or test), update state, history, and files."""
        _ = self._
        log_prefix = '[Test Alert]' if is_test else '[Real Alert]'

        now_dt = datetime.now()
        now_iso = now_dt.isoformat(timespec='microseconds')

        # --- 1. Parse Incoming Data ---
        try:
            cat_str = data.get('cat', '1')
            cat = int(cat_str) if str(cat_str).isdigit() else 1
            aid = int(data.get('id', 0))
            desc = data.get('desc', '')
            title = data.get('title', _('Alert'))
            raw_payload_cities = data.get('data', [])
            payload_cities_raw = []
            if isinstance(raw_payload_cities, str):
                payload_cities_raw = [c.strip() for c in raw_payload_cities.split(',') if c.strip()]
            elif isinstance(raw_payload_cities, list):
                payload_cities_raw = [str(city) for city in raw_payload_cities if isinstance(city, (str, int))]

            # Filter out test/drill cities
            forbidden_strings = ['בדיקה', 'תרגיל']
            filtered_cities_raw = []
            for city_name in payload_cities_raw:
                if not any(forbidden in city_name for forbidden in forbidden_strings):
                    filtered_cities_raw.append(city_name)
                else:
                    self.log(f"{log_prefix} Filtering out city: '{city_name}' due to forbidden string.", level='INFO')

            if not filtered_cities_raw and payload_cities_raw:
                self.log(
                    f'{log_prefix} All cities in payload ID {data.get("id", "N/A")} were filtered out. Skipping further processing.',
                    level='INFO',
                )
                return

            stds_this_payload = set(standardize_name(n) for n in filtered_cities_raw if n)

        except Exception as e:
            self.log(f'{log_prefix} CRITICAL Error parsing alert data payload: {e}. Data: {data}', level='CRITICAL', exc_info=True)
            return

        # --- Check for identical payload ---
        if self.last_active_payload_details is not None and not is_test:
            is_identical = (
                self.last_active_payload_details['id'] == aid
                and self.last_active_payload_details['cat'] == cat
                and self.last_active_payload_details['title'] == title
                and self.last_active_payload_details['desc'] == desc
                and self.last_active_payload_details['stds'] == stds_this_payload
            )
            if is_identical:
                self.last_alert_time = time.time()
                return

        self.last_active_payload_details = {'id': aid, 'cat': cat, 'title': title, 'desc': desc, 'stds': stds_this_payload}

        # --- Check if sensor was previously off ---
        sensor_was_off = await self.get_state(self.main_sensor) == 'off'
        if sensor_was_off:
            self.log(f'{log_prefix} Sensor was off. Starting new alert window for ID: {aid}.', level='INFO')
            self.cities_past_window_std = set()
            self.alert_sequence_count = 0
            self.window_alerts_grouped = {}
            if self.file_manager:
                self.file_manager.clear_last_saved_id()
            self.history_manager.clear_poll_tracker()
            self.last_processed_alert_id = None
            self.last_active_payload_details = None

        # --- 2. Update History ---
        self.history_manager.clear_poll_tracker()
        self.history_manager.update_history(title, stds_this_payload)
        hist_attrs = self.history_manager.get_history_attributes()
        if not isinstance(hist_attrs, dict):
            self.log(f'{log_prefix} Failed to get valid history attributes after update. Using fallback.', level='ERROR')
            hist_attrs = {'last_24h_alerts': [], 'cities_past_24h': []}

        # --- 3. Accumulate Overall Cities ---
        newly_added_cities_overall = stds_this_payload - self.cities_past_window_std
        if newly_added_cities_overall:
            self.cities_past_window_std.update(newly_added_cities_overall)

        # --- 3b. Populate Grouped Window Data ---
        unknown_cities_logged_grouped = set()
        current_payload_title = title
        alert_group = self.window_alerts_grouped.setdefault(current_payload_title, {})
        populated_count_grouped = 0
        for std in stds_this_payload:
            det = self.city_data_manager.get_city_details(std)
            area = DEFAULT_UNKNOWN_AREA
            orig_city_name = std
            if det:
                area = det.get('area', DEFAULT_UNKNOWN_AREA)
                orig_city_name = det.get('original_name', std)
            elif std not in unknown_cities_logged_grouped:
                unknown_cities_logged_grouped.add(std)
            area_group = alert_group.setdefault(area, set())
            if orig_city_name not in area_group:
                area_group.add(orig_city_name)
                populated_count_grouped += 1
        if populated_count_grouped > 0:
            self.log(
                f"{log_prefix} Updated window_alerts_grouped for title '{current_payload_title}' with {populated_count_grouped} new entries.",
                level='DEBUG',
            )

        # --- 4. Update Window State Variables ---
        self.alert_sequence_count += 1

        # --- 5. Reset the Idle Timer ---
        self.last_alert_time = time.time()

        # --- 6. Process Data for HA State ---
        try:
            info = self.alert_processor.process_alert_window_data(
                category=cat,
                title=title,
                description=desc,
                window_std_cities=self.cities_past_window_std,
                window_alerts_grouped=self.window_alerts_grouped,
            )
        except Exception as e:
            self.log(f'{log_prefix} Error calling alert_processor.process_alert_window_data: {e}', level='CRITICAL', exc_info=True)
            info = {
                'areas_alert_str': 'Error',
                'cities_list_sorted': list(self.cities_past_window_std),
                'data_count': len(self.cities_past_window_std),
                'alerts_cities_str': 'Error processing cities',
                'icon_alert': 'mdi:alert-circle-outline',
                'icon_emoji': '🆘',
                'duration': 0,
                'text_wa_grouped': _('Error processing alert'),
                'text_tg_grouped': _('Error processing alert'),
                'text_status': 'Error processing',
                'full_message_str': 'Error',
                'alert_txt': 'Error',
                'full_message_list': [],
                'input_text_state': 'Error',
            }

        # --- 7. Get Previous State Attributes ---
        prev_state_attrs = {}
        try:
            prev_ha_state_data = await self.get_state(self.main_sensor, attribute='all')
            if prev_ha_state_data and 'attributes' in prev_ha_state_data:
                prev_state_attrs = prev_ha_state_data['attributes']
        except Exception as e:
            self.log(f'{log_prefix} Error fetching previous state attributes: {e}', level='WARNING')
        default_prev = {
            'cat': 0,
            'title': '',
            'desc': '',
            'areas': '',
            'cities': [],
            'data': '',
            'data_count': 0,
            'duration': 0,
            'alerts_count': 0,
            'last_changed': now_iso,
        }
        for k, v in default_prev.items():
            prev_state_attrs.setdefault(k, v)

        # --- 8. Construct Final Attributes ---
        special_update = cat == PRE_ALERT_CATEGORY or any(phrase in title for phrase in PRE_ALERT_TITLE_PHRASES)

        final_attributes = {
            'active_now': True,
            'special_update': special_update,
            'id': aid,
            'cat': cat,
            'title': title,
            'desc': desc,
            'areas': info.get('areas_alert_str', ''),
            'cities': info.get('cities_list_sorted', []),
            'data': info.get('alerts_cities_str', ''),
            'data_count': info.get('data_count', 0),
            'duration': info.get('duration', 0),
            'icon': info.get('icon_alert', 'mdi:alert'),
            'emoji': info.get('icon_emoji', '❗'),
            'alerts_count': self.alert_sequence_count,
            'last_changed': now_iso,
            'my_cities': sorted(list(set(self.areas_of_interest_config))),
            'alert': info.get('text_status', ''),
            'alert_alt': info.get('full_message_str', ''),
            'alert_txt': info.get('alert_txt', ''),
            'alert_wa': info.get('text_wa_grouped', ''),
            'alert_tg': info.get('text_tg_grouped', ''),
            'prev_cat': prev_state_attrs.get('cat'),
            'prev_title': prev_state_attrs.get('title'),
            'prev_desc': prev_state_attrs.get('desc'),
            'prev_areas': prev_state_attrs.get('areas'),
            'prev_cities': prev_state_attrs.get('cities'),
            'prev_data': prev_state_attrs.get('data'),
            'prev_data_count': prev_state_attrs.get('data_count'),
            'prev_duration': prev_state_attrs.get('duration'),
            'prev_alerts_count': prev_state_attrs.get('alerts_count'),
            'prev_last_changed': prev_state_attrs.get('last_changed'),
            'prev_special_update': prev_state_attrs.get('special_update'),
            'prev_alert_wa': prev_state_attrs.get('alert_wa'),
            'prev_alert_tg': prev_state_attrs.get('alert_tg'),
            'prev_icon': prev_state_attrs.get('icon'),
            'prev_emoji': prev_state_attrs.get('emoji'),
            'script_status': 'running',
        }

        if special_update:
            final_attributes['icon'] = 'mdi:Alarm-Light-Outline'
            final_attributes['emoji'] = '🔜'

        # --- 9. Check Attribute Size Limit ---
        try:
            if len(final_attributes.get('data', '')) > self.alert_processor.max_attr_len:
                final_attributes['data'] = self.alert_processor._check_len(
                    final_attributes['data'],
                    final_attributes.get('data_count', 0),
                    final_attributes.get('areas', ''),
                    self.alert_processor.max_attr_len,
                    'Final Data Attr Re-Check',
                )
        except Exception as size_err:
            self.log(f'{log_prefix} Error during final attribute size re-check: {size_err}', level='ERROR')

        # --- 10. Store Final Attributes ---
        self.prev_alert_final_attributes = final_attributes.copy()

        # --- 11. Determine City Sensor State ---
        city_sensor_should_be_on = bool(self.cities_past_window_std.intersection(self.areas_of_interest_std))
        if is_test and bool(self.areas_of_interest_std):
            city_sensor_should_be_on = True
        city_state_final = 'on' if city_sensor_should_be_on else 'off'

        # --- 12. Update Home Assistant States ---
        try:
            await self._update_ha_state(
                main_state='on',
                city_state=city_state_final,
                text_state=info.get('input_text_state', _('Alert')),
                attributes=final_attributes,
                text_icon=info.get('icon_alert', 'mdi:alert'),
            )
        except Exception as e:
            self.log(f'{log_prefix} Error occurred during _update_ha_state call: {e}', level='ERROR', exc_info=True)

        # --- 13. Update Dedicated History Sensors ---
        try:
            count_cities = len(hist_attrs.get('cities_past_24h', []))
            count_alerts = len(hist_attrs.get('last_24h_alerts', []))
            tasks = [
                self.set_state(
                    self.history_cities_sensor,
                    state=str(count_cities),
                    attributes={'cities_past_24h': hist_attrs.get('cities_past_24h', []), 'script_status': 'running'},
                ),
                self.set_state(
                    self.history_list_sensor,
                    state=str(count_alerts),
                    attributes={'last_24h_alerts': hist_attrs.get('last_24h_alerts', []), 'script_status': 'running'},
                ),
                self.set_state(
                    self.history_group_sensor,
                    state=str(count_alerts),
                    attributes={'last_24h_alerts_group': hist_attrs.get('last_24h_alerts_group', {}), 'script_status': 'running'},
                ),
            ]
            await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            self.log(f'{log_prefix} Unexpected error setting history sensor states: {e}', level='WARNING', exc_info=True)

        # --- 14. Save Backup JSON & Update GeoJSON Files ---
        if self.save_to_file:
            current_alert_id = aid
            if current_alert_id != self.last_processed_alert_id:
                backup_data = {
                    'id': final_attributes.get('id'),
                    'cat': str(final_attributes.get('cat')),
                    'title': final_attributes.get('title'),
                    'data': final_attributes.get('cities', []),
                    'desc': final_attributes.get('desc'),
                    'alertDate': final_attributes.get('last_changed'),
                    'last_changed': final_attributes.get('last_changed'),
                    'alerts_count': final_attributes.get('alerts_count'),
                }
                try:
                    self.file_manager.save_json_backup(backup_data)
                except Exception as e:
                    self.log(f'{log_prefix} Error during save_json_backup call: {e}', level='ERROR', exc_info=True)

                try:
                    await self._save_latest_geojson(final_attributes)
                except Exception as e:
                    self.log(f'{log_prefix} Error during _save_latest_geojson call: {e}', level='ERROR', exc_info=True)

                self.last_processed_alert_id = current_alert_id

            try:
                await self._save_history_geojson(hist_attrs)
            except Exception as e:
                self.log(f'{log_prefix} Error during _save_history_geojson call: {e}', level='ERROR', exc_info=True)

        # --- 15. Fire MQTT & Home Assistant Event ---
        event_data_dict = {
            'id': aid,
            'category': cat,
            'title': title,
            'cities': info.get('cities_list_sorted', []),
            'areas': info.get('areas_alert_str', ''),
            'description': desc,
            'timestamp': now_iso,
            'alerts_count': self.alert_sequence_count,
            'is_test': is_test,
        }
        if self.mqtt_topic:
            mqtt_base_topic = self.mqtt_topic if isinstance(self.mqtt_topic, str) and self.mqtt_topic.strip() else f'home/{self.sensor_name}'
            mqtt_topic_name = f'{mqtt_base_topic}/event'
            try:
                payload_to_publish = json.dumps(event_data_dict, ensure_ascii=False)
                await self.call_service('mqtt/publish', topic=mqtt_topic_name, payload=payload_to_publish, qos=0, retain=False)
            except Exception as e:
                self.log(f'{log_prefix} Error publishing MQTT event to {mqtt_topic_name}: {e}', level='ERROR')
        if self.ha_event:
            try:
                ha_event_name = f'{self.sensor_name}_event'
                await self.fire_event(ha_event_name, **event_data_dict)
            except Exception as e:
                self.log(f"{log_prefix} Error firing HA event '{ha_event_name}': {e}", level='ERROR')

        self.log(
            f'{log_prefix} Finished processing alert ID: {aid}. '
            f'Window payloads: {self.alert_sequence_count}, Total unique cities in window: {len(self.cities_past_window_std)}.',
            level='INFO' if not is_test else 'WARNING',
        )

    async def _check_reset_sensors(self):
        """Check if the idle timer has expired and reset sensors if needed."""
        _ = self._
        now = time.time()
        log_prefix = '[Sensor Reset Check]'

        main_sensor_exists = await self.entity_exists(self.main_sensor)
        if not main_sensor_exists:
            self.log(f'{log_prefix} Main sensor {self.main_sensor} not found. Cannot check state.', level='WARNING')
            return

        main_sensor_current_state = 'unknown'
        try:
            main_sensor_current_state = await self.get_state(self.main_sensor)
        except Exception as e:
            self.log(f"{log_prefix} Error getting main sensor state: {e}. Assuming 'unknown'.", level='WARNING')

        if main_sensor_current_state == 'off' and self.last_alert_time is None:
            if self.prev_alert_final_attributes:
                self.prev_alert_final_attributes = None
            return

        if self.last_alert_time is None:
            return

        time_since_last_alert = now - self.last_alert_time
        timer_expired = time_since_last_alert > self.timer_duration
        confirmed_idle = self.no_active_alerts_polls > 0
        can_reset = timer_expired and confirmed_idle

        if can_reset:
            self.log(
                f'{log_prefix} Alert timer expired ({time_since_last_alert:.1f}s > {self.timer_duration}s) '
                f'& confirmed idle ({self.no_active_alerts_polls} poll(s)). Resetting sensors.'
            )

            # --- 1. Save History Files ---
            if self.save_to_file and self.file_manager:
                if self.prev_alert_final_attributes:
                    last_alert_id = self.prev_alert_final_attributes.get('id', 'N/A')
                    self.log(f'{log_prefix} Saving history files (TXT/CSV) for last window (ID: {last_alert_id})...')
                    try:
                        self.file_manager.save_history_files(self.prev_alert_final_attributes)
                    except Exception as e:
                        self.log(f'{log_prefix} Error during save_history_files: {e}', level='ERROR', exc_info=True)
                else:
                    self.log(f'{log_prefix} Cannot save history file on reset: prev_alert_final_attributes missing.', level='WARNING')

            # --- 2. Format Previous State ---
            fallback_time_iso = datetime.now().isoformat(timespec='microseconds')
            last_alert_wa = ''
            last_alert_tg = ''

            if self.prev_alert_final_attributes:
                prev_data = self.prev_alert_final_attributes
                last_alert_wa = prev_data.get('alert_wa', '')
                last_alert_tg = prev_data.get('alert_tg', '')
                formatted_prev = {
                    'prev_cat': prev_data.get('cat', 0),
                    'prev_title': prev_data.get('title', ''),
                    'prev_desc': prev_data.get('desc', ''),
                    'prev_areas': prev_data.get('areas', ''),
                    'prev_cities': prev_data.get('cities', []),
                    'prev_data': prev_data.get('data', ''),
                    'prev_data_count': prev_data.get('data_count', 0),
                    'prev_duration': prev_data.get('duration', 0),
                    'prev_last_changed': prev_data.get('last_changed', fallback_time_iso),
                    'prev_alerts_count': prev_data.get('alerts_count', 0),
                }
            else:
                self.log(f"{log_prefix} Previous alert attributes missing during reset. Using defaults for 'prev_'.", level='WARNING')
                formatted_prev = {
                    'prev_cat': 0,
                    'prev_title': '',
                    'prev_desc': '',
                    'prev_areas': '',
                    'prev_cities': [],
                    'prev_data': '',
                    'prev_data_count': 0,
                    'prev_duration': 0,
                    'prev_last_changed': fallback_time_iso,
                    'prev_alerts_count': 0,
                }

            # --- 3. Clear Internal State Variables ---
            self.prev_alert_final_attributes = None
            self.last_alert_time = None
            self.last_processed_alert_id = None
            self.cities_past_window_std = set()
            self.window_alerts_grouped = {}
            self.alert_sequence_count = 0
            self.no_active_alerts_polls = 0

            # --- 4. Get Final History & Define Reset Attributes ---
            hist_attrs = self.history_manager.get_history_attributes()
            reset_attrs = {
                'active_now': False,
                'special_update': False,
                'id': 0,
                'cat': 0,
                'title': _('No alerts'),
                'desc': _('Routine'),
                'areas': '',
                'cities': [],
                'data': '',
                'data_count': 0,
                'duration': 0,
                'icon': 'mdi:check-circle-outline',
                'emoji': '✅',
                'alerts_count': 0,
                'last_changed': datetime.now().isoformat(timespec='microseconds'),
                'my_cities': sorted(list(set(self.areas_of_interest_config))),
                **formatted_prev,
                'alert_wa': last_alert_wa,
                'alert_tg': last_alert_tg,
                'script_status': 'running',
            }

            # --- 5. Update HA States ---
            try:
                await self._update_ha_state(
                    main_state='off',
                    city_state='off',
                    text_state=_('No alerts'),
                    attributes=reset_attrs,
                    text_icon='mdi:check-circle-outline',
                )
            except Exception as e:
                self.log(f'{log_prefix} Error during _update_ha_state call on reset: {e}', level='ERROR', exc_info=True)

            # --- 6. Re-affirm History Sensor States ---
            try:
                count_cities = len(hist_attrs.get('cities_past_24h', []))
                count_alerts = len(hist_attrs.get('last_24h_alerts', []))
                tasks = [
                    self.set_state(
                        self.history_cities_sensor,
                        state=str(count_cities),
                        attributes={'cities_past_24h': hist_attrs.get('cities_past_24h', []), 'script_status': 'running'},
                    ),
                    self.set_state(
                        self.history_list_sensor,
                        state=str(count_alerts),
                        attributes={'last_24h_alerts': hist_attrs.get('last_24h_alerts', []), 'script_status': 'running'},
                    ),
                    self.set_state(
                        self.history_group_sensor,
                        state=str(count_alerts),
                        attributes={'last_24h_alerts_group': hist_attrs.get('last_24h_alerts_group', {}), 'script_status': 'running'},
                    ),
                ]
                await asyncio.gather(*tasks, return_exceptions=True)
            except Exception as e:
                self.log(f'{log_prefix} Error re-affirming history sensors: {e}', level='ERROR', exc_info=True)

            # --- 7. Update GeoJSON Files for Idle State ---
            if self.save_to_file:
                try:
                    await self._save_history_geojson(hist_attrs)
                    idle_geojson_attrs = {
                        'title': reset_attrs['title'],
                        'desc': reset_attrs['desc'],
                        'cat': reset_attrs['cat'],
                        'cities': [],
                        'last_changed': reset_attrs['last_changed'],
                    }
                    await self._save_latest_geojson(idle_geojson_attrs)
                except Exception as e:
                    self.log(f'{log_prefix} Error during GeoJSON update on reset: {e}', level='ERROR', exc_info=True)

            self.log(f"{log_prefix} Sensor reset complete. State is now 'off'.")

        elif timer_expired and not confirmed_idle:
            self.log(
                f'{log_prefix} Timer expired ({time_since_last_alert:.1f}s > {self.timer_duration}s), '
                f'but last poll was not confirmed idle ({self.no_active_alerts_polls}). Awaiting confirmation poll.',
                level='DEBUG',
            )

    async def _update_ha_state(self, main_state, city_state, text_state, attributes, text_icon='mdi:information'):
        """Update the state and attributes of core HA entities."""
        attributes = attributes or {}
        attributes['last_changed'] = datetime.now().isoformat(timespec='microseconds')
        attributes['script_status'] = 'running'

        cat_value = attributes.get('cat', 0)
        title_alert = attributes.get('title', '')
        pre_alert = cat_value == PRE_ALERT_CATEGORY or any(phrase in title_alert for phrase in PRE_ALERT_TITLE_PHRASES)

        update_tasks = []

        # --- Main Sensor Update ---
        try:
            main_attrs = attributes.copy()
            update_tasks.append(self.set_state(self.main_sensor, state=main_state, attributes=main_attrs))
            if pre_alert:
                update_tasks.append(self.set_state(self.main_sensor_pre_alert, state=main_state, attributes=main_attrs))
            else:
                update_tasks.append(self.set_state(self.main_sensor_active_alert, state=main_state, attributes=main_attrs))
                update_tasks.append(self.set_state(self.main_sensor_pre_alert, state='off', attributes=main_attrs))
        except Exception as e:
            self.log(f'[HA Update] Error preparing task for {self.main_sensor}: {e}', level='ERROR')

        # --- City Sensor Update ---
        try:
            city_attrs = attributes.copy()
            update_tasks.append(self.set_state(self.city_sensor, state=city_state, attributes=city_attrs))
            if pre_alert:
                update_tasks.append(self.set_state(self.city_sensor_pre_alert, state=city_state, attributes=city_attrs))
            else:
                update_tasks.append(self.set_state(self.city_sensor_active_alert, state=city_state, attributes=city_attrs))
                update_tasks.append(self.set_state(self.city_sensor_pre_alert, state='off', attributes=city_attrs))
        except Exception as e:
            self.log(f'[HA Update] Error preparing task for {self.city_sensor}: {e}', level='ERROR')

        # --- Input Text Update ---
        try:
            if main_state == 'on':
                safe_text_state = text_state[:255] if isinstance(text_state, str) else 'Error'
                current_text_state = await self.get_state(self.main_text)
                if safe_text_state != current_text_state:
                    update_tasks.append(self.set_state(self.main_text, state=safe_text_state, attributes={'icon': text_icon}))
        except Exception as e:
            self.log(f'[HA Update] Error preparing/checking task for {self.main_text}: {e}', level='ERROR')

        # --- Execute Updates ---
        if update_tasks:
            try:
                results = await asyncio.gather(*update_tasks, return_exceptions=True)
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        self.log(f'[HA Update] Error during HA state update task {i + 1}: {result}', level='ERROR')
            except Exception as e:
                self.log(f'[HA Update] Unexpected error executing HA state updates: {e}', level='ERROR', exc_info=True)

    async def poll_alerts(self):
        """Fetch alerts from API, process them, or check for sensor reset."""
        log_prefix = '[Poll Cycle]'

        live_data = None
        api_error = False
        try:
            live_data = await self.api_client.get_live_alerts()
        except Exception as e:
            self.log(f'{log_prefix} Error fetching live alerts from Home Front Command API: {e}', level='WARNING')
            live_data = None
            api_error = True

        try:
            is_alert_active = isinstance(live_data, dict) and live_data.get('data')

            # History fallback: if live is empty and we're in the early idle window
            # (first 3 idle polls after a gap), check history for alerts we may have missed.
            # The history endpoint has ~30-60s latency but catches brief live pulses.
            if not is_alert_active and not api_error and self.last_alert_time is None and 1 <= self.no_active_alerts_polls <= 3:
                try:
                    recent = await self.api_client.get_recent_alerts_from_history(max_age_seconds=120)
                    if recent:
                        live_data = recent[0]
                        is_alert_active = isinstance(live_data, dict) and live_data.get('data')
                        if is_alert_active:
                            self.log(
                                f'{log_prefix} History fallback: processing missed alert (cat={live_data.get("cat")}, {len(live_data.get("data", []))} cities)'
                            )
                except Exception as e:
                    self.log(f'{log_prefix} History fallback check failed: {e}', level='DEBUG')

            # Check for explicit all-clear signal (category 13) before processing as alert
            if is_alert_active:
                cat_raw = live_data.get('cat', '0') if isinstance(live_data, dict) else '0'
                try:
                    incoming_cat = int(cat_raw) if str(cat_raw).isdigit() else 0
                except (TypeError, ValueError):
                    incoming_cat = 0

                if incoming_cat == ALL_CLEAR_CATEGORY:
                    self.log(f'{log_prefix} All-clear signal received (category 13). Triggering immediate sensor reset.')
                    self.no_active_alerts_polls = 1
                    if self.last_alert_time is not None:
                        self.last_alert_time = time.time() - self.timer_duration - 1
                    is_alert_active = False

            if is_alert_active:
                self.no_active_alerts_polls = 0
                await self._process_active_alert(live_data, is_test=False)

                if self.test_alert_cycle_flag > 0:
                    self.log(f'{log_prefix} Real alert detected during active test window. Cancelling test mode.', level='INFO')
                    self.test_alert_cycle_flag = 0
                    self.test_alert_start_time = 0
                    try:
                        if await self.get_state(self.activate_alert) == 'on':
                            await self.call_service('input_boolean/turn_off', entity_id=self.activate_alert)
                    except Exception as e_bool:
                        self.log(f'{log_prefix} Error turning off test boolean after interruption: {e_bool}', level='WARNING')

            else:
                if not api_error:
                    self.no_active_alerts_polls += 1
                else:
                    self.log(f'{log_prefix} API error occurred, not incrementing idle poll count.', level='DEBUG')

                # --- Efficient History Update on Idle Poll ---
                try:
                    current_hist_attrs = self.history_manager.get_history_attributes()
                    if isinstance(current_hist_attrs, dict) and current_hist_attrs != self.last_history_attributes_cache:
                        self.log(f'{log_prefix} History data has changed. Updating sensors.', level='DEBUG')
                        count_alerts = len(current_hist_attrs.get('last_24h_alerts', []))
                        tasks = [
                            self.set_state(
                                self.history_cities_sensor,
                                state=str(len(current_hist_attrs.get('cities_past_24h', []))),
                                attributes={'cities_past_24h': current_hist_attrs['cities_past_24h'], 'script_status': 'running'},
                            ),
                            self.set_state(
                                self.history_list_sensor,
                                state=str(count_alerts),
                                attributes={'last_24h_alerts': current_hist_attrs['last_24h_alerts'], 'script_status': 'running'},
                            ),
                            self.set_state(
                                self.history_group_sensor,
                                state=str(count_alerts),
                                attributes={'last_24h_alerts_group': current_hist_attrs['last_24h_alerts_group'], 'script_status': 'running'},
                            ),
                        ]
                        if self.save_to_file and self.file_manager:
                            tasks.append(self._save_history_geojson(current_hist_attrs))
                        await asyncio.gather(*tasks, return_exceptions=True)
                        self.last_history_attributes_cache = current_hist_attrs
                except Exception as e:
                    self.log(f'{log_prefix} Error updating history sensors during idle poll: {e}', level='ERROR', exc_info=True)

                # --- Handle Test Window Expiration & Sensor Reset ---
                if self.test_alert_cycle_flag > 0:
                    if time.time() - self.test_alert_start_time >= self.timer_duration:
                        self.log(f'{log_prefix} Test alert timer expired. Ending test window.', level='INFO')
                        self.test_alert_cycle_flag = 0
                        self.test_alert_start_time = 0
                        await self._check_reset_sensors()
                    else:
                        return
                else:
                    await self._check_reset_sensors()

        except Exception as e:
            self.log(f'{log_prefix} Error in poll_alerts processing/reset logic: {e}', level='ERROR', exc_info=True)
            if self.test_alert_cycle_flag > 0:
                self.log(f'{log_prefix} Clearing test flag due to error.', level='WARNING')
                self.test_alert_cycle_flag = 0

    # --- Test Alert Handling ---
    def _test_boolean_callback(self, entity, attribute, old, new, kwargs):
        """Callback when the test input_boolean is turned on."""
        if new == 'on':
            self.log(f'Test input_boolean {entity} turned on. Initiating test alert sequence.', level='WARNING')
            self.create_task(self._handle_test_alert())

    async def _handle_test_alert(self):
        """Initiate a test alert sequence using configured or default cities."""
        _ = self._
        log_prefix = '[Test Sequence]'
        if self.test_alert_cycle_flag != 0:
            try:
                await self.call_service('input_boolean/turn_off', entity_id=self.activate_alert)
            except Exception:
                pass
            return

        current_state = await self.get_state(self.main_sensor)
        if current_state == 'on' and self.test_alert_cycle_flag == 0:
            self.log(f'{log_prefix} Cannot start test alert: A real alert is currently active.', level='WARNING')
            try:
                await self.call_service('input_boolean/turn_off', entity_id=self.activate_alert)
            except Exception:
                pass
            return

        self.test_alert_cycle_flag = 1
        self.test_alert_start_time = time.time()
        self.log(f'--- {log_prefix} Initiating Test Alert Sequence ---', level='WARNING')

        test_cities_orig = []
        if self.areas_of_interest_std:
            found_cities = []
            for std_name in self.areas_of_interest_std:
                details = self.city_data_manager.get_city_details(std_name)
                if details and details.get('original_name'):
                    found_cities.append(details['original_name'])
                else:
                    found_cities.append(std_name)
            test_cities_orig = found_cities
        else:
            default_test_city = 'תל אביב - מרכז העיר'
            self.log(f"{log_prefix} No valid areas_of_interest configured. Using default '{default_test_city}' for test.", level='WARNING')
            test_cities_orig = [default_test_city]

        if not test_cities_orig:
            test_cities_orig = ['תל אביב - מרכז העיר']

        test_alert_data = {
            'id': int(time.time() * 1000),
            'cat': '1',
            'title': _('Rocket and missile fire (test alert)'),
            'data': test_cities_orig,
            'desc': _('Test alert - enter shelter briefly for testing'),
        }

        try:
            await self._process_active_alert(test_alert_data, is_test=True)
        except Exception as test_proc_err:
            self.log(f'{log_prefix} Error during processing of test alert data: {test_proc_err}', level='ERROR', exc_info=True)
            self.test_alert_cycle_flag = 0
            self.test_alert_start_time = 0

        try:
            if await self.get_state(self.activate_alert) == 'on':
                await self.call_service('input_boolean/turn_off', entity_id=self.activate_alert)
                self.log(f'{log_prefix} Test alert processed. Turned off input_boolean: {self.activate_alert}', level='INFO')
        except Exception as e:
            self.log(f'{log_prefix} Error turning off test input_boolean ({self.activate_alert}): {e}', level='WARNING')

    async def _save_latest_geojson(self, attributes):
        """Generate and save only the latest GeoJSON file."""
        if not self.save_to_file or not self.file_manager:
            return
        if not attributes:
            self.log('Skipping Latest GeoJSON save: Attributes missing.', level='WARNING')
            return
        try:
            latest_geojson_data = generate_geojson_data(attributes, 'latest', self.city_data_manager, self.log, self.language)
            path = self.file_paths.get('geojson_latest')
            if path:
                self.file_manager.save_geojson_file(latest_geojson_data, path)
            else:
                self.log('Skipping Latest GeoJSON save: Path not found.', level='WARNING')
        except Exception as e:
            self.log(f'Error saving Latest GeoJSON: {e}', level='ERROR', exc_info=True)

    async def _save_history_geojson(self, history_attributes):
        """Generate and save only the history GeoJSON file."""
        if not self.save_to_file or not self.file_manager:
            return
        if not history_attributes or 'last_24h_alerts' not in history_attributes:
            self.log('Skipping History GeoJSON save: History attributes missing or invalid.', level='WARNING')
            return
        try:
            history_geojson_data = generate_geojson_data(history_attributes, 'history', self.city_data_manager, self.log, self.language)
            path = self.file_paths.get('geojson_history')
            if path:
                self.file_manager.save_geojson_file(history_geojson_data, path)
            else:
                self.log('Skipping History GeoJSON save: Path not found.', level='WARNING')
        except Exception as e:
            self.log(f'Error saving History GeoJSON: {e}', level='ERROR', exc_info=True)

    def _format_backup_data_as_prev(self, data):
        """Format data loaded from JSON backup into the prev_* attribute structure."""
        if not isinstance(data, dict):
            self.log('Backup data is not a dictionary, cannot format.', level='WARNING')
            return {}

        cat_str = data.get('cat', '0')
        cat = int(cat_str) if isinstance(cat_str, str) and cat_str.isdigit() else 0
        title = data.get('title', '')
        raw_cities_data = data.get('data', [])
        cities_from_backup = []

        if isinstance(raw_cities_data, str):
            cities_from_backup = [c.strip() for c in raw_cities_data.split(',') if c.strip()]
        elif isinstance(raw_cities_data, list):
            cities_from_backup = [str(c) for c in raw_cities_data if isinstance(c, (str, int))]

        desc = data.get('desc', '')
        last = self._is_iso_format(data.get('last_changed', data.get('alertDate', '')))
        dur = self.alert_processor.extract_duration_from_desc(desc) if self.alert_processor else 0

        areas_set = set()
        orig_cities_set = set(cities_from_backup)
        unknown_cities_logged = set()

        if self.city_data_manager:
            refined_orig_cities = set()
            for city_name_from_backup in cities_from_backup:
                if not city_name_from_backup:
                    continue
                std = standardize_name(city_name_from_backup)
                if not std:
                    refined_orig_cities.add(city_name_from_backup)
                    continue

                det = self.city_data_manager.get_city_details(std)
                if det:
                    areas_set.add(det.get('area', DEFAULT_UNKNOWN_AREA))
                    refined_orig_cities.add(det.get('original_name', city_name_from_backup))
                else:
                    areas_set.add(DEFAULT_UNKNOWN_AREA)
                    refined_orig_cities.add(city_name_from_backup)
                    if std not in unknown_cities_logged:
                        unknown_cities_logged.add(std)
            orig_cities_set = refined_orig_cities

        sorted_orig_cities = sorted(list(orig_cities_set))
        areas_str = ', '.join(sorted(list(areas_set))) if areas_set else ''
        prev_data_str = ', '.join(sorted_orig_cities)

        return {
            'prev_cat': cat,
            'prev_title': title,
            'prev_desc': desc,
            'prev_areas': areas_str,
            'prev_cities': sorted_orig_cities,
            'prev_data': prev_data_str,
            'prev_data_count': len(sorted_orig_cities),
            'prev_duration': dur,
            'prev_last_changed': last,
            'prev_alerts_count': data.get('alerts_count', 0),
        }
