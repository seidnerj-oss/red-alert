"""
UniFi LED alert monitor.

Polls the Home Front Command API and sets UniFi AP LED colors
based on alert state. Each state (routine, pre_alert, alert) is
independently configurable with on/off, color, brightness, and blink.

Supports multiple monitors with per-area device groups: different
devices can react to different alert areas with different LED settings.

Supports pyunifiapi (default) or aiounifi as the controller backend.
Blink uses the controller's native locate mode (flash LED).

Usage:
    python -m red_alert.integrations.outputs.unifi --config config.json
"""

import asyncio
import datetime
import logging
import time
import zoneinfo

import httpx

from red_alert.core.state import AlertState, AlertStateTracker
from red_alert.core.utils import standardize_name
from red_alert.integrations.inputs.hfc.api_client import HomeFrontCommandApiClient
from red_alert.integrations.outputs.unifi.led_controller import UnifiLedController, _color_label, rgb_to_hex

logger = logging.getLogger('red_alert.unifi')

API_URLS = {
    'live': 'https://www.oref.org.il/WarningMessages/alert/alerts.json',
    'history': 'https://www.oref.org.il/WarningMessages/alert/History/AlertsHistory.json',
}

SESSION_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (compatible; red-alert/4.0; UniFi)',
    'Referer': 'https://www.oref.org.il/',
    'X-Requested-With': 'XMLHttpRequest',
    'Accept': 'application/json',
    'Accept-Encoding': 'gzip, deflate, br',
    'Accept-Language': 'he,en;q=0.9',
    'Connection': 'keep-alive',
    'Pragma': 'no-cache',
    'Cache-Control': 'no-cache',
}

NAMED_COLORS = {
    'red': (255, 0, 0),
    'green': (0, 255, 0),
    'blue': (0, 0, 255),
    'yellow': (255, 255, 0),
    'white': (255, 255, 255),
    'warm': (255, 180, 100),
}

DEFAULT_LED_STATES = {
    'alert': {'on': True, 'color': 'red', 'brightness': 100, 'blink': False},
    'pre_alert': {'on': True, 'color': 'yellow', 'brightness': 100, 'blink': False},
    'all_clear': {'on': True, 'color': 'green', 'brightness': 100, 'blink': False},
    'routine': {'on': True, 'color': 'white', 'brightness': 100, 'blink': False},
}

STATE_KEY_MAP = {
    'alert': AlertState.ALERT,
    'pre_alert': AlertState.PRE_ALERT,
    'all_clear': AlertState.ALL_CLEAR,
    'routine': AlertState.ROUTINE,
}

DEFAULT_CONFIG: dict = {
    'interval': 1,
    'hold_seconds': {},
    'areas_of_interest': [],
    'host': None,
    'username': None,
    'password': None,
    'port': 443,
    'site': None,
    'device_macs': [],
    'led_states': {},
    'device_overrides': {},
    'totp_secret': None,
    'backend': None,
    'connection': 'local',
    'device_id': None,
}

# Keys that belong to the connection (shared across all monitors/controllers)
_CONNECTION_KEYS = {'host', 'username', 'password', 'port', 'site', 'totp_secret', 'backend', 'interval', 'connection', 'device_id'}

# Keys that belong to a controller (per-controller in multi-controller config)
_CONTROLLER_KEYS = {'name', 'device_id', 'host', 'port', 'site', 'backend', 'monitors'}

# Keys that belong to a monitor (per-monitor config)
_MONITOR_KEYS = {'name', 'areas_of_interest', 'device_macs', 'led_states', 'device_overrides', 'hold_seconds'}


def _resolve_color(color) -> tuple[int, int, int]:
    """Resolve a color value (name string, hex string, or [R,G,B] list) to an RGB tuple."""
    if isinstance(color, str):
        if color.startswith('#') and len(color) == 7:
            return (int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16))
        return NAMED_COLORS.get(color, NAMED_COLORS['white'])
    return tuple(color[:3])


