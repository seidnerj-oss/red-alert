"""Tests for the LTE SSH re-enable helper and CbsBridge._enable_lte_ssh (fakes only, no network)."""

import importlib
import logging
import sys
import types
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from red_alert.integrations.inputs.cbs.bridge import CbsBridge

LTE_SSH_MODULE = 'red_alert.integrations.inputs.cbs.lte_ssh'
PUBKEY = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAItest test@host'


class FakeSSHSessionError(Exception):
    pass


@dataclass
class FakeControllerConfig:
    host: str
    username: str
    password: str
    port: int = 443
    site: str = 'default'
    totp_secret: str | None = None


@dataclass
class FakeCloudConfig:
    username: str
    password: str
    device_id: str
    totp_secret: str
    site: str = 'default'


@dataclass
class FakeSSHEnableResult:
    key_added: bool
    dropbear_started: bool


@pytest.fixture
def fake_pyunifiapi(monkeypatch):
    """Install a fake pyunifiapi (0.2.0 surface) and import lte_ssh fresh against it."""
    module = types.ModuleType('pyunifiapi')
    module.ControllerConfig = FakeControllerConfig
    module.CloudConfig = FakeCloudConfig
    module.SSHEnableResult = FakeSSHEnableResult
    module.SSHSessionError = FakeSSHSessionError
    module.enable_ssh = AsyncMock(return_value=FakeSSHEnableResult(key_added=True, dropbear_started=True))
    monkeypatch.setitem(sys.modules, 'pyunifiapi', module)
    monkeypatch.delitem(sys.modules, LTE_SSH_MODULE, raising=False)
    lte_ssh = importlib.import_module(LTE_SSH_MODULE)
    yield module, lte_ssh
    sys.modules.pop(LTE_SSH_MODULE, None)


class TestEnableSsh:
    @pytest.mark.asyncio
    async def test_delegates_to_pyunifiapi_and_returns_result(self, fake_pyunifiapi):
        module, lte_ssh = fake_pyunifiapi
        config = FakeControllerConfig(host='ctrl', username='u', password='p')

        result = await lte_ssh.enable_ssh(config, 'aa:bb:cc:dd:ee:ff', PUBKEY)

        module.enable_ssh.assert_awaited_once_with(config, 'aa:bb:cc:dd:ee:ff', PUBKEY)
        assert result == FakeSSHEnableResult(key_added=True, dropbear_started=True)

    @pytest.mark.asyncio
    async def test_logs_what_changed(self, fake_pyunifiapi, caplog):
        module, lte_ssh = fake_pyunifiapi
        module.enable_ssh.return_value = FakeSSHEnableResult(key_added=False, dropbear_started=True)

        with caplog.at_level(logging.INFO, logger='red_alert.cbs.lte_ssh'):
            await lte_ssh.enable_ssh(FakeControllerConfig(host='h', username='u', password='p'), 'aa:bb', PUBKEY)

        assert 'key_added=False' in caplog.text
        assert 'dropbear_started=True' in caplog.text

    @pytest.mark.asyncio
    async def test_propagates_value_error_and_session_error(self, fake_pyunifiapi):
        module, lte_ssh = fake_pyunifiapi
        config = FakeControllerConfig(host='h', username='u', password='p')

        module.enable_ssh.side_effect = ValueError('bad key')
        with pytest.raises(ValueError):
            await lte_ssh.enable_ssh(config, 'aa:bb', 'nonsense')

        module.enable_ssh.side_effect = FakeSSHSessionError('dropbear not running')
        with pytest.raises(FakeSSHSessionError):
            await lte_ssh.enable_ssh(config, 'aa:bb', PUBKEY)


class TestBuildControllerConfig:
    def test_direct(self, fake_pyunifiapi):
        _, lte_ssh = fake_pyunifiapi
        config = lte_ssh.build_controller_config(host='ctrl', username='u', password='p', port=8443, site='s', totp_secret='T')
        assert config == FakeControllerConfig(host='ctrl', username='u', password='p', port=8443, site='s', totp_secret='T')

    def test_cloud_requires_totp(self, fake_pyunifiapi):
        _, lte_ssh = fake_pyunifiapi
        with pytest.raises(ValueError, match='totp_secret'):
            lte_ssh.build_controller_config(device_id='dev', username='u', password='p')

    def test_cloud(self, fake_pyunifiapi):
        _, lte_ssh = fake_pyunifiapi
        config = lte_ssh.build_controller_config(device_id='dev', username='u', password='p', totp_secret='T')
        assert isinstance(config, FakeCloudConfig)

    def test_requires_host_or_device_id(self, fake_pyunifiapi):
        _, lte_ssh = fake_pyunifiapi
        with pytest.raises(ValueError, match='host'):
            lte_ssh.build_controller_config(username='u', password='p')


