"""
Philips Hue alert monitor.

Polls the Home Front Command API and sets Hue light colors
based on alert state:
    - ROUTINE   -> default color (white or warm, configurable)
    - PRE_ALERT -> yellow
    - ALERT     -> red

Usage:
    python -m red_alert.integrations.outputs.hue --config config.json
"""

import asyncio
import logging

import httpx

from red_alert.integrations.inputs.hfc.api_client import HomeFrontCommandApiClient
from red_alert.core.state import AlertState, AlertStateTracker
from red_alert.integrations.outputs.hue.light_controller import HueLightController

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

NAMED_COLORS = {
    'red': (255, 0, 0),
    'green': (0, 255, 0),
    'blue': (0, 0, 255),
    'yellow': (255, 255, 0),
    'white': (255, 255, 255),
    'warm': (255, 180, 100),
}

DEFAULT_STATE_COLORS = {
    'alert': 'red',
    'pre_alert': 'yellow',
    'all_clear': 'green',
    'routine': 'white',
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
    'bridge_ip': None,
    'api_key': None,
    'lights': [],
    'groups': [],
    'state_colors': {},
    'light_overrides': {},
}


def _resolve_color(color) -> tuple[int, int, int]:
    """Resolve a color value (name string, hex string, or [R,G,B] list) to an RGB tuple."""
    if isinstance(color, str):
        if color.startswith('#') and len(color) == 7:
            return (int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16))
        return NAMED_COLORS.get(color, NAMED_COLORS['white'])
    return tuple(color[:3])


def _build_state_colors(user_cfg: dict) -> dict[AlertState, tuple[int, int, int]]:
    """Build AlertState -> RGB color mapping from user config merged over defaults."""
    merged = {**DEFAULT_STATE_COLORS, **user_cfg}
    return {STATE_KEY_MAP[key]: _resolve_color(merged[key]) for key in STATE_KEY_MAP}


def _build_light_overrides(
    base_colors: dict[AlertState, tuple[int, int, int]],
    light_ids: list[str],
    group_ids: list[str],
    overrides_cfg: dict,
) -> dict[str, dict[AlertState, tuple[int, int, int]]] | None:
    """Build per-light/group color overrides. Returns None if no overrides configured."""
    if not overrides_cfg:
        return None

    result: dict[str, dict[AlertState, tuple[int, int, int]]] = {}
    all_ids = [(lid, 'light') for lid in light_ids] + [(gid, 'group') for gid in group_ids]

    for device_id, _ in all_ids:
        override = overrides_cfg.get(device_id, {})
        if not override:
            result[device_id] = base_colors
            continue

        state_overrides = override.get('state_colors', override)
        device_colors = {}
        for key, alert_state in STATE_KEY_MAP.items():
            if key in state_overrides:
                device_colors[alert_state] = _resolve_color(state_overrides[key])
            else:
                device_colors[alert_state] = base_colors[alert_state]
        result[device_id] = device_colors

    return result


def _log_adapter(msg, level='INFO', **kwargs):
    getattr(logger, level.lower(), logger.info)(msg)


class HueAlertMonitor:
    """Polls the Home Front Command API and controls Hue lights based on alert state."""

    def __init__(
        self,
        api_client: HomeFrontCommandApiClient,
        light_controller: HueLightController,
        state_tracker: AlertStateTracker,
        state_colors: dict[AlertState, tuple[int, int, int]] | None = None,
        light_overrides: dict[str, dict[AlertState, tuple[int, int, int]]] | None = None,
    ):
        self._api_client = api_client
        self._lights = light_controller
        self._state = state_tracker
        self._state_colors = state_colors or _build_state_colors({})
        self._light_overrides = light_overrides
        self._current_alert_state: AlertState | None = None

    @property
    def alert_state(self) -> AlertState:
        return self._state.state

    def _color_for_state(self, state: AlertState, device_id: str | None = None) -> tuple[int, int, int]:
        if device_id and self._light_overrides and device_id in self._light_overrides:
            return self._light_overrides[device_id].get(state, self._state_colors[AlertState.ROUTINE])
        return self._state_colors.get(state, self._state_colors[AlertState.ROUTINE])

    async def _apply_state(self, state: AlertState):
        """Send the color to lights, with per-light overrides if configured."""
        if self._light_overrides:
            for lid in self._lights._lights:
                color = self._color_for_state(state, lid)
                await self._lights.set_light_color(lid, *color)
            for gid in self._lights._groups:
                color = self._color_for_state(state, gid)
                await self._lights.set_group_color(gid, *color)
        else:
            color = self._color_for_state(state)
            await self._lights.set_color(*color)

    async def poll(self):
        data = await self._api_client.get_live_alerts()
        state = self._state.update(data)

        if state != self._current_alert_state:
            self._current_alert_state = state
            await self._apply_state(state)

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

    state_colors = _build_state_colors(cfg.get('state_colors', {}))

    http_client = httpx.AsyncClient(headers=SESSION_HEADERS, timeout=15.0)

    api_client = HomeFrontCommandApiClient(http_client, API_URLS, _log_adapter)
    state_tracker = AlertStateTracker(areas_of_interest=cfg.get('areas_of_interest'), hold_seconds=cfg.get('hold_seconds'))

    light_ids = [str(lid) for lid in cfg.get('lights', [])]
    group_ids = [str(gid) for gid in cfg.get('groups', [])]

    light_controller = HueLightController(
        bridge_ip=cfg['bridge_ip'],
        api_key=cfg['api_key'],
        lights=cfg.get('lights'),
        groups=cfg.get('groups'),
    )

    overrides_cfg = cfg.get('light_overrides', {})
    light_overrides = _build_light_overrides(state_colors, light_ids, group_ids, overrides_cfg)

    monitor = HueAlertMonitor(api_client, light_controller, state_tracker, state_colors, light_overrides)
    interval = cfg['interval']

    logger.info(
        'Starting Hue monitor: %d light(s), %d group(s), polling every %ss, areas=%s',
        len(light_ids),
        len(group_ids),
        interval,
        cfg.get('areas_of_interest') or 'all',
    )

    routine_color = state_colors[AlertState.ROUTINE]
    await light_controller.set_color(*routine_color)

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
