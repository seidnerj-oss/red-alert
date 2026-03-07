# Installation

red-alert supports multiple platforms. Choose the integration guide for your setup:

## Integrations

- **[Home Assistant (AppDaemon)](integrations/homeassistant.md)** - Full-featured integration with binary sensors, history, GeoJSON maps, MQTT, and events
- **[Homebridge (HomeKit)](integrations/homebridge.md)** - Lightweight HTTP server exposing alert state as HomeKit contact sensors
- **[UniFi LED](integrations/unifi.md)** - Control RGB LEDs on UniFi access points based on alert state (white/yellow/red)
- **[Philips Hue](integrations/hue.md)** - Control Philips Hue lights/groups based on alert state via the Hue Bridge REST API

## Core Library

The core library can also be used standalone in any Python project:

```bash
pip install httpx
```

```python
import asyncio
import httpx
from red_alert.core.api_client import HomeFrontCommandApiClient

async def main():
    async with httpx.AsyncClient(headers={
        'Referer': 'https://www.oref.org.il/',
        'X-Requested-With': 'XMLHttpRequest',
    }) as client:
        api = HomeFrontCommandApiClient(client, {
            'live': 'https://www.oref.org.il/WarningMessages/alert/alerts.json',
        }, print)
        alerts = await api.get_live_alerts()
        print(alerts)

asyncio.run(main())
```

## Other Resources

- [City Names Reference](CITIES.md)
