"""
HomePod audio controller.

Plays audio on Apple HomePod devices via AirPlay by shelling out to
pyatv's atvremote CLI. pyatv requires pydantic-core which cannot build
on Python 3.14 (PyO3 limitation), so we run it as a subprocess under
Python 3.13 via uv.

Requires:
    - HomePod on the local network with "Anyone on the Same Network" speaker access
    - uv installed (for running pyatv under Python 3.13)
    - Audio files in WAV format (pyatv uses miniaudio which doesn't support AIFF)
"""

import asyncio
import logging

logger = logging.getLogger('red_alert.homepod')

DEFAULT_ATVREMOTE_CMD = ['uv', 'run', '--python', '3.13', '--no-project', '--with', 'pyatv', 'atvremote']


class HomepodController:
    """Controls audio playback on a single HomePod device via atvremote subprocess."""

    def __init__(
        self,
        identifier: str,
        host: str | None = None,
        name: str | None = None,
        atvremote_cmd: list[str] | None = None,
        credentials: dict[str, str] | None = None,
    ):
        """
        Args:
            identifier: Device identifier (from atvremote scan).
            host: Device IP address (avoids mDNS discovery timeouts).
            name: Human-readable name for logging.
            atvremote_cmd: Custom atvremote command (default: uv run under Python 3.13).
            credentials: Unused, kept for config compatibility. Credentials are not
                needed when HomeKit speaker access is set to "Anyone on the Same Network".
        """
        self._identifier = identifier
        self._host = host
        self._name = name or identifier
        self._atvremote_cmd = atvremote_cmd or DEFAULT_ATVREMOTE_CMD
        self._loop_task: asyncio.Task | None = None
        self._stream_proc: asyncio.subprocess.Process | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def identifier(self) -> str:
        return self._identifier

    async def connect(self) -> None:
        """Verify the device is reachable by running a quick 'playing' command."""
        returncode, stdout, stderr = await self._run_atvremote('playing')
        if returncode != 0:
            raise ConnectionError(f'Cannot reach HomePod {self._name}: {stderr}')
        logger.info('HomePod reachable: %s (%s)', self._name, self._host or self._identifier)

    async def play(self, audio: str, volume: int | None = None, loop: bool = False) -> None:
        """Play audio on the HomePod.

        Args:
            audio: File path to play (WAV format recommended).
            volume: Volume level (0-100). Requires credentials (not supported in credential-free mode).
            loop: Whether to loop the audio continuously.
        """
        self._cancel_loop()

        if volume is not None:
            returncode, _, stderr = await self._run_atvremote(f'set_volume={volume}')
            if returncode != 0:
                logger.warning('Failed to set volume on %s: %s', self._name, stderr)

        returncode, _, stderr = await self._run_atvremote(f'stream_file={audio}')
        if returncode != 0:
            logger.error('Failed to stream to %s: %s', self._name, stderr)
            return

        logger.info('Streaming %s to %s', audio, self._name)

        if loop:
            self._loop_task = asyncio.create_task(self._loop_audio(audio))

    async def stop(self) -> None:
        """Stop playback on the HomePod."""
        self._cancel_loop()
        returncode, _, stderr = await self._run_atvremote('stop')
        if returncode != 0:
            logger.debug('Stop playback on %s: %s', self._name, stderr)

    async def set_volume(self, volume: int) -> None:
        """Set volume on the HomePod (0-100). Requires credentials."""
        vol = max(0, min(100, volume))
        returncode, _, stderr = await self._run_atvremote(f'set_volume={vol}')
        if returncode != 0:
            logger.warning('Failed to set volume on %s: %s', self._name, stderr)

    async def close(self) -> None:
        """Cancel any active loop task."""
        self._cancel_loop()

    async def _run_atvremote(self, *commands: str, timeout: float = 30.0) -> tuple[int, str, str]:
        """Run an atvremote command and return (returncode, stdout, stderr)."""
        cmd = list(self._atvremote_cmd) + ['--id', self._identifier]
        if self._host:
            cmd.extend(['--scan-hosts', self._host])
        cmd.extend(commands)

        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            stdout = stdout_bytes.decode(errors='replace').strip()
            stderr = stderr_bytes.decode(errors='replace').strip()
            return (proc.returncode or 0, stdout, stderr)
        except asyncio.TimeoutError:
            logger.warning('atvremote timed out on %s after %.0fs', self._name, timeout)
            if proc:
                proc.kill()
            return (1, '', 'timeout')
        except Exception as e:
            logger.error('atvremote failed on %s: %s', self._name, e)
            return (1, '', str(e))

    async def _loop_audio(self, audio: str) -> None:
        """Background task to re-play audio when playback finishes."""
        try:
            while True:
                await asyncio.sleep(3)
                returncode, stdout, _ = await self._run_atvremote('playing')
                if returncode == 0 and ('Idle' in stdout or 'Stopped' in stdout or 'Paused' in stdout):
                    await self._run_atvremote(f'stream_file={audio}')
        except asyncio.CancelledError:
            pass

    def _cancel_loop(self) -> None:
        """Cancel any active loop task."""
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
            self._loop_task = None
