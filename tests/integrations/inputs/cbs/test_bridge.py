"""Tests for CBS socat bridge management."""

import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from red_alert.integrations.inputs.cbs import bridge as bridge_module
from red_alert.integrations.inputs.cbs.bridge import (
    FRESH_START_DEVICE_COMMAND,
    SELF_MANAGED_REQUIRED_KEYS,
    CbsBridge,
    build_supervisor,
)

pytest.importorskip('lte_pro_access')


@dataclass
class FakeTickResult:
    actions: list[str]
    relays_up: bool


@dataclass
class FakeSupervisor:
    """Records calls in order; each tick pops the next scripted result (or raises it)."""

    results: list = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    relays_up: bool = False
    closed: bool = False

    async def tick(self) -> FakeTickResult:
        self.calls.append('tick')
        outcome = self.results.pop(0)
        if isinstance(outcome, BaseException):
            self.relays_up = False
            raise outcome
        self.relays_up = outcome.relays_up
        return outcome

    async def run_on_device(self, command: str) -> str:
        self.calls.append(f'run:{command}')
        return ''

    def close(self) -> None:
        self.closed = True


UP = FakeTickResult(actions=[], relays_up=True)
REPAIRED = FakeTickResult(actions=['started-qmi-relay'], relays_up=True)
DOWN = FakeTickResult(actions=['failed-qmi-relay'], relays_up=False)


def _self_bridge(supervisor: FakeSupervisor) -> CbsBridge:
    return CbsBridge(lte_host='192.168.1.100', bridge_port=18222, device='/dev/cdc-wdm0', management='self', supervisor=supervisor)  # type: ignore[arg-type]


def _external_bridge(lte_host: str = '192.168.1.100', bridge_port: int = 18222) -> CbsBridge:
    return CbsBridge(lte_host=lte_host, bridge_port=bridge_port, device='/dev/cdc-wdm0', management='external', supervisor=None)


class TestCbsBridgeInit:
    def test_values(self):
        bridge = CbsBridge(lte_host='10.0.0.1', bridge_port=9999, device='/dev/cdc-wdm1', management='self', supervisor=FakeSupervisor())  # type: ignore[arg-type]
        assert bridge.lte_host == '10.0.0.1'
        assert bridge.bridge_port == 9999
        assert bridge.device == '/dev/cdc-wdm1'
        assert bridge.management == 'self'

    def test_self_requires_a_supervisor(self):
        with pytest.raises(ValueError, match='supervisor'):
            CbsBridge(lte_host='192.168.1.100', bridge_port=18222, device='/dev/cdc-wdm0', management='self', supervisor=None)


class TestSelfEnsureBridge:
    @pytest.mark.asyncio
    async def test_first_call_kills_device_processes_then_ticks(self):
        supervisor = FakeSupervisor(results=[UP])
        bridge = _self_bridge(supervisor)

        assert await bridge.ensure_bridge() is True

        assert supervisor.calls == [f'run:{FRESH_START_DEVICE_COMMAND}', 'tick']

    def test_fresh_start_command_clears_stale_wms_registrations(self):
        assert FRESH_START_DEVICE_COMMAND.startswith('killall socat qmicli qmi-proxy')

    @pytest.mark.asyncio
    async def test_later_calls_only_tick(self):
        supervisor = FakeSupervisor(results=[UP, UP])
        bridge = _self_bridge(supervisor)

        assert await bridge.ensure_bridge() is True
        supervisor.calls.clear()
        assert await bridge.ensure_bridge() is True

        assert supervisor.calls == ['tick']

    @pytest.mark.asyncio
    async def test_request_fresh_start_kills_again(self):
        supervisor = FakeSupervisor(results=[UP, UP])
        bridge = _self_bridge(supervisor)
        await bridge.ensure_bridge()
        supervisor.calls.clear()

        bridge.request_fresh_start()
        assert await bridge.ensure_bridge() is True

        assert supervisor.calls == [f'run:{FRESH_START_DEVICE_COMMAND}', 'tick']

    @pytest.mark.asyncio
    async def test_relays_down_after_tick_is_a_failure_and_keeps_the_fresh_start(self):
        supervisor = FakeSupervisor(results=[DOWN, UP])
        bridge = _self_bridge(supervisor)

        assert await bridge.ensure_bridge() is False
        supervisor.calls.clear()
        assert await bridge.ensure_bridge() is True

        assert supervisor.calls == [f'run:{FRESH_START_DEVICE_COMMAND}', 'tick']

    @pytest.mark.asyncio
    async def test_tick_error_is_a_failure(self):
        supervisor = FakeSupervisor(results=[OSError('host key changed')])
        bridge = _self_bridge(supervisor)

        assert await bridge.ensure_bridge() is False

    @pytest.mark.asyncio
    async def test_device_command_error_is_a_failure_and_skips_the_tick(self):
        supervisor = FakeSupervisor(results=[UP])
        supervisor.run_on_device = AsyncMock(side_effect=OSError('SSH still refused'))  # type: ignore[method-assign]
        bridge = _self_bridge(supervisor)

        assert await bridge.ensure_bridge() is False
        assert 'tick' not in supervisor.calls

    @pytest.mark.asyncio
    async def test_hung_tick_times_out(self, monkeypatch):
        import lte_pro_access

        supervisor = FakeSupervisor()

        async def hang():
            await asyncio.sleep(60)

        supervisor.tick = hang  # type: ignore[method-assign]
        supervisor.run_on_device = AsyncMock(return_value='')  # type: ignore[method-assign]
        monkeypatch.setattr(lte_pro_access, 'TICK_TIMEOUT_SECONDS', 0.05)

        assert await _self_bridge(supervisor).ensure_bridge() is False


