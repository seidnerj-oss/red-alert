#!/usr/bin/env python3
"""Discover UniFi cloud controllers and devices via the Site Manager API.

Requires an API key from https://unifi.ui.com (Settings > API Keys).

Usage:
    python scripts/discover_cloud_devices.py --api-key YOUR_API_KEY
    python scripts/discover_cloud_devices.py --api-key-file /path/to/key.txt
"""

import argparse
import asyncio

import httpx

API_BASE = 'https://api.ui.com'


async def main(api_key: str):
    headers = {
        'X-API-KEY': api_key,
        'Accept': 'application/json',
    }

    async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
        # List hosts (controllers/gateways)
        print('=== HOSTS (Controllers) ===')
        resp = await client.get(f'{API_BASE}/ea/hosts')
        if resp.status_code != 200:
            # Try v1 path
            resp = await client.get(f'{API_BASE}/v1/hosts')
        if resp.status_code != 200:
            print(f'  Error: {resp.status_code} {resp.text[:500]}')
        else:
            data = resp.json()
            hosts = data if isinstance(data, list) else data.get('data', data.get('hosts', [data]))
            for host in hosts:
                if isinstance(host, dict):
                    print(f'  ID: {host.get("id", host.get("deviceId", host.get("_id", "?")))}')
                    print(f'  Name: {host.get("name", host.get("hostname", "?"))}')
                    print(f'  Model: {host.get("model", host.get("hardwareShortname", "?"))}')
                    print(f'  MAC: {host.get("mac", "?")}')
                    print(f'  IP: {host.get("ip", host.get("ipAddress", host.get("reportedWan1Ip", "?")))}')
                    print(f'  Firmware: {host.get("firmwareVersion", host.get("version", "?"))}')
                    print('  ---')
            if not hosts:
                print('  (no hosts found)')
            # Dump raw for inspection
            print(f'\n  Raw keys (first host): {list(hosts[0].keys()) if hosts and isinstance(hosts[0], dict) else "N/A"}')

        print()

        # List devices (APs, switches, etc.)
        print('=== DEVICES ===')
        resp = await client.get(f'{API_BASE}/ea/devices')
        if resp.status_code != 200:
            resp = await client.get(f'{API_BASE}/v1/devices')
        if resp.status_code != 200:
            print(f'  Error: {resp.status_code} {resp.text[:500]}')
        else:
            data = resp.json()
            devices = data if isinstance(data, list) else data.get('data', data.get('devices', []))
            for dev in devices:
                if isinstance(dev, dict):
                    mac = dev.get('mac', '?')
                    name = dev.get('name', dev.get('hostname', '?'))
                    model = dev.get('model', dev.get('shortname', '?'))
                    dev_type = dev.get('type', '?')
                    host_id = dev.get('hostId', dev.get('host_id', '?'))
                    ip = dev.get('ip', '?')
                    print(f'  {mac} | {name} | model={model} | type={dev_type} | host={host_id} | ip={ip}')
            if not devices:
                print('  (no devices found)')
            if devices and isinstance(devices[0], dict):
                print(f'\n  Raw keys (first device): {list(devices[0].keys())}')

        # Also try /ea/sites
        print()
        print('=== SITES ===')
        resp = await client.get(f'{API_BASE}/ea/sites')
        if resp.status_code != 200:
            resp = await client.get(f'{API_BASE}/v1/sites')
        if resp.status_code != 200:
            print(f'  Error: {resp.status_code}')
        else:
            data = resp.json()
            sites = data if isinstance(data, list) else data.get('data', data.get('sites', []))
            for site in sites:
                if isinstance(site, dict):
                    print(f'  {site.get("name", "?")} | id={site.get("id", site.get("_id", "?"))} | host={site.get("hostId", "?")}')
            if not sites:
                print('  (no sites found)')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Discover UniFi cloud controllers and devices')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--api-key', help='UniFi Site Manager API key')
    group.add_argument('--api-key-file', help='Path to file containing the API key')
    args = parser.parse_args()

    if args.api_key_file:
        with open(args.api_key_file) as f:
            api_key = f.read().strip()
    else:
        api_key = args.api_key

    asyncio.run(main(api_key))
