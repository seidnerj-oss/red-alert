"""Runtime socat bridge management for CBS monitoring via a remote LTE modem.

Manages the socat QMI proxy bridge between an LTE device (e.g., UniFi LTE Backup Pro)
and the local machine. The bridge allows qmicli running locally to communicate with
the remote device's qmi-proxy over TCP.

Architecture:
    LTE Device                              Local Machine
      qmi-proxy (stock)                       socat (apt)
           |                                       |
      socat (deployed via SSH)              ABSTRACT-LISTEN:qmi-proxy
      TCP-LISTEN:<port>,fork <-network->    TCP:<lte-host>:<port>
      ABSTRACT-CONNECT:qmi-proxy                   |
                                            qmicli --wms-monitor (local subprocess)

socat MIPS binary source:
    Package: socat_1.7.3.1-1_mips_24kc.ipk
    URL: https://downloads.openwrt.org/releases/17.01.6/packages/mips_24kc/packages/socat_1.7.3.1-1_mips_24kc.ipk
    SHA256: to be verified on first download
    Extracted binary: usr/bin/socat (164KB, ELF 32-bit MSB, MIPS, dynamically linked, musl)
    If the URL becomes unavailable, any socat 1.7.x build for mips_24kc musl should work.
"""

from __future__ import annotations

import asyncio
import logging
import shutil

import asyncssh

logger = logging.getLogger('red_alert.cbs.bridge')

DEFAULT_BRIDGE_PORT = 18222
SOCAT_REMOTE_PATH = '/tmp/socat'


