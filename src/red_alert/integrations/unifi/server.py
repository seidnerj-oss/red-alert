"""
UniFi LED alert monitor.

Polls the Home Front Command API and sets UniFi AP LED colors
based on alert state:
    - ROUTINE   -> default color (green or white, configurable)
    - PRE_ALERT -> yellow
    - ALERT     -> red

Usage:
    python -m red_alert.integrations.unifi --config config.json
"""

import asyncio
import logging

import httpx

from red_alert.core.api_client import HomeFrontCommandApiClient
from red_alert.core.state import AlertState, AlertStateTracker
from red_alert.integrations.unifi.led_controller import UnifiLedController

logger = logging.getLogger('red_alert.unifi')

API_URLS = {
    'live': 'https://www.oref.org.il/WarningMessages/alert/alerts.json',
    'history': 'https://www.oref.org.il/WarningMessages/alert/History/AlertsHistory.json',
}

SESSION_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (compatible; RedAlert/3.0; UniFi)',
    'Referer': 'https://www.oref.org.il/',
    'X-Requested-With': 'XMLHttpRequest',
    'Accept': 'application/json',
    'Accept-Encoding': 'gzip, deflate, br',
    'Accept-Language': 'he,en;q=0.9',
    'Connection': 'keep-alive',
    'Pragma': 'no-cache',
    'Cache-Control': 'no-cache',
}

# LED colors as RGB tuples
LED_COLORS = {
    AlertState.ALERT: (255, 0, 0),  # Red
    AlertState.PRE_ALERT: (255, 255, 0),  # Yellow
}

DEFAULT_COLORS = {
    'green': (0, 255, 0),
    'white': (255, 255, 255),
}

DEFAULT_CONFIG = {
    'interval': 1,
    'default_color': 'white',
    'areas_of_interest': [],
    'ssh_username': 'admin',
    'ssh_key_path': None,
    'known_hosts': None,
    'devices': [],
}


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
        default_color: tuple[int, int, int] = (255, 255, 255),
    ):
        self._api_client = api_client
        self._led = led_controller
        self._state = state_tracker
        self._default_color = default_color

    @property
    def alert_state(self) -> AlertState:
        return self._state.state

    def _color_for_state(self, state: AlertState) -> tuple[int, int, int]:
        return LED_COLORS.get(state, self._default_color)

    async def poll(self):
        """Poll the API, classify the alert, and update LED color."""
        data = await self._api_client.get_live_alerts()
        state = self._state.update(data)
        color = self._color_for_state(state)
        await self._led.set_color(*color)
        return state


async def run_monitor(config: dict):
    """Main loop: create components and poll indefinitely."""
    cfg = {**DEFAULT_CONFIG, **config}

    if not cfg['devices']:
        logger.error('No UniFi devices configured. Add "devices" to config.')
        return

    default_color = DEFAULT_COLORS.get(cfg['default_color'], DEFAULT_COLORS['white'])

    http_client = httpx.AsyncClient(headers=SESSION_HEADERS, timeout=15.0)

    api_client = HomeFrontCommandApiClient(http_client, API_URLS, _log_adapter)
    state_tracker = AlertStateTracker(areas_of_interest=cfg.get('areas_of_interest'))

    led_controller = UnifiLedController(
        devices=cfg['devices'],
        ssh_username=cfg.get('ssh_username', 'admin'),
        ssh_key_path=cfg.get('ssh_key_path'),
        known_hosts=cfg.get('known_hosts'),
    )

    monitor = UnifiAlertMonitor(api_client, led_controller, state_tracker, default_color)
    interval = cfg['interval']

    logger.info(
        'Starting UniFi LED monitor: %d device(s), polling every %ss, areas=%s, default_color=%s',
        len(cfg['devices']),
        interval,
        cfg.get('areas_of_interest') or 'all',
        cfg['default_color'],
    )

    # Set initial color
    await led_controller.set_color(*default_color)

    try:
        while True:
            try:
                state = await monitor.poll()
                logger.debug('State: %s', state.value)
            except Exception:
                logger.exception('Error during poll cycle')
            await asyncio.sleep(interval)
    finally:
        await http_client.aclose()
