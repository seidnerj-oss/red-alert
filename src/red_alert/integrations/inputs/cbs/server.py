"""
Cell Broadcast alert monitor.

Spawns qmicli --wms-monitor as a subprocess, parses incoming CBS pages,
reassembles multi-page messages, and maps CBS message IDs to AlertState.

Usage:
    python -m red_alert.integrations.inputs.cbs --config config.json
"""

import asyncio
import logging
import os

import httpx

from red_alert.core.city_data import find_cities_near
from red_alert.core.polygon_data import PolygonDataManager
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

_DEFAULT_POLYGON_CACHE_PATH = os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', 'data', 'polygon_cache.json')

POLYGON_REFRESH_INTERVAL = 86400

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
    'ssh_key_path': None,
    'ssh_username': None,
    'socat_remote_binary': None,
    'health_check_interval': 300,
}


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

        try:
            if proc.stdout:
                async for raw_line in proc.stdout:
                    line = raw_line.decode('utf-8', errors='replace')
                    await self._process_line(line)
        finally:
            proc.terminate()
            await proc.wait()

        returncode = proc.returncode
        if returncode and proc.stderr:
            stderr = await proc.stderr.read()
            logger.error('qmicli exited with code %d: %s', returncode, stderr.decode(errors='replace').strip())

        return returncode


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
    cache_path = cfg.get('polygon_cache_path') or _DEFAULT_POLYGON_CACHE_PATH
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            polygon_mgr = PolygonDataManager(client, cache_path, logger)
            if await polygon_mgr.load():
                cities = polygon_mgr.find_cities_at_point(lat, lon)
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


async def _periodic_polygon_refresh(cfg: dict):
    """Periodically refresh polygon cache in the background."""
    cache_path = cfg.get('polygon_cache_path') or _DEFAULT_POLYGON_CACHE_PATH
    while True:
        await asyncio.sleep(POLYGON_REFRESH_INTERVAL)
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                polygon_mgr = PolygonDataManager(client, cache_path, logger)
                if await polygon_mgr.refresh():
                    logger.info('Polygon data refreshed successfully.')
                else:
                    logger.warning('Polygon data refresh failed.')
        except Exception as e:
            logger.warning('Error during polygon refresh: %s', e)


def _create_bridge(cfg: dict):
    """Create a CbsBridge if bridge mode is configured (lte_host is set)."""
    lte_host = cfg.get('lte_host')
    if not lte_host:
        return None

    from red_alert.integrations.inputs.cbs.bridge import CbsBridge

    return CbsBridge(
        lte_host=lte_host,
        bridge_port=cfg.get('bridge_port', 18222),
        device=cfg['device'],
        ssh_key_path=cfg.get('ssh_key_path'),
        ssh_username=cfg.get('ssh_username'),
        socat_remote_binary=cfg.get('socat_remote_binary'),
    )


async def _periodic_health_check(bridge, qmicli_path: str, interval: int):
    """Periodically check bridge health and log status."""
    while True:
        await asyncio.sleep(interval)
        try:
            status = await bridge.health_check(qmicli_path)
            if status['lte_bridge'] and status['local_bridge']:
                logger.info('Bridge health check: OK (CBS channels: %s)', status.get('cbs_channels', 'unknown'))
            else:
                logger.warning('Bridge health check: LTE=%s, local=%s', status['lte_bridge'], status['local_bridge'])
        except Exception as e:
            logger.warning('Bridge health check error: %s', e)


async def run_monitor(config: dict):
    """Main entry point: run CBS monitor with reconnection logic."""
    cfg = {**DEFAULT_CONFIG, **config}

    message_id_map = None
    if cfg.get('message_id_map'):
        message_id_map = {int(k): AlertState(v) for k, v in cfg['message_id_map'].items()}

    async def on_state_change(old: AlertState, new: AlertState, message: CbsMessage):
        text_preview = message.text[:100] if message.text else ''
        logger.info('Alert state: %s -> %s | %s', old.value, new.value, text_preview)

    async def on_message(message: CbsMessage, state: AlertState):
        logger.info('Message text:\n%s', message.text)

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

    refresh_task = None
    health_check_task = None

    if has_coords:
        refresh_task = asyncio.create_task(_periodic_polygon_refresh(cfg))

    try:
        if bridge:
            if not await bridge.ensure_bridge():
                raise RuntimeError('Failed to establish socat bridge to LTE device')

            logger.info('Configuring CBS channels via bridge...')
            if not await bridge.configure_cbs(cfg['qmicli_path'], cfg['channels']):
                logger.warning('CBS channel configuration failed - monitor may not receive alerts')

            health_interval = cfg.get('health_check_interval', 300)
            if health_interval > 0:
                health_check_task = asyncio.create_task(_periodic_health_check(bridge, cfg['qmicli_path'], health_interval))

        while True:
            try:
                if bridge_mode and not await bridge.ensure_bridge():
                    logger.error('Bridge is down, waiting %ds before retry...', delay)
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, max_delay)
                    continue

                returncode = await monitor.run_subprocess()
                if returncode == 0:
                    delay = cfg['reconnect_delay']
                logger.warning('qmicli process ended (code=%s), reconnecting in %ds...', returncode, delay)
            except Exception:
                logger.exception('Error in CBS monitor, reconnecting in %ds...', delay)

            await asyncio.sleep(delay)
            delay = min(delay * 2, max_delay)
    finally:
        for task in (refresh_task, health_check_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        if bridge:
            await bridge.close()
