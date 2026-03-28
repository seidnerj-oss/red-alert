# Cell Broadcast (CBS) Integration

red-alert can receive emergency alerts directly from the cellular network via Cell Broadcast (CBS/ETWS/CMAS). This provides an independent alert source that works without internet connectivity - alerts are pushed by cell towers to all devices on the network.

## How It Works

Two deployment modes are supported:

### Direct mode (qmicli on the LTE device)

1. A patched `qmicli` binary runs directly on the LTE device
2. The red-alert CBS integration spawns `qmicli --wms-monitor` as a subprocess and parses its output
3. Multi-page CBS messages are reassembled and decoded from UCS-2 into text
4. CBS message IDs are mapped to alert states: 4370=PRE_ALERT, 4371-4372=ALERT, 4373=ALL_CLEAR
5. State change callbacks can trigger notifications (Telegram, etc.)

### Bridge mode (qmicli on a separate monitoring host)

In bridge mode, qmicli runs on a separate machine (Raspberry Pi, Mac, Linux server, etc.) and communicates with the LTE device's QMI modem over a TCP bridge using socat:

```
LTE Device (<lte-device-ip>)                   Monitoring Host (Pi, Mac, etc.)
  qmi-proxy (stock, always running)               socat (apt/brew, persistent)
       |                                               |
  socat (MIPS, deployed via SSH)                  ABSTRACT-LISTEN:qmi-proxy
  TCP-LISTEN:18222 <--------network-------->      TCP:<lte-device-ip>:18222
  ABSTRACT-CONNECT:qmi-proxy                           |
                                                  qmicli --wms-monitor (aarch64/darwin)
                                                    local subprocess
                                                       |
                                                  CbsAlertMonitor (Python)
```

This eliminates the need for any patched binary on the LTE device - only a stock `socat` binary is deployed. The monitoring host runs the patched qmicli natively (aarch64 or darwin build).

Each CBS message contains the alert text in Hebrew, English, Arabic, and Russian.

## Prerequisites

### Direct mode
- A device with a QMI-capable LTE modem (tested on UniFi LTE Backup Pro with Sierra Wireless WP7607)
- SSH access to the device (see [UniFi LTE Backup Pro SSH setup guide](CBS-UNIFI-LTE-PRO-SSH.md))
- Docker on your build machine (for cross-compiling qmicli)
- Python 3.14+

### Bridge mode (additional)
- A monitoring host (Raspberry Pi, Mac, Linux server) with network access to the LTE device
- `socat` installed on the monitoring host (`apt install socat` or `brew install socat`)
- `asyncssh` Python package: `pip install red-alert[cbs]`
- SSH access to the LTE device (automated via `scripts/setup-lte-pro-ssh.py`)

## Hardware: UniFi LTE Backup Pro

