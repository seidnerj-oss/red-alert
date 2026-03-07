"""Entry point: python -m red_alert.integrations.homebridge"""

import argparse
import json
import logging
import sys

from aiohttp import web

from red_alert.integrations.homebridge.server import create_app


def main():
    parser = argparse.ArgumentParser(
        description='red-alert Homebridge HTTP server - exposes Home Front Command alert state for Homebridge plugins',
    )
    parser.add_argument('--config', '-c', type=str, help='Path to JSON config file')
    parser.add_argument('--port', '-p', type=int, default=None, help='Server port (default: 8512)')
    parser.add_argument('--host', type=str, default=None, help='Server host (default: 0.0.0.0)')
    parser.add_argument('--interval', '-i', type=float, default=None, help='Polling interval in seconds (default: 1)')
    args = parser.parse_args()

    config = {}
    if args.config:
        try:
            with open(args.config) as f:
                config = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f'Error loading config file: {e}', file=sys.stderr)
            sys.exit(1)

    # CLI args override config file
    if args.port is not None:
        config['port'] = args.port
    if args.host is not None:
        config['host'] = args.host
    if args.interval is not None:
        config['interval'] = args.interval

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(name)s %(levelname)s %(message)s',
    )

    app = create_app(config)
    web.run_app(app, host=config.get('host', '0.0.0.0'), port=config.get('port', 8512), print=None)


if __name__ == '__main__':
    main()
