"""
HomePod audio alert output for the orchestrator.

Receives AlertEvents and plays audio on HomePod devices based on
merged multi-source state changes.
"""

from __future__ import annotations

import asyncio
import logging

from red_alert.core.orchestrator import AlertEvent, AlertOutput, MultiSourceStateTracker
from red_alert.core.state import AlertState
from red_alert.integrations.outputs.homepod.audio_controller import HomepodController
from red_alert.integrations.outputs.homepod.server import DeviceAction, _build_device_actions

logger = logging.getLogger('red_alert.output.homepod')

DEFAULT_CONFIG: dict = {
    'hold_seconds': {},
    'areas_of_interest': [],
    'devices': [],
}


class HomepodOutput(AlertOutput):
    """Orchestrator output that plays audio on HomePod devices on state changes."""

    def __init__(self, config: dict):
        self._config = config
        self._tracker: MultiSourceStateTracker | None = None
        self._devices: list[tuple[HomepodController, dict[AlertState, DeviceAction | None]]] = []
        self._previous_state = AlertState.ROUTINE

    @property
    def name(self) -> str:
        return 'homepod'

    async def start(self) -> None:
        cfg = {**DEFAULT_CONFIG, **self._config}

        if not cfg['devices']:
            raise ValueError('No HomePod devices configured')

        self._tracker = MultiSourceStateTracker(
            areas_of_interest=cfg.get('areas_of_interest'),
            hold_seconds=cfg.get('hold_seconds'),
            logger=logger,
        )

        for dev_cfg in cfg['devices']:
            controller = HomepodController(
                identifier=dev_cfg['identifier'],
                credentials=dev_cfg.get('credentials'),
                name=dev_cfg.get('name'),
            )
            actions = _build_device_actions(dev_cfg.get('actions', {}))
            self._devices.append((controller, actions))

        for controller, _ in self._devices:
            try:
                await controller.connect()
            except Exception as e:
                logger.error('Failed to connect to %s: %s', controller.name, e)

        logger.info(
            'HomePod output started: %d device(s), areas=%s',
            len(self._devices),
            cfg.get('areas_of_interest') or 'all',
        )

    async def handle_event(self, event: AlertEvent) -> None:
        if not self._tracker:
            return

        old_merged, new_merged = self._tracker.update(event)

        if new_merged != self._previous_state:
            self._previous_state = new_merged
            logger.info('State changed to %s, updating %d device(s)', new_merged.value, len(self._devices))
            tasks = [self._apply_action(controller, actions.get(new_merged)) for controller, actions in self._devices]
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _apply_action(self, controller: HomepodController, action: DeviceAction | None) -> None:
        try:
            if action is None or action.audio is None:
                await controller.stop()
                if action and action.volume is not None:
                    await controller.set_volume(action.volume)
            else:
                await controller.play(action.audio, volume=action.volume, loop=action.loop)
        except Exception as e:
            logger.error('Error applying action on %s: %s', controller.name, e)

    async def stop(self) -> None:
        for controller, _ in self._devices:
            await controller.close()
