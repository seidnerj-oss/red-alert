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

In ``self`` bridge management both relays are supervised in-process by the
lte-pro-access library (github.com/seidnerj-oss/lte-pro-access): it keeps SSH enabled
on the device (re-enabling it through the UniFi controller after a reboot), uploads the
MIPS socat binary when the device copy is missing or differs, and starts whichever relay
is down. This module adds red-alert's own policy on top: a fresh start that clears stale
WMS client registrations on the device.

socat MIPS binary (``data/socat-mips``, placed there by ``scripts/setup-cbs.py``):
    Package: socat_1.7.3.1-1_mips_24kc.ipk
    URL: https://downloads.openwrt.org/releases/17.01.6/packages/mips_24kc/packages/socat_1.7.3.1-1_mips_24kc.ipk
    Extracted binary: usr/bin/socat (164KB, ELF 32-bit MSB, MIPS, dynamically linked, musl)
    If the URL becomes unavailable, any socat 1.7.x build for mips_24kc musl should work.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from collections.abc import Awaitable
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from lte_pro_access import Supervisor

logger = logging.getLogger('red_alert.cbs.bridge')

LOCAL_QMI_PROXY_SOCKET = '\0qmi-proxy'
PROBE_TIMEOUT = 3.0

# The MIPS socat that lte-pro-access uploads to the device as /tmp/socat. The path walks up
# from this module to the repo root, so it assumes a source checkout (the documented git
# clone deployment); in a wheel install it would point outside the package.
SOCAT_DEVICE_BINARY = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', '..', 'data', 'socat-mips'))

# Killing qmi-proxy is necessary because it holds QMI client registrations in memory:
# even after socat and qmicli are killed it keeps the stale WMS client slot and rejects
# new connections with InvalidClientId. qmi-proxy restarts on the next qmicli connection
# via the device's init system. Killing socat also drops the device relay, which the
# supervisor tick that follows starts again. The sleep gives the killed processes time to
# exit before that tick probes the device: a socat still exiting would read as a listening
# relay, and the tick would leave it down.
FRESH_START_DEVICE_COMMAND = 'killall socat qmicli qmi-proxy 2>/dev/null; sleep 3; true'

# CBS config keys that only ``self`` bridge management needs, all required there.
SELF_MANAGED_REQUIRED_KEYS = (
    'lte_device_mac',
    'lte_device_ssh_key_path',
    'ssh_username',
    'unifi',
    'monitoring_host_address',
    'bridge_state_dir',
)

BridgeManagement = Literal['self', 'external']
BRIDGE_MANAGEMENT_MODES = ('self', 'external')

# Supervisor tick actions that mean a relay was (re)started, i.e. the path qmicli is attached
# to was rebuilt under it. Other actions (uploaded-socat, enabled-ssh) leave a listening relay
# in place, so they are not a repair as far as qmicli is concerned.
REBUILD_ACTIONS = ('started-qmi-relay', 'started-pi-relay')

DOWN = {'lte_bridge': False, 'local_bridge': False}

# Status key set by a self-managed health check that found the relay path rebuilt under qmicli.
# Unlike a failed probe this is definitive, not a blip, so the caller rebuilds at once.
REBUILD_NOW_KEY = 'rebuild_now'


def build_supervisor(cfg: dict, qmi_port: int, check_interval_seconds: int) -> Supervisor:
    """Build the lte-pro-access supervisor for ``self`` bridge management from the CBS config.

    Raises ValueError when a required key is missing or the removed
    ``socat_remote_binary`` key is present, FileNotFoundError when the local
    socat or the bundled MIPS socat is missing, and ModuleNotFoundError when lte-pro-access
    or pyunifiapi is not installed - all at startup rather than on the first LTE reboot.
    """
    if 'socat_remote_binary' in cfg:
        raise ValueError(f'socat_remote_binary is no longer used: the MIPS socat is read from {SOCAT_DEVICE_BINARY}; remove the key')
    missing = [key for key in SELF_MANAGED_REQUIRED_KEYS if not cfg.get(key)]
    if missing:
        raise ValueError(f'bridge_management "self" requires these CBS config keys: {", ".join(missing)}')

    socat_pi_binary = shutil.which('socat')
    if socat_pi_binary is None:
        raise FileNotFoundError('socat not found in PATH; the local relay needs it. Install it (e.g., apt install socat or brew install socat)')
    if not os.path.isfile(SOCAT_DEVICE_BINARY):
        raise FileNotFoundError(f'MIPS socat binary not found at {SOCAT_DEVICE_BINARY}; run scripts/setup-cbs.py --download-socat-only')

    try:
        from lte_pro_access import Config, create_supervisor
    except ModuleNotFoundError as e:
        raise ModuleNotFoundError(
            'bridge_management "self" needs lte-pro-access, which is not installed (see the unifi extra in pyproject.toml)',
            name='lte_pro_access',
        ) from e

    config = Config(
        lte_host=cfg['lte_host'],
        lte_mac=cfg['lte_device_mac'],
        pi_address=cfg['monitoring_host_address'],
        ssh_user=cfg['ssh_username'],
        ssh_key_path=Path(cfg['lte_device_ssh_key_path']).expanduser(),
        state_dir=Path(cfg['bridge_state_dir']).expanduser(),
        socat_device_binary=Path(SOCAT_DEVICE_BINARY),
        socat_pi_binary=Path(socat_pi_binary),
        qmi_port=qmi_port,
        at_relay=None,
        check_interval_seconds=check_interval_seconds,
        unifi=cfg['unifi'],
    )
    return create_supervisor(config)


