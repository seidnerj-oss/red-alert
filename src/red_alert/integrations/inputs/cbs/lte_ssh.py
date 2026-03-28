"""Enable SSH on a UniFi LTE Backup Pro via the controller's WebRTC debug terminal.

Connects to the device through the UniFi Network Controller (same protocol as the
browser debug terminal), injects an SSH public key, and starts the dropbear SSH server.

dropbear does not persist across LTE device reboots, so this must be re-run after
each reboot. The CbsBridge calls this automatically when SSH connection is refused.

Requires pyunifiapi (already a dependency for the UniFi output integration).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger('red_alert.cbs.lte_ssh')


def build_controller_config(
    *,
    host: str | None = None,
    device_id: str | None = None,
    username: str,
    password: str,
    port: int = 443,
    site: str = 'default',
    totp_secret: str | None = None,
) -> Any:
    """Build a UniFi controller config for either direct or cloud connection.

    Returns a ControllerConfig (direct) or CloudConfig (cloud).
    """
    from pyunifiapi._types import CloudConfig, ControllerConfig

    if not host and not device_id:
        raise ValueError('Either host (direct) or device_id (cloud) must be provided')

    if device_id:
        if not totp_secret:
            raise ValueError('totp_secret is required for cloud connections')
        return CloudConfig(
            username=username,
            password=password,
            device_id=device_id,
            totp_secret=totp_secret,
            site=site,
        )

    return ControllerConfig(
        host=host or '',
        username=username,
        password=password,
        port=port,
        site=site,
        totp_secret=totp_secret,
    )


def read_pubkey(path: str | Path) -> str:
    """Read and validate an SSH public key file."""
    pubkey_path = Path(path).expanduser()
    if not pubkey_path.exists():
        raise FileNotFoundError(f'Public key file not found: {pubkey_path}')

    pubkey = pubkey_path.read_text().strip()
    if not pubkey.startswith(('ssh-', 'ecdsa-', 'sk-')):
        raise ValueError(f'Does not look like a public key: {pubkey[:40]}...')

    return pubkey


async def enable_ssh(
    controller_config: Any,
    device_mac: str,
    pubkey: str,
) -> None:
    """Enable SSH on a UniFi LTE Backup Pro device.

    1. Connects to the device via the controller's WebRTC debug terminal
    2. Writes the public key to /etc/dropbear/authorized_keys
    3. Starts the dropbear SSH server

    Args:
        controller_config: UniFi controller connection details (ControllerConfig or CloudConfig).
        device_mac: MAC address of the LTE Backup Pro device.
        pubkey: SSH public key string to authorize.
    """
    from pyunifiapi._types import SSHConfig
    from pyunifiapi.ssh.session import SSHSession

    ssh_config = SSHConfig(mac=device_mac)

    async with SSHSession(controller=controller_config, ssh=ssh_config) as ssh:
        logger.info('Connected to device %s via WebRTC debug terminal', device_mac)

        # Wait for the login banner and initial shell prompt
        banner = await ssh.read_until('# ', timeout=60.0)
        logger.debug('Banner received (%d bytes)', len(banner))

        await ssh.execute(f'echo "{pubkey}" > /etc/dropbear/authorized_keys')
        await ssh.execute('chmod 600 /etc/dropbear/authorized_keys')
        logger.info('Public key written to /etc/dropbear/authorized_keys')

        output = await ssh.execute('dropbear -R')
        logger.info('dropbear started: %s', output.strip())

        output = await ssh.execute('ps | grep dropbear')
        if 'dropbear' in output:
            logger.info('dropbear is running - SSH access enabled')
        else:
            logger.warning('dropbear may not have started correctly')
