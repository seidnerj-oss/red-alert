"""Entry point: python -m red_alert.integrations.inputs.cbs"""

import argparse
import asyncio
import json
import logging
import os
import sys

from red_alert.integrations.inputs.cbs.server import run_monitor


def main():
    parser = argparse.ArgumentParser(
        description='red-alert CBS monitor - receives Cell Broadcast alerts via QMI modem',
    )
    parser.add_argument('--config', '-c', type=str, help='Path to JSON config file')
    args = parser.parse_args()

    if not args.config:
        parser.print_help()
        sys.exit(1)

    try:
        with open(args.config) as f:
            config = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f'Error loading config file: {e}', file=sys.stderr)
        sys.exit(1)

    # Default history_path to the data directory
    if not config.get('history_path'):
        config_dir = os.path.dirname(os.path.abspath(args.config))
        config['history_path'] = os.path.join(config_dir, 'data', 'cbs_history.json')

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(name)s %(levelname)s %(message)s',
    )

    asyncio.run(run_monitor(config))


if __name__ == '__main__':
    main()
