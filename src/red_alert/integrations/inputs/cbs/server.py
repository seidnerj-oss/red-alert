"""
Cell Broadcast alert monitor.

Spawns qmicli --wms-monitor as a subprocess, parses incoming CBS pages,
reassembles multi-page messages, and maps CBS message IDs to AlertState.

Usage:
    python -m red_alert.integrations.inputs.cbs --config config.json
"""

import asyncio
import contextlib
import logging
import time

import httpx

from red_alert.core.city_data import find_cities_near
from red_alert.core.state import AlertState
from red_alert.integrations.inputs.cbs.parser import CbsMessage, CbsMessageAssembler, CbsPageParser

logger = logging.getLogger('red_alert.cbs')

# CBS Message ID to AlertState mapping
# Based on real captures from Israeli Home Front Command broadcasts:
#   4370 = Presidential Alert: "Alerts are expected in a few minutes" -> PRE_ALERT
#   4371-4372 = Extreme alerts (active rocket/missile fire) -> ALERT
#   4373-4378 = Severe alerts: "The event has ended" (observed as all-clear) -> ALL_CLEAR
#   4379 = AMBER -> ALERT
#   4380-4382 = Test/exercise -> ROUTINE
#   4383 = EU-Alert Level 1 -> ALERT
DEFAULT_MESSAGE_ID_MAP: dict[int, AlertState] = {
    4370: AlertState.PRE_ALERT,
    4371: AlertState.ALERT,
    4372: AlertState.ALERT,
    4373: AlertState.ALL_CLEAR,
    4374: AlertState.ALL_CLEAR,
    4375: AlertState.ALL_CLEAR,
    4376: AlertState.ALL_CLEAR,
    4377: AlertState.ALL_CLEAR,
    4378: AlertState.ALL_CLEAR,
    4379: AlertState.ALERT,
    4380: AlertState.ROUTINE,
    4381: AlertState.ROUTINE,
    4382: AlertState.ROUTINE,
    4383: AlertState.ALERT,
}

DEFAULT_CONFIG: dict = {
    'qmicli_path': '/tmp/qmicli',
    'device': '/dev/cdc-wdm0',
    'device_open_proxy': True,
    'channels': '919,4370-4383',
    'message_id_map': None,
    'reconnect_delay': 5,
    'max_reconnect_delay': 60,
    'latitude': None,
    'longitude': None,
    'areas_of_interest': [],
    'city_data_path': None,
    'location_radius_km': 5.0,
    'polygon_cache_path': None,
    'lte_host': None,
    'bridge_port': 18222,
    'bridge_management': 'self',
    'health_check_interval': 300,
    'history_path': None,
    'history_max_age': 3600,
}

# Consecutive failed bridge health checks before the monitor is restarted to rebuild
# the bridge. One failure is not enough: the health check reports any SSH error as
# "down", so a momentary network blip would otherwise cost a full teardown (and the
# CBS blind spot while it runs). Two in a row means the link has been bad across a
# whole health_check_interval, which a blip does not survive.
HEALTH_CHECK_FAILURES_BEFORE_REBUILD = 2

# Seconds qmicli gets to exit after SIGTERM before it is killed. On a dead link it may
# block trying to release its WMS client through the proxy.
QMICLI_STOP_TIMEOUT = 10.0


