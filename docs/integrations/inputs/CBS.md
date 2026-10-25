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
  socat (MIPS, uploaded via SSH)                  ABSTRACT-LISTEN:qmi-proxy
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
- For `bridge_management` `self` (the default): the `unifi` extra, which brings [lte-pro-access](https://github.com/seidnerj-oss/lte-pro-access) (and through it `asyncssh`), plus `pyunifiapi` 0.2.0 or later installed from source (see the `unifi` extra in `pyproject.toml`)
- SSH key access to the LTE device (first setup via `scripts/setup-lte-pro-ssh.py`; the monitor re-enables SSH itself after reboots)

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
uv sync --extra unifi
uv pip install "pyunifiapi @ git+https://github.com/seidnerj-oss/pyunifiapi@v0.2.0"  # 0.2.0 or later

python scripts/setup-cbs.py --ssh-key ~/.ssh/id_ed25519_lte
```

**What the setup script does (infrastructure only):**

1. **Builds qmicli** - clones [qmicli-cbs](https://github.com/seidnerj/qmicli-cbs) and runs Docker cross-compilation (aarch64 + MIPS + darwin)
2. **Downloads socat** - fetches a MIPS socat binary from the LEDE 17.01.6 package repository and places it at `data/socat-mips`, where the monitor reads it
3. **Sets up the monitoring host** - checks socat is installed, prints instructions for deploying qmicli
4. **Generates an SSH key** for the LTE device if `--ssh-key` is not given (`~/.ssh/id_ed25519_lte`)

It does **not** enable SSH on the device, upload socat, start the bridge or configure CBS channels. That is handled at runtime by `CbsBridge` when the CBS monitor starts. For the first setup, authorize the key on the device once with [`scripts/setup-lte-pro-ssh.py`](../../../scripts/setup-lte-pro-ssh.py) (see the [SSH setup guide](CBS-UNIFI-LTE-PRO-SSH.md#automating-via-pyunifiapi)).

Individual steps can be re-run independently:

```bash
python scripts/setup-cbs.py --build-only
python scripts/setup-cbs.py --download-socat-only
python scripts/setup-cbs.py --setup-host-only
```

### After LTE device reboot

The LTE device's dropbear SSH server and `/tmp` contents do not survive reboots. With `bridge_management` `self`, a running monitor recovers on its own: it re-enables SSH through the UniFi controller and uploads socat again (see [Health checks and recovery](#runtime-behavior)). Nothing needs to be re-run.

### Bridge mode config

**`cbs-bridge-config.json`:**
```json
{
    "qmicli_path": "/usr/local/bin/qmicli-cbs",
    "device": "/tmp/cdc-wdm0",
    "device_open_proxy": true,
    "channels": "919,4370-4383",
    "lte_host": "<lte-device-ip>",
    "bridge_port": 18222,
    "lte_device_mac": "<lte-device-mac>",
    "lte_device_ssh_key_path": "~/.ssh/id_ed25519_lte",
    "ssh_username": "root",
    "monitoring_host_address": "<monitoring-host-ip>",
    "bridge_state_dir": "~/.local/state/red-alert",
    "unifi": {
        "host": "<controller-ip>",
        "username": "admin",
        "password": "<pass>"
    },
    "health_check_interval": 300,
    "latitude": 32.0853,
    "longitude": 34.7818,
    "areas_of_interest": ["תל אביב - יפו"]
}
```

```bash
python -m red_alert.integrations.inputs.cbs --config cbs-bridge-config.json
```

Setting `lte_host` activates bridge mode. With `bridge_management` `self` (the default) the relays are supervised in-process by [lte-pro-access](https://github.com/seidnerj-oss/lte-pro-access), and the monitor will:
- Ensure the socat bridge is running on both the LTE device and locally before starting qmicli
- Auto-enable SSH on the LTE device via the UniFi controller if the connection is refused
- Upload `data/socat-mips` to the LTE device as `/tmp/socat` when it is missing or differs
- Configure CBS channels through the bridge on first connection
- Run periodic health checks (every `health_check_interval` seconds) and rebuild the bridge after two consecutive failures (see [Runtime behavior](#runtime-behavior))
- Re-verify the bridge before restarting qmicli after any exit

> **Device SSH host key:** lte-pro-access pins the device's host key per boot cycle (trust on first use) in `bridge_state_dir/lte_host_key.pub`. A changed key is accepted only right after the monitor re-enabled SSH and the controller confirmed it started dropbear (a reboot). Any other change is refused and the bridge stays down; if that happens without a reboot, delete the pin file and restart the monitor. Never start dropbear on the device by hand while the monitor runs.

> **Closing the local relay:** when the monitor stops, lte-pro-access terminates the local socat with SIGTERM and does not wait for it or escalate to SIGKILL (red-alert's own earlier code waited 5 s and then killed it). A socat that ignores SIGTERM can outlive the monitor; this is library behavior and red-alert does not change it.

> **Bridge mode device node:** In bridge mode, the QMI device is remote but qmicli still requires a local character device node at the configured `device` path. The systemd service creates this automatically via `ExecStartPre`:
> ```ini
> ExecStartPre=+/bin/sh -c 'test -e /dev/cdc-wdm0 || mknod -m 0666 /dev/cdc-wdm0 c 180 0'
> ```
> The `+` prefix runs the command as root. The device node persists until reboot.

| Parameter | Description | Default |
|-----------|-------------|---------|
| `lte_host` | LTE device hostname/IP (enables bridge mode when set) | `null` |
| `bridge_port` | TCP port for the socat bridge | `18222` |
| `bridge_management` | `self` (this monitor deploys and runs the bridge) or `external` (the bridge is managed by another process, see [External bridge management](#external-bridge-management)) | `self` |
| `health_check_interval` | Seconds between bridge health checks (0 disables both the checks and the automatic rebuild) | `300` |

With `bridge_management` `self`, these keys are also required; none has a default and the monitor refuses to start without them:

| Parameter | Description |
|-----------|-------------|
| `lte_device_mac` | MAC address of the LTE device, used to re-enable SSH through the controller |
| `lte_device_ssh_key_path` | Path to the SSH private key for the LTE device (its `.pub` is what gets authorized) |
| `ssh_username` | SSH username on the LTE device |
| `unifi` | UniFi controller credentials passed to pyunifiapi: `host`, `username`, `password` (and optionally `port`, `site`, `totp_secret`) for a local controller, or `device_id`, `username`, `password`, `totp_secret` for the cloud |
| `monitoring_host_address` | This host's IP address as the LTE device sees it; the device relay accepts connections only from it |
| `bridge_state_dir` | Writable directory for bridge state (the pinned device SSH host key) |

The MIPS socat is read from `data/socat-mips` (placed there by `scripts/setup-cbs.py`) and the local socat from `PATH`. The removed `socat_remote_binary` key is rejected. The `data/socat-mips` path is relative to the red-alert source checkout, so self mode assumes a git clone deployment rather than a wheel install.

**Self mode owns socat on the device.** A fresh start runs `killall socat qmicli qmi-proxy` on the LTE device, which also stops any socat that something else runs there (for example another lte-pro-access instance relaying AT commands). Self mode therefore assumes red-alert is the only owner of socat on the device. If the device is shared, use `bridge_management` `external` and let the other process own the bridge.

#### Upgrading an existing self mode config

Earlier versions of self mode deployed and ran the bridge with red-alert's own SSH code. It now runs on lte-pro-access, which changes the config. A config written for the old code stops the CBS input at startup with an error that names what is wrong, so update it before upgrading a running host:

- Remove `socat_remote_binary`. It is rejected; the MIPS socat is always read from `data/socat-mips`.
- Add the new required keys `monitoring_host_address` and `bridge_state_dir`. `lte_device_mac`, `lte_device_ssh_key_path`, `ssh_username` and `unifi` were optional or had defaults before; each is now required in self mode with no default (see the table above).
- Produce `data/socat-mips` by running `python scripts/setup-cbs.py --download-socat-only`. It is not part of a fresh clone, and self mode refuses to start without it. Older versions kept the download in `~/.cache/red-alert/socat-mips`, which is no longer read.
- Install the `unifi` extra (`uv sync --extra unifi`), which brings lte-pro-access.

### External bridge management

By default (`"bridge_management": "self"`, also what a config without the key gets) the monitor owns the bridge: lte-pro-access uploads and starts socat on the LTE device over SSH and runs the local socat, and a fresh start clears the device side.

Set `"bridge_management": "external"` when the bridge is managed by another process. The monitor then never opens SSH, deploys, starts or kills anything. It only probes:

- `ensure_bridge()` succeeds when the local abstract `qmi-proxy` socket accepts a connection, and fails (so the reconnect loop backs off and retries) when it does not.
- Health checks report the LTE side from a TCP connect to `lte_host:bridge_port` and the local side from a connect to the local `qmi-proxy` socket.
- A fresh-start request (after repeated failed health checks) restarts qmicli, but there is nothing for the monitor to rebuild: the other process is expected to restore the bridge.

In this mode `lte_device_mac`, `lte_device_ssh_key_path`, `ssh_username`, `unifi`, `monitoring_host_address` and `bridge_state_dir` are unused and lte-pro-access is not needed. The other process must provide the local abstract `qmi-proxy` socket that qmicli connects to.

### Runtime behavior

When the CBS monitor starts in bridge mode, `CbsBridge` (with the lte-pro-access supervisor) handles all operational work:
- Starts the socat bridge on the LTE device (via SSH) and locally
- Configures CBS channels through the bridge (set-cbs-channels, set-broadcast-activation)
- Runs periodic health checks
- Restarts socat on either side if it crashes

**Health checks and recovery.** Every `health_check_interval` seconds the monitor runs a supervisor tick, which checks that the LTE device is listening on `bridge_port` and that the local socat is running. qmicli does not notice when the link under it dies - after an LTE device reboot its connection stays half-open, so it never prints or exits, and the reconnect loop never gets a chance to run. So a failed check has to make the monitor stop qmicli (SIGTERM, then SIGKILL after 10 s) and mark the bridge for a fresh start. There are two kinds of failed check:

- A tick that **started a relay** (`started-qmi-relay` or `started-pi-relay`) is definitive: the relay path qmicli is attached to was rebuilt under it, even though the relay is back. The monitor rebuilds at once, without waiting for a second check. A tick that only uploaded socat or re-enabled SSH while the relays kept listening does not break qmicli's path and is not a failure.
- A tick that found a relay down or errored out may be a network blip, so only **two consecutive** such checks trigger the rebuild.

The reconnect loop then does what a service restart would:

1. Kills socat, qmicli and qmi-proxy on the LTE device (clearing any stale WMS client registration), re-enabling SSH via the controller first if the connection is refused
2. Runs a supervisor tick: uploads socat if `/tmp/socat` is gone or differs, and starts whichever side of the bridge is down
3. Reconfigures CBS channels and restarts qmicli

A single failed probe only logs a warning: the health check reports any SSH error as "down", so one failure may be a network blip, and a rebuild leaves CBS blind while it runs. Two in a row means the bridge has been down across a whole interval, so a dead bridge is detected within `2 x health_check_interval` (10 minutes by default); a rebuilt relay path is acted on at the first check. A check that errors out is logged and neither counts toward nor resets the failure count. If the rebuild itself fails (device still booting), the reconnect loop retries with exponential backoff up to `max_reconnect_delay`.

With `bridge_management` set to `external`, none of the steps above apply; see [External bridge management](#external-bridge-management).

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

In bridge mode with `bridge_management` `self`, the [lte-pro-access](https://github.com/seidnerj-oss/lte-pro-access) library supervises the relays and SSH (install via the `unifi` extra), and `pyunifiapi` 0.2.0 or later (install from source, see the `unifi` extra in `pyproject.toml`) is required for enabling SSH via the UniFi controller; the monitor refuses to start without either. `scripts/setup-lte-pro-ssh.py` also requires `pyunifiapi`. With `external` no extra packages are needed.

The `qmicli` binary handles all QMI protocol communication with the modem.

The patched qmicli commands added for CBS:
- `--wms-set-broadcast-activation` - activates CBS reception via QMI_WMS_SET_BROADCAST_ACTIVATION
- `--wms-monitor` - registers for WMS Event Report indications, decodes CBS headers, ETWS messages, and PLMN info

These patches have been submitted upstream to the [libqmi project](https://gitlab.freedesktop.org/mobile-broadband/libqmi/-/issues/131).
