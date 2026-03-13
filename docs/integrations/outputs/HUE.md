# Philips Hue Integration

red-alert can control Philips Hue lights based on alert state. Lights change color to indicate the current situation:

- **Routine** - white (or warm white, configurable)
- **Pre-alert** - yellow (imminent warning, category 14)
- **Alert** - red (active alert - missiles, hostile aircraft, earthquakes, etc.)

## How It Works

1. The red-alert monitor polls the Home Front Command API every second
2. It classifies the response into one of three states: ROUTINE, PRE_ALERT, or ALERT
3. It sends color commands to the Hue Bridge via its local REST API
4. The bridge updates all configured lights and/or groups

## Prerequisites

- Philips Hue Bridge on the local network
- Hue lights that support color (e.g. Hue Color, Hue Go, Hue Bloom)
- Python 3.11+ with httpx installed

## Setup

### 1. Install red-alert

```bash
git clone https://github.com/seidnerj/red-alert.git
cd red-alert

pip install httpx
```

### 2. Register with the Hue Bridge

Press the **link button** on your Hue Bridge, then run:

```bash
python -m red_alert.integrations.outputs.hue --register 192.168.1.50
```

Replace `192.168.1.50` with your bridge IP. The command will print an API key. Save this for the config file.

### 3. Find Your Light and Group IDs

Visit `http://<bridge-ip>/api/<api-key>/lights` in your browser to see all lights and their IDs. Visit `http://<bridge-ip>/api/<api-key>/groups` for groups.

### 4. Create a Config File

**`config.json`:**
```json
{
    "bridge_ip": "192.168.1.50",
    "api_key": "your-api-key-from-registration",
    "lights": [1, 3, 5],
    "groups": [1],
    "default_color": "white",
    "interval": 1,
    "areas_of_interest": []
}
```

| Parameter | Description | Default |
|-----------|-------------|---------|
| `bridge_ip` | IP address of the Hue Bridge | required |
| `api_key` | API key from bridge registration | required |
| `lights` | List of individual light IDs to control | `[]` |
| `groups` | List of group IDs to control (efficient for rooms) | `[]` |
| `default_color` | Color when no alert: `white` or `warm` | `white` |
| `interval` | API polling interval in seconds | `1` |
| `areas_of_interest` | Cities/areas to filter alerts for (empty = all of Israel) | `[]` |

You can specify `lights`, `groups`, or both. Groups are more efficient when you want to control all lights in a room.

### 5. Start the Monitor

```bash
python -m red_alert.integrations.outputs.hue --config config.json
```

## Areas of Interest

By default, the lights react to alerts anywhere in Israel. To only react to alerts in specific areas:

```json
{
    "areas_of_interest": [
        "tel aviv - city center",
        "haifa - city center",
        "kfar saba"
    ]
}
```

## Alert States

| State | Light Color | Description |
|-------|-------------|-------------|
| ROUTINE | White (or warm) | No active alerts (or alerts not in areas of interest) |
| PRE_ALERT | Yellow | Imminent warning - alerts expected in the coming minutes |
| ALERT | Red | Active alert - take shelter immediately |

## Running as a Service

**systemd example (`/etc/systemd/system/redalert-hue.service`):**
```ini
[Unit]
Description=red-alert Hue Light Monitor
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/path/to/red-alert
ExecStart=/usr/bin/python3 -m red_alert.integrations.outputs.hue --config /path/to/config.json
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable redalert-hue
sudo systemctl start redalert-hue
```

## Technical Details

The integration uses the Hue Bridge v1/CLIP local REST API. Colors are converted from RGB to the Hue API's hue/saturation/brightness format using Python's `colorsys` module. All configured lights and groups are updated in parallel.

The controller skips redundant updates - if the color hasn't changed since the last update, no API calls are made.
