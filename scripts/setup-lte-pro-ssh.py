#!/usr/bin/env python3
"""Automate SSH setup on a UniFi LTE Backup Pro via the controller's WebRTC debug terminal.

Connects to the device through the UniFi Network Controller (same protocol as the
browser debug terminal), injects an SSH public key, and starts the dropbear SSH server.

Requirements:
    pip install py-unifiapi

Usage:
    python scripts/setup-lte-pro-ssh.py \
        --host <controller-ip> \
        --username admin \
        --password <controller-password> \
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

from pyunifiapi import ControllerConfig, SSHConfig, UnifiClient

logger = logging.getLogger(__name__)


async def setup_ssh(
    controller_config: ControllerConfig,
    device_mac: str,
    pubkey: str,
) -> None:
    """Enable SSH on a UniFi LTE Backup Pro device.

    1. Connects to the device via the controller's WebRTC debug terminal
    2. Writes the public key to /etc/dropbear/authorized_keys
    3. Starts the dropbear SSH server

    Args:
        controller_config: UniFi controller connection details.
        device_mac: MAC address of the LTE Backup Pro device.
        pubkey: SSH public key string to authorize.
    """
    ssh_config = SSHConfig(mac=device_mac)

    async with UnifiClient(controller_config) as client:
        async with client.ssh(ssh_config) as ssh:
            logger.info('Connected to device %s via WebRTC debug terminal', device_mac)

            # Write the public key
            # Use /etc/dropbear/ - NOT /.ssh/ (root fs is world-writable, dropbear rejects it)
            await ssh.execute(f'echo "{pubkey}" > /etc/dropbear/authorized_keys')
            await ssh.execute('chmod 600 /etc/dropbear/authorized_keys')
            logger.info('Public key written to /etc/dropbear/authorized_keys')

            # Start dropbear (-R generates host keys if missing)
            output = await ssh.execute('dropbear -R')
            logger.info('dropbear started: %s', output.strip())

            # Verify it's running
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
    parser.add_argument('--host', required=True, help='UniFi controller hostname or IP')
    parser.add_argument('--username', required=True, help='Controller username')
    parser.add_argument('--password', required=True, help='Controller password')
    parser.add_argument('--port', type=int, default=443, help='Controller port (default: 443)')
    parser.add_argument('--site', default='default', help='Controller site (default: default)')
    parser.add_argument('--mac', required=True, help='Device MAC address (e.g., aa:bb:cc:dd:ee:ff)')
    parser.add_argument('--totp-secret', help='TOTP secret (base32) for 2FA-enabled controllers')
    parser.add_argument('--pubkey', required=True, help='Path to SSH public key file (e.g., ~/.ssh/id_ed25519.pub)')
    parser.add_argument('-v', '--verbose', action='store_true', help='Enable debug logging')

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    )

    pubkey_path = Path(args.pubkey).expanduser()
    if not pubkey_path.exists():
        print(f'Error: public key file not found: {pubkey_path}', file=sys.stderr)
        sys.exit(1)

    pubkey = pubkey_path.read_text().strip()
    if not pubkey.startswith(('ssh-', 'ecdsa-', 'sk-')):
        print(f'Error: does not look like a public key: {pubkey[:40]}...', file=sys.stderr)
        sys.exit(1)

    controller_config = ControllerConfig(
        host=args.host,
        username=args.username,
        password=args.password,
        port=args.port,
        site=args.site,
        totp_secret=args.totp_secret,
    )

    asyncio.run(setup_ssh(controller_config, args.mac, pubkey))


if __name__ == '__main__':
    main()
