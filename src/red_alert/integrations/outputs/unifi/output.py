"""
UniFi LED alert output for the orchestrator.

Receives AlertEvents and controls UniFi AP LEDs based on merged multi-source
state. Each monitor has its own MultiSourceStateTracker for area filtering
and per-source hold timers.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from red_alert.core.orchestrator import AlertEvent, AlertOutput, MultiSourceStateTracker
from red_alert.core.state import AlertState
from red_alert.integrations.inputs.hfc.api_client import HomeFrontCommandApiClient
from red_alert.integrations.outputs.unifi.led_controller import UnifiLedController
from red_alert.integrations.outputs.unifi.server import (
    _build_monitors_for_controller,
    _normalize_config,
    _validate_areas_of_interest,
    UnifiAlertMonitor,
)

logger = logging.getLogger('red_alert.output.unifi')


class _MultiSourceMonitor:
    """Wraps a UnifiAlertMonitor with a MultiSourceStateTracker for multi-input support."""

    def __init__(
        self,
        monitor: UnifiAlertMonitor,
        areas_of_interest: list[str] | None,
        hold_seconds: dict[str, float] | None,
        name: str,
    ):
        self.monitor = monitor
        self.tracker = MultiSourceStateTracker(
            areas_of_interest=areas_of_interest,
            hold_seconds=hold_seconds,
            logger=logger.getChild(name),
        )
        self.name = name
        self._current_merged_state: AlertState | None = None

    async def handle_event(self, event: AlertEvent) -> None:
        """Route an event through the multi-source tracker and update LEDs on change."""
        old_merged, new_merged = self.tracker.update(event)

        if new_merged != self._current_merged_state:
            self._current_merged_state = new_merged
            await self.monitor.update(event.data, alert_time=event.alert_time)


class UnifiOutput(AlertOutput):
    """Orchestrator output that controls UniFi AP LEDs."""

    def __init__(self, config: dict):
        self._config = config
        self._led_controllers: list[UnifiLedController] = []
        self._monitors: list[_MultiSourceMonitor] = []
        self._all_raw_monitors: list[UnifiAlertMonitor] = []
        self._api_client: HomeFrontCommandApiClient | None = None
        self._http_client: httpx.AsyncClient | None = None
        self._reconcile_task: asyncio.Task | None = None
        self._metadata_task: asyncio.Task | None = None

    @property
    def name(self) -> str:
        return 'unifi'

    async def start(self) -> None:
        from red_alert.integrations.outputs.unifi.server import API_URLS, SESSION_HEADERS

        controller_groups = _normalize_config(self._config)

        first_connection = controller_groups[0][0]
        if not first_connection.get('username') or not first_connection.get('password'):
            raise ValueError('UniFi controller credentials required')

        self._http_client = httpx.AsyncClient(headers=SESSION_HEADERS, timeout=15.0)
        self._api_client = HomeFrontCommandApiClient(self._http_client, API_URLS, logger)

        total_monitor_cfgs = sum(len(mon_cfgs) for _, mon_cfgs in controller_groups)
        multi_monitor = total_monitor_cfgs > 1

        for connection, monitor_cfgs in controller_groups:
            ctrl_macs: list[str] = []
            for mon_cfg in monitor_cfgs:
                ctrl_macs.extend(mon_cfg.get('device_macs', []))

            if not ctrl_macs:
                logger.warning('Controller has no device MACs, skipping')
                continue

            seen: set[str] = set()
            for mac in ctrl_macs:
                seen.add(mac.lower())

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
            self._led_controllers.append(led_controller)

            raw_monitors = _build_monitors_for_controller(
                connection,
                monitor_cfgs,
                self._api_client,
                led_controller,
                multi_monitor,
            )
            self._all_raw_monitors.extend(raw_monitors)

            for i, (mon_cfg, raw_mon) in enumerate(zip(monitor_cfgs, raw_monitors)):
                ms_monitor = _MultiSourceMonitor(
                    monitor=raw_mon,
                    areas_of_interest=mon_cfg.get('areas_of_interest'),
                    hold_seconds=mon_cfg.get('hold_seconds'),
                    name=mon_cfg.get('name', f'monitor-{i}'),
                )
                self._monitors.append(ms_monitor)

        for ctrl in self._led_controllers:
            await ctrl.connect()

        self._reconcile_task = asyncio.create_task(self._reconcile_loop())
        self._metadata_task = asyncio.create_task(self._metadata_loop())

        logger.info(
            'UniFi output started: %d controller(s), %d monitor(s)',
            len(self._led_controllers),
            len(self._monitors),
        )

    async def handle_event(self, event: AlertEvent) -> None:
        for ms_monitor in self._monitors:
            try:
                await ms_monitor.handle_event(event)
            except Exception:
                logger.exception('Error in monitor %s', ms_monitor.name)

        for ms_monitor in self._monitors:
            try:
                await ms_monitor.monitor.check_schedule()
            except Exception:
                logger.debug('Error checking schedule for %s', ms_monitor.name, exc_info=True)

    async def stop(self) -> None:
        for task in (self._reconcile_task, self._metadata_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        for ctrl in self._led_controllers:
            await ctrl.close()
        if self._http_client:
            await self._http_client.aclose()

    async def _reconcile_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            try:
                for ctrl in self._led_controllers:
                    try:
                        await ctrl._controller.refresh_devices()
                    except Exception:
                        logger.debug('Failed to refresh devices', exc_info=True)
                total = 0
                for ms_monitor in self._monitors:
                    try:
                        total += await ms_monitor.monitor.reconcile()
                    except Exception:
                        logger.debug('Reconciliation error for %s', ms_monitor.name, exc_info=True)
                if total:
                    logger.info('Reconciliation corrected %d device(s)', total)
                else:
                    logger.info('Reconciliation: all devices in sync')
            except Exception:
                logger.debug('Reconciliation cycle error', exc_info=True)

    async def _metadata_loop(self) -> None:
        while True:
            await asyncio.sleep(3600)
            try:
                if self._api_client:
                    await _validate_areas_of_interest(self._api_client, self._all_raw_monitors)
            except Exception:
                logger.debug('Metadata validation error', exc_info=True)
