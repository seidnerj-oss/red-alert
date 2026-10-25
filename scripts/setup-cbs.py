#!/usr/bin/env python3
"""CBS bridge infrastructure provisioning script.

Builds and positions the binaries the CBS socat bridge needs. Does NOT start any
services, enable SSH on the LTE device or configure CBS channels - that is handled
at runtime by CbsBridge, whose lte-pro-access supervisor re-enables SSH through the
UniFi controller when the device refuses it and uploads the MIPS socat when the
device copy is missing or differs.

This is designed for a remote QMI modem setup where qmicli runs on a local
monitoring host (Raspberry Pi, Mac, etc.) and communicates with the LTE device's
qmi-proxy over a socat TCP bridge.

Architecture:
    LTE Device (<lte-host>)                   Monitoring Host (Pi, Mac, etc.)
      qmi-proxy (stock, always running)         socat (apt/brew)
           |                                         |
      socat (MIPS, uploaded via SSH)            ABSTRACT-LISTEN:qmi-proxy
      TCP-LISTEN:18222 <----network---->        TCP:<lte-host>:18222
      ABSTRACT-CONNECT:qmi-proxy                     |
                                                qmicli --wms-monitor (aarch64/darwin)
                                                  local subprocess
                                                     |
                                                CbsAlertMonitor (Python)

Usage:
    # Full setup (all steps):
    python scripts/setup-cbs.py

    # Individual steps:
    python scripts/setup-cbs.py --build-only
    python scripts/setup-cbs.py --download-socat-only   # place the MIPS socat at data/socat-mips
    python scripts/setup-cbs.py --setup-host-only
"""

from __future__ import annotations

import argparse
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

import httpx

logger = logging.getLogger('setup-cbs')

SOCAT_IPK_URL = 'https://downloads.openwrt.org/releases/17.01.6/packages/mips_24kc/packages/socat_1.7.3.1-1_mips_24kc.ipk'
SOCAT_IPK_SHA256 = None  # Set after first verified download

CACHE_DIR = Path.home() / '.cache' / 'red-alert'
# Where CbsBridge reads the MIPS socat it uploads to the LTE device.
SOCAT_DEVICE_BINARY = Path(__file__).resolve().parents[1] / 'data' / 'socat-mips'
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
    extracts the MIPS binary, and places it at data/socat-mips, where
    CbsBridge reads it.

    Returns:
        Path to the extracted socat MIPS binary.
    """
    destination = SOCAT_DEVICE_BINARY
    if destination.exists():
        logger.info('Using existing socat MIPS binary: %s', destination)
        return destination

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
            f'  Place the extracted binary at: {destination}\n',
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
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(socat_binary, destination)
    os.chmod(destination, 0o755)
    ipk_path.unlink(missing_ok=True)

    logger.info('socat MIPS binary placed at: %s', destination)
    return destination


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


# ---------- CLI ----------


def main() -> None:
    parser = argparse.ArgumentParser(
        description='CBS bridge infrastructure provisioning - build, deploy, and position all binaries.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Examples:\n'
            '  Full setup:\n'
            '    python scripts/setup-cbs.py\n'
            '\n'
            '  Only fetch the MIPS socat into data/socat-mips:\n'
            '    python scripts/setup-cbs.py --download-socat-only\n'
        ),
    )

    step_group = parser.add_mutually_exclusive_group()
    step_group.add_argument('--build-only', action='store_true', help='Only build qmicli (step a)')
    step_group.add_argument('--download-socat-only', action='store_true', help='Only place the MIPS socat at data/socat-mips (step b)')
    step_group.add_argument('--setup-host-only', action='store_true', help='Only set up the local monitoring host (step c)')

    parser.add_argument('--ssh-key', help='Path to SSH private key for LTE device (default: auto-generate ~/.ssh/id_ed25519_lte)')

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

    if args.download_socat_only:
        _step_download_socat()
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


def _full_setup(args) -> None:
    qmicli = _step_build(args)
    socat_binary = _step_download_socat()

    logger.info('=== Step c: Set up monitoring host ===')
    setup_monitoring_host(qmicli, args.qmicli_install_path)

    ssh_key = _resolve_ssh_key(args)

    logger.info('Setup complete! Start the CBS monitor to bring up the bridge and configure channels.')
    logger.info('MIPS socat for the LTE device: %s', socat_binary)
    logger.info('SSH key for LTE device access (lte_device_ssh_key_path): %s', ssh_key)


if __name__ == '__main__':
    main()
