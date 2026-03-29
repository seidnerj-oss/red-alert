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
import os
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
        device: str = '/tmp/cdc-wdm0',
        lte_device_ssh_key_path: str | None = None,
        ssh_username: str | None = None,
        socat_remote_binary: str | None = None,
        lte_device_mac: str | None = None,
        unifi: dict | None = None,
    ):
        self._lte_host = lte_host
        self._bridge_port = bridge_port
        self._device = device
        self._lte_device_ssh_key_path = lte_device_ssh_key_path
        self._ssh_username = ssh_username or 'root'
        self._socat_remote_binary = socat_remote_binary
        self._local_socat_proc: asyncio.subprocess.Process | None = None
        self._fresh_start = True
        self._lte_device_mac = lte_device_mac
        self._unifi = unifi or {}

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
        if self._lte_device_ssh_key_path:
            opts['client_keys'] = [self._lte_device_ssh_key_path]
        return opts

    async def _ssh_run(self, command: str) -> asyncssh.SSHCompletedProcess:
        """Run a command on the LTE device via SSH.

        If SSH connection is refused (port 22 closed), attempts to re-enable SSH
        on the LTE device via the UniFi controller's WebRTC debug terminal, then retries.
        """
        opts = self._build_ssh_options()
        try:
            async with asyncssh.connect(**opts) as conn:
                return await conn.run(command)
        except OSError as e:
            if 'Connect call failed' not in str(e) and 'Connection refused' not in str(e):
                raise
            if not await self._enable_lte_ssh():
                raise
            async with asyncssh.connect(**opts) as conn:
                return await conn.run(command)

    async def _enable_lte_ssh(self) -> bool:
        """Re-enable SSH on the LTE device via the UniFi controller.

        Uses the WebRTC debug terminal to write the SSH public key and start dropbear.
        Requires a 'unifi' section in the CBS config with controller credentials,
        plus lte_device_mac and lte_device_ssh_key_path.
        """
        unifi = self._unifi
        if not unifi.get('host') or not unifi.get('username') or not unifi.get('password') or not self._lte_device_mac:
            logger.warning('Cannot auto-enable SSH: unifi controller credentials or lte_device_mac not configured in CBS config')
            return False
        if not self._lte_device_ssh_key_path:
            logger.warning('Cannot auto-enable SSH: lte_device_ssh_key_path not configured')
            return False

        try:
            from red_alert.integrations.inputs.cbs.lte_ssh import build_controller_config, enable_ssh, read_pubkey

            pubkey_path = self._lte_device_ssh_key_path + '.pub'
            pubkey = read_pubkey(pubkey_path)
            config = build_controller_config(
                host=unifi.get('host'),
                username=unifi['username'],
                password=unifi['password'],
                port=unifi.get('port', 443),
                site=unifi.get('site', 'default'),
                totp_secret=unifi.get('totp_secret'),
            )
            logger.info('SSH connection refused - re-enabling SSH on LTE device %s via controller', self._lte_device_mac)
            await enable_ssh(config, self._lte_device_mac, pubkey)
            await asyncio.sleep(3)
            return True
        except Exception as e:
            logger.error('Failed to auto-enable SSH on LTE device: %s', e)
            return False

    async def check_lte_bridge(self) -> bool:
        """Check if socat bridge is running on the LTE device."""
        try:
            result = await self._ssh_run('ps w | grep "socat TCP-LISTEN" | grep -v grep')
            return result.exit_status == 0 and bool(result.stdout and result.stdout.strip())
        except Exception as e:
            logger.error('Failed to check LTE bridge status: %s', e)
            return False

    async def _deploy_socat_to_lte(self) -> bool:
        """Deploy socat binary to the LTE device via SSH.

        Uses SCP (not SFTP) because the LTE device runs dropbear which
        does not support the SFTP subsystem.
        """
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

                logger.info('Deploying socat to LTE device at %s via SCP', SOCAT_REMOTE_PATH)
                await asyncssh.scp(self._socat_remote_binary, (conn, SOCAT_REMOTE_PATH))
                await conn.run(f'chmod +x {SOCAT_REMOTE_PATH}')
                logger.info('socat deployed successfully')
                return True
        except Exception as e:
            logger.error('Failed to deploy socat to LTE device: %s', e)
            return False

    async def _kill_lte_socat(self) -> None:
        """Kill socat, qmicli, and qmi-proxy on the LTE device.

        Killing qmi-proxy is necessary because it holds QMI client
        registrations in memory. Even after socat and qmicli are killed,
        qmi-proxy retains the stale WMS client slot and rejects new
        connections with InvalidClientId. qmi-proxy auto-restarts on the
        next qmicli connection via the device's init system.
        """
        try:
            await self._ssh_run('killall socat qmicli qmi-proxy 2>/dev/null; true')
            await asyncio.sleep(3.0)
        except Exception as e:
            logger.debug('Failed to kill LTE processes (may not have been running): %s', e)

    async def ensure_lte_bridge(self) -> bool:
        """Ensure the socat bridge is running on the LTE device.

        On first call (or after a failed reconnect), kills any existing socat
        to clear stale QMI client registrations from a previous process, then
        starts a fresh bridge. On subsequent calls where the bridge is already
        running, returns immediately.
        """
        if not self._fresh_start and await self.check_lte_bridge():
            logger.debug('LTE bridge already running')
            return True

        await self._kill_lte_socat()

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
                self._fresh_start = False
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

    async def _kill_local_bridge(self) -> None:
        """Kill the local socat bridge process if running."""
        if self._local_socat_proc and self._local_socat_proc.returncode is None:
            self._local_socat_proc.terminate()
            try:
                await asyncio.wait_for(self._local_socat_proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                self._local_socat_proc.kill()
                await self._local_socat_proc.wait()
        self._local_socat_proc = None

    def _ensure_device_node(self) -> None:
        """Ensure the QMI device path exists locally.

        qmicli with --device-open-proxy still stats the device path before
        connecting to the qmi-proxy abstract socket. In bridge mode the device
        is remote, so there's no real device node. We create an empty file as
        a placeholder. Use a writable path like /tmp/cdc-wdm0 (not /dev/)
        since the service runs as a non-root user.
        """
        if not os.path.exists(self._device):
            try:
                os.makedirs(os.path.dirname(self._device), exist_ok=True)
                open(self._device, 'w').close()
                logger.info('Created QMI device placeholder at %s', self._device)
            except OSError as e:
                logger.warning('Could not create device placeholder at %s: %s', self._device, e)

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
            self._ensure_device_node()
            return True

        except Exception as e:
            logger.error('Failed to start local bridge: %s', e)
            self._local_socat_proc = None
            return False

    async def ensure_bridge(self) -> bool:
        """Ensure both LTE-side and local-side bridges are running.

        On first call, kills both sides to clear stale QMI client registrations
        from a previous process. On subsequent calls, only restarts bridges that
        are down.

        Returns True if both sides are operational.
        """
        if self._fresh_start:
            await self._kill_local_bridge()

        if not await self.ensure_lte_bridge():
            logger.error('LTE-side bridge is down')
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

    async def health_check(self) -> dict:
        """Verify bridge connectivity.

        Checks that both the LTE-side and local-side socat processes are running.

        Previously this also ran ``qmicli --wms-get-cbs-channels`` to verify CBS
        channel configuration. That was removed because QMI only allows one WMS
        client at a time - the ``qmicli --wms-monitor`` subprocess already holds
        it for the lifetime of the monitor, so a second qmicli instance always
        fails with ``InvalidClientId`` and never returns useful output.

        Returns a dict with status of each component.
        """
        return {
            'lte_bridge': await self.check_lte_bridge(),
            'local_bridge': await self.check_local_bridge(),
        }

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