def _resolve_led_state(cfg: dict) -> dict:
    """Normalize a single LED state config entry."""
    return {
        'on': cfg.get('on', True),
        'color': _resolve_color(cfg.get('color', (255, 255, 255))),
        'brightness': max(0, min(100, cfg.get('brightness', 100))),
        'blink': cfg.get('blink', False),
    }


def _build_led_states(user_cfg: dict) -> dict[AlertState, dict]:
    """Merge user LED state config over defaults and return AlertState-keyed dict."""
    result = {}
    for key, alert_state in STATE_KEY_MAP.items():
        merged = {**DEFAULT_LED_STATES[key], **user_cfg.get(key, {})}
        result[alert_state] = _resolve_led_state(merged)
    return result


def _build_device_led_states(
    base_states: dict[AlertState, dict],
    device_macs: list[str],
    device_overrides: dict,
) -> dict[str, dict[AlertState, dict]]:
    """Build per-device LED states by merging device overrides onto the base states.

    Returns a dict keyed by MAC address, each value an AlertState-keyed dict.
    Devices without overrides get the base states as-is.
    """
    result = {}
    for mac in device_macs:
        mac_lower = mac.lower()
        # Look up overrides by original MAC, lowercased MAC, or lowercased override keys
        override_cfg = device_overrides.get(mac, device_overrides.get(mac_lower, {}))
        if not override_cfg:
            for key, val in device_overrides.items():
                if key.lower() == mac_lower:
                    override_cfg = val
                    break
        override_led = override_cfg.get('led_states', {}) if isinstance(override_cfg, dict) else {}

        if not override_led:
            result[mac_lower] = base_states
            continue

        device_states = {}
        for key, alert_state in STATE_KEY_MAP.items():
            base = base_states[alert_state]
            per_device = override_led.get(key, {})
            if per_device:
                merged = {**base, 'color': base['color']}
                for field in ('on', 'brightness', 'blink'):
                    if field in per_device:
                        merged[field] = per_device[field]
                if 'color' in per_device:
                    merged['color'] = _resolve_color(per_device['color'])
                merged['brightness'] = max(0, min(100, merged['brightness']))
                device_states[alert_state] = merged
            else:
                device_states[alert_state] = base
        result[mac_lower] = device_states

    return result


def _parse_schedule(schedule: dict) -> dict:
    """Parse a schedule entry into a normalized form.

    Schedule format: {"time": "08:00-20:00", "timezone": "Asia/Jerusalem", "led_states": {...}}
    The time range is HH:MM-HH:MM. Ranges that cross midnight (e.g. 20:00-08:00) are supported.
    """
    time_range = schedule.get('time', '')
    if '-' not in time_range:
        raise ValueError(f'Invalid schedule time range: {time_range!r} (expected HH:MM-HH:MM)')
    start_str, end_str = time_range.split('-', 1)
    start = datetime.time.fromisoformat(start_str.strip())
    end = datetime.time.fromisoformat(end_str.strip())
    tz_name = schedule.get('timezone')
    if not tz_name:
        raise ValueError('Schedule is missing required "timezone" field')
    tz = zoneinfo.ZoneInfo(tz_name)
    led_states = schedule.get('led_states', {})
    return {'start': start, 'end': end, 'tz': tz, 'led_states': led_states}


def _schedule_active(schedule: dict) -> bool:
    """Check if a parsed schedule is currently active."""
    now = datetime.datetime.now(tz=schedule['tz']).time()
    start, end = schedule['start'], schedule['end']
    if start <= end:
        return bool(start <= now < end)
    # Crosses midnight (e.g. 20:00-08:00)
    return bool(now >= start or now < end)


