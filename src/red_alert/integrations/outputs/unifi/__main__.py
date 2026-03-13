"""Entry point: python -m red_alert.integrations.outputs.unifi"""

import argparse
import asyncio
import json
import logging
import sys

from red_alert.integrations.outputs.unifi.server import run_monitor


def main():
    parser = argparse.ArgumentParser(
        description='red-alert UniFi LED monitor - changes AP LED colors based on alert state',
    )
    parser.add_argument('--config', '-c', type=str, required=True, help='Path to JSON config file')
    args = parser.parse_args()

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
