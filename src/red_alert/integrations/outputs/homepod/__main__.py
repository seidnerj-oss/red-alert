"""Entry point: python -m red_alert.integrations.outputs.homepod"""

# pyright: reportMissingImports=false

import argparse
import asyncio
import json
import logging
import sys

from red_alert.integrations.outputs.homepod.server import run_monitor


async def scan_devices():
    """Discover HomePod and AirPlay devices on the network."""
    import pyatv

    print('Scanning for AirPlay devices...')
    devices = await pyatv.scan(timeout=5)

    if not devices:
        print('No devices found.')
        return

    print(f'Found {len(devices)} device(s):')
    print()
    for dev in devices:
        print(f'  Name:       {dev.name}')
        print(f'  Identifier: {dev.identifier}')
        print(f'  Address:    {dev.address}')
        services = [str(s.protocol) for s in dev.services]
        print(f'  Protocols:  {", ".join(services)}')
        print()


async def pair_device(identifier: str):
    """Pair with a specific device to obtain credentials."""
    import pyatv

    print(f'Scanning for device {identifier}...')
    devices = await pyatv.scan(identifier=identifier, timeout=5)

    if not devices:
        print(f'Device not found: {identifier}', file=sys.stderr)
        sys.exit(1)

    config = devices[0]
    print(f'Found: {config.name}')
    print()

    credentials = {}
    for service in config.services:
        proto = service.protocol
        print(f'Pairing protocol: {proto}...')

        pairing = await pyatv.pair(config, protocol=proto)
        await pairing.begin()

        if pairing.device_provides_pin:
            pin = input('Enter PIN displayed on device: ')
            pairing.pin(pin)
        else:
            print(f'Enter this PIN on the device: {pairing.pin_code}')
            input('Press Enter when done...')

        await pairing.finish()

        if pairing.has_paired:
            creds = pairing.service.credentials
            proto_name = str(proto).split('.')[-1].lower()
            credentials[proto_name] = creds
            print(f'  Paired! Credentials: {creds[:20]}...')
        else:
            print(f'  Pairing failed for {proto}')

        await pairing.close()
        print()

    if credentials:
        print('Add this to your config.json device entry:')
        print(f'  "credentials": {json.dumps(credentials, indent=2)}')
    else:
        print('No protocols were paired successfully.', file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description='red-alert HomePod monitor - plays audio on HomePod based on alert state',
    )
    parser.add_argument('--config', '-c', type=str, help='Path to JSON config file')
    parser.add_argument('--scan', action='store_true', help='Scan for HomePod/AirPlay devices on the network')
    parser.add_argument('--pair', type=str, metavar='IDENTIFIER', help='Pair with a device to get credentials')
    args = parser.parse_args()

    if args.scan:
        asyncio.run(scan_devices())
        return

    if args.pair:
        asyncio.run(pair_device(args.pair))
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