def _build_device_schedules(device_overrides: dict) -> dict[str, list[dict]]:
    """Extract and parse schedules from device overrides.

    Returns a dict keyed by lowercase MAC -> list of parsed schedules.
    """
    result = {}
    for mac, override in device_overrides.items():
        if not isinstance(override, dict):
            continue
        raw_schedules = override.get('schedules', [])
        if raw_schedules:
            parsed = [_parse_schedule(s) for s in raw_schedules]
            result[mac.lower()] = parsed
    return result


def _normalize_config(config: dict) -> list[tuple[dict, list[dict]]]:
    """Normalize config into a list of (connection, monitors) controller groups.

    Returns:
        A list of ``(connection_cfg, monitor_cfgs_list)`` tuples, one per
        controller. For local or single-controller configs this is a
        single-element list. For multi-controller cloud configs, one entry
        per controller.

    Config shapes supported:

    1. **Flat config** (backward compatible): single controller, single monitor.
    2. **Local with monitors**: single controller, multiple monitors.
    3. **Cloud with controllers**: multiple controllers, each with its own monitors.
    """
    cfg = {**DEFAULT_CONFIG, **config}
    top_hold = cfg.get('hold_seconds', {})
    top_led_states = config.get('led_states', {})

    # Multi-controller: controllers list (supports mixed local + cloud)
    if 'controllers' in config:
        shared = {k: cfg[k] for k in _CONNECTION_KEYS if k in cfg}

        groups = []
        for ctrl in config['controllers']:
            ctrl_connection = {**shared}
            # Per-controller overrides
            for k in ('device_id', 'host', 'port', 'site', 'backend'):
                if k in ctrl:
                    ctrl_connection[k] = ctrl[k]

            # Auto-detect connection mode: device_id -> cloud, host -> local
            if ctrl_connection.get('device_id'):
                ctrl_connection['connection'] = 'cloud'
                ctrl_connection['backend'] = 'pyunifiapi'
            elif ctrl_connection.get('host'):
                ctrl_connection.setdefault('connection', 'local')

            monitors = []
            for mon in ctrl.get('monitors', []):
                monitor_cfg = {}
                for k in _MONITOR_KEYS:
                    if k in mon:
                        monitor_cfg[k] = mon[k]
                if top_hold or 'hold_seconds' in mon:
                    monitor_cfg['hold_seconds'] = {**top_hold, **mon.get('hold_seconds', {})}
                if top_led_states or 'led_states' in mon:
                    merged_led = {}
                    for state_key in ('routine', 'pre_alert', 'alert', 'all_clear'):
                        merged = {**top_led_states.get(state_key, {}), **mon.get('led_states', {}).get(state_key, {})}
                        if merged:
                            merged_led[state_key] = merged
                    if merged_led:
                        monitor_cfg['led_states'] = merged_led
                monitors.append(monitor_cfg)

            groups.append((ctrl_connection, monitors))
        return groups

    # Single controller
    connection = {k: cfg[k] for k in _CONNECTION_KEYS if k in cfg}

    if 'monitors' in config:
        monitors = []
        for mon in config['monitors']:
            monitor_cfg = {}
            for k in _MONITOR_KEYS:
                if k in mon:
                    monitor_cfg[k] = mon[k]
            if top_hold or 'hold_seconds' in mon:
                monitor_cfg['hold_seconds'] = {**top_hold, **mon.get('hold_seconds', {})}
            if top_led_states or 'led_states' in mon:
                merged_led = {}
                for state_key in ('routine', 'pre_alert', 'alert', 'all_clear'):
                    merged = {**top_led_states.get(state_key, {}), **mon.get('led_states', {}).get(state_key, {})}
                    if merged:
                        merged_led[state_key] = merged
                if merged_led:
                    monitor_cfg['led_states'] = merged_led
            monitors.append(monitor_cfg)
        return [(connection, monitors)]

    # Flat config: wrap as single monitor
    monitor_cfg = {k: cfg[k] for k in _MONITOR_KEYS if k in cfg}
    return [(connection, [monitor_cfg])]


