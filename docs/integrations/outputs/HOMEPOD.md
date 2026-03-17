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
- Python with [pyatv](https://github.com/postlund/pyatv) installed (see note below)
- Audio files to play (local files or URLs)

> **Python version note:** pyatv depends on pydantic-core, which uses PyO3 (a Rust-Python binding framework). As of March 2026, PyO3 does not support Python 3.14, so pyatv cannot be installed on 3.14. Use Python 3.13 for scanning and pairing. The credentials and identifiers obtained are portable and work from any Python version.

> **Credentials may not be required.** If your HomePod's HomeKit settings allow "Anyone on the Same Network" for speaker access (Home app -> Home Settings -> Allow Speaker & TV Access), pyatv can connect and stream audio without pairing credentials. Test with `atvremote --id <IDENTIFIER> playing` first - if it connects, you can skip the pairing step entirely and omit `credentials` from the config.

## Setup

### 1. Install red-alert with HomePod Support

```bash
git clone https://github.com/seidnerj/red-alert.git
cd red-alert
pip install ".[homepod]"
```

If using Python 3.14 where pyatv cannot build, use Python 3.13 for setup commands (scanning and pairing):

```bash
uv run --python 3.13 --no-project --with pyatv atvremote scan
```

### 2. Discover Devices

Scan the local network for HomePod and other AirPlay devices:

```bash
python -m red_alert.integrations.outputs.homepod --scan
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

### 3. Pair with Each Device (Optional)

> **Skip this step** if your HomePods allow "Anyone on the Same Network" in HomeKit speaker access settings. You can verify by running `atvremote --id <IDENTIFIER> playing` - if it shows the current playback state, credentials are not needed and you can omit the `credentials` field from your config.

Pair with a device to obtain credentials. You'll be prompted for a PIN displayed on your iOS device:

```bash
python -m red_alert.integrations.outputs.homepod --pair AABBCCDD-1122-3344-5566-778899AABBCC
```

The command will output credentials for your config file:
```
Add this to your config.json device entry:
  "credentials": {
    "airplay": "xxxxxxxxxxxx...",
    "companion": "yyyyyyyyyyyy..."
  }
```

> **Pairing troubleshooting:** If no PIN appears on your iPhone, the AirPlay pairing protocol may have changed with newer HomePod firmware. pyatv's `--protocol airplay` pairing may fail with `Connection Authorization Required` (HTTP 470) and `--protocol raop` may fail with `TlvValue.Salt`. In this case, rely on the credential-free approach above.

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
python -m red_alert.integrations.outputs.homepod --config config.json
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
| `credentials` | Protocol credentials from `--pair`. Not needed if HomeKit allows "Anyone on the Same Network" | no |
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

## Stereo Pairs

HomeKit stereo pairs do **not** automatically relay audio to both speakers. Streaming to one speaker in a stereo pair only plays on that speaker. To play on both, add each speaker as a separate device in the config:

```json
{
    "devices": [
        {
            "name": "Living Room Left",
            "identifier": "52:3C:36:2E:29:56",
            "actions": { "alert": { "audio": "/path/to/siren.wav", "loop": true } }
        },
        {
            "name": "Living Room Right",
            "identifier": "4A:42:FD:C5:EA:B0",
            "actions": { "alert": { "audio": "/path/to/siren.wav", "loop": true } }
        }
    ]
}
```

Use `--scan` to find both identifiers - stereo pairs show up as two separate devices.

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
ExecStart=/usr/bin/python3 -m red_alert.integrations.outputs.homepod --config /path/to/config.json
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

Supported credential protocols: `airplay`, `companion`, `raop`. Typically only `airplay` credentials are needed for audio streaming. If HomeKit speaker access is set to "Anyone on the Same Network", no credentials are needed at all.
