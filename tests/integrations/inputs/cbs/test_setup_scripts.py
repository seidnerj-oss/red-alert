"""Tests for the CBS setup scripts (fakes only, no network)."""

import importlib.util
import sys
import types
from dataclasses import dataclass
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[4] / 'scripts'
PUBKEY = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAItest test@host'


def _load_script(name: str) -> types.ModuleType:
    path = SCRIPTS_DIR / name
    spec = importlib.util.spec_from_file_location(f'_script_{path.stem.replace("-", "_")}', path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@dataclass
class FakeControllerConfig:
    host: str
    username: str
    password: str
    port: int
    site: str
    totp_secret: str | None


@dataclass
class FakeCloudConfig:
    username: str
    password: str
    device_id: str
    totp_secret: str
    site: str


@pytest.fixture
def no_pyunifiapi(monkeypatch):
    monkeypatch.setitem(sys.modules, 'pyunifiapi', None)


@pytest.fixture
def fake_pyunifiapi(monkeypatch):
    module = types.ModuleType('pyunifiapi')
    module.ControllerConfig = FakeControllerConfig  # type: ignore[attr-defined]
    module.CloudConfig = FakeCloudConfig  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, 'pyunifiapi', module)
    return module


class TestImportWithoutOptionalDependencies:
    @pytest.mark.parametrize('script', ['setup-cbs.py', 'setup-lte-pro-ssh.py'])
    def test_scripts_import_without_pyunifiapi(self, no_pyunifiapi, script):
        _load_script(script)


class TestSetupCbs:
    def test_socat_lands_where_the_bridge_reads_it(self):
        from red_alert.integrations.inputs.cbs.bridge import SOCAT_DEVICE_BINARY

        assert _load_script('setup-cbs.py').SOCAT_DEVICE_BINARY == Path(SOCAT_DEVICE_BINARY)


class TestSetupLteProSsh:
    def test_read_pubkey_strips(self, tmp_path):
        path = tmp_path / 'id.pub'
        path.write_text(PUBKEY + '\n')
        assert _load_script('setup-lte-pro-ssh.py').read_pubkey(path) == PUBKEY

    def test_read_pubkey_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            _load_script('setup-lte-pro-ssh.py').read_pubkey(tmp_path / 'nope.pub')

    def test_read_pubkey_rejects_non_key(self, tmp_path):
        path = tmp_path / 'id.pub'
        path.write_text('not a key')
        with pytest.raises(ValueError):
            _load_script('setup-lte-pro-ssh.py').read_pubkey(path)

    def test_direct_controller_config(self, fake_pyunifiapi):
        script = _load_script('setup-lte-pro-ssh.py')
        config = script.build_controller_config(host='ctrl', device_id=None, username='u', password='p', port=8443, site='s', totp_secret='T')
        assert config == FakeControllerConfig(host='ctrl', username='u', password='p', port=8443, site='s', totp_secret='T')

    def test_cloud_controller_config(self, fake_pyunifiapi):
        script = _load_script('setup-lte-pro-ssh.py')
        config = script.build_controller_config(host=None, device_id='dev', username='u', password='p', port=443, site='default', totp_secret='T')
        assert isinstance(config, FakeCloudConfig)

    def test_cloud_requires_totp(self, fake_pyunifiapi):
        script = _load_script('setup-lte-pro-ssh.py')
        with pytest.raises(ValueError, match='totp_secret'):
            script.build_controller_config(host=None, device_id='dev', username='u', password='p', port=443, site='default', totp_secret=None)

    def test_requires_host_or_device_id(self, fake_pyunifiapi):
        script = _load_script('setup-lte-pro-ssh.py')
        with pytest.raises(ValueError, match='host'):
            script.build_controller_config(host=None, device_id=None, username='u', password='p', port=443, site='default', totp_secret=None)

    def test_controller_config_needs_pyunifiapi(self, no_pyunifiapi):
        script = _load_script('setup-lte-pro-ssh.py')
        with pytest.raises(ImportError):
            script.build_controller_config(host='ctrl', device_id=None, username='u', password='p', port=443, site='default', totp_secret=None)
