"""Entry point: python -m red_alert.integrations.outputs.homepod"""

import argparse
import asyncio
import json
import logging
import subprocess
import sys

from red_alert.integrations.outputs.homepod.audio_controller import DEFAULT_ATVREMOTE_CMD
from red_alert.integrations.outputs.homepod.server import run_monitor


def _run_atvremote(*args: str) -> None:
    """Run atvremote as a subprocess, passing through stdin/stdout/stderr."""
    cmd = DEFAULT_ATVREMOTE_CMD + list(args)
    sys.exit(subprocess.call(cmd))


def main():
    parser = argparse.ArgumentParser(
        description='red-alert HomePod monitor - plays audio on HomePod based on alert state',
    )
    parser.add_argument('--config', '-c', type=str, help='Path to JSON config file')
    parser.add_argument('--scan', action='store_true', help='Scan for HomePod/AirPlay devices on the network')
    parser.add_argument('--pair', type=str, metavar='IDENTIFIER', help='Pair with a device to get credentials')
    args = parser.parse_args()

    if args.scan:
        _run_atvremote('scan')
        return

    if args.pair:
        _run_atvremote('--id', args.pair, '--protocol', 'airplay', 'pair')
        return

    if not args.config:
        parser.print_help()
        sys.exit(1)

    try:
        with open(args.config) as f:
            config = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f'Error loading config file: {e}', file=sys.stderr)
        sys.exit(1)

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(name)s %(levelname)s %(message)s',
    )
    logging.getLogger('httpx').setLevel(logging.WARNING)

    asyncio.run(run_monitor(config))


if __name__ == '__main__':
    main()
