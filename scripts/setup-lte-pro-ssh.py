#!/usr/bin/env python3
"""Automate SSH setup on a UniFi LTE Backup Pro via the controller's WebRTC debug terminal.

Authorizes an SSH public key on the device (appending, never overwriting the existing
authorized_keys) and starts dropbear only when it is not already running, via
pyunifiapi's enable_ssh. The CBS monitor does this itself when the device refuses SSH
(after a reboot); run this script for the first setup, or when dropbear is running but
does not accept your key yet, which the monitor cannot repair because the connection
is not refused.

Requires pyunifiapi >= 0.2.0 (see the unifi extra in pyproject.toml). It is imported
lazily, so importing this script works without it.

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
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyunifiapi import CloudConfig, ControllerConfig


def read_pubkey(path: str | Path) -> str:
    """Read and validate an SSH public key file."""
    pubkey_path = Path(path).expanduser()
    if not pubkey_path.exists():
        raise FileNotFoundError(f'Public key file not found: {pubkey_path}')

    pubkey = pubkey_path.read_text().strip()
    if not pubkey.startswith(('ssh-', 'ecdsa-', 'sk-')):
        raise ValueError(f'Does not look like a public key: {pubkey[:40]}...')

    return pubkey


def build_controller_config(
    *,
    host: str | None,
    device_id: str | None,
    username: str,
    password: str,
    port: int,
    site: str,
    totp_secret: str | None,
) -> ControllerConfig | CloudConfig:
    """Build a UniFi controller config for a direct (host) or cloud (device_id) connection."""
    from pyunifiapi import CloudConfig, ControllerConfig

    if device_id:
        if not totp_secret:
            raise ValueError('totp_secret is required for cloud connections')
        return CloudConfig(username=username, password=password, device_id=device_id, totp_secret=totp_secret, site=site)
    if not host:
        raise ValueError('Either host (direct) or device_id (cloud) must be provided')
    return ControllerConfig(host=host, username=username, password=password, port=port, site=site, totp_secret=totp_secret)


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
        config = build_controller_config(
            host=args.host,
            device_id=args.device_id,
            username=args.username,
            password=args.password,
            port=args.port,
            site=args.site,
            totp_secret=args.totp_secret,
        )
        from pyunifiapi import enable_ssh

        result = asyncio.run(enable_ssh(config, args.mac, pubkey))
        print(f'SSH enabled (key_added={result.key_added}, dropbear_started={result.dropbear_started})')
    except Exception as e:
        print(f'Error: {e}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
