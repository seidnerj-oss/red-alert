#!/usr/bin/env python3
"""Automate SSH setup on a UniFi LTE Backup Pro via the controller's WebRTC debug terminal.

Thin CLI wrapper around red_alert.integrations.inputs.cbs.lte_ssh.

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

from red_alert.integrations.inputs.cbs.lte_ssh import build_controller_config, enable_ssh, read_pubkey


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
        asyncio.run(enable_ssh(config, args.mac, pubkey))
    except Exception as e:
        print(f'Error: {e}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