class TestSelfHealthCheck:
    @pytest.mark.asyncio
    async def test_reports_down_without_ticking_before_the_first_rebuild(self):
        supervisor = FakeSupervisor()
        status = await _self_bridge(supervisor).health_check()

        assert status == {'lte_bridge': False, 'local_bridge': False}
        assert supervisor.calls == []

    @pytest.mark.asyncio
    async def test_quiet_tick_with_relays_up_is_healthy(self):
        supervisor = FakeSupervisor(results=[UP, UP])
        bridge = _self_bridge(supervisor)
        await bridge.ensure_bridge()

        assert await bridge.health_check() == {'lte_bridge': True, 'local_bridge': True}

    @pytest.mark.asyncio
    async def test_tick_that_repaired_something_reports_down_and_requests_a_fresh_start(self):
        """A relay restarted under a running qmicli leaves qmicli on a dead path: rebuild."""
        supervisor = FakeSupervisor(results=[UP, REPAIRED, UP])
        bridge = _self_bridge(supervisor)
        await bridge.ensure_bridge()

        assert await bridge.health_check() == {'lte_bridge': False, 'local_bridge': False}
        # stays down without ticking until ensure_bridge rebuilds it
        supervisor.calls.clear()
        assert await bridge.health_check() == {'lte_bridge': False, 'local_bridge': False}
        assert supervisor.calls == []

        assert await bridge.ensure_bridge() is True
        assert supervisor.calls == [f'run:{FRESH_START_DEVICE_COMMAND}', 'tick']

    @pytest.mark.asyncio
    async def test_tick_error_reports_down(self):
        supervisor = FakeSupervisor(results=[UP, OSError('timeout')])
        bridge = _self_bridge(supervisor)
        await bridge.ensure_bridge()

        assert await bridge.health_check() == {'lte_bridge': False, 'local_bridge': False}

    @pytest.mark.asyncio
    async def test_relays_down_reports_down(self):
        supervisor = FakeSupervisor(results=[UP, FakeTickResult(actions=[], relays_up=False)])
        bridge = _self_bridge(supervisor)
        await bridge.ensure_bridge()

        assert await bridge.health_check() == {'lte_bridge': False, 'local_bridge': False}


class TestSelfClose:
    @pytest.mark.asyncio
    async def test_close_stops_the_supervisor(self):
        supervisor = FakeSupervisor()
        await _self_bridge(supervisor).close()
        assert supervisor.closed is True


@pytest.fixture
def full_self_cfg(tmp_path):
    return {
        'lte_host': '192.168.1.100',
        'lte_device_mac': 'aa:bb:cc:dd:ee:ff',
        'lte_device_ssh_key_path': str(tmp_path / 'id_ed25519'),
        'ssh_username': 'root',
        'unifi': {'host': 'ctrl', 'username': 'u', 'password': 'p'},
        'monitoring_host_address': '192.168.1.10',
        'bridge_state_dir': str(tmp_path / 'state'),
    }


@pytest.fixture
def device_socat(tmp_path, monkeypatch):
    path = tmp_path / 'socat-mips'
    path.write_bytes(b'mips')
    monkeypatch.setattr(bridge_module, 'SOCAT_DEVICE_BINARY', str(path))
    return path


