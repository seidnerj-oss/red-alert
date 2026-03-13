# UniFi LED Integration

red-alert can control the RGB LEDs on UniFi access points via the UniFi Network controller REST API. LEDs change based on alert state:

- **Routine** - white (configurable color, brightness, on/off)
- **Pre-alert** - yellow (imminent warning, category 14)
- **Alert** - red (active alert - missiles, hostile aircraft, earthquakes, etc.)

Each state supports independent configuration of color, brightness, on/off, and blink (locate mode).

## How It Works

1. The red-alert monitor polls the Home Front Command API every second
2. It classifies the response into one of three states: ROUTINE, PRE_ALERT, or ALERT
3. It calls the UniFi Network controller REST API to update LED color, brightness, and on/off state on each configured device

## Prerequisites

- UniFi Network controller (UDM, UDR, Cloud Key, or self-hosted)
- A controller account - local accounts recommended, cloud/SSO accounts with TOTP 2FA supported via `totp_secret`
- MAC addresses of the APs to control
- Python 3.14+

## Setup

### 1. Install red-alert with UniFi Support

```bash
git clone https://github.com/seidnerj/red-alert.git
cd red-alert
pip install ".[unifi]"
```

### 2. Controller Account

A **local controller account** is recommended for simplicity. If your account uses TOTP-based 2FA, add `totp_secret` to your config (see below).

To create a local account:

1. Open UniFi Network controller
2. Go to **Settings > Admins & Users > Admins**
3. Add a new admin with **Local Access Only**
4. Give it a role with device management permissions

### 3. Find Your Device MAC Addresses

Device MAC addresses can be found in the UniFi controller:

1. Navigate to **Devices**
2. Click on an AP
3. The MAC address is shown in the device properties

### 4. Create a Config File

**`config.json`:**
```json
{
    "host": "192.168.1.1",
    "username": "redalert",
    "password": "your-password",
    "port": 443,
    "site": "default",
    "device_macs": [
        "aa:bb:cc:dd:ee:ff",
        "11:22:33:44:55:66"
    ],
    "interval": 1,
    "areas_of_interest": []
}
```

### 5. Start the Monitor

```bash
python -m red_alert.integrations.outputs.unifi --config config.json
```

## Configuration Reference

