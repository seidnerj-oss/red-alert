#!/usr/bin/env python3
"""Automate SSH setup on a UniFi LTE Backup Pro via the controller's WebRTC debug terminal.

Connects to the device through the UniFi Network Controller (same protocol as the
browser debug terminal), injects an SSH public key, and starts the dropbear SSH server.

Supports two connection modes:
- **Direct**: connect to the controller by IP/hostname (--host)
- **Cloud**: connect via Ubiquiti cloud WebRTC (--device-id)

Requirements:
    pip install pyunifiapi

Usage (direct):
    python scripts/setup-lte-pro-ssh.py \
        --host <controller-ip> \
        --username admin \
        --password <controller-password> \
        --mac <device-mac> \
        --pubkey ~/.ssh/id_ed25519.pub

Usage (cloud):
    python scripts/setup-lte-pro-ssh.py \
        --device-id <cloud-device-id> \
        --username <sso-email> \
        --password <sso-password> \
        --totp-secret <totp-secret> \
        --mac <device-mac> \
        --pubkey ~/.ssh/id_ed25519.pub

After running, you can SSH directly to the device:
    ssh -i ~/.ssh/id_ed25519 <user>@<device-ip>
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from pyunifiapi._types import CloudConfig, ControllerConfig, SSHConfig
from pyunifiapi.ssh.session import SSHSession

logger = logging.getLogger(__name__)


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

    Args:
        host: Controller hostname/IP for direct connection.
        device_id: Cloud device ID for cloud connection.
        username: Controller username or SSO email.
        password: Controller password or SSO password.
        port: Controller port (default: 443).
        site: Controller site (default: default).
        totp_secret: TOTP secret for 2FA (required for cloud).

    Returns:
        A ControllerConfig (direct) or CloudConfig (cloud).

    Raises:
        ValueError: If neither host nor device_id is provided, or if
            device_id is used without totp_secret.
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
        host=host,
        username=username,
        password=password,
        port=port,
        site=site,
        totp_secret=totp_secret,
    )


def read_pubkey(path: str | Path) -> str:
    """Read and validate an SSH public key file.

    Args:
        path: Path to the public key file.

    Returns:
        The public key string.

    Raises:
        FileNotFoundError: If the file doesn't exist.
        ValueError: If the file doesn't look like a public key.
    """
    pubkey_path = Path(path).expanduser()
    if not pubkey_path.exists():
        raise FileNotFoundError(f'Public key file not found: {pubkey_path}')

    pubkey = pubkey_path.read_text().strip()
    if not pubkey.startswith(('ssh-', 'ecdsa-', 'sk-')):
        raise ValueError(f'Does not look like a public key: {pubkey[:40]}...')

    return pubkey


async def setup_ssh(
    controller_config: ControllerConfig | CloudConfig,
    device_mac: str,
    pubkey: str,
) -> None:
    """Enable SSH on a UniFi LTE Backup Pro device.

    1. Connects to the device via the controller's WebRTC debug terminal
    2. Writes the public key to /etc/dropbear/authorized_keys
    3. Starts the dropbear SSH server

    Args:
        controller_config: UniFi controller connection details (local or cloud).
        device_mac: MAC address of the LTE Backup Pro device.
        pubkey: SSH public key string to authorize.
    """
    ssh_config = SSHConfig(mac=device_mac)

    async with SSHSession(controller=controller_config, ssh=ssh_config) as ssh:
        logger.info('Connected to device %s via WebRTC debug terminal', device_mac)

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

    logger.info('Done. You can now SSH to the device directly.')


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Enable SSH on a UniFi LTE Backup Pro via the controller WebRTC debug terminal.',
    )

    conn_group = parser.add_mutually_exclusive_group(required=True)
    conn_group.add_argument('--host', help='UniFi controller hostname or IP (direct connection)')
    conn_group.add_argument('--device-id', help='Cloud controller device ID (cloud connection via WebRTC)')

    parser.add_argument('--username', required=True, help='Controller username or SSO email')
    parser.add_argument('--password', required=True, help='Controller password or SSO password')
    parser.add_argument('--port', type=int, default=443, help='Controller port (default: 443)')
    parser.add_argument('--site', default='default', help='Controller site (default: default)')
    parser.add_argument('--mac', required=True, help='Device MAC address (e.g., aa:bb:cc:dd:ee:ff)')
    parser.add_argument('--totp-secret', help='TOTP secret (base32) for 2FA')
    parser.add_argument('--pubkey', required=True, help='Path to SSH public key file (e.g., ~/.ssh/id_ed25519.pub)')
    parser.add_argument('-v', '--verbose', action='store_true', help='Enable debug logging')

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    )

    try:
        pubkey = read_pubkey(args.pubkey)
    except (FileNotFoundError, ValueError) as e:
        print(f'Error: {e}', file=sys.stderr)
        sys.exit(1)

    try:
        controller_config = build_controller_config(
            host=args.host,
            device_id=args.device_id,
            username=args.username,
            password=args.password,
            port=args.port,
            site=args.site,
            totp_secret=args.totp_secret,
        )
    except ValueError as e:
        print(f'Error: {e}', file=sys.stderr)
        sys.exit(1)

    if args.device_id:
        print(f'Connecting via cloud to controller {args.device_id[:30]}...')
    else:
        print(f'Connecting directly to controller at {args.host}...')

    asyncio.run(setup_ssh(controller_config, args.mac, pubkey))


if __name__ == '__main__':
    main()
