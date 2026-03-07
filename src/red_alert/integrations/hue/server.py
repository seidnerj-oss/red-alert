"""
Philips Hue alert monitor.

Polls the Home Front Command API and sets Hue light colors
based on alert state:
    - ROUTINE   -> default color (white or warm, configurable)
    - PRE_ALERT -> yellow
    - ALERT     -> red

Usage:
    python -m red_alert.integrations.hue --config config.json
"""

import asyncio
import logging

import httpx

from red_alert.core.api_client import HomeFrontCommandApiClient
from red_alert.core.state import AlertState, AlertStateTracker
from red_alert.integrations.hue.light_controller import HueLightController

logger = logging.getLogger('red_alert.hue')

API_URLS = {
    'live': 'https://www.oref.org.il/WarningMessages/alert/alerts.json',
    'history': 'https://www.oref.org.il/WarningMessages/alert/History/AlertsHistory.json',
}

SESSION_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (compatible; red-alert/4.0; Hue)',
    'Referer': 'https://www.oref.org.il/',
    'X-Requested-With': 'XMLHttpRequest',
    'Accept': 'application/json',
    'Accept-Encoding': 'gzip, deflate, br',
    'Accept-Language': 'he,en;q=0.9',
    'Connection': 'keep-alive',
    'Pragma': 'no-cache',
    'Cache-Control': 'no-cache',
}

LED_COLORS = {
    AlertState.ALERT: (255, 0, 0),
    AlertState.PRE_ALERT: (255, 255, 0),
}

DEFAULT_COLORS = {
    'white': (255, 255, 255),
    'warm': (255, 180, 100),
}

DEFAULT_CONFIG = {
    'interval': 1,
    'default_color': 'white',
    'areas_of_interest': [],
    'bridge_ip': None,
    'api_key': None,
    'lights': [],
    'groups': [],
}


def _log_adapter(msg, level='INFO', **kwargs):
    getattr(logger, level.lower(), logger.info)(msg)


class HueAlertMonitor:
    """Polls the Home Front Command API and controls Hue lights based on alert state."""

    def __init__(
        self,
        api_client: HomeFrontCommandApiClient,
        light_controller: HueLightController,
        state_tracker: AlertStateTracker,
        default_color: tuple[int, int, int] = (255, 255, 255),
    ):
        self._api_client = api_client
        self._lights = light_controller
        self._state = state_tracker
        self._default_color = default_color

    @property
    def alert_state(self) -> AlertState:
        return self._state.state

    def _color_for_state(self, state: AlertState) -> tuple[int, int, int]:
        return LED_COLORS.get(state, self._default_color)

    async def poll(self):
        data = await self._api_client.get_live_alerts()
        state = self._state.update(data)
        color = self._color_for_state(state)
        await self._lights.set_color(*color)
        return state


async def run_monitor(config: dict):
    """Main loop: create components and poll indefinitely."""
    cfg = {**DEFAULT_CONFIG, **config}

    if not cfg['bridge_ip'] or not cfg['api_key']:
        logger.error('Hue Bridge IP and API key are required. Run with --register to get an API key.')
        return

    if not cfg['lights'] and not cfg['groups']:
        logger.error('No lights or groups configured. Add "lights" and/or "groups" to config.')
        return

    default_color = DEFAULT_COLORS.get(cfg['default_color'], DEFAULT_COLORS['white'])

    http_client = httpx.AsyncClient(headers=SESSION_HEADERS, timeout=15.0)

    api_client = HomeFrontCommandApiClient(http_client, API_URLS, _log_adapter)
    state_tracker = AlertStateTracker(areas_of_interest=cfg.get('areas_of_interest'))

    light_controller = HueLightController(
        bridge_ip=cfg['bridge_ip'],
        api_key=cfg['api_key'],
        lights=cfg.get('lights'),
        groups=cfg.get('groups'),
    )

    monitor = HueAlertMonitor(api_client, light_controller, state_tracker, default_color)
    interval = cfg['interval']

    logger.info(
        'Starting Hue monitor: %d light(s), %d group(s), polling every %ss, areas=%s, default_color=%s',
        len(cfg.get('lights', [])),
        len(cfg.get('groups', [])),
        interval,
        cfg.get('areas_of_interest') or 'all',
        cfg['default_color'],
    )

    await light_controller.set_color(*default_color)

    try:
        while True:
            try:
                state = await monitor.poll()
                logger.debug('State: %s', state.value)
            except Exception:
                logger.exception('Error during poll cycle')
            await asyncio.sleep(interval)
    finally:
        await light_controller.close()
        await http_client.aclose()
