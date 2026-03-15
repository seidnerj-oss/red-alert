# Cell Broadcast (CBS) Integration

red-alert can receive emergency alerts directly from the cellular network via Cell Broadcast (CBS/ETWS/CMAS). This provides an independent alert source that works without internet connectivity - alerts are pushed by cell towers to all devices on the network.

## How It Works

1. A patched `qmicli` binary monitors the QMI modem's WMS (Wireless Messaging Service) for CBS indications
2. The red-alert CBS integration spawns `qmicli --wms-monitor` as a subprocess and parses its output
3. Multi-page CBS messages are reassembled and decoded from UCS-2 into text
4. CBS message IDs are mapped to alert states: 4370=PRE_ALERT, 4371-4372=ALERT, 4373=ALL_CLEAR
5. State change callbacks can trigger notifications (Telegram, etc.)

Each CBS message contains the alert text in Hebrew, English, Arabic, and Russian.

## Prerequisites

- A device with a QMI-capable LTE modem (tested on UniFi LTE Backup Pro with Sierra Wireless WP7607)
- SSH access to the device (see [UniFi LTE Backup Pro SSH setup guide](CBS-UNIFI-LTE-PRO-SSH.md))
- Docker on your build machine (for cross-compiling qmicli)
- Python 3.14+

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

**`cbs-config.json`:**
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

The CBS integration uses only Python stdlib modules (`asyncio.subprocess`, `dataclasses`, `re`). No additional pip packages are required. The `qmicli` binary handles all QMI protocol communication with the modem.

The patched qmicli commands added for CBS:
- `--wms-set-event-report` - enables MT message event reporting via QMI_WMS_SET_EVENT_REPORT
- `--wms-set-broadcast-activation` - activates CBS reception via QMI_WMS_SET_BROADCAST_ACTIVATION
- `--wms-monitor` - registers for WMS Event Report indications, decodes CBS headers, ETWS messages, and PLMN info

These patches have been submitted upstream to the [libqmi project](https://gitlab.freedesktop.org/mobile-broadband/libqmi/-/issues/131).
