"""Enable SSH on a UniFi LTE Backup Pro via the controller's WebRTC debug terminal.

Connects to the device through the UniFi Network Controller (same protocol as the
browser debug terminal), authorizes an SSH public key and makes sure the dropbear SSH
server is running. The device-side work lives in pyunifiapi's ``enable_ssh``: it
appends the key (never overwriting the existing ``authorized_keys``), starts dropbear
only when it is not already running, and verifies it.

dropbear does not persist across LTE device reboots, so this must be re-run after
each reboot. The CbsBridge calls this automatically when SSH connection is refused.

Requires pyunifiapi >= 0.2.0 (see the unifi extra in pyproject.toml).
"""

from __future__ import annotations

import logging
from pathlib import Path

from pyunifiapi import CloudConfig, ControllerConfig, SSHEnableResult
from pyunifiapi import enable_ssh as pyunifiapi_enable_ssh

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
) -> ControllerConfig | CloudConfig:
    """Build a UniFi controller config for either direct or cloud connection.

    Returns a ControllerConfig (direct) or CloudConfig (cloud).
    """
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
    controller_config: ControllerConfig | CloudConfig,
    device_mac: str,
    pubkey: str,
) -> SSHEnableResult:
    """Enable SSH on a UniFi LTE Backup Pro device.

    Delegates to pyunifiapi's ``enable_ssh`` and logs what it changed.

    Args:
        controller_config: UniFi controller connection details (ControllerConfig or CloudConfig).
        device_mac: MAC address of the LTE Backup Pro device.
        pubkey: SSH public key string to authorize.

    Returns:
        Which of the two steps changed something on the device.

    Raises:
        ValueError: ``pubkey`` is not a single OpenSSH public key line.
        pyunifiapi.SSHSessionError: dropbear is not running after being started.
    """
    result = await pyunifiapi_enable_ssh(controller_config, device_mac, pubkey)
    logger.info('SSH enabled on %s: key_added=%s dropbear_started=%s', device_mac, result.key_added, result.dropbear_started)
    return result
