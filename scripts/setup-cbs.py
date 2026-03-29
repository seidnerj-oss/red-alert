#!/usr/bin/env python3
"""CBS bridge infrastructure provisioning script.

Builds and positions all binaries needed for the CBS socat bridge, and enables
SSH on the LTE device. Does NOT start any services or configure CBS channels -
that's handled at runtime by CbsBridge.

This is designed for a remote QMI modem setup where qmicli runs on a local
monitoring host (Raspberry Pi, Mac, etc.) and communicates with the LTE device's
qmi-proxy over a socat TCP bridge.

Architecture:
    LTE Device (<lte-host>)                   Monitoring Host (Pi, Mac, etc.)
      qmi-proxy (stock, always running)         socat (apt/brew, persistent)
           |                                         |
      socat (MIPS, deployed via SSH)            ABSTRACT-LISTEN:qmi-proxy
      TCP-LISTEN:18222 <----network---->        TCP:<lte-host>:18222
      ABSTRACT-CONNECT:qmi-proxy                     |
                                                qmicli --wms-monitor (aarch64/darwin)
                                                  local subprocess
                                                     |
                                                CbsAlertMonitor (Python)

Requirements:
    pip install asyncssh pyunifiapi

Usage:
    # Full setup (all steps):
    python scripts/setup-cbs.py \\
        --lte-host <lte-device-ip> \\
        --controller-host <controller-ip> \\
        --controller-username admin \\
        --controller-password <pass> \\
        --device-mac <lte-device-mac> \\
        --ssh-key ~/.ssh/id_ed25519

    # Individual steps:
    python scripts/setup-cbs.py --build-only
    python scripts/setup-cbs.py --setup-host-only
    python scripts/setup-cbs.py --enable-ssh-only   # re-run after LTE device reboot
    python scripts/setup-cbs.py --deploy-lte-only   # deploy socat binary to LTE device
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import io
import logging
import os
import platform
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import asyncssh
import httpx

from red_alert.integrations.inputs.cbs import lte_ssh

logger = logging.getLogger('setup-cbs')

SOCAT_IPK_URL = 'https://downloads.openwrt.org/releases/17.01.6/packages/mips_24kc/packages/socat_1.7.3.1-1_mips_24kc.ipk'
SOCAT_IPK_SHA256 = None  # Set after first verified download

SOCAT_REMOTE_PATH = '/tmp/socat'

CACHE_DIR = Path.home() / '.cache' / 'red-alert'
QMICLI_CBS_REPO = 'https://github.com/seidnerj/qmicli-cbs.git'

VALID_ARCHS = ('aarch64', 'darwin', 'mips')


def _get_qmicli_binary_name(arch: str | None = None) -> str:
    if arch:
        if arch not in VALID_ARCHS:
            raise ValueError(f'Unknown arch: {arch}. Valid options: {", ".join(VALID_ARCHS)}')
        return f'qmicli-{arch}'
    system = platform.system().lower()
    machine = platform.machine().lower()
    if machine in ('aarch64', 'arm64'):
        return 'qmicli-aarch64'
    if system == 'darwin':
        return 'qmicli-darwin'
    raise ValueError(f'Unsupported platform: {system}/{machine}. Use --arch to specify a target explicitly. Valid options: {", ".join(VALID_ARCHS)}')


# ---------- Step a: Build qmicli ----------


def _ensure_build_prerequisites(arch: str | None) -> None:
    """Ensure build tools are installed for the target platform."""
    resolved_arch = arch or _get_qmicli_binary_name().removeprefix('qmicli-')
    system = platform.system().lower()

    if resolved_arch in ('mips', 'aarch64'):
        if not shutil.which('docker'):
            raise RuntimeError('Docker is required for cross-compilation. Install Docker Desktop: https://www.docker.com/products/docker-desktop/')

    if resolved_arch == 'darwin' or system == 'darwin':
        missing = [tool for tool in ('meson', 'ninja') if not shutil.which(tool)]
        if missing:
            if system == 'darwin' and shutil.which('brew'):
                logger.info('Installing missing build tools via brew: %s', ', '.join(missing))
                subprocess.run(['brew', 'install'] + missing, check=True)
            else:
                raise RuntimeError(f'Missing build tools: {", ".join(missing)}. Install with: brew install meson ninja')


def build_qmicli(qmicli_cbs_path: Path | None = None, arch: str | None = None) -> Path:
    """Build qmicli-cbs via Docker cross-compilation.

    Args:
        qmicli_cbs_path: Path to existing qmicli-cbs repo. If None, clones from GitHub.
        arch: Target architecture override (aarch64, darwin, mips). Auto-detected if None.

    Returns:
        Path to the built qmicli binary for the target platform.
    """
    _ensure_build_prerequisites(arch)

    if qmicli_cbs_path is None:
        qmicli_cbs_path = CACHE_DIR / 'qmicli-cbs'
        if not qmicli_cbs_path.exists():
            logger.info('Cloning qmicli-cbs from %s...', QMICLI_CBS_REPO)
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            subprocess.run(['git', 'clone', QMICLI_CBS_REPO, str(qmicli_cbs_path)], check=True)
        else:
            logger.info('Updating qmicli-cbs at %s...', qmicli_cbs_path)
            subprocess.run(['git', '-C', str(qmicli_cbs_path), 'pull', '--ff-only'], check=True)

    build_script = qmicli_cbs_path / 'build.sh'
    if not build_script.exists():
        raise FileNotFoundError(f'build.sh not found at {build_script}')

    binary_name = _get_qmicli_binary_name(arch)
    output_path = qmicli_cbs_path / 'output' / binary_name

    if output_path.exists():
        logger.info('qmicli binary already built: %s', output_path)
        return output_path

    logger.info('Building qmicli-cbs (Docker cross-compilation)...')
    subprocess.run(['bash', str(build_script)], cwd=str(qmicli_cbs_path), check=True)

    if not output_path.exists():
        available = list((qmicli_cbs_path / 'output').glob('qmicli-*'))
        raise FileNotFoundError(f'Expected {output_path} after build. Available: {[p.name for p in available]}')

    logger.info('qmicli built successfully: %s', output_path)
    return output_path


# ---------- Step b: Download socat for LTE device ----------


def download_socat_mips() -> Path:
    """Download and extract the socat MIPS binary for the LTE device.

    Downloads the socat ipk from the LEDE 17.01.6 package repository,
    extracts the MIPS binary, and caches it locally.

    Returns:
        Path to the extracted socat MIPS binary.
    """
    cached = CACHE_DIR / 'socat-mips'
    if cached.exists():
        logger.info('Using cached socat MIPS binary: %s', cached)
        return cached

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    ipk_path = CACHE_DIR / 'socat_mips_24kc.ipk'

    logger.info('Downloading socat MIPS binary from %s...', SOCAT_IPK_URL)
    try:
        with httpx.Client(timeout=60.0, follow_redirects=True) as client:
            resp = client.get(SOCAT_IPK_URL)
            resp.raise_for_status()
            ipk_path.write_bytes(resp.content)
    except httpx.HTTPError as e:
        print(
            f'\nFailed to download socat: {e}\n\n'
            f'If the LEDE URL is unavailable, manually download or build:\n'
            f'  Package: socat_1.7.3.1-1_mips_24kc.ipk\n'
            f'  URL: {SOCAT_IPK_URL}\n'
            f'  Any socat 1.7.x build for mips_24kc musl should work.\n'
            f'  Place the extracted binary at: {cached}\n',
            file=sys.stderr,
        )
        raise

    if SOCAT_IPK_SHA256:
        sha256 = hashlib.sha256(ipk_path.read_bytes()).hexdigest()
        if sha256 != SOCAT_IPK_SHA256:
            ipk_path.unlink()
            raise ValueError(f'SHA256 mismatch: expected {SOCAT_IPK_SHA256}, got {sha256}')
        logger.info('SHA256 checksum verified')

    socat_binary = _extract_socat_from_ipk(ipk_path)
    shutil.copy2(socat_binary, cached)
    os.chmod(cached, 0o755)
    ipk_path.unlink(missing_ok=True)

    logger.info('socat MIPS binary cached at: %s', cached)
    return cached


def _extract_socat_from_ipk(ipk_path: Path) -> Path:
    """Extract the socat binary from an ipk (ar archive containing data.tar.gz)."""
    extract_dir = CACHE_DIR / 'socat-extract'
    extract_dir.mkdir(parents=True, exist_ok=True)

    subprocess.run(['ar', 'x', str(ipk_path)], cwd=str(extract_dir), check=True)

    data_tar_gz = extract_dir / 'data.tar.gz'
    if not data_tar_gz.exists():
        raise FileNotFoundError(f'data.tar.gz not found in ipk archive at {extract_dir}')

    with gzip.open(data_tar_gz, 'rb') as gz:
        with tarfile.open(fileobj=io.BytesIO(gz.read())) as tar:
            socat_members = [m for m in tar.getmembers() if m.name.endswith('/socat') or m.name == 'socat']
            if not socat_members:
                all_members = [m.name for m in tar.getmembers()]
                raise FileNotFoundError(f'socat binary not found in data.tar.gz. Contents: {all_members}')

            tar.extract(socat_members[0], path=str(extract_dir))
            extracted_path = extract_dir / socat_members[0].name

    for f in extract_dir.iterdir():
        if f != extracted_path and f.is_file():
            f.unlink(missing_ok=True)

    return extracted_path


# ---------- Step c: Monitoring host setup ----------


def setup_monitoring_host(qmicli_binary: Path, install_path: str = '/usr/local/bin/qmicli-cbs') -> None:
    """Set up the local monitoring host.

    - Checks that socat is installed (apt/brew)
    - Creates dummy /dev/cdc-wdm0 if not present (Linux only)
    - Deploys qmicli to install_path

    NOTE: Some steps require sudo. The script will print instructions for
    commands that need elevated privileges rather than running sudo directly.
    """
    if not shutil.which('socat'):
        system = platform.system().lower()
        if system == 'linux':
            print('socat is not installed. Run: sudo apt install socat')
        elif system == 'darwin':
            print('socat is not installed. Run: brew install socat')
        else:
            print('socat is not installed. Please install socat for your platform.')
        sys.exit(1)
    logger.info('socat is installed')

    if platform.system().lower() == 'linux':
        dev_path = Path('/dev/cdc-wdm0')
        if not dev_path.exists():
            print(f'{dev_path} does not exist. Run: sudo mknod {dev_path} c 180 176')
            print('This creates a dummy character device for qmicli to open.')

    install = Path(install_path)
    if install.exists():
        logger.info('qmicli already deployed at %s', install)
    else:
        print(f'Deploy qmicli to {install_path}:')
        print(f'  sudo cp {qmicli_binary} {install_path}')
        print(f'  sudo chmod +x {install_path}')


DEFAULT_SSH_KEY_PATH = Path.home() / '.ssh' / 'id_ed25519_lte'


def ensure_ssh_keypair(key_path: Path | None = None) -> Path:
    """Generate an SSH key pair for LTE device access if one doesn't exist.

    Args:
        key_path: Path for the private key. Defaults to ~/.ssh/id_ed25519_lte.

    Returns:
        Path to the private key file.
    """
    key_path = key_path or DEFAULT_SSH_KEY_PATH

    if key_path.exists():
        logger.info('SSH key already exists: %s', key_path)
        return key_path

    key_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info('Generating SSH key pair for LTE device access: %s', key_path)
    subprocess.run(
        ['ssh-keygen', '-t', 'ed25519', '-f', str(key_path), '-N', '', '-C', f'{key_path.stem}@{platform.node()}'],
        check=True,
    )
    logger.info('SSH key pair generated: %s (public: %s.pub)', key_path, key_path)
    return key_path


# ---------- Step d: Enable SSH on LTE device ----------


async def enable_lte_ssh(
    controller_host: str | None,
    controller_device_id: str | None,
    controller_username: str,
    controller_password: str,
    controller_port: int,
    controller_site: str,
    totp_secret: str | None,
    device_mac: str,
    ssh_pubkey_path: str,
) -> None:
    """Enable SSH on the LTE device via the UniFi controller WebRTC debug terminal.

    Reuses the setup-lte-pro-ssh.py script's setup_ssh() function.
    Must be re-run after every LTE device reboot (dropbear doesn't persist).
    """
    pubkey = lte_ssh.read_pubkey(ssh_pubkey_path)

    controller_config = lte_ssh.build_controller_config(
        host=controller_host,
        device_id=controller_device_id,
        username=controller_username,
        password=controller_password,
        port=controller_port,
        site=controller_site,
        totp_secret=totp_secret,
    )

    logger.info('Enabling SSH on LTE device %s via controller...', device_mac)
    await lte_ssh.enable_ssh(controller_config, device_mac, pubkey)
    logger.info('SSH enabled on LTE device')


# ---------- Step e: Deploy socat to LTE device ----------


async def deploy_socat_to_lte(
    lte_host: str,
    socat_binary: Path,
    lte_device_ssh_key_path: str | None = None,
    ssh_username: str = 'root',
) -> None:
    """Deploy the socat binary to the LTE device via SSH.

    Only deploys the binary - does NOT start it. The CbsBridge runtime
    handles starting and managing the socat bridge process.

    Args:
        lte_host: LTE device hostname or IP.
        socat_binary: Path to the local socat MIPS binary to deploy.
        lte_device_ssh_key_path: Path to SSH private key.
        ssh_username: SSH username on the LTE device.
    """
    ssh_opts: dict = {
        'host': lte_host,
        'username': ssh_username,
        'known_hosts': None,
    }
    if lte_device_ssh_key_path:
        ssh_opts['client_keys'] = [lte_device_ssh_key_path]

    async with asyncssh.connect(**ssh_opts) as conn:
        logger.info('Connected to LTE device at %s via SSH', lte_host)

        result = await conn.run(f'test -x {SOCAT_REMOTE_PATH} && echo exists')
        if result.stdout and 'exists' in str(result.stdout):
            logger.info('socat already present on LTE device at %s', SOCAT_REMOTE_PATH)
            return

        logger.info('Deploying socat to LTE device via SCP...')
        await asyncssh.scp(str(socat_binary), (conn, SOCAT_REMOTE_PATH))
        await conn.run(f'chmod +x {SOCAT_REMOTE_PATH}')
        logger.info('socat deployed to %s', SOCAT_REMOTE_PATH)


# ---------- CLI ----------


def main() -> None:
    parser = argparse.ArgumentParser(
        description='CBS bridge infrastructure provisioning - build, deploy, and position all binaries.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Examples:\n'
            '  Full setup:\n'
            '    python scripts/setup-cbs.py \\\n'
            '      --lte-host <lte-device-ip> \\\n'
            '      --controller-host <controller-ip> \\\n'
            '      --controller-username admin \\\n'
            '      --controller-password <pass> \\\n'
            '      --device-mac <mac> \\\n'
            '      --ssh-key ~/.ssh/id_ed25519\n'
            '\n'
            '  After LTE device reboot:\n'
            '    python scripts/setup-cbs.py --enable-ssh-only [controller args]\n'
            '    python scripts/setup-cbs.py --deploy-lte-only --lte-host <ip> --ssh-key ~/.ssh/id_ed25519\n'
        ),
    )

    step_group = parser.add_mutually_exclusive_group()
    step_group.add_argument('--build-only', action='store_true', help='Only build qmicli (step a)')
    step_group.add_argument('--setup-host-only', action='store_true', help='Only set up the local monitoring host (step c)')
    step_group.add_argument('--enable-ssh-only', action='store_true', help='Only enable SSH on the LTE device via controller (step d)')
    step_group.add_argument('--deploy-lte-only', action='store_true', help='Only deploy socat binary to LTE device (step e)')

    parser.add_argument('--lte-host', help='LTE device hostname or IP')
    parser.add_argument('--ssh-key', help='Path to SSH private key for LTE device (default: auto-generate ~/.ssh/id_ed25519_lte)')
    parser.add_argument('--ssh-username', default='root', help='SSH username on LTE device (default: root)')
    parser.add_argument('--ssh-pubkey', help='Path to SSH public key file (default: <ssh-key>.pub)')

    controller_group = parser.add_argument_group('controller', 'UniFi controller connection (for enabling SSH)')
    conn_group = controller_group.add_mutually_exclusive_group()
    conn_group.add_argument('--controller-host', help='Controller hostname or IP (direct)')
    conn_group.add_argument('--controller-device-id', help='Cloud controller device ID')
    controller_group.add_argument('--controller-username', help='Controller username')
    controller_group.add_argument('--controller-password', help='Controller password')
    controller_group.add_argument('--controller-port', type=int, default=443, help='Controller port (default: 443)')
    controller_group.add_argument('--controller-site', default='default', help='Controller site (default: default)')
    controller_group.add_argument('--totp-secret', help='TOTP secret for 2FA (cloud)')
    controller_group.add_argument('--device-mac', help='LTE device MAC address')

    build_group = parser.add_argument_group('build', 'Build options')
    build_group.add_argument('--qmicli-cbs-path', type=Path, help='Path to qmicli-cbs repo (default: clone from GitHub)')
    build_group.add_argument('--qmicli-install-path', default='/usr/local/bin/qmicli-cbs', help='Where to install qmicli on monitoring host')
    build_group.add_argument(
        '--arch',
        choices=VALID_ARCHS,
        default=None,
        help='Target architecture for qmicli binary (default: auto-detect from current platform)',
    )

    parser.add_argument('-v', '--verbose', action='store_true', help='Enable debug logging')

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    )

    if args.build_only:
        _step_build(args)
        return

    if args.setup_host_only:
        _step_setup_host(args)
        return

    if args.enable_ssh_only:
        _step_enable_ssh(args)
        return

    if args.deploy_lte_only:
        _step_deploy_lte(args)
        return

    _full_setup(args)


def _step_build(args) -> Path:
    logger.info('=== Step a: Build qmicli ===')
    return build_qmicli(args.qmicli_cbs_path, arch=args.arch)


def _step_download_socat() -> Path:
    logger.info('=== Step b: Download socat MIPS binary ===')
    return download_socat_mips()


def _step_setup_host(args) -> None:
    logger.info('=== Step c: Set up monitoring host ===')
    qmicli = build_qmicli(args.qmicli_cbs_path, arch=args.arch)
    setup_monitoring_host(qmicli, args.qmicli_install_path)


def _resolve_ssh_key(args) -> Path:
    """Resolve the SSH key path, generating a key pair if needed."""
    if args.ssh_key:
        key_path = Path(args.ssh_key).expanduser()
        if not key_path.exists():
            raise FileNotFoundError(f'SSH key not found: {key_path}')
        return key_path
    return ensure_ssh_keypair()


def _step_enable_ssh(args) -> None:
    logger.info('=== Step d: Enable SSH on LTE device ===')
    if not args.device_mac:
        print('Error: --device-mac is required for --enable-ssh-only', file=sys.stderr)
        sys.exit(1)
    if not args.controller_host and not args.controller_device_id:
        print('Error: --controller-host or --controller-device-id is required', file=sys.stderr)
        sys.exit(1)
    if not args.controller_username or not args.controller_password:
        print('Error: --controller-username and --controller-password are required', file=sys.stderr)
        sys.exit(1)

    ssh_key = _resolve_ssh_key(args)
    ssh_pubkey = args.ssh_pubkey or str(ssh_key) + '.pub'

    asyncio.run(
        enable_lte_ssh(
            controller_host=args.controller_host,
            controller_device_id=args.controller_device_id,
            controller_username=args.controller_username,
            controller_password=args.controller_password,
            controller_port=args.controller_port,
            controller_site=args.controller_site,
            totp_secret=args.totp_secret,
            device_mac=args.device_mac,
            ssh_pubkey_path=ssh_pubkey,
        )
    )


def _step_deploy_lte(args) -> None:
    logger.info('=== Step e: Deploy socat to LTE device ===')
    if not args.lte_host:
        print('Error: --lte-host is required for --deploy-lte-only', file=sys.stderr)
        sys.exit(1)

    socat_binary = download_socat_mips()
    ssh_key = _resolve_ssh_key(args)

    asyncio.run(
        deploy_socat_to_lte(
            lte_host=args.lte_host,
            socat_binary=socat_binary,
            lte_device_ssh_key_path=str(ssh_key),
            ssh_username=args.ssh_username,
        )
    )


def _full_setup(args) -> None:
    qmicli = _step_build(args)
    socat_binary = _step_download_socat()

    logger.info('=== Step c: Set up monitoring host ===')
    setup_monitoring_host(qmicli, args.qmicli_install_path)

    ssh_key = _resolve_ssh_key(args)

    if args.device_mac and (args.controller_host or args.controller_device_id):
        _step_enable_ssh(args)
    else:
        logger.info('Skipping SSH enable (no controller/device-mac args). Use --enable-ssh-only later.')

    if args.lte_host:
        asyncio.run(
            deploy_socat_to_lte(
                lte_host=args.lte_host,
                socat_binary=socat_binary,
                lte_device_ssh_key_path=str(ssh_key),
                ssh_username=args.ssh_username,
            )
        )
    else:
        logger.info('Skipping LTE deployment (no --lte-host). Use --deploy-lte-only later.')

    logger.info('Setup complete! Start the CBS monitor to bring up the bridge and configure channels.')
    logger.info('SSH key for LTE device access: %s', ssh_key)


if __name__ == '__main__':
    main()