class TestBuildSupervisor:
    def test_maps_cbs_keys_onto_the_library_config(self, full_self_cfg, device_socat, tmp_path):
        with (
            patch('red_alert.integrations.inputs.cbs.bridge.shutil.which', return_value='/usr/bin/socat'),
            patch('lte_pro_access.create_supervisor', return_value='SUPERVISOR') as create,
        ):
            assert build_supervisor(full_self_cfg, 18222, 300) == 'SUPERVISOR'

        cfg = create.call_args.args[0]
        assert cfg.lte_host == '192.168.1.100'
        assert cfg.lte_mac == 'aa:bb:cc:dd:ee:ff'
        assert cfg.pi_address == '192.168.1.10'
        assert cfg.ssh_user == 'root'
        assert cfg.ssh_key_path == tmp_path / 'id_ed25519'
        assert cfg.state_dir == tmp_path / 'state'
        assert cfg.socat_device_binary == device_socat
        assert cfg.socat_pi_binary == Path('/usr/bin/socat')
        assert cfg.qmi_port == 18222
        assert cfg.at_relay is None
        assert cfg.check_interval_seconds == 300
        assert cfg.unifi == {'host': 'ctrl', 'username': 'u', 'password': 'p'}

    def test_expands_home_in_paths(self, full_self_cfg, device_socat):
        full_self_cfg['lte_device_ssh_key_path'] = '~/.ssh/id_ed25519_lte'
        with (
            patch('red_alert.integrations.inputs.cbs.bridge.shutil.which', return_value='/usr/bin/socat'),
            patch('lte_pro_access.create_supervisor') as create,
        ):
            build_supervisor(full_self_cfg, 18222, 300)

        assert '~' not in str(create.call_args.args[0].ssh_key_path)

    @pytest.mark.parametrize('key', SELF_MANAGED_REQUIRED_KEYS)
    def test_each_self_managed_key_is_required(self, full_self_cfg, device_socat, key):
        del full_self_cfg[key]
        with pytest.raises(ValueError, match=key):
            build_supervisor(full_self_cfg, 18222, 300)

    def test_removed_socat_remote_binary_key_is_rejected(self, full_self_cfg, device_socat):
        full_self_cfg['socat_remote_binary'] = '/cache/socat-mips'
        with pytest.raises(ValueError, match='socat_remote_binary'):
            build_supervisor(full_self_cfg, 18222, 300)

    def test_missing_local_socat_is_a_clear_error(self, full_self_cfg, device_socat):
        with (
            patch('red_alert.integrations.inputs.cbs.bridge.shutil.which', return_value=None),
            pytest.raises(FileNotFoundError, match='socat not found in PATH'),
        ):
            build_supervisor(full_self_cfg, 18222, 300)

    def test_missing_device_socat_is_a_clear_error(self, full_self_cfg, monkeypatch, tmp_path):
        monkeypatch.setattr(bridge_module, 'SOCAT_DEVICE_BINARY', str(tmp_path / 'absent'))
        with (
            patch('red_alert.integrations.inputs.cbs.bridge.shutil.which', return_value='/usr/bin/socat'),
            pytest.raises(FileNotFoundError, match='MIPS socat'),
        ):
            build_supervisor(full_self_cfg, 18222, 300)

    def test_missing_library_is_a_clear_error(self, full_self_cfg, device_socat, monkeypatch):
        monkeypatch.setitem(sys.modules, 'lte_pro_access', None)
        with (
            patch('red_alert.integrations.inputs.cbs.bridge.shutil.which', return_value='/usr/bin/socat'),
            pytest.raises(ModuleNotFoundError, match='lte-pro-access'),
        ):
            build_supervisor(full_self_cfg, 18222, 300)

    def test_bundled_device_socat_path_is_the_repo_data_dir(self):
        repo_root = Path(__file__).resolve().parents[4]
        assert Path(bridge_module.SOCAT_DEVICE_BINARY) == repo_root / 'data' / 'socat-mips'


class TestConfigureCbs:
    @pytest.mark.asyncio
    async def test_runs_all_commands(self):
        bridge = _external_bridge()

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b'OK', b''))

        with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock, return_value=mock_proc) as mock_exec:
            result = await bridge.configure_cbs('/usr/local/bin/qmicli')

        assert result is True
        flags = [call[0][4] for call in mock_exec.call_args_list]
        assert flags == ['--wms-set-cbs-channels=919,4370-4383', '--wms-set-broadcast-activation']

    @pytest.mark.asyncio
    async def test_custom_channels(self):
        bridge = _external_bridge()

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b'OK', b''))

        with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock, return_value=mock_proc) as mock_exec:
            await bridge.configure_cbs('/usr/local/bin/qmicli', channels='919,4370-4372')

        first_call_args = mock_exec.call_args_list[0][0]
        assert '--wms-set-cbs-channels=919,4370-4372' in first_call_args

    @pytest.mark.asyncio
    async def test_fails_on_command_error(self):
        bridge = _external_bridge()

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b'', b'error: device busy'))

        with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock, return_value=mock_proc):
            result = await bridge.configure_cbs('/usr/local/bin/qmicli')

        assert result is False

    @pytest.mark.asyncio
    async def test_stops_on_first_failure(self):
        bridge = _external_bridge()

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b'', b'error'))

        with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock, return_value=mock_proc) as mock_exec:
            await bridge.configure_cbs('/usr/local/bin/qmicli')

        assert mock_exec.call_count == 1


