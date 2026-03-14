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
| `host` | Hostname or IP of the UniFi controller | Required (local) |
| `username` | Controller/SSO account username | Required |
| `password` | Controller/SSO account password | Required |
| `port` | Controller port | `443` |
| `site` | UniFi site name | `default` |
| `device_macs` | List of AP MAC addresses to control | Required |
| `interval` | API polling interval in seconds | `1` |
| `areas_of_interest` | Cities/areas to filter alerts for (empty = all of Israel) | `[]` |
| `totp_secret` | TOTP secret (base32) for 2FA - see [2FA Support](#2fa-support) | `null` |
| `backend` | Controller library: `"aiounifi"` or `"pyunifiapi"` - see [Backend](#backend) | `"aiounifi"` |
| `led_states` | Per-state LED configuration (see below) | See defaults |
| `monitors` | Per-area device groups - see [Multi-Monitor](#multi-monitor-per-area-device-groups) | `null` |
| `controllers` | Multi-controller cloud config - see [Cloud Connection](#cloud-connection-multi-controller) | `null` |
| `device_id` | Cloud controller device ID (single-controller cloud) | `null` |

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

## Multi-Monitor (Per-Area Device Groups)

Different APs can react to different alert areas with different LED settings. A `monitors` list defines per-area device groups, all sharing a single controller connection and API poll.

```json
{
    "host": "192.168.1.1",
    "username": "redalert",
    "password": "your-password",
    "interval": 1,
    "monitors": [
        {
            "name": "Home",
            "areas_of_interest": ["kfar saba", "raanana"],
            "device_macs": ["ac:8b:a9:dc:3b:60", "ac:8b:a9:dc:3b:68"],
            "led_states": {
                "routine": {"brightness": 20}
            }
        },
        {
            "name": "Bedroom",
            "areas_of_interest": ["tel aviv"],
            "device_macs": ["ac:8b:a9:dc:38:84"],
            "led_states": {
                "alert": {"color": "blue", "blink": true}
            }
        }
    ]
}
```

Each monitor has its own `AlertStateTracker` - they track state independently based on their own `areas_of_interest`. Only the devices listed in a monitor's `device_macs` are updated when that monitor's state changes.

### Per-Monitor Options

| Parameter | Description |
|-----------|-------------|
| `name` | Display name for logging (defaults to `monitor-0`, `monitor-1`, etc.) |
| `areas_of_interest` | Cities/areas to filter alerts for this group |
| `device_macs` | AP MAC addresses controlled by this monitor |
| `led_states` | LED config for this monitor (same format as top-level `led_states`) |
| `device_overrides` | Per-device LED overrides within this monitor |
| `hold_seconds` | Hold duration overrides for this monitor |

### Inheritance

- **Connection settings** (`host`, `username`, `password`, `port`, `site`, `totp_secret`, `backend`, `interval`) are always top-level and shared across all monitors.
- **`hold_seconds`** defined at the top level is inherited by all monitors as a default. Per-monitor `hold_seconds` overrides specific keys while inheriting the rest.

### Backward Compatibility

If `monitors` is absent, the flat config format works exactly as before (single monitor with top-level `device_macs`, `areas_of_interest`, etc.). No config changes needed for existing setups.

## Cloud Connection (Multi-Controller)

If your UniFi account has access to multiple controllers (e.g., home and parents' home), you can control devices across all of them using Ubiquiti cloud connections. This connects via WebRTC through the Ubiquiti cloud, so the controllers don't need to be on the local network.

**Requirements:**
- `pyunifiapi` backend (cloud is not supported with aiounifi)
- Your SSO account credentials and TOTP secret
- The `device_id` of each controller (see [Finding Controller Device IDs](#finding-controller-device-ids))

```json
{
    "username": "your-sso@email.com",
    "password": "your-sso-password",
    "totp_secret": "YOUR_TOTP_SECRET",
    "controllers": [
        {
            "name": "Home",
            "device_id": "abc123def456",
            "site": "default",
            "monitors": [
                {
                    "name": "Living Room",
                    "device_macs": ["ac:8b:a9:dc:3b:60", "ac:8b:a9:dc:3b:68"],
                    "areas_of_interest": ["kfar saba", "raanana"]
                }
            ]
        },
        {
            "name": "Parents' Home",
            "device_id": "789ghi012jkl",
            "site": "default",
            "monitors": [
                {
                    "name": "Hallway",
                    "device_macs": ["11:22:33:44:55:66"],
                    "areas_of_interest": ["herzliya"]
                }
            ]
        }
    ]
}
```

Each controller gets its own WebRTC connection. All controllers share the same SSO credentials and alert API poll. The `controllers` key automatically sets `connection: "cloud"` and `backend: "pyunifiapi"`.

### Per-Controller Options

| Parameter | Description |
|-----------|-------------|
| `name` | Display name for logging |
| `device_id` | Cloud controller device ID (required) |
| `site` | UniFi site name on this controller (default: `"default"`) |
| `monitors` | List of monitor groups for this controller (same format as top-level `monitors`) |

### Finding Controller Device IDs

The `device_id` identifies each controller in the Ubiquiti cloud. You can find it by:

1. Logging into [unifi.ui.com](https://unifi.ui.com) with your SSO account
2. Opening your browser's developer tools (Network tab)
3. Looking for API calls that include device identifiers
4. Alternatively, using the pyunifiapi `CloudHttpTransport.list_devices()` API after SSO authentication

### Cloud Connection Notes

- Cloud connections use WebRTC data channels proxied through Ubiquiti's cloud infrastructure
- Only controllers running UniFi OS (Dream Machine, UCG, UDR, etc.) support cloud connections
- The `host` field is not required for cloud connections
- Each controller maintains its own WebRTC connection independently
- If a controller's WebRTC connection drops, other controllers continue operating

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
| `pyunifiapi` | [pyunifiapi](https://github.com/seidnerj/pyunifiapi) | Native | httpx | Also supports WebRTC SSH, cloud API |

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
pip install pyunifiapi
```

**Note:** pyunifiapi is not yet published to PyPI. Install from source until it is published.

## Technical Details

Authentication uses the controller's internal REST API (the same API the web UI uses). Both backends support UniFi OS (UDM, UDR, UCG) and legacy controllers.

LED control uses three device properties:
- `led_override`: `"on"` or `"off"`
- `led_override_color`: hex color string (e.g., `"#FF0000"`)
- `led_override_color_brightness`: integer 0-100

Color and brightness are only sent to devices that have an LED ring (`supports_led_ring` hardware capability).

Blink mode uses the controller's native device locate feature (`set-locate`/`unset-locate`), which makes the LED flash.

**Note:** A local controller account is recommended. TOTP-based 2FA is supported via the `totp_secret` config option (requires `pyotp` for aiounifi, built-in for pyunifiapi).
