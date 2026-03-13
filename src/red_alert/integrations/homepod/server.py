"""
HomePod alert audio monitor.

Polls the Home Front Command API and plays audio on HomePod devices
based on alert state. Each device is independently configurable with
per-state actions (audio file, volume, loop).

Usage:
    python -m red_alert.integrations.homepod --config config.json
"""

import asyncio
import logging
from dataclasses import dataclass

import httpx

from red_alert.core.api_client import HomeFrontCommandApiClient
from red_alert.core.state import AlertState, AlertStateTracker
from red_alert.integrations.homepod.audio_controller import HomepodController

logger = logging.getLogger('red_alert.homepod')

API_URLS = {
    'live': 'https://www.oref.org.il/WarningMessages/alert/alerts.json',
    'history': 'https://www.oref.org.il/WarningMessages/alert/History/AlertsHistory.json',
}

SESSION_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (compatible; red-alert/4.0; HomePod)',
    'Referer': 'https://www.oref.org.il/',
    'X-Requested-With': 'XMLHttpRequest',
    'Accept': 'application/json',
    'Accept-Encoding': 'gzip, deflate, br',
    'Accept-Language': 'he,en;q=0.9',
    'Connection': 'keep-alive',
    'Pragma': 'no-cache',
    'Cache-Control': 'no-cache',
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
    'devices': [],
}


@dataclass
class DeviceAction:
    """Action to perform on a HomePod device for a given alert state."""

    audio: str | None = None
    volume: int | None = None
    loop: bool = False


def _parse_action(cfg: dict | None) -> DeviceAction | None:
    """Parse a device action config dict into a DeviceAction."""
    if cfg is None:
        return None
    return DeviceAction(
        audio=cfg.get('audio'),
        volume=cfg.get('volume'),
        loop=cfg.get('loop', False),
    )


def _build_device_actions(actions_cfg: dict) -> dict[AlertState, DeviceAction | None]:
    """Parse per-state action config into AlertState-keyed dict."""
    result: dict[AlertState, DeviceAction | None] = {}
    for key, alert_state in STATE_KEY_MAP.items():
        result[alert_state] = _parse_action(actions_cfg.get(key))
    return result


def _log_adapter(msg, level='INFO', **kwargs):
    getattr(logger, level.lower(), logger.info)(msg)


class HomepodAlertMonitor:
    """Polls the Home Front Command API and controls HomePod audio based on alert state."""

    def __init__(
        self,
        api_client: HomeFrontCommandApiClient,
        devices: list[tuple[HomepodController, dict[AlertState, DeviceAction | None]]],
        state_tracker: AlertStateTracker,
    ):
        self._api_client = api_client
        self._devices = devices
        self._state = state_tracker
        self._previous_state: AlertState = AlertState.ROUTINE

    @property
    def alert_state(self) -> AlertState:
        return self._state.state

    async def poll(self) -> AlertState:
        """Poll the API, update state, and trigger device actions on state change."""
        data = await self._api_client.get_live_alerts()
        new_state = self._state.update(data)

        if new_state != self._previous_state:
            await self._on_state_change(new_state)
            self._previous_state = new_state

        return new_state

    async def _on_state_change(self, state: AlertState):
        """Apply the configured action for each device in parallel."""
        logger.info('State changed to %s, updating %d device(s)', state.value, len(self._devices))
        tasks = [self._apply_action(controller, actions.get(state)) for controller, actions in self._devices]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _apply_action(self, controller: HomepodController, action: DeviceAction | None):
        """Execute a single device action: play audio, stop, or adjust volume."""
        try:
            if action is None or action.audio is None:
                await controller.stop()
                if action and action.volume is not None:
                    await controller.set_volume(action.volume)
            else:
                await controller.play(action.audio, volume=action.volume, loop=action.loop)
        except Exception as e:
            logger.error('Error applying action on %s: %s', controller.name, e)


async def run_monitor(config: dict):
    """Main loop: create components and poll indefinitely."""
    cfg = {**DEFAULT_CONFIG, **config}

    if not cfg['devices']:
        logger.error('No devices configured. Add "devices" to config.')
        return

    http_client = httpx.AsyncClient(headers=SESSION_HEADERS, timeout=15.0)
    api_client = HomeFrontCommandApiClient(http_client, API_URLS, _log_adapter)
    state_tracker = AlertStateTracker(areas_of_interest=cfg.get('areas_of_interest'), hold_seconds=cfg.get('hold_seconds'))

    devices: list[tuple[HomepodController, dict[AlertState, DeviceAction | None]]] = []
    for dev_cfg in cfg['devices']:
        controller = HomepodController(
            identifier=dev_cfg['identifier'],
            credentials=dev_cfg.get('credentials'),
            name=dev_cfg.get('name'),
        )
        actions = _build_device_actions(dev_cfg.get('actions', {}))
        devices.append((controller, actions))

    monitor = HomepodAlertMonitor(api_client, devices, state_tracker)
    interval = cfg['interval']

    for controller, _ in devices:
        try:
            await controller.connect()
        except Exception as e:
            logger.error('Failed to connect to %s: %s', controller.name, e)

    logger.info(
        'Starting HomePod monitor: %d device(s), polling every %ss, areas=%s',
        len(devices),
        interval,
        cfg.get('areas_of_interest') or 'all',
    )

    try:
        while True:
            try:
                state = await monitor.poll()
                logger.debug('State: %s', state.value)
            except Exception:
                logger.exception('Error during poll cycle')
            await asyncio.sleep(interval)
    finally:
        for controller, _ in devices:
            await controller.close()
        await http_client.aclose()
