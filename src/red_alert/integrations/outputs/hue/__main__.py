"""Entry point: python -m red_alert.integrations.outputs.hue"""

import argparse
import asyncio
import json
import logging
import sys

import httpx

from red_alert.integrations.outputs.hue.server import run_monitor


async def register_bridge(bridge_ip: str):
    """Register with the Hue Bridge and print the API key."""
    url = f'http://{bridge_ip}/api'
    payload = {'devicetype': 'red_alert#instance'}

    print(f'Registering with Hue Bridge at {bridge_ip}...')
    print('Make sure you have pressed the link button on the bridge.')
    print()

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload)
        data = resp.json()

    if isinstance(data, list) and data:
        entry = data[0]
        if 'success' in entry:
            api_key = entry['success']['username']
            print('Registration successful!')
            print(f'API key: {api_key}')
            print()
            print('Add this to your config.json:')
            print(f'  "api_key": "{api_key}"')
            return
        if 'error' in entry:
            print(f'Error: {entry["error"]["description"]}', file=sys.stderr)
            if entry['error'].get('type') == 101:
                print('Press the link button on the bridge and try again.', file=sys.stderr)
            sys.exit(1)

    print(f'Unexpected response: {data}', file=sys.stderr)
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description='red-alert Hue monitor - changes light colors based on alert state',
    )
    parser.add_argument('--config', '-c', type=str, help='Path to JSON config file')
    parser.add_argument('--register', type=str, metavar='BRIDGE_IP', help='Register with Hue Bridge to get an API key')
    args = parser.parse_args()

    if args.register:
        asyncio.run(register_bridge(args.register))
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

    asyncio.run(run_monitor(config))


if __name__ == '__main__':
    main()