| Parameter | Description | Default |
|-----------|-------------|---------|
| `host` | Hostname or IP of the UniFi controller | Required |
| `username` | Local controller account username | Required |
| `password` | Controller account password | Required |
| `port` | Controller port | `443` |
| `site` | UniFi site name | `default` |
| `device_macs` | List of AP MAC addresses to control | Required |
| `interval` | API polling interval in seconds | `1` |
| `areas_of_interest` | Cities/areas to filter alerts for (empty = all of Israel) | `[]` |
| `totp_secret` | TOTP secret (base32) for 2FA - see [2FA Support](#2fa-support) | `null` |
| `backend` | Controller library: `"aiounifi"` or `"pyunifiapi"` - see [Backend](#backend) | `"aiounifi"` |
| `led_states` | Per-state LED configuration (see below) | See defaults |

## LED State Configuration

Each alert state can be individually configured with `on`, `color`, `brightness`, and `blink`:

```json
{
    "led_states": {
        "alert": {
            "on": true,
            "color": "red",
            "brightness": 100,
            "blink": true
        },
        "pre_alert": {
            "on": true,
            "color": "yellow",
            "brightness": 100,
            "blink": false
        },
        "routine": {
            "on": true,
            "color": "white",
            "brightness": 50,
            "blink": false
        }
    }
}
```

| Property | Description | Default |
|----------|-------------|---------|
| `on` | Whether the LED is on (`true`) or off (`false`) | `true` |
| `color` | LED color - named color, hex string, or `[R, G, B]` array | Varies per state |
| `brightness` | Brightness percentage (0-100) | `100` |
| `blink` | Whether the LED should blink (uses controller locate mode) | `false` |

**Named colors:** `red`, `green`, `blue`, `yellow`, `white`, `warm`

**Color formats:**
- Named: `"red"`, `"white"`, `"warm"`
- Hex: `"#FF0000"`, `"#821E1E"`
- RGB array: `[255, 128, 0]`

**Default states (when `led_states` is not specified):**

| State | On | Color | Brightness | Blink |
|-------|----|-------|------------|-------|
| alert | true | red | 100 | false |
| pre_alert | true | yellow | 100 | false |
| routine | true | white | 100 | false |

## Examples

### LED off during routine, blinking red on alert

```json
{
    "led_states": {
        "routine": {"on": false},
        "pre_alert": {"on": true, "color": "yellow", "brightness": 75},
        "alert": {"on": true, "color": "red", "brightness": 100, "blink": true}
    }
}
```

### Dim white during routine, bright colors on alert

```json
{
    "led_states": {
        "routine": {"color": "warm", "brightness": 20},
        "pre_alert": {"color": "#FFA500", "brightness": 80},
        "alert": {"color": "red", "brightness": 100}
    }
}
```

## Areas of Interest

By default, the LEDs react to alerts anywhere in Israel. To only react to alerts in specific areas:

```json
{
    "areas_of_interest": [
        "tel aviv - city center",
        "haifa - city center",
        "kfar saba"
    ]
}
```

## 2FA Support

If your controller account uses TOTP-based two-factor authentication, you can provide the TOTP secret so that red-alert generates codes automatically on each login.

### Finding Your TOTP Secret

The TOTP secret is the base32 string shown when you first set up 2FA (often displayed as a QR code). It looks like `JBSWY3DPEHPK3PXP`. If you've already set up 2FA and don't have the secret, you'll need to disable and re-enable 2FA to get it.

### Config Example

```json
{
    "host": "192.168.1.1",
    "username": "redalert",
    "password": "your-password",
    "totp_secret": "JBSWY3DPEHPK3PXP",
    "device_macs": ["aa:bb:cc:dd:ee:ff"]
}
```

When `totp_secret` is set, a fresh TOTP code is generated and included in each login request as `ubic_2fa_token`. This uses the same mechanism as entering the code manually in the UniFi web UI.

**Note:** If your account does not use 2FA, omit `totp_secret` entirely.

## Running as a Service

**systemd example (`/etc/systemd/system/redalert-unifi.service`):**
```ini
[Unit]
Description=red-alert UniFi LED Monitor
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/path/to/red-alert
ExecStart=/usr/bin/python3 -m red_alert.integrations.outputs.unifi --config /path/to/config.json
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable redalert-unifi
sudo systemctl start redalert-unifi
```

## Backend

The integration supports two controller libraries:

| Backend | Library | 2FA | HTTP Client | Notes |
|---------|---------|-----|-------------|-------|
| `aiounifi` (default) | [aiounifi](https://github.com/Kane610/aiounifi) | Monkey-patch | aiohttp | Same library used by Home Assistant's UniFi integration |
| `pyunifiapi` | [py-unifiapi](https://github.com/seidnerj/py-unifiapi) | Native | httpx | Also supports WebRTC SSH, cloud API |

To switch backends, add `"backend": "pyunifiapi"` to your config:

```json
{
    "host": "192.168.1.1",
    "username": "redalert",
    "password": "your-password",
    "backend": "pyunifiapi",
    "device_macs": ["aa:bb:cc:dd:ee:ff"]
}
```

Install the chosen backend:
```bash
# aiounifi (default, installed with the unifi extra)
pip install "red-alert[unifi]"

# pyunifiapi (install separately)
pip install py-unifiapi
```

**Note:** py-unifiapi is not yet published to PyPI. Install from source until it is published.

## Technical Details

Authentication uses the controller's internal REST API (the same API the web UI uses). Both backends support UniFi OS (UDM, UDR, UCG) and legacy controllers.

LED control uses three device properties:
- `led_override`: `"on"` or `"off"`
- `led_override_color`: hex color string (e.g., `"#FF0000"`)
- `led_override_color_brightness`: integer 0-100

Color and brightness are only sent to devices that have an LED ring (`supports_led_ring` hardware capability).

Blink mode uses the controller's native device locate feature (`set-locate`/`unset-locate`), which makes the LED flash.

**Note:** A local controller account is recommended. TOTP-based 2FA is supported via the `totp_secret` config option (requires `pyotp` for aiounifi, built-in for pyunifiapi).
