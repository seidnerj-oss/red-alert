"""
Cell Broadcast alert monitor.

Spawns qmicli --wms-monitor as a subprocess, parses incoming CBS pages,
reassembles multi-page messages, and maps CBS message IDs to AlertState.

Usage:
    python -m red_alert.integrations.inputs.cbs --config config.json
"""

import asyncio
import logging

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

    monitor = CbsAlertMonitor(
        qmicli_path=cfg['qmicli_path'],
        device=cfg['device'],
        device_open_proxy=cfg.get('device_open_proxy', True),
        message_id_map=message_id_map,
        on_state_change=on_state_change,
        on_message=on_message,
        latitude=cfg.get('latitude'),
        longitude=cfg.get('longitude'),
        areas_of_interest=cfg.get('areas_of_interest', []),
    )

    delay = cfg['reconnect_delay']
    max_delay = cfg['max_reconnect_delay']

    areas = cfg.get('areas_of_interest', [])
    lat, lon = cfg.get('latitude'), cfg.get('longitude')
    location_desc = f'lat={lat}, lon={lon}' if lat is not None and lon is not None else 'not set'

    logger.info(
        'Starting CBS monitor: device=%s, qmicli=%s, location=%s, areas=%s',
        cfg['device'],
        cfg['qmicli_path'],
        location_desc,
        areas or 'all',
    )

    while True:
        try:
            returncode = await monitor.run_subprocess()
            if returncode == 0:
                delay = cfg['reconnect_delay']
            logger.warning('qmicli process ended (code=%s), reconnecting in %ds...', returncode, delay)
        except Exception:
            logger.exception('Error in CBS monitor, reconnecting in %ds...', delay)

        await asyncio.sleep(delay)
        delay = min(delay * 2, max_delay)
