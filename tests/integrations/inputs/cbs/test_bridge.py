"""Tests for CBS bridge management that must run without the lte-pro-access library."""

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from red_alert.integrations.inputs.cbs.bridge import CbsBridge


def _external_bridge(lte_host: str = '192.168.1.100', bridge_port: int = 18222) -> CbsBridge:
    return CbsBridge(lte_host=lte_host, bridge_port=bridge_port, device='/dev/cdc-wdm0', management='external', supervisor=None)


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

    @pytest.mark.asyncio
    async def test_works_without_the_lte_pro_access_library(self, monkeypatch):
        """External mode must not import lte-pro-access: a plain install has no unifi extra."""
        monkeypatch.setitem(sys.modules, 'lte_pro_access', None)
        with pytest.raises(ImportError):
            import lte_pro_access  # noqa: F401

        bridge = _external_bridge(lte_host='127.0.0.1', bridge_port=1)
        connect, _ = _fake_unix_connection(accepts=True)

        with patch('red_alert.integrations.inputs.cbs.bridge.asyncio.open_unix_connection', connect):
            bridge.request_fresh_start()
            assert await bridge.ensure_bridge() is True
            assert await bridge.health_check() == {'lte_bridge': False, 'local_bridge': True}
            await bridge.close()

    def test_invalid_management_is_rejected(self):
        with pytest.raises(ValueError, match='management'):
            CbsBridge(lte_host='192.168.1.100', bridge_port=18222, device='/dev/cdc-wdm0', management='bogus', supervisor=None)  # type: ignore[arg-type]

    def test_supervisor_is_not_allowed(self):
        with pytest.raises(ValueError, match='supervisor'):
            CbsBridge(lte_host='192.168.1.100', bridge_port=18222, device='/dev/cdc-wdm0', management='external', supervisor=MagicMock())

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
