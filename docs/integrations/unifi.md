# UniFi LED Integration

RedAlert can control the RGB LEDs on UniFi U6/U7 access points based on alert state. LEDs change color to indicate the current situation:

- **Routine** - white (or green, configurable)
- **Pre-alert** - yellow (imminent warning, category 14)
- **Alert** - red (active alert - missiles, hostile aircraft, earthquakes, etc.)

## How It Works

1. The RedAlert monitor polls the Home Front Command API every second
2. It classifies the response into one of three states: ROUTINE, PRE_ALERT, or ALERT
3. It connects to each configured UniFi AP via SSH and writes the RGB color to `/proc/ubnt_ledbar/custom_color`

## Prerequisites

- UniFi U6 or U7 access points with LED bar
- SSH enabled on the APs (UniFi Network > Settings > System > Device SSH Authentication)
- SSH key-based authentication configured (password auth is not supported)
- Python 3.11+ with asyncssh installed

## Setup

### 1. Install RedAlert with UniFi Support

```bash
git clone https://github.com/idodov/RedAlert.git
cd RedAlert

# Install with UniFi dependencies
pip install aiohttp asyncssh
```

### 2. Enable SSH on Your APs

1. Open UniFi Network controller
2. Go to **Settings > System > Advanced**
3. Enable **Device SSH Authentication**
4. Set a username and password (used for initial key setup)

### 3. Set Up SSH Key Authentication

```bash
# Generate a key if you don't have one
ssh-keygen -t ed25519 -f ~/.ssh/unifi_ap -N ""

# Copy the key to each AP
ssh-copy-id -i ~/.ssh/unifi_ap admin@192.168.1.10
ssh-copy-id -i ~/.ssh/unifi_ap admin@192.168.1.11
```

### 4. Create a Config File

**`config.json`:**
```json
{
    "devices": [
        {"host": "192.168.1.10"},
        {"host": "192.168.1.11", "port": 2222}
    ],
    "ssh_username": "admin",
    "ssh_key_path": "/home/user/.ssh/unifi_ap",
    "default_color": "white",
    "interval": 1,
    "areas_of_interest": []
}
```

| Parameter | Description | Default |
|-----------|-------------|---------|
| `devices` | List of APs with `host` (required) and `port` (optional, default 22) | `[]` |
| `ssh_username` | SSH username for AP login | `admin` |
| `ssh_key_path` | Path to SSH private key file | `null` (uses default keys) |
| `known_hosts` | Path to known_hosts file. `null` disables host key checking | `null` |
| `default_color` | LED color when no alert is active: `white` or `green` | `white` |
| `interval` | API polling interval in seconds | `1` |
| `areas_of_interest` | Cities/areas to filter alerts for (empty = all of Israel) | `[]` |

### 5. Start the Monitor

```bash
python -m red_alert.integrations.unifi --config config.json
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

When configured, only alerts that include at least one of the listed areas will change the LED color. Other alerts are ignored.

## Alert States

| State | LED Color | Description |
|-------|-----------|-------------|
| ROUTINE | White (or green) | No active alerts (or alerts not in areas of interest) |
| PRE_ALERT | Yellow | Imminent warning - alerts expected in the coming minutes |
| ALERT | Red | Active alert - take shelter immediately |

## Running as a Service

**systemd example (`/etc/systemd/system/redalert-unifi.service`):**
```ini
[Unit]
Description=RedAlert UniFi LED Monitor
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/path/to/RedAlert
ExecStart=/usr/bin/python3 -m red_alert.integrations.unifi --config /path/to/config.json
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable redalert-unifi
sudo systemctl start redalert-unifi
```

## Technical Details

The UniFi AP LED is controlled by writing RGB values directly to the kernel interface:

```bash
echo -n 255,0,0 > /proc/ubnt_ledbar/custom_color
```

This works on U6 and U7 firmware. The UniFi REST API `led_override_color` field does NOT control the actual LED color on these models - only the direct proc write works.

The controller uses asyncssh for non-blocking SSH connections and updates all configured APs in parallel. If the color hasn't changed since the last update, the SSH commands are skipped.