class CbsAlertMonitor:
    """Monitors CBS messages from qmicli and tracks alert state."""

    def __init__(
        self,
        qmicli_path: str,
        device: str,
        device_open_proxy: bool = True,
        message_id_map: dict[int, AlertState] | None = None,
        on_state_change=None,
        on_message=None,
        latitude: float | None = None,
        longitude: float | None = None,
        areas_of_interest: list[str] | None = None,
    ):
        self._qmicli_path = qmicli_path
        self._device = device
        self._device_open_proxy = device_open_proxy
        self._message_id_map = message_id_map or DEFAULT_MESSAGE_ID_MAP
        self._on_state_change = on_state_change
        self._on_message = on_message
        self._parser = CbsPageParser()
        self._assembler = CbsMessageAssembler()
        self._state = AlertState.ROUTINE
        self._previous_state = AlertState.ROUTINE
        self._latitude = latitude
        self._longitude = longitude
        self._areas_of_interest = areas_of_interest or []
        self._proc: asyncio.subprocess.Process | None = None

    @property
    def alert_state(self) -> AlertState:
        return self._state

    @property
    def areas_of_interest(self) -> list[str]:
        return self._areas_of_interest

    @property
    def location(self) -> tuple[float, float] | None:
        if self._latitude is not None and self._longitude is not None:
            return (self._latitude, self._longitude)
        return None

    def classify_message(self, message: CbsMessage) -> AlertState:
        """Map a CBS message ID to an AlertState."""
        return self._message_id_map.get(message.message_id, AlertState.ROUTINE)

    async def _handle_message(self, message: CbsMessage):
        """Process a complete CBS message."""
        new_state = self.classify_message(message)

        logger.info(
            'CBS message: id=%d serial=0x%04x code=%d pages=%d state=%s',
            message.message_id,
            message.serial_number,
            message.message_code,
            message.total_pages,
            new_state.value,
        )

        if self._on_message:
            await self._on_message(message, new_state)

        if new_state != self._state:
            old_state = self._state
            self._state = new_state
            logger.info('State changed: %s -> %s', old_state.value, new_state.value)
            if self._on_state_change:
                await self._on_state_change(old_state, new_state, message)

    async def _process_line(self, line: str):
        """Feed a line to the parser pipeline."""
        page = self._parser.feed_line(line)
        if page:
            message = self._assembler.add_page(page)
            if message:
                await self._handle_message(message)

    async def run_subprocess(self):
        """Spawn qmicli --wms-monitor and process its output."""
        cmd = [self._qmicli_path, '-d', self._device]
        if self._device_open_proxy:
            cmd.append('--device-open-proxy')
        cmd.append('--wms-monitor')

        logger.info('Starting qmicli: %s', ' '.join(cmd))

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._proc = proc

        try:
            if proc.stdout:
                async for raw_line in proc.stdout:
                    line = raw_line.decode('utf-8', errors='replace')
                    await self._process_line(line)
        finally:
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.terminate()
            await proc.wait()
            if self._proc is proc:
                self._proc = None

        returncode = proc.returncode
        if returncode and proc.stderr:
            stderr = await proc.stderr.read()
            logger.error('qmicli exited with code %d: %s', returncode, stderr.decode(errors='replace').strip())

        return returncode

    async def stop_subprocess(self, timeout: float) -> None:
        """Stop the running qmicli process so ``run_subprocess`` returns.

        qmicli does not notice when the link under it dies (the TCP connection to a
        rebooted device stays half-open), so it can sit forever producing no output.
        SIGTERM first, SIGKILL if it has not exited after ``timeout`` seconds.
        No-op if no process is running.
        """
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout)
        except TimeoutError:
            logger.warning('qmicli did not exit %.0fs after SIGTERM, killing it', timeout)
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()


async def _resolve_location(cfg: dict) -> list[str]:
    """Resolve device coordinates to areas of interest.

    Uses polygon data (point-in-polygon) as primary method, falling back
    to centroid radius matching if polygon data is unavailable.

    Raises ValueError if no location is configured or coordinates don't resolve to any cities.
    """
    areas = list(cfg.get('areas_of_interest', []))
    lat = cfg.get('latitude')
    lon = cfg.get('longitude')
    has_coords = lat is not None and lon is not None

    if not has_coords and not areas:
        raise ValueError('CBS monitor requires device location. Set areas_of_interest or latitude/longitude in config.')

    if not has_coords:
        return areas

    assert lat is not None and lon is not None
    lat_f: float = float(lat)
    lon_f: float = float(lon)

    # Try polygon-based resolution first
    polygon_cities = await _resolve_via_polygons(lat_f, lon_f, cfg)

    # Fall back to centroid radius matching
    if polygon_cities is None:
        polygon_cities = _resolve_via_centroids(lat_f, lon_f, cfg)

    if areas:
        _validate_areas_overlap(lat_f, lon_f, areas, polygon_cities, cfg)
        return areas

    if polygon_cities:
        logger.info('Resolved device coordinates (%.4f, %.4f) to %d cities: %s', lat_f, lon_f, len(polygon_cities), polygon_cities[:10])
        return polygon_cities

    raise ValueError(
        f'Device coordinates ({lat_f}, {lon_f}) did not resolve to any known cities. Set areas_of_interest explicitly or verify coordinates.'
    )