class TestReadPubkey:
    def test_reads_and_strips(self, fake_pyunifiapi, tmp_path):
        _, lte_ssh = fake_pyunifiapi
        path = tmp_path / 'id.pub'
        path.write_text(PUBKEY + '\n')
        assert lte_ssh.read_pubkey(path) == PUBKEY

    def test_missing_file(self, fake_pyunifiapi, tmp_path):
        _, lte_ssh = fake_pyunifiapi
        with pytest.raises(FileNotFoundError):
            lte_ssh.read_pubkey(tmp_path / 'nope.pub')

    def test_rejects_non_key(self, fake_pyunifiapi, tmp_path):
        _, lte_ssh = fake_pyunifiapi
        path = tmp_path / 'id.pub'
        path.write_text('not a key')
        with pytest.raises(ValueError):
            lte_ssh.read_pubkey(path)


@pytest.fixture
def bridge_with_unifi(tmp_path):
    key_path = tmp_path / 'id_ed25519'
    (tmp_path / 'id_ed25519.pub').write_text(PUBKEY + '\n')
    return CbsBridge(
        lte_host='192.168.1.100',
        lte_device_ssh_key_path=str(key_path),
        lte_device_mac='aa:bb:cc:dd:ee:ff',
        unifi={'host': 'ctrl', 'username': 'u', 'password': 'p', 'port': 8443, 'site': 'main', 'totp_secret': 'T'},
    )


@pytest.fixture(autouse=False)
def no_sleep(monkeypatch):
    monkeypatch.setattr('red_alert.integrations.inputs.cbs.bridge.asyncio.sleep', AsyncMock())


class TestBridgeEnableLteSsh:
    @pytest.mark.asyncio
    async def test_success_passes_config_mac_and_key(self, fake_pyunifiapi, bridge_with_unifi, no_sleep, caplog):
        module, _ = fake_pyunifiapi

        with caplog.at_level(logging.INFO, logger='red_alert.cbs.bridge'):
            assert await bridge_with_unifi._enable_lte_ssh() is True

        config, mac, key = module.enable_ssh.await_args.args
        assert config == FakeControllerConfig(host='ctrl', username='u', password='p', port=8443, site='main', totp_secret='T')
        assert (mac, key) == ('aa:bb:cc:dd:ee:ff', PUBKEY)
        assert 'key_added=True' in caplog.text
        assert 'dropbear_started=True' in caplog.text

    @pytest.mark.asyncio
    async def test_session_error_is_a_failure(self, fake_pyunifiapi, bridge_with_unifi, no_sleep, caplog):
        module, _ = fake_pyunifiapi
        module.enable_ssh.side_effect = FakeSSHSessionError('dropbear still not running')

        assert await bridge_with_unifi._enable_lte_ssh() is False
        assert 'dropbear not running' in caplog.text

    @pytest.mark.asyncio
    async def test_malformed_key_is_a_failure(self, fake_pyunifiapi, bridge_with_unifi, no_sleep):
        module, _ = fake_pyunifiapi
        module.enable_ssh.side_effect = ValueError('bad key')

        assert await bridge_with_unifi._enable_lte_ssh() is False

    @pytest.mark.asyncio
    async def test_missing_pyunifiapi_is_a_failure(self, monkeypatch, bridge_with_unifi, no_sleep):
        monkeypatch.setitem(sys.modules, 'pyunifiapi', None)
        monkeypatch.delitem(sys.modules, LTE_SSH_MODULE, raising=False)

        assert await bridge_with_unifi._enable_lte_ssh() is False

    @pytest.mark.asyncio
    async def test_missing_unifi_config_skips_without_calling(self, fake_pyunifiapi):
        module, _ = fake_pyunifiapi
        bridge = CbsBridge(lte_host='192.168.1.100', lte_device_ssh_key_path='/k', lte_device_mac='aa:bb')

        assert await bridge._enable_lte_ssh() is False
        module.enable_ssh.assert_not_called()