class CbsBridge:
    """Manages the socat QMI proxy bridge between an LTE device and the local machine."""

    def __init__(
        self,
        lte_host: str,
        bridge_port: int = DEFAULT_BRIDGE_PORT,
        device: str = '/dev/cdc-wdm0',
        ssh_key_path: str | None = None,
        ssh_username: str | None = None,
        socat_remote_binary: str | None = None,
    ):
        self._lte_host = lte_host
        self._bridge_port = bridge_port
        self._device = device
        self._ssh_key_path = ssh_key_path
        self._ssh_username = ssh_username or 'root'
        self._socat_remote_binary = socat_remote_binary
        self._local_socat_proc: asyncio.subprocess.Process | None = None

    @property
    def lte_host(self) -> str:
        return self._lte_host

    @property
    def bridge_port(self) -> int:
        return self._bridge_port

    @property
    def device(self) -> str:
        return self._device

    def _build_ssh_options(self) -> dict:
        opts: dict = {
            'host': self._lte_host,
            'username': self._ssh_username,
            'known_hosts': None,
        }
        if self._ssh_key_path:
            opts['client_keys'] = [self._ssh_key_path]
        return opts

    async def _ssh_run(self, command: str) -> asyncssh.SSHCompletedProcess:
        """Run a command on the LTE device via SSH."""
        opts = self._build_ssh_options()
        async with asyncssh.connect(**opts) as conn:
            return await conn.run(command)

    async def check_lte_bridge(self) -> bool:
        """Check if socat bridge is running on the LTE device."""
        try:
            result = await self._ssh_run('ps w | grep "socat TCP-LISTEN" | grep -v grep')
            return result.exit_status == 0 and bool(result.stdout and result.stdout.strip())
        except Exception as e:
            logger.error('Failed to check LTE bridge status: %s', e)
            return False

    async def _deploy_socat_to_lte(self) -> bool:
        """Deploy socat binary to the LTE device via SSH."""
        if not self._socat_remote_binary:
            logger.error('No socat binary path configured for deployment')
            return False

        try:
            opts = self._build_ssh_options()
            async with asyncssh.connect(**opts) as conn:
                result = await conn.run(f'test -x {SOCAT_REMOTE_PATH} && echo exists')
                if result.stdout and 'exists' in str(result.stdout):
                    logger.info('socat already present on LTE device at %s', SOCAT_REMOTE_PATH)
                    return True

                logger.info('Deploying socat to LTE device at %s', SOCAT_REMOTE_PATH)
                async with conn.start_sftp_client() as sftp:
                    await sftp.put(self._socat_remote_binary, SOCAT_REMOTE_PATH)
                await conn.run(f'chmod +x {SOCAT_REMOTE_PATH}')
                logger.info('socat deployed successfully')
                return True
        except Exception as e:
            logger.error('Failed to deploy socat to LTE device: %s', e)
            return False

    async def ensure_lte_bridge(self) -> bool:
        """Ensure the socat bridge is running on the LTE device.

        Checks if socat is already running. If not, deploys the binary (if needed)
        and starts the bridge process.
        """
        if await self.check_lte_bridge():
            logger.debug('LTE bridge already running')
            return True

        if self._socat_remote_binary:
            if not await self._deploy_socat_to_lte():
                return False

        try:
            result = await self._ssh_run(f'test -x {SOCAT_REMOTE_PATH} && echo exists')
            if not (result.stdout and 'exists' in str(result.stdout)):
                logger.error(
                    'socat not found on LTE device at %s. Deploy it manually or set socat_remote_binary in config.',
                    SOCAT_REMOTE_PATH,
                )
                return False

            cmd = f'nohup {SOCAT_REMOTE_PATH} TCP-LISTEN:{self._bridge_port},reuseaddr,fork ABSTRACT-CONNECT:qmi-proxy > /dev/null 2>&1 &'
            await self._ssh_run(cmd)
            await asyncio.sleep(0.5)

            if await self.check_lte_bridge():
                logger.info('LTE bridge started on port %d', self._bridge_port)
                return True

            logger.error('LTE bridge failed to start')
            return False

        except Exception as e:
            logger.error('Failed to start LTE bridge: %s', e)
            return False

    async def check_local_bridge(self) -> bool:
        """Check if the local socat bridge process is running."""
        if self._local_socat_proc is None:
            return False
        return self._local_socat_proc.returncode is None

    async def ensure_local_bridge(self) -> bool:
        """Start the local socat bridge if not running.

        Creates a local socat process that listens on an abstract Unix socket
        (mimicking qmi-proxy) and forwards to the LTE device's TCP bridge.
        """
        if await self.check_local_bridge():
            logger.debug('Local bridge already running')
            return True

        socat_path = shutil.which('socat')
        if not socat_path:
            logger.error('socat not found in PATH. Install it (e.g., apt install socat)')
            return False

        try:
            self._local_socat_proc = await asyncio.create_subprocess_exec(
                socat_path,
                'ABSTRACT-LISTEN:qmi-proxy,fork',
                f'TCP:{self._lte_host}:{self._bridge_port}',
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.sleep(0.3)

            if self._local_socat_proc.returncode is not None:
                stderr = b''
                if self._local_socat_proc.stderr:
                    stderr = await self._local_socat_proc.stderr.read()
                logger.error('Local socat exited immediately: %s', stderr.decode(errors='replace').strip())
                self._local_socat_proc = None
                return False

            logger.info('Local bridge started (socat PID %d)', self._local_socat_proc.pid)
            return True

        except Exception as e:
            logger.error('Failed to start local bridge: %s', e)
            self._local_socat_proc = None
            return False

    async def ensure_bridge(self) -> bool:
        """Ensure both LTE-side and local-side bridges are running.

        Returns True if both sides are operational. This should be called
        before starting qmicli and periodically during monitoring.
        """
        if not await self.ensure_lte_bridge():
            logger.error(
                'LTE-side bridge is down. If the LTE device was rebooted, re-run: '
                'python scripts/setup-cbs.py --enable-ssh-only && python scripts/setup-cbs.py --provision-lte-only'
            )
            return False

        if not await self.ensure_local_bridge():
            return False

        return True

    async def configure_cbs(self, qmicli_path: str, channels: str = '919,4370-4383') -> bool:
        """Configure CBS channels on the modem via qmicli through the bridge.

        Runs the sequence: set-cbs-channels, set-broadcast-activation, set-event-report.
        """
        commands = [
            (f'--wms-set-cbs-channels={channels}', f'Setting CBS channels: {channels}'),
            ('--wms-set-broadcast-activation', 'Activating CBS reception'),
            ('--wms-set-event-report', 'Enabling event reporting'),
        ]

        base_cmd = [qmicli_path, '-d', self._device, '--device-open-proxy']

        for flag, desc in commands:
            logger.info('%s', desc)
            try:
                proc = await asyncio.create_subprocess_exec(
                    *base_cmd,
                    flag,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()

                if proc.returncode != 0:
                    logger.error('%s failed (code %d): %s', desc, proc.returncode, stderr.decode(errors='replace').strip())
                    return False

                logger.info('%s: %s', desc, stdout.decode(errors='replace').strip())

            except Exception as e:
                logger.error('%s failed: %s', desc, e)
                return False

        return True

    async def health_check(self, qmicli_path: str) -> dict:
        """Verify bridge connectivity and CBS configuration.

        Returns a dict with status of each component.
        """
        status: dict = {
            'lte_bridge': False,
            'local_bridge': False,
            'cbs_channels': None,
        }

        status['lte_bridge'] = await self.check_lte_bridge()
        status['local_bridge'] = await self.check_local_bridge()

        if status['lte_bridge'] and status['local_bridge']:
            try:
                proc = await asyncio.create_subprocess_exec(
                    qmicli_path,
                    '-d',
                    self._device,
                    '--device-open-proxy',
                    '--wms-get-cbs-channels',
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()

                if proc.returncode == 0:
                    status['cbs_channels'] = stdout.decode(errors='replace').strip()
                else:
                    status['cbs_channels'] = f'error: {stderr.decode(errors="replace").strip()}'
            except Exception as e:
                status['cbs_channels'] = f'error: {e}'

        return status

    async def close(self) -> None:
        """Stop the local socat bridge process."""
        if self._local_socat_proc and self._local_socat_proc.returncode is None:
            logger.info('Stopping local bridge (PID %d)', self._local_socat_proc.pid)
            self._local_socat_proc.terminate()
            try:
                await asyncio.wait_for(self._local_socat_proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._local_socat_proc.kill()
                await self._local_socat_proc.wait()
            self._local_socat_proc = None