async def _resolve_via_polygons(lat: float, lon: float, cfg: dict) -> list[str] | None:
    """Try to resolve coordinates using polygon data. Returns None if polygon data is unavailable."""
    from red_alert.core.city_data import CityDataManager, _DEFAULT_CITY_DATA_PATH
    from red_alert.integrations.inputs.hfc.api_client import HomeFrontCommandApiClient

    city_data_path = cfg.get('city_data_path') or _DEFAULT_CITY_DATA_PATH
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            api_client = HomeFrontCommandApiClient(client, {}, logger)
            city_mgr = CityDataManager(city_data_path, '', api_client, logger.info)
            await city_mgr._fetch_polygons()
            if city_mgr._polygons:
                cities = city_mgr.find_cities_at_point(lat, lon)
                if cities:
                    logger.info('Polygon resolution: found %d cities at (%.4f, %.4f): %s', len(cities), lat, lon, cities[:10])
                    return cities
                logger.info('Polygon resolution: no cities contain point (%.4f, %.4f), falling back to centroid.', lat, lon)
                return None
    except Exception as e:
        logger.warning('Polygon resolution failed: %s', e)
    return None


def _resolve_via_centroids(lat: float, lon: float, cfg: dict) -> list[str]:
    """Resolve coordinates using centroid radius matching."""
    nearby = find_cities_near(lat, lon, radius_km=cfg.get('location_radius_km', 5.0), city_data_path=cfg.get('city_data_path'))
    if nearby:
        logger.info('Centroid resolution: found %d nearby cities at (%.4f, %.4f): %s', len(nearby), lat, lon, nearby[:10])
    return nearby


def _validate_areas_overlap(lat: float, lon: float, areas: list[str], resolved: list[str] | None, cfg: dict):
    """Log warnings/confirmations about overlap between explicit areas and resolved locations."""
    if not resolved:
        logger.warning(
            'Device coordinates (%.4f, %.4f) did not resolve to any known cities (radius=%.1fkm)',
            lat,
            lon,
            cfg.get('location_radius_km', 5.0),
        )
        return

    areas_set = set(areas)
    resolved_set = set(resolved)
    overlap = areas_set & resolved_set
    if not overlap:
        logger.warning(
            'Device coordinates (%.4f, %.4f) resolved to %s, but none overlap with configured areas_of_interest %s',
            lat,
            lon,
            list(resolved_set)[:10],
            areas,
        )
    else:
        logger.info('Device coordinates confirm overlap with configured areas: %s', sorted(overlap))


def _create_bridge(cfg: dict):
    """Create a CbsBridge if bridge mode is configured (lte_host is set).

    With ``bridge_management`` "self" (the default) this also builds the lte-pro-access
    supervisor that runs the relays; see ``build_supervisor`` for the keys it requires.
    """
    lte_host = cfg.get('lte_host')
    if not lte_host:
        return None

    from red_alert.integrations.inputs.cbs.bridge import BRIDGE_MANAGEMENT_MODES, CbsBridge, build_supervisor

    management = cfg.get('bridge_management', 'self')
    if management not in BRIDGE_MANAGEMENT_MODES:
        raise ValueError(f'bridge_management must be one of {BRIDGE_MANAGEMENT_MODES}, got {management!r}')
    bridge_port = cfg.get('bridge_port', 18222)
    supervisor = None
    if management == 'self':
        supervisor = build_supervisor(cfg, bridge_port, cfg.get('health_check_interval', 300))

    return CbsBridge(
        lte_host=lte_host,
        bridge_port=bridge_port,
        device=cfg['device'],
        management=management,
        supervisor=supervisor,
    )


async def _periodic_health_check(bridge, monitor: CbsAlertMonitor, interval: int, failure_threshold: int, stop_timeout: float):
    """Periodically check bridge health; rebuild the bridge after repeated failures.

    After ``failure_threshold`` consecutive checks with either side down, marks the
    bridge for a fresh start and stops the qmicli monitor. The reconnect loop in
    ``run_monitor`` then runs ``ensure_bridge`` (full teardown and rebuild) and
    ``configure_cbs`` before starting qmicli again - the same path as a service
    restart. Without this, a qmicli attached to a dead link never exits, so the
    reconnect loop never runs.

    A check that raises is logged and neither counts as a failure nor resets the count.
    """
    failures = 0
    while True:
        await asyncio.sleep(interval)
        try:
            status = await bridge.health_check()
        except Exception as e:
            logger.warning('Bridge health check error: %s', e)
            continue

        if status['lte_bridge'] and status['local_bridge']:
            failures = 0
            logger.info('Bridge health check: OK')
            continue

        failures += 1
        logger.warning(
            'Bridge health check: LTE=%s, local=%s (%d/%d consecutive failures)',
            status['lte_bridge'],
            status['local_bridge'],
            failures,
            failure_threshold,
        )
        if failures >= failure_threshold:
            logger.warning('Bridge down for %d consecutive health checks - restarting qmicli to rebuild the bridge', failures)
            failures = 0
            bridge.request_fresh_start()
            await monitor.stop_subprocess(stop_timeout)


