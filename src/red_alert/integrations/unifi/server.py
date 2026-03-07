"""
UniFi LED alert monitor.

Polls the Home Front Command API and sets UniFi AP LED colors
based on alert state via aiounifi. Each state (routine, pre_alert, alert)
is independently configurable with on/off, color, brightness, and blink.

Blink uses the controller's native locate mode (flash LED).

Usage:
    python -m red_alert.integrations.unifi --config config.json
"""

import asyncio
import logging

import httpx

from red_alert.core.api_client import HomeFrontCommandApiClient
from red_alert.core.state import AlertState, AlertStateTracker
from red_alert.integrations.unifi.led_controller import UnifiLedController, rgb_to_hex

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
    'routine': {'on': True, 'color': 'white', 'brightness': 100, 'blink': False},
}

STATE_KEY_MAP = {
    'alert': AlertState.ALERT,
    'pre_alert': AlertState.PRE_ALERT,
    'routine': AlertState.ROUTINE,
}

DEFAULT_CONFIG = {
    'interval': 1,
    'areas_of_interest': [],
    'host': None,
    'username': None,
    'password': None,
    'port': 443,
    'site': 'default',
    'device_macs': [],
    'led_states': {},
    'totp_secret': None,
}


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


def _log_adapter(msg, level='INFO', **kwargs):
    """Adapt Python logging to the core logger interface."""
    getattr(logger, level.lower(), logger.info)(msg)


class UnifiAlertMonitor:
    """Polls the Home Front Command API and controls UniFi AP LEDs based on alert state."""

    def __init__(
        self,
        api_client: HomeFrontCommandApiClient,
        led_controller: UnifiLedController,
        state_tracker: AlertStateTracker,
        led_states: dict[AlertState, dict] | None = None,
    ):
        self._api_client = api_client
        self._led = led_controller
        self._state = state_tracker
        self._led_states = led_states or _build_led_states({})
        self._current_alert_state: AlertState | None = None
        self._locating = False

    @property
    def alert_state(self) -> AlertState:
        return self._state.state

    def _state_cfg(self, state: AlertState) -> dict:
        return self._led_states.get(state, self._led_states[AlertState.ROUTINE])

    async def _apply_led_state(self, cfg: dict):
        """Send the LED state to the controller."""
        color_hex = rgb_to_hex(*cfg['color'])
        await self._led.set_led(on=cfg['on'], color_hex=color_hex, brightness=cfg['brightness'])

    async def poll(self):
        """Poll the API, classify the alert, and update LED state."""
        data = await self._api_client.get_live_alerts()
        state = self._state.update(data)

        if state != self._current_alert_state:
            self._current_alert_state = state
            cfg = self._state_cfg(state)

            # Handle blink via controller locate mode
            should_blink = cfg['blink'] and cfg['on']
            if should_blink != self._locating:
                await self._led.locate(enable=should_blink)
                self._locating = should_blink

            await self._apply_led_state(cfg)

        return state


async def run_monitor(config: dict):
    """Main loop: create components and poll indefinitely."""
    cfg = {**DEFAULT_CONFIG, **config}

    if not cfg['host']:
        logger.error('No controller host configured. Set "host" in config.')
        return

    if not cfg['username'] or not cfg['password']:
        logger.error('Controller credentials required. Set "username" and "password" in config.')
        return

    if not cfg['device_macs']:
        logger.error('No device MACs configured. Add "device_macs" to config.')
        return

    led_states = _build_led_states(cfg.get('led_states', {}))

    http_client = httpx.AsyncClient(headers=SESSION_HEADERS, timeout=15.0)
    api_client = HomeFrontCommandApiClient(http_client, API_URLS, _log_adapter)
    state_tracker = AlertStateTracker(areas_of_interest=cfg.get('areas_of_interest'))

    led_controller = UnifiLedController(
        host=cfg['host'],
        username=cfg['username'],
        password=cfg['password'],
        device_macs=cfg['device_macs'],
        port=cfg.get('port', 443),
        site=cfg.get('site', 'default'),
        totp_secret=cfg.get('totp_secret'),
    )

    monitor = UnifiAlertMonitor(api_client, led_controller, state_tracker, led_states)
    interval = cfg['interval']

    logger.info(
        'Starting UniFi LED monitor: %d device(s), polling every %ss, areas=%s',
        len(cfg['device_macs']),
        interval,
        cfg.get('areas_of_interest') or 'all',
    )

    # Connect and set initial LED state
    await led_controller.connect()
    initial_cfg = led_states[AlertState.ROUTINE]
    initial_hex = rgb_to_hex(*initial_cfg['color'])
    await led_controller.set_led(on=initial_cfg['on'], color_hex=initial_hex, brightness=initial_cfg['brightness'])

    try:
        while True:
            try:
                state = await monitor.poll()
                logger.debug('State: %s', state.value)
            except Exception:
                logger.exception('Error during poll cycle')
            await asyncio.sleep(interval)
    finally:
        await led_controller.close()
        await http_client.aclose()