def _log_adapter(msg, level='INFO', **kwargs):
    """Adapt Python logging to the core logger interface."""
    getattr(logger, level.lower(), logger.info)(msg)


def _prefixed_log_adapter(prefix: str):
    """Create a log adapter that prepends a prefix to each message."""

    def adapter(msg, level='INFO', **kwargs):
        getattr(logger, level.lower(), logger.info)(f'{prefix}{msg}')

    return adapter


class UnifiAlertMonitor:
    """Polls the Home Front Command API and controls UniFi AP LEDs based on alert state."""

    def __init__(
        self,
        api_client: HomeFrontCommandApiClient,
        led_controller: UnifiLedController,
        state_tracker: AlertStateTracker,
        led_states: dict[AlertState, dict] | None = None,
        device_led_states: dict[str, dict[AlertState, dict]] | None = None,
        device_macs: list[str] | None = None,
        device_schedules: dict[str, list[dict]] | None = None,
        name: str | None = None,
    ):
        self._api_client = api_client
        self._led = led_controller
        self._state = state_tracker
        self._led_states = led_states or _build_led_states({})
        self._device_led_states = device_led_states
        self._device_macs = [mac.lower() for mac in device_macs] if device_macs else None
        self._device_schedules = device_schedules or {}
        self._name = name
        self._current_alert_state: AlertState | None = None
        self._locating = False
        self._active_schedule_key: str | None = None

    @property
    def alert_state(self) -> AlertState:
        return self._state.state

    def _compute_schedule_key(self) -> str:
        """Compute a key representing which schedules are currently active.

        Changes when a schedule boundary is crossed, triggering LED re-apply.
        """
        parts = []
        for mac in sorted(self._device_schedules):
            for i, schedule in enumerate(self._device_schedules[mac]):
                if _schedule_active(schedule):
                    parts.append(f'{mac}:{i}')
        return '|'.join(parts)

    async def check_schedule(self) -> bool:
        """Re-apply LED state if a schedule boundary was crossed. Returns True if LEDs were updated."""
        if not self._device_schedules or self._current_alert_state is None:
            return False
        key = self._compute_schedule_key()
        if key != self._active_schedule_key:
            self._active_schedule_key = key
            logger.info('%s schedule changed, re-applying LED state', self._name)
            await self._apply_led_state(self._current_alert_state)
            return True
        return False

    def _state_cfg(self, state: AlertState, mac: str | None = None) -> dict:
        if mac and self._device_led_states and mac in self._device_led_states:
            base = self._device_led_states[mac]
            cfg = base.get(state, base.get(AlertState.ROUTINE, self._led_states[AlertState.ROUTINE]))
        else:
            cfg = self._led_states.get(state, self._led_states[AlertState.ROUTINE])

        if mac and mac in self._device_schedules:
            for schedule in self._device_schedules[mac]:
                if _schedule_active(schedule):
                    state_key = next((k for k, v in STATE_KEY_MAP.items() if v == state), None)
                    if state_key and state_key in schedule['led_states']:
                        override = schedule['led_states'][state_key]
                        cfg = dict(cfg)
                        for field in ('on', 'brightness', 'blink'):
                            if field in override:
                                cfg[field] = override[field]
                        if 'color' in override:
                            cfg['color'] = _resolve_color(override['color'])
                        cfg['brightness'] = max(0, min(100, cfg['brightness']))
                    break
        return cfg

    def _iter_macs(self) -> list[str]:
        """Return the list of MAC addresses this monitor controls."""
        if self._device_macs is not None:
            return self._device_macs
        return self._led._device_macs

    def _use_per_device(self) -> bool:
        """Whether to use per-device LED control instead of bulk."""
        return self._device_macs is not None or self._device_led_states is not None

    async def _apply_led_state(self, state: AlertState):
        """Send the LED state to the controller, with per-device overrides if configured."""
        if self._use_per_device():
            for mac in self._iter_macs():
                cfg = self._state_cfg(state, mac)
                color_hex = rgb_to_hex(*cfg['color'])
                await self._led.set_device_led(mac, on=cfg['on'], color_hex=color_hex, brightness=cfg['brightness'])
        else:
            cfg = self._state_cfg(state)
            color_hex = rgb_to_hex(*cfg['color'])
            await self._led.set_led(on=cfg['on'], color_hex=color_hex, brightness=cfg['brightness'])

    async def _apply_locate(self, enable: bool):
        """Enable/disable locate (blink) mode, per-device if in multi-monitor mode."""
        if self._device_macs is not None:
            for mac in self._device_macs:
                await self._led.locate_device(mac, enable)
        else:
            await self._led.locate(enable=enable)

    async def update(self, data: dict | None) -> AlertState:
        """Classify pre-fetched alert data and update LED state.

        Args:
            data: Parsed JSON from the alerts API, or None if no alert.

        Returns:
            The current AlertState after classification.
        """
        state = self._state.update(data)

        if state != self._current_alert_state:
            self._current_alert_state = state

            cfg = self._state_cfg(state)
            should_blink = cfg['blink'] and cfg['on']
            blink_changed = should_blink != self._locating
            color_hex = rgb_to_hex(*cfg['color'])
            blink_label = ', blink' if should_blink else ''
            logger.info('%s -> %s: %s brightness=%d%s', self._name, state.value, _color_label(color_hex), cfg['brightness'], blink_label)

            # Apply LED color and blink concurrently to avoid visible gap
            if blink_changed:
                self._locating = should_blink
                await asyncio.gather(
                    self._apply_led_state(state),
                    self._apply_locate(should_blink),
                )
            else:
                await self._apply_led_state(state)

        return state

    async def poll(self) -> AlertState:
        """Poll the API, classify the alert, and update LED state."""
        data = await self._api_client.get_live_alerts()
        return await self.update(data)

    async def reconcile(self) -> int:
        """Check actual device LED state and fix any mismatches with the current expected state.

        Reads each device's current LED settings from the controller and compares
        with what the current alert state expects. Re-sends LED commands for any
        device that doesn't match.

        Returns:
            Number of devices that were corrected.
        """
        if self._current_alert_state is None:
            return 0

        state = self._current_alert_state
        corrected = 0

        for mac in self._iter_macs():
            cfg = self._state_cfg(state, mac)
            expected_color = rgb_to_hex(*cfg['color']).upper()
            expected_brightness = cfg['brightness']
            expected_on = cfg['on']
            expected_status = 'on' if expected_on else 'off'

            device = self._led._controller.devices.get(mac)
            if device is None:
                continue

            actual_color = (device.raw.get('led_override_color') or '#FFFFFF').upper()
            actual_brightness = device.raw.get('led_override_color_brightness', 100)
            actual_status = device.raw.get('led_override', 'on')

            if actual_color != expected_color or actual_brightness != expected_brightness or actual_status != expected_status:
                logger.warning(
                    'Reconcile %s: expected %s/%s/%d, actual %s/%s/%d - correcting',
                    mac,
                    expected_status,
                    expected_color,
                    expected_brightness,
                    actual_status,
                    actual_color,
                    actual_brightness,
                )
                await self._led.set_device_led(mac, on=expected_on, color_hex=expected_color, brightness=expected_brightness)
                corrected += 1

        return corrected


