# UniFi LTE Backup Pro - SSH and Device Access

Guide for enabling SSH access to the UniFi LTE Backup Pro and exploring its internal modem interfaces. This is a prerequisite for the [CBS integration](CBS.md).

## Device Overview

The [UniFi LTE Backup Pro](https://store.ui.com/us/en/collections/unifi-accessory-tech-lte-backup/products/u-lte-backup-pro) is a managed LTE failover appliance. Internally:

- **CPU:** Qualcomm Atheros QCA956X (MIPS 32r2, big-endian, soft-float)
- **OS:** LEDE 17.01.6 (OpenWrt fork), BusyBox v1.25.1 (ash shell)
- **C library:** musl (`/lib/ld-musl-mips-sf.so.1`)
- **LTE modem:** Sierra Wireless WP7607, connected via internal USB
- **Modem interfaces:** `/dev/ttyUSB0` (QMI/control), `/dev/ttyUSB1` (diagnostics), `/dev/ttyUSB2` (AT commands), `/dev/cdc-wdm0` (QMI)
- **Modem daemon:** `/usr/bin/uiwwand` (manages SIM state, modem profiles, LTE connection)
- **SSH server:** dropbear (present but not running by default)

## Accessing the Debug Terminal

Unlike most UniFi devices, the LTE Backup Pro **does not expose standard SSH credentials** in the UniFi Network Controller UI. Instead, it provides a browser-based debug terminal.

1. Open **UniFi Network Controller**
2. Navigate to **Devices** > **LTE Backup Pro**
3. Click **Debug** (opens a WebRTC terminal session in the browser)

You should see a shell prompt:

```
ubnt@u-lte-pro:~$
```

or:

```
root@u-lte-pro:~#
```

The debug terminal has root-level access - you typically don't need to escalate privileges.

## Enabling SSH Access

The debug terminal is useful for one-off commands, but for persistent access (and for tools like `scp`), you need proper SSH via dropbear.

Note: The device runs dropbear which does not support `scp`/`sftp`. For file transfers, use `ssh` with `cat` piping instead:

```bash
# Upload a file to the device
ssh user@<device-ip> 'cat > /tmp/remote-file' < local-file

# Download a file from the device
ssh user@<device-ip> 'cat /tmp/remote-file' > local-file
```

### Prerequisites

- Access to the debug terminal (see above)
- Your SSH public key (e.g. `~/.ssh/id_ed25519.pub`)

### Steps

**1. Add your public key to dropbear's config directory:**

From the debug terminal:

```bash
echo "ssh-ed25519 AAAA... your-public-key" > /etc/dropbear/authorized_keys
chmod 600 /etc/dropbear/authorized_keys
```

**2. Start dropbear:**

```bash
dropbear -R
```

**3. Connect from your local machine:**

```bash
ssh -i ~/.ssh/your_key <username>@<device-ip>
```

### Important notes

- **Do not put `authorized_keys` in `/.ssh/`** - the root filesystem (`/`) is world-writable (`drwxrwxrwt`), which causes dropbear to reject pubkey auth for security reasons. Use `/etc/dropbear/` instead.
- `/etc/dropbear/` is on the persistent partition, but SSH config **may be reset on firmware update or reprovision**.
- The `$HOME` for the SSH user is `/` (not `/etc/persistent` where `~` resolves in the shell), which is why `/.ssh/authorized_keys` does not work.
- After a device reboot, you may need to start dropbear again from the debug terminal (`dropbear -R`).

## Automating via py-unifiapi

The manual steps above can be automated using [py-unifiapi](https://github.com/seidnerj/py-unifiapi), a Python library that accesses UniFi devices via the controller's WebRTC data channel - the same protocol used by the browser debug terminal.

> **Note:** py-unifiapi is not yet published to PyPI. Install from source for now (see below).

A ready-made script is provided at [`scripts/setup-lte-pro-ssh.py`](../../scripts/setup-lte-pro-ssh.py). It connects to the device through the controller, injects your SSH public key, and starts dropbear - all in one command:

```bash
pip install py-unifiapi  # once published, or: pip install git+https://github.com/seidnerj/py-unifiapi.git

python scripts/setup-lte-pro-ssh.py \
    --host <controller-ip> \
    --username admin \
    --password <controller-password> \
    --mac <device-mac> \
    --pubkey ~/.ssh/id_ed25519.pub
```

This is especially useful for re-enabling SSH after a device reboot (when dropbear needs to be restarted) without manually opening the browser debug terminal.

### Ad-hoc commands via CLI

You can also run arbitrary commands on the device using the `pyunifiapi` CLI:

```bash
# One-shot command
pyunifiapi ssh --host <controller-ip> --username admin --password <pass> --mac <device-mac> -c "dropbear -R"

# Interactive shell (stdin/stdout piped through WebRTC)
pyunifiapi ssh --host <controller-ip> --username admin --password <pass> --mac <device-mac>
```

### Python API

```python
import asyncio
from pyunifiapi import UnifiClient, ControllerConfig, SSHConfig

async def enable_ssh():
    config = ControllerConfig(
        host='<controller-ip>',
        username='admin',
        password='<pass>',
    )
    async with UnifiClient(config) as client:
        ssh_config = SSHConfig(mac='<device-mac>')
        async with client.ssh(ssh_config) as ssh:
            # Add your public key
            pubkey = 'ssh-ed25519 AAAA... your-key'
            await ssh.execute(f'echo "{pubkey}" > /etc/dropbear/authorized_keys')
            await ssh.execute('chmod 600 /etc/dropbear/authorized_keys')

            # Start dropbear
            output = await ssh.execute('dropbear -R')
            print(output)

asyncio.run(enable_ssh())
```

## Verifying the Device

Once you have shell access, confirm the environment:

```bash
# Check OS
uname -a
cat /etc/os-release

# Check modem interfaces
ls /dev/ttyUSB*
# Expected: /dev/ttyUSB0 /dev/ttyUSB1 /dev/ttyUSB2

# Verify QMI interface
ls /dev/cdc-wdm0

# Identify the modem
echo -e "ATI\r" > /dev/ttyUSB2
cat /dev/ttyUSB2
# Expected: Manufacturer: Sierra Wireless, Model: RC7611 (or WP7607)

# Check modem control daemon
ps | grep ww
# Expected: uiwwand
```

### USB device details

```bash
cat /sys/kernel/debug/usb/devices
```

Look for:

```
Vendor=1199 ProdID=68c0
Product=Sierra Wireless WP7607
Driver=GobiSerial    (ttyUSB ports)
Driver=qmi_wwan      (cdc-wdm0)
```

### Port mapping (Sierra Wireless WP7607)

| Port | Function |
|------|----------|
| `/dev/ttyUSB0` | QMI / control |
| `/dev/ttyUSB1` | Diagnostics / NMEA |
| `/dev/ttyUSB2` | AT commands |
| `/dev/cdc-wdm0` | QMI (used by qmicli) |

### QMI services

The modem advertises these QMI services (via `qmicli --get-service-version-info`):

| Service | Version | Notes |
|---------|---------|-------|
| wms | 1.10 | Wireless Messaging Service (CBS lives here) |
| cat2 | 2.24 | Cellular Alert Technology (ETWS/CMAS) |
| nas | 1.25 | Network Access Service |
| dms | 1.0 | Device Management |
| ... | ... | 30+ services total |

The presence of **cat2 (2.24)** confirms the modem supports the 3GPP public warning system interface used for ETWS/CMAS alerts. The stock `qmicli` on the device (v1.30.2) does not include CBS monitoring commands - this is why a [patched build](https://github.com/seidnerj/qmicli-cbs) is needed.

## On-device tools

The device ships with a minimal set of QMI tools:

```
/usr/bin/qmicli           (v1.30.2 - lacks CBS commands)
/usr/bin/qmi-network
/usr/bin/qmi-firmware-update
/usr/lib/qmi-proxy        (allows shared modem access)
```

The stock qmicli has no WMS monitoring, event reporting, or broadcast activation commands. See the [CBS integration docs](CBS.md) for building a patched version.

## Useful commands

```bash
# Check modem logs
logread

# Check LTE status
dmesg | grep -i lte

# Check network interfaces
ip a
# Look for wwan0 (LTE data interface)

# Check running processes
ps | grep ww
# uiwwand manages the modem - do not kill permanently

# Query modem via AT commands
echo -e "AT+CSQ\r" > /dev/ttyUSB2 && cat /dev/ttyUSB2   # Signal strength
echo -e "AT+COPS?\r" > /dev/ttyUSB2 && cat /dev/ttyUSB2  # Operator
echo -e "AT+CSCB?\r" > /dev/ttyUSB2 && cat /dev/ttyUSB2  # CBS config
```

## Warnings

- **Do not kill `uiwwand` permanently** - it manages the LTE connection. It may automatically restart via watchdog.
- **Do not write to flash partitions** (`/dev/mtd*`, `/dev/mmc*`) - this can brick the device.
- **Do not run Sierra engineering commands** (`AT!ENTERCND`, `AT!IMPREF`, `AT!RESET`, `AT!NV*`, `AT!IMAGE*`, `AT!BOOT*`) - these can change persistent modem configuration.
- AT query commands (`ATI`, `AT+CSCB?`, `AT+CLAC`) and runtime toggles (`AT+CSCB=0`, `AT+CNMI=...`) are safe - they do not persist after reboot.