def _fake_unix_connection(accepts: bool):
    """Stand-in for asyncio.open_unix_connection: succeeds with a closable writer, or refuses."""
    writer = MagicMock()
    writer.wait_closed = AsyncMock()
    if accepts:
        return AsyncMock(return_value=(MagicMock(), writer)), writer
    return AsyncMock(side_effect=ConnectionRefusedError()), writer


@pytest.fixture
def listening_tcp_port():
    """Yield a callable that starts a real local TCP listener and returns its port."""
    servers = []

    async def start() -> int:
        server = await asyncio.start_server(lambda r, w: w.close(), '127.0.0.1', 0)
        servers.append(server)
        return server.sockets[0].getsockname()[1]

    yield start

    for server in servers:
        server.close()


class TestExternalManagement:
    """bridge_management 'external': another process owns both socat halves."""

    def test_invalid_management_is_rejected(self):
        with pytest.raises(ValueError, match='management'):
            CbsBridge(lte_host='192.168.1.100', bridge_port=18222, device='/dev/cdc-wdm0', management='bogus', supervisor=None)  # type: ignore[arg-type]

    def test_supervisor_is_not_allowed(self):
        with pytest.raises(ValueError, match='supervisor'):
            CbsBridge(lte_host='192.168.1.100', bridge_port=18222, device='/dev/cdc-wdm0', management='external', supervisor=FakeSupervisor())

    @pytest.mark.asyncio
    async def test_external_mode_never_touches_the_device(self):
        bridge = _external_bridge(lte_host='127.0.0.1', bridge_port=1)
        connect, _ = _fake_unix_connection(accepts=True)

        with (
            patch('red_alert.integrations.inputs.cbs.bridge.asyncio.open_unix_connection', connect),
            patch('red_alert.integrations.inputs.cbs.bridge.asyncio.create_subprocess_exec') as spawn,
            patch('red_alert.integrations.inputs.cbs.bridge.build_supervisor') as build,
        ):
            bridge.request_fresh_start()
            assert await bridge.ensure_bridge() is True
            bridge.request_fresh_start()
            assert await bridge.ensure_bridge() is True
            # port 1 on loopback refuses: health reports LTE down but still must not touch the device
            assert (await bridge.health_check())['lte_bridge'] is False
            await bridge.close()

        spawn.assert_not_called()
        build.assert_not_called()

    @pytest.mark.asyncio
    async def test_external_ensure_bridge_true_when_local_socket_accepts(self):
        bridge = _external_bridge()
        connect, writer = _fake_unix_connection(accepts=True)

        with patch('red_alert.integrations.inputs.cbs.bridge.asyncio.open_unix_connection', connect):
            assert await bridge.ensure_bridge() is True

        assert connect.call_args.args[0] == '\0qmi-proxy'
        writer.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_external_ensure_bridge_false_when_local_socket_refuses(self):
        bridge = _external_bridge()
        connect, _ = _fake_unix_connection(accepts=False)

        with patch('red_alert.integrations.inputs.cbs.bridge.asyncio.open_unix_connection', connect):
            assert await bridge.ensure_bridge() is False

    @pytest.mark.asyncio
    async def test_external_ensure_bridge_false_when_local_socket_hangs(self):
        bridge = _external_bridge()

        async def hang(*args, **kwargs):
            await asyncio.sleep(60)

        with (
            patch('red_alert.integrations.inputs.cbs.bridge.asyncio.open_unix_connection', hang),
            patch('red_alert.integrations.inputs.cbs.bridge.PROBE_TIMEOUT', 0.05),
        ):
            assert await bridge.ensure_bridge() is False

    @pytest.mark.asyncio
    async def test_external_mode_health_uses_tcp(self, listening_tcp_port):
        port = await listening_tcp_port()
        bridge = _external_bridge(lte_host='127.0.0.1', bridge_port=port)
        connect, _ = _fake_unix_connection(accepts=True)

        with patch('red_alert.integrations.inputs.cbs.bridge.asyncio.open_unix_connection', connect):
            status = await bridge.health_check()

        assert status == {'lte_bridge': True, 'local_bridge': True}

    @pytest.mark.asyncio
    async def test_external_mode_health_reports_both_down_when_nothing_accepts(self):
        bridge = _external_bridge(lte_host='127.0.0.1', bridge_port=1)
        connect, _ = _fake_unix_connection(accepts=False)

        with patch('red_alert.integrations.inputs.cbs.bridge.asyncio.open_unix_connection', connect):
            status = await bridge.health_check()

        assert status == {'lte_bridge': False, 'local_bridge': False}

    @pytest.mark.asyncio
    async def test_external_close_is_a_noop(self):
        bridge = _external_bridge()
        await bridge.close()
