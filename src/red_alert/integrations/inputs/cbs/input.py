"""
CBS alert input for the orchestrator.

Wraps the existing CbsAlertMonitor to emit AlertEvents when CBS messages
are received. CBS only produces PRE_ALERT and ALL_CLEAR states (active
ALERT only comes from the HFC API).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from red_alert.core.orchestrator import AlertEvent, AlertInput
from red_alert.core.state import AlertState
from red_alert.integrations.inputs.cbs.parser import CbsMessage
from red_alert.integrations.inputs.cbs.server import (
    CbsAlertMonitor,
    _resolve_location,
    _create_bridge,
    _periodic_polygon_refresh,
    _periodic_health_check,
)

logger = logging.getLogger('red_alert.input.cbs')

SOURCE_NAME = 'cbs'

CBS_STATE_TO_CAT: dict[AlertState, str] = {
    AlertState.PRE_ALERT: '14',
    AlertState.ALERT: '1',
    AlertState.ALL_CLEAR: '13',
}

CBS_STATE_TO_TITLE: dict[AlertState, str] = {
    AlertState.PRE_ALERT: 'CBS pre-alert',
    AlertState.ALERT: 'CBS alert',
    AlertState.ALL_CLEAR: 'האירוע הסתיים',
}


def _cbs_to_alert_dict(state: AlertState, areas: list[str]) -> dict | None:
    """Convert a CBS state to a standard alert dict for the state tracker."""
    if state == AlertState.ROUTINE:
        return None
    return {
        'cat': CBS_STATE_TO_CAT.get(state, '0'),
        'title': CBS_STATE_TO_TITLE.get(state, ''),
        'data': list(areas),
        'desc': '',
    }


class CbsInput(AlertInput):
    """Wraps CbsAlertMonitor to produce AlertEvents for the orchestrator."""

    def __init__(self, config: dict):
        self._config = config
        self._monitor: CbsAlertMonitor | None = None
        self._areas: list[str] = []
        self._background_tasks: list[asyncio.Task] = []

    @property
    def name(self) -> str:
        return SOURCE_NAME

    async def run(self, emit: Callable[[AlertEvent], Awaitable[None]]) -> None:
        cfg = self._config
        self._areas = await _resolve_location(cfg)

        from red_alert.integrations.inputs.cbs.history import CbsHistory

        history_path = cfg.get('history_path')
        if not history_path:
            raise ValueError('history_path is required in CBS config')

        history = CbsHistory(
            path=history_path,
            max_age_seconds=cfg.get('history_max_age', 3600),
        )

        async def on_state_change(old: AlertState, new: AlertState, message: CbsMessage) -> None:
            text_preview = message.text[:100] if message.text else ''
            logger.info('CBS state: %s -> %s | %s', old.value, new.value, text_preview)

            data = _cbs_to_alert_dict(new, self._areas)
            event = AlertEvent(source=SOURCE_NAME, state=new, data=data)
            await emit(event)

        async def on_message(message: CbsMessage, state: AlertState) -> None:
            logger.info('CBS message text:\n%s', message.text)
            history.record(message, state)

        message_id_map = None
        if cfg.get('message_id_map'):
            message_id_map = {int(k): AlertState(v) for k, v in cfg['message_id_map'].items()}

        self._monitor = CbsAlertMonitor(
            qmicli_path=cfg.get('qmicli_path', '/tmp/qmicli'),
            device=cfg.get('device', '/dev/cdc-wdm0'),
            device_open_proxy=cfg.get('device_open_proxy', True),
            message_id_map=message_id_map,
            on_state_change=on_state_change,
            on_message=on_message,
            latitude=cfg.get('latitude'),
            longitude=cfg.get('longitude'),
            areas_of_interest=self._areas,
        )

        latest = history.get_latest_state()
        if latest:
            state, timestamp = latest
            if state != AlertState.ROUTINE:
                self._monitor._state = state
                age = time.time() - timestamp
                logger.info('Startup: restored CBS state %s from history (%.0fs ago)', state.value, age)

                data = _cbs_to_alert_dict(state, self._areas)
                event = AlertEvent(source=SOURCE_NAME, state=state, data=data, alert_time=time.monotonic() - age)
                await emit(event)

        lat = cfg.get('latitude')
        lon = cfg.get('longitude')
        has_coords = lat is not None and lon is not None

        if has_coords:
            self._background_tasks.append(asyncio.create_task(_periodic_polygon_refresh(cfg)))

        bridge = _create_bridge(cfg)
        bridge_mode = bridge is not None

        delay = cfg.get('reconnect_delay', 5)
        max_delay = cfg.get('max_reconnect_delay', 60)

        logger.info(
            'CBS input running: device=%s, areas=%s, bridge=%s',
            cfg.get('device', '/dev/cdc-wdm0'),
            self._areas or 'all',
            f'{bridge.lte_host}:{bridge.bridge_port}' if bridge else 'disabled',
        )

        try:
            if bridge:
                if not await bridge.ensure_bridge():
                    raise RuntimeError('Failed to establish socat bridge to LTE device')

                if not await bridge.configure_cbs(cfg.get('qmicli_path', '/tmp/qmicli'), cfg.get('channels', '919,4370-4383')):
                    logger.warning('CBS channel configuration failed')

                health_interval = cfg.get('health_check_interval', 300)
                if health_interval > 0:
                    self._background_tasks.append(asyncio.create_task(_periodic_health_check(bridge, health_interval)))

            while True:
                try:
                    if bridge_mode and not await bridge.ensure_bridge():
                        logger.error('Bridge is down, waiting %ds before retry...', delay)
                        await asyncio.sleep(delay)
                        delay = min(delay * 2, max_delay)
                        continue

                    returncode = await self._monitor.run_subprocess()
                    if returncode == 0:
                        delay = cfg.get('reconnect_delay', 5)
                    logger.warning('qmicli process ended (code=%s), reconnecting in %ds...', returncode, delay)
                except Exception:
                    logger.exception('Error in CBS monitor, reconnecting in %ds...', delay)

                await asyncio.sleep(delay)
                delay = min(delay * 2, max_delay)
        finally:
            for task in self._background_tasks:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            if bridge:
                await bridge.close()

    async def stop(self) -> None:
        for task in self._background_tasks:
            task.cancel()
