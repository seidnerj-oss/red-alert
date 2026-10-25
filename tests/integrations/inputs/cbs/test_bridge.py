"""Tests for CBS socat bridge management."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from red_alert.integrations.inputs.cbs.bridge import CbsBridge, DEFAULT_BRIDGE_PORT, SOCAT_REMOTE_PATH


class TestCbsBridgeInit:
    def test_default_values(self):
        bridge = CbsBridge(lte_host='192.168.1.100')
        assert bridge.lte_host == '192.168.1.100'
        assert bridge.bridge_port == DEFAULT_BRIDGE_PORT
        assert bridge.device == '/dev/cdc-wdm0'

    def test_custom_values(self):
        bridge = CbsBridge(
            lte_host='10.0.0.1',
            bridge_port=9999,
            device='/dev/cdc-wdm1',
            lte_device_ssh_key_path='/home/user/.ssh/id_ed25519',
            ssh_username='admin',
            socat_remote_binary='/tmp/socat-mips',
        )
        assert bridge.lte_host == '10.0.0.1'
        assert bridge.bridge_port == 9999
        assert bridge.device == '/dev/cdc-wdm1'


class TestCheckLteBridge:
    @pytest.mark.asyncio
    async def test_returns_true_when_port_is_listening(self):
        bridge = CbsBridge(lte_host='192.168.1.100')

        mock_result = MagicMock()
        mock_result.exit_status = 0
        mock_result.stdout = 'LISTENING\n'

        with patch.object(bridge, '_ssh_run', new_callable=AsyncMock, return_value=mock_result) as mock_ssh:
            assert await bridge.check_lte_bridge() is True

        assert ':18222' in mock_ssh.call_args[0][0]

    @pytest.mark.asyncio
    async def test_checks_the_configured_bridge_port(self):
        bridge = CbsBridge(lte_host='192.168.1.100', bridge_port=9999)

        mock_result = MagicMock()
        mock_result.exit_status = 0
        mock_result.stdout = 'LISTENING\n'

        with patch.object(bridge, '_ssh_run', new_callable=AsyncMock, return_value=mock_result) as mock_ssh:
            assert await bridge.check_lte_bridge() is True

        assert ':9999' in mock_ssh.call_args[0][0]

    @pytest.mark.asyncio
    async def test_returns_false_when_nothing_is_listening(self):
        bridge = CbsBridge(lte_host='192.168.1.100')

        mock_result = MagicMock()
        mock_result.exit_status = 1
        mock_result.stdout = ''

        with patch.object(bridge, '_ssh_run', new_callable=AsyncMock, return_value=mock_result):
            assert await bridge.check_lte_bridge() is False

    @pytest.mark.asyncio
    async def test_returns_false_when_only_a_forked_child_remains(self):
        """A dead listener with a live connected child must not read as healthy.

        The old ps-based check matched the child's identical command line and
        reported a healthy bridge; nothing is listening in that state.
        """
        bridge = CbsBridge(lte_host='192.168.1.100')

        mock_result = MagicMock()
        mock_result.exit_status = 1
        mock_result.stdout = '19269 ISey  /tmp/socat TCP-LISTEN:18222,reuseaddr,fork ABSTRACT-CONNECT:qmi-proxy'

        with patch.object(bridge, '_ssh_run', new_callable=AsyncMock, return_value=mock_result):
            assert await bridge.check_lte_bridge() is False

    @pytest.mark.asyncio
    async def test_falls_back_to_process_check_without_netstat(self):
        bridge = CbsBridge(lte_host='192.168.1.100')

        no_netstat = MagicMock()
        no_netstat.exit_status = 0
        no_netstat.stdout = 'NO_NETSTAT\n'

        ps_hit = MagicMock()
        ps_hit.exit_status = 0
        ps_hit.stdout = '12345 root  /tmp/socat TCP-LISTEN:18222,reuseaddr,fork ABSTRACT-CONNECT:qmi-proxy'

        with patch.object(bridge, '_ssh_run', new_callable=AsyncMock, side_effect=[no_netstat, ps_hit]) as mock_ssh:
            assert await bridge.check_lte_bridge() is True

        assert 'ps w' in mock_ssh.call_args[0][0]

    @pytest.mark.asyncio
    async def test_fallback_returns_false_when_no_socat_process(self):
        bridge = CbsBridge(lte_host='192.168.1.100')

        no_netstat = MagicMock()
        no_netstat.exit_status = 0
        no_netstat.stdout = 'NO_NETSTAT\n'

        ps_miss = MagicMock()
        ps_miss.exit_status = 1
        ps_miss.stdout = ''

        with patch.object(bridge, '_ssh_run', new_callable=AsyncMock, side_effect=[no_netstat, ps_miss]):
            assert await bridge.check_lte_bridge() is False

    @pytest.mark.asyncio
    async def test_returns_false_on_ssh_error(self):
        bridge = CbsBridge(lte_host='192.168.1.100')

        with patch.object(bridge, '_ssh_run', new_callable=AsyncMock, side_effect=OSError('Connection refused')):
            assert await bridge.check_lte_bridge() is False


class TestDeploySocatToLte:
    @pytest.mark.asyncio
    async def test_returns_false_when_no_binary_configured(self):
        bridge = CbsBridge(lte_host='192.168.1.100')
        assert await bridge._deploy_socat_to_lte() is False

    @pytest.mark.asyncio
    async def test_skips_deploy_when_already_present(self):
        bridge = CbsBridge(lte_host='192.168.1.100', socat_remote_binary='/local/socat')

        mock_conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.stdout = 'exists'
        mock_conn.run = AsyncMock(return_value=mock_result)
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)

        with patch('asyncssh.connect', return_value=mock_conn):
            assert await bridge._deploy_socat_to_lte() is True

    @pytest.mark.asyncio
    async def test_deploys_when_not_present(self):
        bridge = CbsBridge(lte_host='192.168.1.100', socat_remote_binary='/local/socat')

        check_result = MagicMock()
        check_result.stdout = ''

        chmod_result = MagicMock()
        chmod_result.exit_status = 0

        mock_conn = AsyncMock()
        mock_conn.run = AsyncMock(side_effect=[check_result, chmod_result])
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)

        with (
            patch('asyncssh.connect', return_value=mock_conn),
            patch('asyncssh.scp', new_callable=AsyncMock) as mock_scp,
        ):
            assert await bridge._deploy_socat_to_lte() is True

        mock_scp.assert_called_once_with('/local/socat', (mock_conn, SOCAT_REMOTE_PATH))

    @pytest.mark.asyncio
    async def test_returns_false_on_ssh_error(self):
        bridge = CbsBridge(lte_host='192.168.1.100', socat_remote_binary='/local/socat')

        with patch('asyncssh.connect', side_effect=OSError('Connection refused')):
            assert await bridge._deploy_socat_to_lte() is False


class TestEnsureLteBridge:
    @pytest.mark.asyncio
    async def test_fresh_start_kills_and_restarts(self):
        bridge = CbsBridge(lte_host='192.168.1.100')

        exists_result = MagicMock()
        exists_result.stdout = 'exists'

        with (
            patch.object(bridge, '_kill_lte_socat', new_callable=AsyncMock) as mock_kill,
            patch.object(bridge, 'check_lte_bridge', new_callable=AsyncMock, return_value=True),
            patch.object(bridge, '_ssh_run', new_callable=AsyncMock, side_effect=[exists_result, MagicMock()]),
        ):
            assert await bridge.ensure_lte_bridge() is True
            mock_kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_kill_when_already_established(self):
        bridge = CbsBridge(lte_host='192.168.1.100')
        bridge._fresh_start = False

        with (
            patch.object(bridge, '_kill_lte_socat', new_callable=AsyncMock) as mock_kill,
            patch.object(bridge, 'check_lte_bridge', new_callable=AsyncMock, return_value=True),
        ):
            assert await bridge.ensure_lte_bridge() is True
            mock_kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_starts_bridge_when_socat_present(self):
        bridge = CbsBridge(lte_host='192.168.1.100')

        exists_result = MagicMock()
        exists_result.stdout = 'exists'

        with (
            patch.object(bridge, '_kill_lte_socat', new_callable=AsyncMock),
            patch.object(bridge, 'check_lte_bridge', new_callable=AsyncMock, return_value=True),
            patch.object(bridge, '_ssh_run', new_callable=AsyncMock, side_effect=[exists_result, MagicMock()]),
        ):
            assert await bridge.ensure_lte_bridge() is True
            assert bridge._fresh_start is False

    @pytest.mark.asyncio
    async def test_fails_when_socat_not_found(self):
        bridge = CbsBridge(lte_host='192.168.1.100')

        not_exists_result = MagicMock()
        not_exists_result.stdout = ''

        with (
            patch.object(bridge, '_kill_lte_socat', new_callable=AsyncMock),
            patch.object(bridge, '_ssh_run', new_callable=AsyncMock, return_value=not_exists_result),
        ):
            assert await bridge.ensure_lte_bridge() is False

    @pytest.mark.asyncio
    async def test_deploys_then_starts(self):
        bridge = CbsBridge(lte_host='192.168.1.100', socat_remote_binary='/local/socat')

        exists_result = MagicMock()
        exists_result.stdout = 'exists'

        with (
            patch.object(bridge, '_kill_lte_socat', new_callable=AsyncMock),
            patch.object(bridge, 'check_lte_bridge', new_callable=AsyncMock, return_value=True),
            patch.object(bridge, '_deploy_socat_to_lte', new_callable=AsyncMock, return_value=True),
            patch.object(bridge, '_ssh_run', new_callable=AsyncMock, side_effect=[exists_result, MagicMock()]),
        ):
            assert await bridge.ensure_lte_bridge() is True

    @pytest.mark.asyncio
    async def test_fails_when_deploy_fails(self):
        bridge = CbsBridge(lte_host='192.168.1.100', socat_remote_binary='/local/socat')

        with (
            patch.object(bridge, '_kill_lte_socat', new_callable=AsyncMock),
            patch.object(bridge, '_deploy_socat_to_lte', new_callable=AsyncMock, return_value=False),
        ):
            assert await bridge.ensure_lte_bridge() is False


class TestLocalBridge:
    @pytest.mark.asyncio
    async def test_check_returns_false_when_no_process(self):
        bridge = CbsBridge(lte_host='192.168.1.100')
        assert await bridge.check_local_bridge() is False

    @pytest.mark.asyncio
    async def test_check_returns_true_when_running(self):
        bridge = CbsBridge(lte_host='192.168.1.100')
        bridge._local_socat_proc = MagicMock()
        bridge._local_socat_proc.returncode = None
        assert await bridge.check_local_bridge() is True

    @pytest.mark.asyncio
    async def test_check_returns_false_when_exited(self):
        bridge = CbsBridge(lte_host='192.168.1.100')
        bridge._local_socat_proc = MagicMock()
        bridge._local_socat_proc.returncode = 1
        assert await bridge.check_local_bridge() is False

    @pytest.mark.asyncio
    async def test_ensure_returns_true_when_already_running(self):
        bridge = CbsBridge(lte_host='192.168.1.100')

        with patch.object(bridge, 'check_local_bridge', new_callable=AsyncMock, return_value=True):
            assert await bridge.ensure_local_bridge() is True

    @pytest.mark.asyncio
    async def test_ensure_fails_when_socat_not_in_path(self):
        bridge = CbsBridge(lte_host='192.168.1.100')

        with (
            patch.object(bridge, 'check_local_bridge', new_callable=AsyncMock, return_value=False),
            patch('shutil.which', return_value=None),
        ):
            assert await bridge.ensure_local_bridge() is False

    @pytest.mark.asyncio
    async def test_ensure_starts_socat_process(self):
        bridge = CbsBridge(lte_host='192.168.1.100')

        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.pid = 12345

        with (
            patch.object(bridge, 'check_local_bridge', new_callable=AsyncMock, return_value=False),
            patch('shutil.which', return_value='/usr/bin/socat'),
            patch('asyncio.create_subprocess_exec', new_callable=AsyncMock, return_value=mock_proc),
        ):
            assert await bridge.ensure_local_bridge() is True
            assert bridge._local_socat_proc is mock_proc

    @pytest.mark.asyncio
    async def test_ensure_fails_when_socat_exits_immediately(self):
        bridge = CbsBridge(lte_host='192.168.1.100')

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.pid = 12345
        mock_proc.stderr = AsyncMock()
        mock_proc.stderr.read = AsyncMock(return_value=b'bind failed')

        with (
            patch.object(bridge, 'check_local_bridge', new_callable=AsyncMock, return_value=False),
            patch('shutil.which', return_value='/usr/bin/socat'),
            patch('asyncio.create_subprocess_exec', new_callable=AsyncMock, return_value=mock_proc),
        ):
            assert await bridge.ensure_local_bridge() is False
            assert bridge._local_socat_proc is None


class TestEnsureBridge:
    @pytest.mark.asyncio
    async def test_both_sides_succeed(self):
        bridge = CbsBridge(lte_host='192.168.1.100')

        with (
            patch.object(bridge, 'ensure_lte_bridge', new_callable=AsyncMock, return_value=True),
            patch.object(bridge, 'ensure_local_bridge', new_callable=AsyncMock, return_value=True),
        ):
            assert await bridge.ensure_bridge() is True

    @pytest.mark.asyncio
    async def test_fails_when_lte_side_fails(self):
        bridge = CbsBridge(lte_host='192.168.1.100')

        with patch.object(bridge, 'ensure_lte_bridge', new_callable=AsyncMock, return_value=False):
            assert await bridge.ensure_bridge() is False

    @pytest.mark.asyncio
    async def test_fails_when_local_side_fails(self):
        bridge = CbsBridge(lte_host='192.168.1.100')

        with (
            patch.object(bridge, 'ensure_lte_bridge', new_callable=AsyncMock, return_value=True),
            patch.object(bridge, 'ensure_local_bridge', new_callable=AsyncMock, return_value=False),
        ):
            assert await bridge.ensure_bridge() is False


class TestRequestFreshStart:
    """request_fresh_start makes an established bridge rebuild from scratch, as on startup."""

    @pytest.mark.asyncio
    async def test_lte_side_is_torn_down_even_when_listener_looks_healthy(self):
        bridge = CbsBridge(lte_host='192.168.1.100')
        bridge._fresh_start = False
        bridge.request_fresh_start()

        exists_result = MagicMock()
        exists_result.stdout = 'exists'

        with (
            patch.object(bridge, '_kill_lte_socat', new_callable=AsyncMock) as mock_kill,
            patch.object(bridge, 'check_lte_bridge', new_callable=AsyncMock, return_value=True),
            patch.object(bridge, '_ssh_run', new_callable=AsyncMock, side_effect=[exists_result, MagicMock()]),
        ):
            assert await bridge.ensure_lte_bridge() is True
            mock_kill.assert_called_once()
        assert bridge._fresh_start is False

    @pytest.mark.asyncio
    async def test_redeploys_socat_after_device_reboot(self):
        bridge = CbsBridge(lte_host='192.168.1.100', socat_remote_binary='/opt/socat-mips')
        bridge._fresh_start = False
        bridge.request_fresh_start()

        exists_result = MagicMock()
        exists_result.stdout = 'exists'

        with (
            patch.object(bridge, '_kill_lte_socat', new_callable=AsyncMock),
            patch.object(bridge, '_deploy_socat_to_lte', new_callable=AsyncMock, return_value=True) as mock_deploy,
            patch.object(bridge, 'check_lte_bridge', new_callable=AsyncMock, return_value=True),
            patch.object(bridge, '_ssh_run', new_callable=AsyncMock, side_effect=[exists_result, MagicMock()]),
        ):
            assert await bridge.ensure_lte_bridge() is True
            mock_deploy.assert_called_once()

    @pytest.mark.asyncio
    async def test_local_side_is_restarted(self):
        bridge = CbsBridge(lte_host='192.168.1.100')
        bridge._fresh_start = False
        bridge.request_fresh_start()

        with (
            patch.object(bridge, '_kill_local_bridge', new_callable=AsyncMock) as mock_kill_local,
            patch.object(bridge, 'ensure_lte_bridge', new_callable=AsyncMock, return_value=True),
            patch.object(bridge, 'ensure_local_bridge', new_callable=AsyncMock, return_value=True),
        ):
            assert await bridge.ensure_bridge() is True
            mock_kill_local.assert_called_once()


class TestConfigureCbs:
    @pytest.mark.asyncio
    async def test_runs_all_commands(self):
        bridge = CbsBridge(lte_host='192.168.1.100')

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
        bridge = CbsBridge(lte_host='192.168.1.100')

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b'OK', b''))

        with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock, return_value=mock_proc) as mock_exec:
            await bridge.configure_cbs('/usr/local/bin/qmicli', channels='919,4370-4372')

        first_call_args = mock_exec.call_args_list[0][0]
        assert '--wms-set-cbs-channels=919,4370-4372' in first_call_args

    @pytest.mark.asyncio
    async def test_fails_on_command_error(self):
        bridge = CbsBridge(lte_host='192.168.1.100')

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b'', b'error: device busy'))

        with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock, return_value=mock_proc):
            result = await bridge.configure_cbs('/usr/local/bin/qmicli')

        assert result is False

    @pytest.mark.asyncio
    async def test_stops_on_first_failure(self):
        bridge = CbsBridge(lte_host='192.168.1.100')

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b'', b'error'))

        with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock, return_value=mock_proc) as mock_exec:
            await bridge.configure_cbs('/usr/local/bin/qmicli')

        assert mock_exec.call_count == 1


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_reports_all_healthy(self):
        bridge = CbsBridge(lte_host='192.168.1.100')

        with (
            patch.object(bridge, 'check_lte_bridge', new_callable=AsyncMock, return_value=True),
            patch.object(bridge, 'check_local_bridge', new_callable=AsyncMock, return_value=True),
        ):
            status = await bridge.health_check()

        assert status['lte_bridge'] is True
        assert status['local_bridge'] is True

    @pytest.mark.asyncio
    async def test_reports_lte_bridge_down(self):
        bridge = CbsBridge(lte_host='192.168.1.100')

        with (
            patch.object(bridge, 'check_lte_bridge', new_callable=AsyncMock, return_value=False),
            patch.object(bridge, 'check_local_bridge', new_callable=AsyncMock, return_value=True),
        ):
            status = await bridge.health_check()

        assert status['lte_bridge'] is False
        assert status['local_bridge'] is True

    @pytest.mark.asyncio
    async def test_reports_local_bridge_down(self):
        bridge = CbsBridge(lte_host='192.168.1.100')

        with (
            patch.object(bridge, 'check_lte_bridge', new_callable=AsyncMock, return_value=True),
            patch.object(bridge, 'check_local_bridge', new_callable=AsyncMock, return_value=False),
        ):
            status = await bridge.health_check()

        assert status['lte_bridge'] is True
        assert status['local_bridge'] is False


class TestClose:
    @pytest.mark.asyncio
    async def test_terminates_local_process(self):
        bridge = CbsBridge(lte_host='192.168.1.100')

        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.pid = 12345
        mock_proc.terminate = MagicMock()
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()

        bridge._local_socat_proc = mock_proc
        await bridge.close()

        mock_proc.terminate.assert_called_once()
        assert bridge._local_socat_proc is None

    @pytest.mark.asyncio
    async def test_kills_on_timeout(self):
        bridge = CbsBridge(lte_host='192.168.1.100')

        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.pid = 12345
        mock_proc.terminate = MagicMock()
        mock_proc.kill = MagicMock()

        wait_count = 0

        async def slow_wait():
            nonlocal wait_count
            wait_count += 1
            if wait_count == 1:
                await asyncio.sleep(10)
            return 0

        mock_proc.wait = AsyncMock(side_effect=slow_wait)

        bridge._local_socat_proc = mock_proc
        await bridge.close()

        mock_proc.terminate.assert_called_once()
        mock_proc.kill.assert_called_once()
        assert bridge._local_socat_proc is None

    @pytest.mark.asyncio
    async def test_noop_when_no_process(self):
        bridge = CbsBridge(lte_host='192.168.1.100')
        await bridge.close()

    @pytest.mark.asyncio
    async def test_noop_when_process_already_exited(self):
        bridge = CbsBridge(lte_host='192.168.1.100')

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        bridge._local_socat_proc = mock_proc

        await bridge.close()
        mock_proc.terminate.assert_not_called()


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
            CbsBridge(lte_host='192.168.1.100', management='bogus')

    def test_default_management_is_self(self):
        assert CbsBridge(lte_host='192.168.1.100').management == 'self'

    @pytest.mark.asyncio
    async def test_external_mode_never_runs_ssh(self):
        bridge = CbsBridge(
            lte_host='127.0.0.1',
            bridge_port=1,
            management='external',
            lte_device_ssh_key_path='/home/user/.ssh/id_ed25519',
            socat_remote_binary='/tmp/socat-mips',
            lte_device_mac='aa:bb:cc:dd:ee:ff',
            unifi={'host': 'ctrl', 'username': 'u', 'password': 'p'},
        )
        connect, _ = _fake_unix_connection(accepts=True)

        with (
            patch('red_alert.integrations.inputs.cbs.bridge.asyncssh.connect') as ssh_connect,
            patch('red_alert.integrations.inputs.cbs.bridge.asyncssh.scp') as ssh_scp,
            patch('red_alert.integrations.inputs.cbs.bridge.asyncio.open_unix_connection', connect),
            patch('red_alert.integrations.inputs.cbs.bridge.asyncio.create_subprocess_exec') as spawn,
            patch.object(bridge, '_enable_lte_ssh', new_callable=AsyncMock) as enable,
            patch.object(bridge, '_deploy_socat_to_lte', new_callable=AsyncMock) as deploy,
            patch.object(bridge, '_kill_lte_socat', new_callable=AsyncMock) as kill_lte,
            patch.object(bridge, '_kill_local_bridge', new_callable=AsyncMock) as kill_local,
        ):
            bridge.request_fresh_start()
            assert await bridge.ensure_bridge() is True
            bridge.request_fresh_start()
            assert await bridge.ensure_bridge() is True
            # port 1 on loopback refuses: health reports LTE down but still must not SSH
            assert (await bridge.health_check())['lte_bridge'] is False
            bridge.request_fresh_start()
            assert await bridge.check_lte_bridge() is False
            await bridge.close()

        ssh_connect.assert_not_called()
        ssh_scp.assert_not_called()
        spawn.assert_not_called()
        enable.assert_not_called()
        deploy.assert_not_called()
        kill_lte.assert_not_called()
        kill_local.assert_not_called()

    @pytest.mark.asyncio
    async def test_external_ensure_bridge_true_when_local_socket_accepts(self):
        bridge = CbsBridge(lte_host='192.168.1.100', management='external')
        connect, writer = _fake_unix_connection(accepts=True)

        with patch('red_alert.integrations.inputs.cbs.bridge.asyncio.open_unix_connection', connect):
            assert await bridge.ensure_bridge() is True

        assert connect.call_args.args[0] == '\0qmi-proxy'
        writer.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_external_ensure_bridge_false_when_local_socket_refuses(self):
        bridge = CbsBridge(lte_host='192.168.1.100', management='external')
        connect, _ = _fake_unix_connection(accepts=False)

        with patch('red_alert.integrations.inputs.cbs.bridge.asyncio.open_unix_connection', connect):
            assert await bridge.ensure_bridge() is False

    @pytest.mark.asyncio
    async def test_external_ensure_bridge_false_when_local_socket_hangs(self):
        bridge = CbsBridge(lte_host='192.168.1.100', management='external')

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
        bridge = CbsBridge(lte_host='127.0.0.1', bridge_port=port, management='external')
        connect, _ = _fake_unix_connection(accepts=True)

        with (
            patch('red_alert.integrations.inputs.cbs.bridge.asyncssh.connect') as ssh_connect,
            patch('red_alert.integrations.inputs.cbs.bridge.asyncio.open_unix_connection', connect),
        ):
            status = await bridge.health_check()

        assert status == {'lte_bridge': True, 'local_bridge': True}
        ssh_connect.assert_not_called()

    @pytest.mark.asyncio
    async def test_external_mode_health_reports_both_down_when_nothing_accepts(self):
        bridge = CbsBridge(lte_host='127.0.0.1', bridge_port=1, management='external')
        connect, _ = _fake_unix_connection(accepts=False)

        with patch('red_alert.integrations.inputs.cbs.bridge.asyncio.open_unix_connection', connect):
            status = await bridge.health_check()

        assert status == {'lte_bridge': False, 'local_bridge': False}

    @pytest.mark.asyncio
    async def test_external_close_is_a_noop(self):
        bridge = CbsBridge(lte_host='192.168.1.100', management='external')
        await bridge.close()


class TestSelfModeUnchanged:
    @pytest.mark.asyncio
    async def test_self_mode_unchanged(self):
        """Without bridge_management the bridge still tears down and rebuilds both sides."""
        bridge = CbsBridge(lte_host='192.168.1.100')

        with (
            patch.object(bridge, '_kill_local_bridge', new_callable=AsyncMock) as kill_local,
            patch.object(bridge, 'ensure_lte_bridge', new_callable=AsyncMock, return_value=True) as ensure_lte,
            patch.object(bridge, 'ensure_local_bridge', new_callable=AsyncMock, return_value=True) as ensure_local,
        ):
            assert await bridge.ensure_bridge() is True

        kill_local.assert_awaited_once()
        ensure_lte.assert_awaited_once()
        ensure_local.assert_awaited_once()
