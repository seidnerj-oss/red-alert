# Installation

RedAlert supports multiple platforms. Choose the integration guide for your setup:

## Integrations

- **[Home Assistant (AppDaemon)](integrations/homeassistant.md)** - Full-featured integration with binary sensors, history, GeoJSON maps, MQTT, and events
- **[Homebridge (HomeKit)](integrations/homebridge.md)** - Lightweight HTTP server exposing alert state as HomeKit contact sensors
- **[UniFi LED](integrations/unifi.md)** - Control RGB LEDs on UniFi U6/U7 access points based on alert state (white/yellow/red)

## Core Library

The core library can also be used standalone in any Python project:

```bash
pip install aiohttp
```

```python
import asyncio
import aiohttp
from red_alert.core.api_client import HomeFrontCommandApiClient

async def main():
    async with aiohttp.ClientSession(headers={
        'Referer': 'https://www.oref.org.il/',
        'X-Requested-With': 'XMLHttpRequest',
    }) as session:
        client = HomeFrontCommandApiClient(session, {
            'live': 'https://www.oref.org.il/WarningMessages/alert/alerts.json',
        }, print)
        alerts = await client.get_live_alerts()
        print(alerts)

asyncio.run(main())
```

## Other Resources

- [City Names Reference](CITIES.md)
- [English Documentation](ENGLISH.md)
- [Hebrew Documentation](HEBREW.md)
