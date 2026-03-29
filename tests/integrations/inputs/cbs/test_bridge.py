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
    async def test_returns_true_when_socat_running(self):
        bridge = CbsBridge(lte_host='192.168.1.100')

        mock_result = MagicMock()
        mock_result.exit_status = 0
        mock_result.stdout = '12345 root  /tmp/socat TCP-LISTEN:18222,reuseaddr,fork ABSTRACT-CONNECT:qmi-proxy'

        with patch.object(bridge, '_ssh_run', new_callable=AsyncMock, return_value=mock_result):
            assert await bridge.check_lte_bridge() is True

    @pytest.mark.asyncio
    async def test_returns_false_when_socat_not_running(self):
        bridge = CbsBridge(lte_host='192.168.1.100')

        mock_result = MagicMock()
        mock_result.exit_status = 1
        mock_result.stdout = ''

        with patch.object(bridge, '_ssh_run', new_callable=AsyncMock, return_value=mock_result):
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
        assert mock_exec.call_count == 3

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