async def _validate_areas_of_interest(
    api_client: HomeFrontCommandApiClient,
    monitors: list[UnifiAlertMonitor],
) -> None:
    """Validate configured areas_of_interest against the HFC district list.

    Fetches the current city/district list from the HFC API and checks that
    each monitor's configured areas still exist. Logs warnings for any
    unrecognized city names.
    """
    districts = await api_client.get_districts()
    if not districts:
        logger.debug('Metadata validation: could not fetch districts')
        return

    known_cities = {standardize_name(d.get('label', '')) for d in districts if d.get('label')}

    all_valid = True
    for monitor in monitors:
        for area in monitor._state._areas:
            if area and area not in known_cities:
                logger.warning('Metadata validation: area "%s" (monitor "%s") not found in HFC district list', area, monitor._name)
                all_valid = False

    if all_valid:
        logger.info('Metadata validation: all areas of interest valid (%d known cities)', len(known_cities))
    else:
        logger.warning('Metadata validation: some areas may not match HFC alert city names')


def _build_monitors_for_controller(
    connection: dict,
    monitor_cfgs: list[dict],
    api_client: HomeFrontCommandApiClient,
    led_controller: UnifiLedController,
    multi_monitor: bool,
) -> list[UnifiAlertMonitor]:
    """Build UnifiAlertMonitor instances for a single controller's monitors."""
    monitors: list[UnifiAlertMonitor] = []
    for i, mon_cfg in enumerate(monitor_cfgs):
        name = mon_cfg.get('name', f'monitor-{i}')
        led_states = _build_led_states(mon_cfg.get('led_states', {}))
        device_macs = mon_cfg.get('device_macs', [])
        device_overrides = mon_cfg.get('device_overrides', {})
        device_led_states = _build_device_led_states(led_states, device_macs, device_overrides) if device_overrides else None
        device_schedules = _build_device_schedules(device_overrides) if device_overrides else None

        log_fn = _prefixed_log_adapter(f'[{name}] ') if multi_monitor else _log_adapter
        state_tracker = AlertStateTracker(
            areas_of_interest=mon_cfg.get('areas_of_interest'),
            hold_seconds=mon_cfg.get('hold_seconds'),
            logger=log_fn,
        )

        monitor = UnifiAlertMonitor(
            api_client=api_client,
            led_controller=led_controller,
            state_tracker=state_tracker,
            led_states=led_states,
            device_led_states=device_led_states,
            device_macs=device_macs if multi_monitor else None,
            device_schedules=device_schedules,
            name=name,
        )
        monitors.append(monitor)

        logger.info(
            'Monitor "%s": %d device(s), areas=%s',
            name,
            len(device_macs),
            mon_cfg.get('areas_of_interest') or 'all',
        )

    return monitors