async def run_monitor(config: dict):
    """Main entry point: run CBS monitor with reconnection logic."""
    cfg = {**DEFAULT_CONFIG, **config}

    message_id_map = None
    if cfg.get('message_id_map'):
        message_id_map = {int(k): AlertState(v) for k, v in cfg['message_id_map'].items()}

    from red_alert.integrations.inputs.cbs.history import CbsHistory

    history_path = cfg.get('history_path')
    if not history_path:
        raise ValueError('history_path is required in CBS config (or will be auto-set when using --config)')

    history = CbsHistory(
        path=history_path,
        max_age_seconds=cfg.get('history_max_age', 3600),
    )

    async def on_state_change(old: AlertState, new: AlertState, message: CbsMessage):
        text_preview = message.text[:100] if message.text else ''
        logger.info('Alert state: %s -> %s | %s', old.value, new.value, text_preview)

    async def on_message(message: CbsMessage, state: AlertState):
        logger.info('Message text:\n%s', message.text)
        history.record(message, state)

    areas = await _resolve_location(cfg)

    monitor = CbsAlertMonitor(
        qmicli_path=cfg['qmicli_path'],
        device=cfg['device'],
        device_open_proxy=cfg.get('device_open_proxy', True),
        message_id_map=message_id_map,
        on_state_change=on_state_change,
        on_message=on_message,
        latitude=cfg.get('latitude'),
        longitude=cfg.get('longitude'),
        areas_of_interest=areas,
    )

    # Seed initial state from history (CBS has no external history endpoint)
    latest = history.get_latest_state()
    if latest:
        state, timestamp = latest
        if state != AlertState.ROUTINE:
            monitor._state = state
            age = time.time() - timestamp
            logger.info('Startup: restored CBS state %s from history (%.0fs ago)', state.value, age)

    delay = cfg['reconnect_delay']
    max_delay = cfg['max_reconnect_delay']

    lat = cfg.get('latitude')
    lon = cfg.get('longitude')
    has_coords = lat is not None and lon is not None
    location_desc = f'lat={lat}, lon={lon}' if has_coords else 'not set'

    bridge = _create_bridge(cfg)
    bridge_mode = bridge is not None

    logger.info(
        'Starting CBS monitor: device=%s, qmicli=%s, location=%s, areas=%s, bridge=%s',
        cfg['device'],
        cfg['qmicli_path'],
        location_desc,
        areas or 'all',
        f'{bridge.lte_host}:{bridge.bridge_port}' if bridge else 'disabled',
    )

    health_check_task = None

    try:
        while True:
            try:
                if bridge_mode:
                    if not await bridge.ensure_bridge():
                        logger.error('Bridge is down, waiting %ds before retry...', delay)
                        await asyncio.sleep(delay)
                        delay = min(delay * 2, max_delay)
                        continue

                    if not await bridge.configure_cbs(cfg['qmicli_path'], cfg['channels']):
                        logger.warning('CBS channel configuration failed - monitor may not receive alerts')

                    if not health_check_task:
                        health_interval = cfg.get('health_check_interval', 300)
                        if health_interval > 0:
                            health_check_task = asyncio.create_task(
                                _periodic_health_check(
                                    bridge,
                                    monitor,
                                    health_interval,
                                    HEALTH_CHECK_FAILURES_BEFORE_REBUILD,
                                    QMICLI_STOP_TIMEOUT,
                                )
                            )

                returncode = await monitor.run_subprocess()
                if returncode == 0:
                    delay = cfg['reconnect_delay']
                logger.warning('qmicli process ended (code=%s), reconnecting in %ds...', returncode, delay)
            except Exception:
                logger.exception('Error in CBS monitor, reconnecting in %ds...', delay)

            await asyncio.sleep(delay)
            delay = min(delay * 2, max_delay)
    finally:
        if health_check_task:
            health_check_task.cancel()
            try:
                await health_check_task
            except asyncio.CancelledError:
                pass

        if bridge:
            await bridge.close()
