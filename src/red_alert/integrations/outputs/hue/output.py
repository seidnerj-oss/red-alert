"""
Hue light alert output for the orchestrator.

Receives AlertEvents and controls Philips Hue lights based on merged
multi-source state.
"""

from __future__ import annotations

import logging

from red_alert.core.orchestrator import AlertEvent, AlertOutput, MultiSourceStateTracker
from red_alert.core.state import AlertState
from red_alert.integrations.outputs.hue.light_controller import HueLightController
from red_alert.integrations.outputs.hue.server import (
    _build_light_overrides,
    _build_state_colors,
)

logger = logging.getLogger('red_alert.output.hue')

DEFAULT_CONFIG: dict = {
    'hold_seconds': {},
    'areas_of_interest': [],
    'bridge_ip': None,
    'api_key': None,
    'lights': [],
    'groups': [],
    'state_colors': {},
    'light_overrides': {},
}


class HueOutput(AlertOutput):
    """Orchestrator output that controls Philips Hue lights."""

    def __init__(self, config: dict):
        self._config = config
        self._tracker: MultiSourceStateTracker | None = None
        self._light_controller: HueLightController | None = None
        self._state_colors: dict[AlertState, tuple[int, int, int]] = {}
        self._light_overrides: dict[str, dict[AlertState, tuple[int, int, int]]] | None = None
        self._current_state = AlertState.ROUTINE

    @property
    def name(self) -> str:
        return 'hue'

    async def start(self) -> None:
        cfg = {**DEFAULT_CONFIG, **self._config}

        if not cfg['bridge_ip'] or not cfg['api_key']:
            raise ValueError('Hue Bridge IP and API key are required')
        if not cfg['lights'] and not cfg['groups']:
            raise ValueError('No lights or groups configured')

        self._state_colors = _build_state_colors(cfg.get('state_colors', {}))
        light_ids = [str(lid) for lid in cfg.get('lights', [])]
        group_ids = [str(gid) for gid in cfg.get('groups', [])]

        self._light_controller = HueLightController(
            bridge_ip=cfg['bridge_ip'],
            api_key=cfg['api_key'],
            lights=cfg.get('lights'),
            groups=cfg.get('groups'),
        )

        overrides_cfg = cfg.get('light_overrides', {})
        self._light_overrides = _build_light_overrides(self._state_colors, light_ids, group_ids, overrides_cfg)

        self._tracker = MultiSourceStateTracker(
            areas_of_interest=cfg.get('areas_of_interest'),
            hold_seconds=cfg.get('hold_seconds'),
            logger=logger,
        )

        routine_color = self._state_colors[AlertState.ROUTINE]
        await self._light_controller.set_color(*routine_color)

        logger.info(
            'Hue output started: %d light(s), %d group(s), areas=%s',
            len(light_ids),
            len(group_ids),
            cfg.get('areas_of_interest') or 'all',
        )

    async def handle_event(self, event: AlertEvent) -> None:
        if not self._tracker or not self._light_controller:
            return

        old_merged, new_merged = self._tracker.update(event)

        if new_merged != self._current_state:
            self._current_state = new_merged
            await self._apply_state(new_merged)

    async def _apply_state(self, state: AlertState) -> None:
        assert self._light_controller is not None
        if self._light_overrides:
            for lid in self._light_controller._lights:
                color = self._color_for(state, lid)
                await self._light_controller.set_light_color(lid, *color)
            for gid in self._light_controller._groups:
                color = self._color_for(state, gid)
                await self._light_controller.set_group_color(gid, *color)
        else:
            color = self._color_for(state)
            await self._light_controller.set_color(*color)

    def _color_for(self, state: AlertState, device_id: str | None = None) -> tuple[int, int, int]:
        if device_id and self._light_overrides and device_id in self._light_overrides:
            return self._light_overrides[device_id].get(state, self._state_colors[AlertState.ROUTINE])
        return self._state_colors.get(state, self._state_colors[AlertState.ROUTINE])

    async def stop(self) -> None:
        if self._light_controller:
            await self._light_controller.close()
