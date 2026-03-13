"""
HomePod audio controller.

Plays audio on Apple HomePod devices via AirPlay using the pyatv library.

Requires:
    - HomePod on the local network
    - Paired credentials (obtained via --pair)
    - pyatv package installed
"""

# pyright: reportMissingImports=false

import asyncio
import logging
from typing import Any

logger = logging.getLogger('red_alert.homepod')

_PROTOCOL_MAP: dict[str, Any] = {}
_IDLE_STATES: set = set()

try:
    import pyatv as _pyatv
    from pyatv.const import DeviceState, Protocol

    _PROTOCOL_MAP = {
        'airplay': Protocol.AirPlay,
        'companion': Protocol.Companion,
        'raop': Protocol.RAOP,
    }
    _IDLE_STATES = {DeviceState.Idle, DeviceState.Stopped, DeviceState.Paused}
except ImportError:
    _pyatv = None  # type: ignore[assignment]


class HomepodController:
    """Controls audio playback on a single HomePod device via AirPlay."""

    def __init__(self, identifier: str, credentials: dict[str, str] | None = None, name: str | None = None):
        """
        Args:
            identifier: Device identifier (from pyatv scan / --scan).
            credentials: Protocol credentials dict, e.g. {"airplay": "xxxx"}.
            name: Human-readable name for logging.
        """
        self._identifier = identifier
        self._credentials = credentials or {}
        self._name = name or identifier
        self._atv: Any = None
        self._loop_task: asyncio.Task | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def identifier(self) -> str:
        return self._identifier

    async def connect(self):
        """Discover and connect to the HomePod."""
        if _pyatv is None:
            raise ImportError('pyatv is not installed. Install with: pip install pyatv')

        configs = await _pyatv.scan(identifier=self._identifier, timeout=5)
        if not configs:
            raise ConnectionError(f'HomePod not found: {self._name} ({self._identifier})')

        config = configs[0]
        for proto_name, creds in self._credentials.items():
            proto = _PROTOCOL_MAP.get(proto_name.lower())
            if proto:
                config.set_credentials(proto, creds)

        self._atv = await _pyatv.connect(config)
        logger.info('Connected to HomePod: %s', self._name)

    async def play(self, audio: str, volume: int | None = None, loop: bool = False):
        """Play audio on the HomePod.

        Args:
            audio: File path or URL to play.
            volume: Volume level (0-100). None = don't change.
            loop: Whether to loop the audio continuously.
        """
        if not self._atv:
            raise RuntimeError(f'Not connected to HomePod: {self._name}')

        self._cancel_loop()

        if volume is not None:
            await self._atv.audio.set_volume(max(0, min(100, volume)))

        await self._stream(audio)

        if loop:
            self._loop_task = asyncio.create_task(self._loop_audio(audio))

    async def stop(self):
        """Stop playback on the HomePod."""
        self._cancel_loop()
        if self._atv:
            try:
                await self._atv.remote_control.stop()
            except Exception as e:
                logger.debug('Stop playback on %s: %s', self._name, e)

    async def set_volume(self, volume: int):
        """Set volume on the HomePod (0-100)."""
        if self._atv:
            await self._atv.audio.set_volume(max(0, min(100, volume)))

    async def close(self):
        """Disconnect from the HomePod."""
        self._cancel_loop()
        if self._atv:
            self._atv.close()
            self._atv = None

    async def _stream(self, audio: str):
        """Stream audio from file path or URL."""
        if audio.startswith(('http://', 'https://')):
            await self._atv.stream.play_url(audio)
        else:
            await self._atv.stream.stream_file(audio)

    async def _loop_audio(self, audio: str):
        """Background task to re-play audio when playback finishes."""
        try:
            while True:
                await asyncio.sleep(1)
                try:
                    playing = await self._atv.metadata.playing()
                    if playing.device_state in _IDLE_STATES:
                        await self._stream(audio)
                except Exception as e:
                    logger.debug('Loop check failed on %s: %s', self._name, e)
                    await asyncio.sleep(2)
        except asyncio.CancelledError:
            pass

    def _cancel_loop(self):
        """Cancel any active loop task."""
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
            self._loop_task = None