async def run_monitor(config: dict):
    """Main loop: create components and poll indefinitely.

    Supports three config modes:

    - **Flat config** (backward compatible): single controller, single monitor.
    - **Multi-monitor**: ``monitors`` list with per-area device groups sharing
      one controller connection and one API poll.
    - **Multi-controller cloud**: ``controllers`` list, each with its own
      ``device_id`` and ``monitors``. One WebRTC connection per controller,
      shared API poll.
    """
    controller_groups = _normalize_config(config)

    # Validate credentials (shared across all controllers)
    first_connection = controller_groups[0][0]

    if not first_connection.get('username') or not first_connection.get('password'):
        logger.error('Controller credentials required. Set "username" and "password" in config.')
        return

    # Validate each controller has either host (local) or device_id (cloud)
    for connection, _ in controller_groups:
        is_cloud = connection.get('connection') == 'cloud'
        if not is_cloud and not connection.get('host'):
            logger.error('Controller missing "host" (local) or "device_id" (cloud). Check config.')
            return
        if is_cloud and connection.get('backend') == 'aiounifi':
            logger.error('Cloud connections require backend="pyunifiapi". aiounifi does not support cloud/WebRTC.')
            return

    # Single HTTP client and API client (shared across all controllers)
    http_client = httpx.AsyncClient(headers=SESSION_HEADERS, timeout=15.0)
    api_client = HomeFrontCommandApiClient(http_client, API_URLS, _log_adapter)

    # Build LED controllers and monitors for each controller group
    led_controllers: list[UnifiLedController] = []
    all_monitors: list[UnifiAlertMonitor] = []
    total_devices = 0

    # Count total monitors across all controllers to determine multi-monitor mode
    total_monitor_cfgs = sum(len(mon_cfgs) for _, mon_cfgs in controller_groups)
    multi_monitor = total_monitor_cfgs > 1

    for connection, monitor_cfgs in controller_groups:
        # Collect device MACs for this controller
        ctrl_macs: list[str] = []
        for mon_cfg in monitor_cfgs:
            ctrl_macs.extend(mon_cfg.get('device_macs', []))

        if not ctrl_macs:
            logger.warning('Controller has no device MACs configured, skipping')
            continue

        # Deduplicate MACs within this controller
        seen: set[str] = set()
        for mac in ctrl_macs:
            mac_lower = mac.lower()
            if mac_lower in seen:
                logger.warning('Device MAC %s appears in multiple monitors', mac)
            seen.add(mac_lower)

        led_controller = UnifiLedController(
            host=connection.get('host', ''),
            username=connection['username'],
            password=connection['password'],
            device_macs=list(seen),
            port=connection.get('port', 443),
            site=connection.get('site'),
            totp_secret=connection.get('totp_secret'),
            backend=connection.get('backend'),
            device_id=connection.get('device_id'),
        )
        led_controllers.append(led_controller)
        total_devices += len(seen)

        monitors = _build_monitors_for_controller(
            connection,
            monitor_cfgs,
            api_client,
            led_controller,
            multi_monitor,
        )
        all_monitors.extend(monitors)

    if not all_monitors:
        logger.error('No monitors configured.')
        return

    interval = first_connection.get('interval', 1)
    logger.info(
        'Starting UniFi LED monitor: %d controller(s), %d monitor(s), %d device(s), polling every %ss',
        len(led_controllers),
        len(all_monitors),
        total_devices,
        interval,
    )

    # Connect all controllers
    for ctrl in led_controllers:
        await ctrl.connect()

    reconcile_interval = 60  # seconds between LED state reconciliation checks
    last_reconcile = 0.0
    metadata_interval = 3600  # seconds between metadata validation checks (1 hour)
    last_metadata_check = 0.0

    try:
        while True:
            try:
                data = await api_client.get_live_alerts()
                for monitor in all_monitors:
                    try:
                        state = await monitor.update(data)
                        logger.debug('%s state: %s', monitor._name, state.value)
                    except Exception:
                        logger.exception('Error updating monitor "%s"', monitor._name)

                # Check for schedule boundary crossings (time-of-day LED changes)
                for monitor in all_monitors:
                    try:
                        await monitor.check_schedule()
                    except Exception:
                        logger.debug('Error checking schedule for "%s"', monitor._name, exc_info=True)

                # Periodic reconciliation: refresh device data and fix mismatches
                now = time.monotonic()
                if now - last_reconcile >= reconcile_interval:
                    last_reconcile = now
                    for ctrl in led_controllers:
                        try:
                            await ctrl._controller.refresh_devices()
                        except Exception:
                            logger.debug('Failed to refresh devices for reconciliation', exc_info=True)
                    total_corrected = 0
                    for monitor in all_monitors:
                        try:
                            total_corrected += await monitor.reconcile()
                        except Exception:
                            logger.debug('Error during reconciliation for "%s"', monitor._name, exc_info=True)
                    if total_corrected:
                        logger.info('Reconciliation corrected %d device(s)', total_corrected)
                    else:
                        logger.info('Reconciliation: all devices in sync')

                # Periodic metadata validation: check areas_of_interest against HFC districts
                if time.monotonic() - last_metadata_check >= metadata_interval:
                    last_metadata_check = time.monotonic()
                    try:
                        await _validate_areas_of_interest(api_client, all_monitors)
                    except Exception:
                        logger.debug('Error during metadata validation', exc_info=True)

            except Exception:
                logger.exception('Error during poll cycle')
            await asyncio.sleep(interval)
    finally:
        for ctrl in led_controllers:
            await ctrl.close()
        await http_client.aclose()