async def _bounded[T](awaitable: Awaitable[T]) -> T:
    """Await a supervisor call, bounded by the library's own tick timeout."""
    from lte_pro_access import TICK_TIMEOUT_SECONDS

    return await asyncio.wait_for(awaitable, TICK_TIMEOUT_SECONDS)


class CbsBridge:
    """Manages the socat QMI proxy bridge between an LTE device and the local machine.

    ``management`` selects who owns the bridge:

    - ``'self'``: ``supervisor`` (an lte-pro-access Supervisor, see ``build_supervisor``)
      runs both relays in-process; this class adds the fresh-start policy.
    - ``'external'``: the bridge is managed by another process and ``supervisor`` must be
      None. This class never opens SSH, deploys, or kills anything; it only probes the
      local abstract ``qmi-proxy`` socket and the LTE device's bridge port over TCP.
    """

    def __init__(
        self,
        lte_host: str,
        bridge_port: int,
        device: str,
        management: BridgeManagement,
        supervisor: Supervisor | None,
    ):
        if management not in BRIDGE_MANAGEMENT_MODES:
            raise ValueError(f'management must be one of {BRIDGE_MANAGEMENT_MODES}, got {management!r}')
        if (management == 'self') != (supervisor is not None):
            raise ValueError('a supervisor is required with management "self" and not allowed with management "external"')
        self._management: BridgeManagement = management
        self._lte_host = lte_host
        self._bridge_port = bridge_port
        self._device = device
        self._supervisor = supervisor
        self._fresh_start = True
        self._fresh_start_requests = 0

    @property
    def lte_host(self) -> str:
        return self._lte_host

    @property
    def bridge_port(self) -> int:
        return self._bridge_port

    @property
    def device(self) -> str:
        return self._device

    @property
    def management(self) -> BridgeManagement:
        return self._management

    def request_fresh_start(self) -> None:
        """Make the next ``ensure_bridge()`` clear the device side and rebuild, as on startup.

        Used when the bridge is found broken while qmicli is still attached to it: the
        rebuild kills socat, qmicli and qmi-proxy on the LTE device (clearing any WMS
        client registration the old monitor left behind), then runs a supervisor tick,
        which re-enables SSH, uploads socat and starts the relays as needed.

        In external bridge mode this only restarts qmicli; the bridge itself is not touched.
        """
        self._fresh_start = True
        self._fresh_start_requests += 1

    def _require_supervisor(self) -> Supervisor:
        if self._supervisor is None:
            raise RuntimeError('no supervisor: the bridge is externally managed')
        return self._supervisor

    async def _probe_tcp(self, host: str, port: int) -> bool:
        """Return whether a TCP connection to ``host:port`` is accepted."""
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=PROBE_TIMEOUT)
        except (OSError, asyncio.TimeoutError) as e:
            logger.debug('TCP probe of %s:%d failed: %s', host, port, e)
            return False
        await self._close_writer(writer)
        return True

    async def _probe_local_socket(self) -> bool:
        """Return whether the local abstract ``qmi-proxy`` socket accepts a connection."""
        try:
            _, writer = await asyncio.wait_for(asyncio.open_unix_connection(LOCAL_QMI_PROXY_SOCKET), timeout=PROBE_TIMEOUT)
        except (OSError, asyncio.TimeoutError) as e:
            logger.debug('Local qmi-proxy socket probe failed: %s', e)
            return False
        await self._close_writer(writer)
        return True

    @staticmethod
    async def _close_writer(writer: asyncio.StreamWriter) -> None:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass

    async def ensure_bridge(self) -> bool:
        """Ensure both sides of the bridge are up.

        In ``self`` management, on the first call (or after ``request_fresh_start``) kills
        socat, qmicli and qmi-proxy on the device first, then runs a supervisor tick, which
        starts whatever is down. Returns True when the tick left every relay up. Errors
        (SSH refused even after re-enabling, a changed host key, a timeout) are logged and
        return False, and a pending fresh start stays pending until a call succeeds.

        In ``external`` management nothing is started, killed or deployed: the bridge
        is managed by another process, so this only reports whether the local abstract
        ``qmi-proxy`` socket accepts a connection.
        """
        if self._management == 'external':
            if await self._probe_local_socket():
                self._fresh_start = False
                return True
            logger.error('External bridge is down: nothing accepts connections on the local qmi-proxy socket')
            return False

        supervisor = self._require_supervisor()
        # A request made while this call awaits the supervisor (a health-check tick that found
        # a rebuilt relay path) must survive it, so success clears only what this call saw.
        requests_seen = self._fresh_start_requests
        try:
            if self._fresh_start:
                logger.info('Fresh start: killing socat, qmicli and qmi-proxy on the LTE device to clear stale WMS client registrations')
                await _bounded(supervisor.run_on_device(FRESH_START_DEVICE_COMMAND))
            await _bounded(supervisor.tick())
        except Exception as e:
            logger.error('Failed to bring up the bridge: %s: %s', type(e).__name__, e)
            return False

        if not supervisor.relays_up:
            logger.error('Bridge is down: not every relay is up after the supervisor tick')
            return False
        if self._fresh_start_requests == requests_seen:
            self._fresh_start = False
        return True

    async def configure_cbs(self, qmicli_path: str, channels: str = '919,4370-4383') -> bool:
        """Configure CBS channels on the modem via qmicli through the bridge.

        Runs the sequence: set-cbs-channels, set-broadcast-activation. Event reporting is
        enabled by --wms-monitor itself.
        """
        commands = [
            (f'--wms-set-cbs-channels={channels}', f'Setting CBS channels: {channels}'),
            ('--wms-set-broadcast-activation', 'Activating CBS reception'),
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

        Acting on a failed check is the caller's decision (see ``request_fresh_start``);
        in ``external`` management this method only reports.

        In ``self`` management it runs a supervisor tick, so the answer is current. A tick
        that had to change anything (re-enable SSH, upload socat, start a relay) means the
        path qmicli is attached to broke since the last rebuild, even though the tick has
        repaired it: qmicli does not notice, and after a device reboot its WMS registration
        is gone. Only a started relay counts (``REBUILD_ACTIONS``); an upload of socat or a
        re-enabled SSH alone leaves a listening relay in place. Such a tick reports both sides
        down with ``rebuild_now`` set, which tells the caller to rebuild without waiting for
        a second failed check, and marks the bridge for a fresh start; the bridge keeps
        reporting down without ticking until ``ensure_bridge`` rebuilds it. A tick that
        raises also reports down.

        Previously this also ran ``qmicli --wms-get-cbs-channels`` to verify CBS
        channel configuration. That was removed because QMI only allows one WMS
        client at a time - the ``qmicli --wms-monitor`` subprocess already holds
        it for the lifetime of the monitor, so a second qmicli instance always
        fails with ``InvalidClientId`` and never returns useful output.

        In ``external`` management the LTE side is a TCP connect to
        ``lte_host:bridge_port`` and the local side is a connect to the local
        abstract ``qmi-proxy`` socket; no SSH is opened.

        Returns a dict with status of each component.
        """
        if self._management == 'external':
            return {
                'lte_bridge': await self._probe_tcp(self._lte_host, self._bridge_port),
                'local_bridge': await self._probe_local_socket(),
            }

        if self._fresh_start:
            return dict(DOWN)

        supervisor = self._require_supervisor()
        try:
            result = await _bounded(supervisor.tick())
        except Exception as e:
            logger.error('Supervisor tick failed during the health check: %s: %s', type(e).__name__, e)
            return dict(DOWN)

        rebuilt = [action for action in result.actions if action in REBUILD_ACTIONS]
        if rebuilt:
            logger.warning(
                'The bridge relay path was rebuilt under the running qmicli (%s); reporting it down until it is rebuilt',
                ', '.join(rebuilt),
            )
            self.request_fresh_start()
            return {**DOWN, REBUILD_NOW_KEY: True}

        up = supervisor.relays_up
        return {'lte_bridge': up, 'local_bridge': up}

    async def close(self) -> None:
        """Stop the local relay in ``self`` management; a no-op in ``external``."""
        if self._supervisor is not None:
            logger.info('Stopping the local bridge relay')
            self._supervisor.close()
