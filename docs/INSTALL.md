# Installation

red-alert supports multiple platforms. Choose the integration guide for your setup:

## Integrations

- **[Home Assistant (AppDaemon)](integrations/outputs/homeassistant.md)** - Full-featured integration with binary sensors, history, GeoJSON maps, MQTT, and events
- **[Homebridge (HomeKit)](integrations/outputs/homebridge.md)** - Lightweight HTTP server exposing alert state as HomeKit contact sensors
- **[UniFi LED](integrations/outputs/unifi.md)** - Control RGB LEDs on UniFi access points based on alert state (white/yellow/red)
- **[Philips Hue](integrations/outputs/hue.md)** - Control Philips Hue lights/groups based on alert state via the Hue Bridge REST API
- **[Telegram](integrations/outputs/telegram.md)** - Real-time alert notifications via Telegram Bot API
- **[HomePod](integrations/outputs/homepod.md)** - Play audio on Apple HomePod devices via AirPlay on alert state changes

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
