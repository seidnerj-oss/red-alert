# HomePod Integration

red-alert can play audio on Apple HomePod devices via AirPlay when alert state changes. Each device is independently configurable with per-state actions:

- **Alert** - play a siren or alarm sound, optionally looping at full volume
- **Pre-alert** - play a warning tone at moderate volume
- **All-clear** - play a confirmation chime
- **Routine** - stop playback

## How It Works

1. The red-alert monitor polls the Home Front Command API every second
2. It classifies the response into one of four states: ROUTINE, PRE_ALERT, ALERT, or ALL_CLEAR
3. On state transitions, each configured HomePod receives its per-state action (play audio, stop, adjust volume)
4. Actions are executed in parallel across all devices

## Prerequisites

- One or more Apple HomePod or HomePod mini devices on the local network
- Python 3.14+ with [pyatv](https://github.com/postlund/pyatv) installed
- Paired credentials for each device (obtained via `--pair`)
- Audio files to play (local files or URLs)

## Setup

### 1. Install red-alert with HomePod Support

```bash
git clone https://github.com/seidnerj/red-alert.git
cd red-alert
pip install ".[homepod]"
```

### 2. Discover Devices

Scan the local network for HomePod and other AirPlay devices:

```bash
python -m red_alert.integrations.homepod --scan
```

Output:
```
Found 2 device(s):

  Name:       Living Room
  Identifier: AABBCCDD-1122-3344-5566-778899AABBCC
  Address:    192.168.1.50
  Protocols:  AirPlay, Companion

  Name:       Bedroom
  Identifier: 11223344-5566-7788-99AA-BBCCDDEEFF00
  Address:    192.168.1.51
  Protocols:  AirPlay, Companion
```

Note the **Identifier** for each device you want to control.

### 3. Pair with Each Device

Pair with a device to obtain credentials. You'll be prompted for a PIN displayed on the device or on your iOS device:

```bash
python -m red_alert.integrations.homepod --pair AABBCCDD-1122-3344-5566-778899AABBCC
```

The command will output credentials for your config file:
```
Add this to your config.json device entry:
  "credentials": {
    "airplay": "xxxxxxxxxxxx...",
    "companion": "yyyyyyyyyyyy..."
  }
```

Repeat for each HomePod you want to control.

### 4. Prepare Audio Files

You'll need audio files for each state you want to handle. For example:
- `siren.mp3` - Home Front Command siren sound for alerts
- `warning.mp3` - warning tone for pre-alerts
- `all_clear.mp3` - confirmation chime for all-clear

Audio can be local file paths or URLs. For local files, any format supported by HomePod works (MP3, AAC, WAV, etc.).

### 5. Create a Config File

**`config.json`:**
```json
{
    "interval": 1,
    "cooldown": 30,
    "areas_of_interest": [],
    "devices": [
        {
            "name": "Living Room",
            "identifier": "AABBCCDD-1122-3344-5566-778899AABBCC",
            "credentials": {
                "airplay": "xxxxxxxxxxxx..."
            },
            "actions": {
                "alert": {
                    "audio": "/path/to/siren.mp3",
                    "volume": 100,
                    "loop": true
                },
                "pre_alert": {
                    "audio": "/path/to/warning.mp3",
                    "volume": 70
                },
                "all_clear": {
                    "audio": "/path/to/all_clear.mp3",
                    "volume": 50
                }
            }
        },
        {
            "name": "Bedroom",
            "identifier": "11223344-5566-7788-99AA-BBCCDDEEFF00",
            "credentials": {
                "airplay": "yyyyyyyyyyyy..."
            },
            "actions": {
                "alert": {
                    "audio": "/path/to/gentle_alarm.mp3",
                    "volume": 60
                }
            }
        }
    ]
}
```

### 6. Start the Monitor

```bash
python -m red_alert.integrations.homepod --config config.json
```

## Configuration Reference

### Top-Level Parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| `interval` | API polling interval in seconds | `1` |
| `cooldown` | Seconds to hold alert state after API goes empty | `null` |
| `areas_of_interest` | Cities/areas to filter alerts for (empty = all of Israel) | `[]` |
| `devices` | List of device configurations (see below) | required |

### Device Parameters

| Parameter | Description | Required |
|-----------|-------------|----------|
| `identifier` | Device identifier from `--scan` | yes |
| `credentials` | Protocol credentials from `--pair` | yes |
| `name` | Human-readable name for logging | no |
| `actions` | Per-state action configuration (see below) | no |

### Action Parameters

Each state (`alert`, `pre_alert`, `all_clear`, `routine`) can have an action:

| Parameter | Description | Default |
|-----------|-------------|---------|
| `audio` | File path or URL to play. If omitted, playback is stopped | `null` |
| `volume` | Volume level (0-100). If omitted, volume is unchanged | `null` |
| `loop` | Continuously re-play audio until state changes | `false` |

**Behavior when `audio` is omitted:** Playback is stopped. If `volume` is set, it is applied after stopping (useful for restoring volume to a default level).

**Behavior when an entire state is omitted:** Playback is stopped (same as `"routine": {}`).

## Per-Device Configuration

Each device is independently configurable. This allows different rooms to behave differently:

```json
{
    "devices": [
        {
            "name": "Living Room",
            "identifier": "...",
            "credentials": {"airplay": "..."},
            "actions": {
                "alert": {"audio": "/path/to/loud_siren.mp3", "volume": 100, "loop": true},
                "pre_alert": {"audio": "/path/to/warning.mp3", "volume": 70},
                "all_clear": {"audio": "/path/to/clear.mp3", "volume": 50}
            }
        },
        {
            "name": "Bedroom",
            "identifier": "...",
            "credentials": {"airplay": "..."},
            "actions": {
                "alert": {"audio": "/path/to/gentle_alarm.mp3", "volume": 60}
            }
        },
        {
            "name": "Office",
            "identifier": "...",
            "credentials": {"airplay": "..."},
            "actions": {
                "alert": {"audio": "https://example.com/siren.mp3", "volume": 80},
                "pre_alert": {"audio": "https://example.com/warning.mp3", "volume": 50}
            }
        }
    ]
}
```

In this example:
- **Living Room** plays a loud siren on loop during alerts, a warning tone for pre-alerts, and a chime on all-clear
- **Bedroom** only plays a gentle alarm on alerts (at lower volume) and is silent for other states
- **Office** plays audio from URLs for alerts and pre-alerts

## Areas of Interest

By default, audio plays for alerts anywhere in Israel. To only react to alerts in specific areas:

```json
{
    "areas_of_interest": [
        "tel aviv - city center",
        "haifa - city center",
        "kfar saba"
    ]
}
```

## Cooldown

The `cooldown` parameter prevents premature return to routine during multi-salvo attacks. When set (e.g., 30 seconds), the alert state is held until either:

- An explicit all-clear (category 13) is received from the API
- The cooldown period expires without new alerts

This prevents the siren from stopping and restarting between salvos.

## Running as a Service

**systemd example (`/etc/systemd/system/redalert-homepod.service`):**
```ini
[Unit]
Description=red-alert HomePod Audio Monitor
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/path/to/red-alert
ExecStart=/usr/bin/python3 -m red_alert.integrations.homepod --config /path/to/config.json
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable redalert-homepod
sudo systemctl start redalert-homepod
```

## Technical Details

The integration uses [pyatv](https://github.com/postlund/pyatv) to communicate with HomePod devices via the AirPlay protocol. Key details:

- **Discovery** uses Zeroconf/mDNS to find devices on the local network
- **Pairing** exchanges credentials using Apple's pairing protocol (PIN-based)
- **Audio streaming** uses `stream.stream_file()` for local files and `stream.play_url()` for URLs
- **Volume control** uses the pyatv audio interface
- **Loop mode** monitors playback state via `metadata.playing()` and re-streams when the device goes idle
- **Parallel execution** - all device actions are dispatched concurrently via `asyncio.gather`

Supported credential protocols: `airplay`, `companion`, `raop`. Typically only `airplay` credentials are needed for audio streaming.
