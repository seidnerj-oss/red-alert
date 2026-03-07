from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from red_alert.integrations.unifi.led_controller import UnifiLedController


@pytest.fixture
def devices():
    return [{'host': '192.168.1.10'}, {'host': '192.168.1.11', 'port': 2222}]


@pytest.fixture
def controller(devices):
    return UnifiLedController(devices, ssh_username='admin', ssh_key_path='/path/to/key')


def _mock_asyncssh():
    """Create a properly mocked asyncssh module.

    asyncssh.connect() returns an async context manager (not a coroutine),
    so we use MagicMock for connect and set up __aenter__/__aexit__ on the
    returned connection mock.
    """
    mock_ssh = MagicMock()
    mock_conn = AsyncMock()
    mock_conn.run = AsyncMock(return_value=MagicMock(stderr=''))
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)
    mock_ssh.connect = MagicMock(return_value=mock_conn)
    return mock_ssh, mock_conn


class TestSetColor:
    @pytest.mark.asyncio
    async def test_sets_color_on_all_devices(self, controller):
        mock_ssh, mock_conn = _mock_asyncssh()
        with patch('red_alert.integrations.unifi.led_controller.asyncssh', mock_ssh):
            await controller.set_color(255, 0, 0)
            assert mock_ssh.connect.call_count == 2

    @pytest.mark.asyncio
    async def test_skips_if_same_color(self, controller):
        mock_ssh, mock_conn = _mock_asyncssh()
        with patch('red_alert.integrations.unifi.led_controller.asyncssh', mock_ssh):
            await controller.set_color(255, 0, 0)
            assert mock_ssh.connect.call_count == 2

            mock_ssh.connect.reset_mock()
            await controller.set_color(255, 0, 0)
            assert mock_ssh.connect.call_count == 0  # skipped

    @pytest.mark.asyncio
    async def test_updates_on_different_color(self, controller):
        mock_ssh, mock_conn = _mock_asyncssh()
        with patch('red_alert.integrations.unifi.led_controller.asyncssh', mock_ssh):
            await controller.set_color(255, 0, 0)
            mock_ssh.connect.reset_mock()
            await controller.set_color(0, 255, 0)
            assert mock_ssh.connect.call_count == 2

    @pytest.mark.asyncio
    async def test_uses_custom_port(self, controller):
        mock_ssh, mock_conn = _mock_asyncssh()
        with patch('red_alert.integrations.unifi.led_controller.asyncssh', mock_ssh):
            await controller.set_color(0, 0, 255)

            calls = mock_ssh.connect.call_args_list
            ports = [c.kwargs.get('port') for c in calls]
            assert 22 in ports
            assert 2222 in ports

    @pytest.mark.asyncio
    async def test_uses_ssh_key_path(self, controller):
        mock_ssh, mock_conn = _mock_asyncssh()
        with patch('red_alert.integrations.unifi.led_controller.asyncssh', mock_ssh):
            await controller.set_color(255, 255, 0)

            call_kwargs = mock_ssh.connect.call_args_list[0].kwargs
            assert call_kwargs['client_keys'] == ['/path/to/key']

    @pytest.mark.asyncio
    async def test_handles_ssh_error_gracefully(self, controller):
        import asyncssh

        with patch('red_alert.integrations.unifi.led_controller.asyncssh') as mock_ssh:
            mock_ssh.Error = asyncssh.Error
            mock_ssh.connect = MagicMock(side_effect=OSError('Connection refused'))

            # Should not raise
            await controller.set_color(255, 0, 0)

    @pytest.mark.asyncio
    async def test_sends_correct_command(self, controller):
        mock_ssh, mock_conn = _mock_asyncssh()
        with patch('red_alert.integrations.unifi.led_controller.asyncssh', mock_ssh):
            await controller.set_color(128, 64, 32)

            cmd = mock_conn.run.call_args_list[0].args[0]
            assert '128,64,32' in cmd
            assert '/proc/ubnt_ledbar/custom_color' in cmd


class TestSetColorNoDevices:
    @pytest.mark.asyncio
    async def test_empty_devices_does_nothing(self):
        controller = UnifiLedController(devices=[])
        # Should not raise
        await controller.set_color(255, 0, 0)