The [UniFi LTE Backup Pro](https://store.ui.com/us/en/collections/unifi-accessory-tech-lte-backup/products/u-lte-backup-pro) is a good fit for this because:

- Always-on LTE connection with a QMI modem (`/dev/cdc-wdm0`)
- SSH accessible from your local network (see [SSH setup guide](CBS-UNIFI-LTE-PRO-SSH.md))
- Low power, designed for 24/7 operation
- Already running `qmi-proxy` for shared modem access

**Device specs relevant to cross-compilation:**
- Architecture: MIPS 32-bit big-endian, soft-float (mips_24kc)
- OS: LEDE 17.01.6 (OpenWrt fork)
- C library: musl
- Linux kernel: 4.4.x

## Setup

### 1. Build the patched qmicli

The stock `qmicli` on the device (v1.30.2) lacks CBS monitoring commands. We need a patched build from libqmi `main` with three new commands.

The build uses Docker to cross-compile a static MIPS binary. From your build machine:

```bash
git clone https://github.com/seidnerj/qmicli-cbs.git
cd qmicli-cbs/
./build.sh
```

This builds `output/qmicli` - a ~8.5MB static binary.

**What the build does:**
- Downloads the musl.cc MIPS cross-compiler toolchain
- Statically links zlib, libffi, PCRE2, GLib, and libqmi (from git main)
- Applies CBS monitoring patches (submitted upstream to [libqmi](https://gitlab.freedesktop.org/mobile-broadband/libqmi/-/issues/131))
- Produces a self-contained binary with no runtime dependencies

### 2. Deploy to the device

```bash
ssh user@<device-ip> 'cat > /tmp/qmicli && chmod +x /tmp/qmicli' < output/qmicli
```

Note: The device runs dropbear which does not support `scp`/`sftp`. Use `ssh` with `cat` piping for file transfers.

Note: `/tmp` is cleared on reboot. For persistence, copy to a writable partition or set up an init script.

### 3. Configure CBS channels

SSH into the device and run:

```bash
# Check current CBS config
/tmp/qmicli -d /dev/cdc-wdm0 --device-open-proxy --wms-get-cbs-channels

# Set channels for Israeli emergency alerts
/tmp/qmicli -d /dev/cdc-wdm0 --device-open-proxy --wms-set-cbs-channels=919,4370-4383

# Activate Cell Broadcast reception
/tmp/qmicli -d /dev/cdc-wdm0 --device-open-proxy --wms-set-broadcast-activation

# Enable event reporting
/tmp/qmicli -d /dev/cdc-wdm0 --device-open-proxy --wms-set-event-report
```

### 4. Verify it works

```bash
# Start monitoring (Ctrl+C to stop)
/tmp/qmicli -d /dev/cdc-wdm0 --device-open-proxy --wms-monitor
```

When a real alert is broadcast, you'll see output like:

```
[/dev/cdc-wdm0] Received WMS event report indication:
  Transfer Route MT Message:
    Format:         gsm-wcdma-broadcast
    Raw Data (88 bytes):
      ...
    CBS Header:
      Serial Number: 0x59c0 (GS: 1, Message Code: 412, Update: 0)
      Message ID:    4370 (0x1112)
      DCS:           0x59
      Page:          1 of 15
```

### 5. Run the red-alert CBS integration

On a machine that can SSH to the device (or on the device itself if Python is available):

**`cbs-config.json` (direct mode):**
```json
{
    "qmicli_path": "/tmp/qmicli",
    "device": "/dev/cdc-wdm0",
    "device_open_proxy": true,
    "channels": "919,4370-4383",
    "reconnect_delay": 5,
    "max_reconnect_delay": 60,
    "latitude": 32.0853,
    "longitude": 34.7818,
    "areas_of_interest": ["תל אביב - יפו"]
}
```

```bash
python -m red_alert.integrations.inputs.cbs --config cbs-config.json
```

| Parameter | Description | Default |
|-----------|-------------|---------|
| `qmicli_path` | Path to the patched qmicli binary | `/tmp/qmicli` |
| `device` | QMI device path | `/dev/cdc-wdm0` |
| `device_open_proxy` | Use `--device-open-proxy` for shared modem access | `true` |
| `channels` | CBS channel IDs to monitor | `919,4370-4383` |
| `message_id_map` | Custom CBS message ID to state mapping (optional) | see below |
| `reconnect_delay` | Initial reconnect delay in seconds if qmicli exits | `5` |
| `max_reconnect_delay` | Maximum reconnect delay (exponential backoff cap) | `60` |
| `latitude` | Latitude of the LTE device's physical location | `null` |
| `longitude` | Longitude of the LTE device's physical location | `null` |
| `areas_of_interest` | City/area names the device's cell coverage maps to (takes precedence over lat/lon) | `[]` |
| `city_data_path` | Path to `city_data.json` for centroid fallback resolution (optional) | runtime cache |
| `location_radius_km` | Radius in km for centroid fallback resolution | `5.0` |
| `polygon_cache_path` | Path to polygon data cache file (optional) | `data/polygon_cache.json` |

## Bridge Mode

Bridge mode runs qmicli on a separate monitoring host instead of on the LTE device itself. This is the recommended setup for the UniFi LTE Backup Pro, since it avoids running patched binaries on the constrained MIPS device.

### Automated setup

The `scripts/setup-cbs.py` script automates the entire bridge setup:

```bash
pip install asyncssh pyunifiapi

python scripts/setup-cbs.py \
    --lte-host <lte-device-ip> \
    --controller-host <controller-ip> \
    --controller-username admin \
    --controller-password <pass> \
    --device-mac <lte-device-mac> \
    --ssh-key ~/.ssh/id_ed25519
```

**What the setup script does (infrastructure only):**

1. **Builds qmicli** - clones [qmicli-cbs](https://github.com/seidnerj/qmicli-cbs) and runs Docker cross-compilation (aarch64 + MIPS + darwin)
2. **Downloads socat** - fetches a MIPS socat binary from the LEDE 17.01.6 package repository
3. **Sets up the monitoring host** - checks socat is installed, prints instructions for deploying qmicli
4. **Enables SSH on the LTE device** - uses the `setup-lte-pro-ssh.py` script to inject an SSH key and start dropbear via the UniFi controller's WebRTC debug terminal
5. **Deploys socat** to the LTE device via SSH

The setup script only positions binaries and enables SSH - it does **not** start the socat bridge or configure CBS channels. That's handled at runtime by `CbsBridge` when the CBS monitor starts.

Individual steps can be re-run independently:

```bash
python scripts/setup-cbs.py --build-only
python scripts/setup-cbs.py --setup-host-only
python scripts/setup-cbs.py --enable-ssh-only [controller args]
python scripts/setup-cbs.py --deploy-lte-only --lte-host <ip> --ssh-key ~/.ssh/id_ed25519
```

### After LTE device reboot

The LTE device's dropbear SSH server and `/tmp` contents do not survive reboots. Re-run:

```bash
python scripts/setup-cbs.py --enable-ssh-only \
    --controller-host <controller-ip> \
    --controller-username admin \
    --controller-password <pass> \
    --device-mac <lte-device-mac> \
    --ssh-key ~/.ssh/id_ed25519

python scripts/setup-cbs.py --deploy-lte-only \
    --lte-host <lte-device-ip> \
    --ssh-key ~/.ssh/id_ed25519
```

Or re-run the full setup (idempotent - skips already-completed steps).

### Bridge mode config

**`cbs-bridge-config.json`:**
```json
{
    "qmicli_path": "/usr/local/bin/qmicli-cbs",
    "device": "/dev/cdc-wdm0",
    "device_open_proxy": true,
    "channels": "919,4370-4383",
    "lte_host": "<lte-device-ip>",
    "bridge_port": 18222,
    "lte_device_ssh_key_path": "~/.ssh/id_ed25519",
    "ssh_username": "root",
    "health_check_interval": 300,
    "latitude": 32.0853,
    "longitude": 34.7818,
    "areas_of_interest": ["תל אביב - יפו"]
}
```

```bash
python -m red_alert.integrations.inputs.cbs --config cbs-bridge-config.json
```

Setting `lte_host` activates bridge mode. The monitor will:
- Ensure the socat bridge is running on both the LTE device and locally before starting qmicli
- Configure CBS channels through the bridge on first connection
- Run periodic health checks (every `health_check_interval` seconds)
- Re-verify the bridge before restarting qmicli after any exit

| Parameter | Description | Default |
|-----------|-------------|---------|
| `lte_host` | LTE device hostname/IP (enables bridge mode when set) | `null` |
| `bridge_port` | TCP port for the socat bridge | `18222` |
| `lte_device_ssh_key_path` | Path to SSH private key for LTE device access | `null` |
| `ssh_username` | SSH username on the LTE device | `null` |
| `socat_remote_binary` | Local path to socat binary for auto-deployment to LTE device | `null` |
| `health_check_interval` | Seconds between bridge health checks (0 to disable) | `300` |

### Runtime behavior

When the CBS monitor starts in bridge mode, `CbsBridge` handles all operational work:
- Starts the socat bridge on the LTE device (via SSH) and locally
- Configures CBS channels through the bridge (set-cbs-channels, set-broadcast-activation, set-event-report)
- Runs periodic health checks
- Restarts socat on either side if it crashes

If the LTE device was rebooted (SSH down, socat binary gone), `CbsBridge` cannot re-enable SSH or redeploy socat on its own - it logs a clear error directing you to re-run the setup script with `--enable-ssh-only` and `--deploy-lte-only`.

## Device Location

Unlike the HTTP API which returns per-city alert data, Cell Broadcast alerts are received based on the cell tower's coverage area - the message itself does not specify which cities are affected. To map a received CBS alert to the correct areas of interest (for downstream consumers that filter by area), you must define where the LTE device is physically located.

There are two ways to specify location:

- **`areas_of_interest`** - explicit list of city/area names (matching the names used throughout red-alert) that the device's cell tower coverage maps to. This is the simpler option when you know which areas your cell tower covers. **Takes precedence** if both are provided.
- **`latitude` / `longitude`** - the device's geographic coordinates. At startup, cities are resolved using polygon-based matching (primary) or centroid radius matching (fallback).

**Resolution behavior:**

1. If only `areas_of_interest` is set, those areas are used directly.
2. If only `latitude`/`longitude` is set:
   - **Primary**: Point-in-polygon matching using HFC polygon boundaries (fetched from the HFC Meser Hadash app backend and cached locally). This provides precise city boundary matching.
   - **Fallback**: If polygon data is unavailable, falls back to centroid radius matching within `location_radius_km` (default: 5 km) using city coordinate data.
3. If both are set, `areas_of_interest` takes precedence. The coordinates are used for validation only - a warning is logged if the resolved cities don't overlap with the configured areas.
4. If neither is set, the CBS monitor **refuses to start**. At least one of `areas_of_interest` or `latitude`/`longitude` must be configured.

Polygon data is fetched from the HFC Meser Hadash app backend at startup and refreshed daily. The data is cached locally (default: `data/polygon_cache.json`). The HFC backend is geo-blocked outside Israel, so the cache allows the system to work even when the backend is temporarily unreachable.

## CBS Channel IDs

| Channel | CMAS Category | AlertState | Description |
|---------|---------------|------------|-------------|
| 919 | Israel-specific | varies | National emergency channel |
| 4370 | Presidential Alert | PRE_ALERT | "Alerts are expected in a few minutes" |
| 4371-4372 | Extreme Alert | ALERT | Active threat (rockets, missiles) |
| 4373-4378 | Severe Alert | ALL_CLEAR | "The event has ended" |
| 4379 | AMBER Alert | ALERT | Child abduction |
| 4380-4382 | Test/Exercise | ROUTINE | Drill messages |
| 4383 | EU-Alert Level 1 | ALERT | EU emergency alert |

To override the default mapping:

```json
{
    "message_id_map": {
        "4370": "pre_alert",
        "4371": "alert",
        "4373": "all_clear"
    }
}
```

## Background logging (without Python)

If you just want to log CBS messages on the device without the Python integration:

```bash
ssh user@<device-ip>

# Set up CBS (one-time)
/tmp/qmicli -d /dev/cdc-wdm0 --device-open-proxy --wms-set-cbs-channels=919,4370-4383
/tmp/qmicli -d /dev/cdc-wdm0 --device-open-proxy --wms-set-broadcast-activation
/tmp/qmicli -d /dev/cdc-wdm0 --device-open-proxy --wms-set-event-report

# Run monitor in background, logging to file
nohup /tmp/qmicli -d /dev/cdc-wdm0 --device-open-proxy --wms-monitor > /tmp/cbs.log 2>&1 &
```

Check for alerts later:
```bash
cat /tmp/cbs.log
```

## Comparison with HTTP API

| | Home Front Command API | Cell Broadcast (CBS) |
|---|---|---|
| **Transport** | Internet (HTTPS polling) | Cellular network (push) |
| **Latency** | Polling interval + API delay | Near-instant (network push) |
| **Internet required** | Yes | No |
| **Location granularity** | Per-city (via API "data" field) | Per-cell tower coverage area |
| **Message content** | JSON with category, cities, instructions | Multi-language text (HE/EN/AR/RU) |
| **Hardware** | Any device with internet | QMI-capable LTE modem |
| **Reliability** | Depends on API availability | Depends on cellular coverage |

The two sources complement each other well. The API provides structured data with city-level granularity; CBS provides faster, internet-independent delivery.

## Technical Details

In direct mode, the CBS integration uses only Python stdlib modules (`asyncio.subprocess`, `dataclasses`, `re`). No additional pip packages are required.

In bridge mode, the `asyncssh` package is required for SSH communication with the LTE device (install via `pip install red-alert[cbs]`). The setup script additionally requires `pyunifiapi` for enabling SSH via the UniFi controller.

The `qmicli` binary handles all QMI protocol communication with the modem.

The patched qmicli commands added for CBS:
- `--wms-set-event-report` - enables MT message event reporting via QMI_WMS_SET_EVENT_REPORT
- `--wms-set-broadcast-activation` - activates CBS reception via QMI_WMS_SET_BROADCAST_ACTIVATION
- `--wms-monitor` - registers for WMS Event Report indications, decodes CBS headers, ETWS messages, and PLMN info

These patches have been submitted upstream to the [libqmi project](https://gitlab.freedesktop.org/mobile-broadband/libqmi/-/issues/131).
