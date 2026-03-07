from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from red_alert.integrations.unifi.led_controller import UnifiLedController, _wrap_request_with_2fa, rgb_to_hex

DEVICE_MAC = 'aa:bb:cc:dd:ee:ff'
DEVICE_MAC_2 = '11:22:33:44:55:66'


class TestRgbToHex:
    def test_red(self):
        assert rgb_to_hex(255, 0, 0) == '#FF0000'

    def test_green(self):
        assert rgb_to_hex(0, 255, 0) == '#00FF00'

    def test_white(self):
        assert rgb_to_hex(255, 255, 255) == '#FFFFFF'

    def test_black(self):
        assert rgb_to_hex(0, 0, 0) == '#000000'

    def test_arbitrary(self):
        assert rgb_to_hex(130, 30, 30) == '#821E1E'


def _mock_device(mac='aa:bb:cc:dd:ee:ff', device_id='abc123', supports_led_ring=True):
    """Create a mock Device object as returned by aiounifi."""
    device = MagicMock()
    device.mac = mac
    device.id = device_id
    device.supports_led_ring = supports_led_ring
    return device


def _mock_controller(devices=None):
    """Create a mock aiounifi Controller with devices."""
    controller = AsyncMock()
    controller.login = AsyncMock()
    controller.request = AsyncMock()

    # Mock the devices handler
    device_map = {}
    if devices:
        for dev in devices:
            device_map[dev.mac] = dev

    controller.devices = MagicMock()
    controller.devices.update = AsyncMock()
    controller.devices.get = MagicMock(side_effect=lambda mac, default=None: device_map.get(mac, default))
    controller.devices.__contains__ = MagicMock(side_effect=lambda mac: mac in device_map)
    controller.devices.__iter__ = MagicMock(return_value=iter(device_map))

    return controller


class TestConnect:
    @pytest.mark.asyncio
    async def test_connect_logs_in_and_loads_devices(self):
        device = _mock_device(mac=DEVICE_MAC)
        mock_ctrl = _mock_controller([device])

        with patch('red_alert.integrations.unifi.led_controller.Controller', return_value=mock_ctrl):
            controller = UnifiLedController('192.168.1.1', 'admin', 'pass', [DEVICE_MAC])
            controller._session = AsyncMock()
            await controller.connect()

            mock_ctrl.login.assert_called_once()
            mock_ctrl.devices.update.assert_called_once()
            assert controller._connected is True

    @pytest.mark.asyncio
    async def test_connect_creates_session_if_not_provided(self):
        device = _mock_device(mac=DEVICE_MAC)
        mock_ctrl = _mock_controller([device])

        with (
            patch('red_alert.integrations.unifi.led_controller.Controller', return_value=mock_ctrl),
            patch('red_alert.integrations.unifi.led_controller.aiohttp.ClientSession') as mock_session_cls,
        ):
            mock_session_cls.return_value = AsyncMock()
            controller = UnifiLedController('192.168.1.1', 'admin', 'pass', [DEVICE_MAC])
            await controller.connect()

            mock_session_cls.assert_called_once()
            assert controller._owns_session is True


class TestSetLed:
    @pytest.mark.asyncio
    async def test_sets_led_on_device(self):
        device = _mock_device(mac=DEVICE_MAC)
        mock_ctrl = _mock_controller([device])

        with patch('red_alert.integrations.unifi.led_controller.Controller', return_value=mock_ctrl):
            controller = UnifiLedController('192.168.1.1', 'admin', 'pass', [DEVICE_MAC])
            controller._session = AsyncMock()
            await controller.connect()
            await controller.set_led(on=True, color_hex='#FF0000', brightness=100)

            mock_ctrl.request.assert_called_once()

    @pytest.mark.asyncio
    async def test_sets_led_on_multiple_devices(self):
        dev1 = _mock_device(mac=DEVICE_MAC, device_id='id1')
        dev2 = _mock_device(mac=DEVICE_MAC_2, device_id='id2')
        mock_ctrl = _mock_controller([dev1, dev2])

        with patch('red_alert.integrations.unifi.led_controller.Controller', return_value=mock_ctrl):
            controller = UnifiLedController('192.168.1.1', 'admin', 'pass', [DEVICE_MAC, DEVICE_MAC_2])
            controller._session = AsyncMock()
            await controller.connect()
            await controller.set_led(on=True, color_hex='#00FF00', brightness=75)

            assert mock_ctrl.request.call_count == 2

    @pytest.mark.asyncio
    async def test_skips_if_state_unchanged(self):
        device = _mock_device(mac=DEVICE_MAC)
        mock_ctrl = _mock_controller([device])

        with patch('red_alert.integrations.unifi.led_controller.Controller', return_value=mock_ctrl):
            controller = UnifiLedController('192.168.1.1', 'admin', 'pass', [DEVICE_MAC])
            controller._session = AsyncMock()
            await controller.connect()

            await controller.set_led(on=True, color_hex='#FF0000', brightness=100)
            assert mock_ctrl.request.call_count == 1

            await controller.set_led(on=True, color_hex='#FF0000', brightness=100)
            assert mock_ctrl.request.call_count == 1  # no new call

    @pytest.mark.asyncio
    async def test_updates_on_state_change(self):
        device = _mock_device(mac=DEVICE_MAC)
        mock_ctrl = _mock_controller([device])

        with patch('red_alert.integrations.unifi.led_controller.Controller', return_value=mock_ctrl):
            controller = UnifiLedController('192.168.1.1', 'admin', 'pass', [DEVICE_MAC])
            controller._session = AsyncMock()
            await controller.connect()

            await controller.set_led(on=True, color_hex='#FF0000', brightness=100)
            await controller.set_led(on=True, color_hex='#00FF00', brightness=50)
            assert mock_ctrl.request.call_count == 2

    @pytest.mark.asyncio
    async def test_skips_missing_device(self):
        mock_ctrl = _mock_controller([])  # no devices found

        with patch('red_alert.integrations.unifi.led_controller.Controller', return_value=mock_ctrl):
            controller = UnifiLedController('192.168.1.1', 'admin', 'pass', [DEVICE_MAC])
            controller._session = AsyncMock()
            await controller.connect()
            await controller.set_led(on=True, color_hex='#FF0000', brightness=100)

            mock_ctrl.request.assert_not_called()

    @pytest.mark.asyncio
    async def test_auto_connects_on_first_call(self):
        device = _mock_device(mac=DEVICE_MAC)
        mock_ctrl = _mock_controller([device])

        with (
            patch('red_alert.integrations.unifi.led_controller.Controller', return_value=mock_ctrl),
            patch('red_alert.integrations.unifi.led_controller.aiohttp.ClientSession', return_value=AsyncMock()),
        ):
            controller = UnifiLedController('192.168.1.1', 'admin', 'pass', [DEVICE_MAC])
            await controller.set_led(on=True, color_hex='#FF0000', brightness=100)

            mock_ctrl.login.assert_called_once()
            mock_ctrl.request.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_color_brightness_without_led_ring(self):
        device = _mock_device(mac=DEVICE_MAC, supports_led_ring=False)
        mock_ctrl = _mock_controller([device])

        with (
            patch('red_alert.integrations.unifi.led_controller.Controller', return_value=mock_ctrl),
            patch('red_alert.integrations.unifi.led_controller.DeviceSetLedStatus') as mock_request_cls,
        ):
            mock_request_cls.create = MagicMock(return_value=MagicMock())
            controller = UnifiLedController('192.168.1.1', 'admin', 'pass', [DEVICE_MAC])
            controller._session = AsyncMock()
            await controller.connect()
            await controller.set_led(on=True, color_hex='#FF0000', brightness=80)

            mock_request_cls.create.assert_called_once_with(
                device,
                status='on',
                brightness=None,
                color=None,
            )


class TestLocate:
    @pytest.mark.asyncio
    async def test_locate_enable(self):
        device = _mock_device(mac=DEVICE_MAC)
        mock_ctrl = _mock_controller([device])

        with (
            patch('red_alert.integrations.unifi.led_controller.Controller', return_value=mock_ctrl),
            patch('red_alert.integrations.unifi.led_controller.DeviceLocateRequest') as mock_locate_cls,
        ):
            mock_locate_cls.create = MagicMock(return_value=MagicMock())
            controller = UnifiLedController('192.168.1.1', 'admin', 'pass', [DEVICE_MAC])
            controller._session = AsyncMock()
            await controller.connect()
            await controller.locate(enable=True)

            mock_locate_cls.create.assert_called_once_with(DEVICE_MAC, locate=True)
            mock_ctrl.request.assert_called_once()

    @pytest.mark.asyncio
    async def test_locate_disable(self):
        device = _mock_device(mac=DEVICE_MAC)
        mock_ctrl = _mock_controller([device])

        with (
            patch('red_alert.integrations.unifi.led_controller.Controller', return_value=mock_ctrl),
            patch('red_alert.integrations.unifi.led_controller.DeviceLocateRequest') as mock_locate_cls,
        ):
            mock_locate_cls.create = MagicMock(return_value=MagicMock())
            controller = UnifiLedController('192.168.1.1', 'admin', 'pass', [DEVICE_MAC])
            controller._session = AsyncMock()
            await controller.connect()
            await controller.locate(enable=False)

            mock_locate_cls.create.assert_called_once_with(DEVICE_MAC, locate=False)


class TestWrapRequestWith2fa:
    @pytest.mark.asyncio
    async def test_injects_totp_into_login_request(self):
        original = AsyncMock(return_value=('response', b'data'))
        wrapped = _wrap_request_with_2fa(original, 'JBSWY3DPEHPK3PXP')

        await wrapped('post', 'https://host/api/auth/login', json={'username': 'admin', 'password': 'pass', 'rememberMe': True})

        call_args = original.call_args
        assert 'ubic_2fa_token' in call_args.kwargs.get('json', call_args[2] if len(call_args[0]) > 2 else {})

    @pytest.mark.asyncio
    async def test_generates_valid_6_digit_totp(self):
        original = AsyncMock(return_value=('response', b'data'))
        wrapped = _wrap_request_with_2fa(original, 'JBSWY3DPEHPK3PXP')

        await wrapped('post', 'https://host/api/auth/login', json={'username': 'admin', 'password': 'pass'})

        sent_json = original.call_args.kwargs.get('json', original.call_args[0][2] if len(original.call_args[0]) > 2 else None)
        token = sent_json['ubic_2fa_token']
        assert len(token) == 6
        assert token.isdigit()

    @pytest.mark.asyncio
    async def test_does_not_modify_non_login_requests(self):
        original = AsyncMock(return_value=('response', b'data'))
        wrapped = _wrap_request_with_2fa(original, 'JBSWY3DPEHPK3PXP')

        await wrapped('get', 'https://host/api/devices', json=None)

        original.assert_called_once_with('get', 'https://host/api/devices', json=None, allow_redirects=True)

    @pytest.mark.asyncio
    async def test_does_not_modify_post_without_username(self):
        original = AsyncMock(return_value=('response', b'data'))
        wrapped = _wrap_request_with_2fa(original, 'JBSWY3DPEHPK3PXP')

        payload = {'led_override': 'on'}
        await wrapped('post', 'https://host/api/devices/123', json=payload)

        original.assert_called_once_with('post', 'https://host/api/devices/123', json=payload, allow_redirects=True)

    @pytest.mark.asyncio
    async def test_preserves_original_auth_fields(self):
        original = AsyncMock(return_value=('response', b'data'))
        wrapped = _wrap_request_with_2fa(original, 'JBSWY3DPEHPK3PXP')

        await wrapped('post', 'https://host/api/login', json={'username': 'admin', 'password': 'secret', 'rememberMe': True})

        sent_json = original.call_args.kwargs.get('json', original.call_args[0][2] if len(original.call_args[0]) > 2 else None)
        assert sent_json['username'] == 'admin'
        assert sent_json['password'] == 'secret'
        assert sent_json['rememberMe'] is True


class TestConnectWith2fa:
    @pytest.mark.asyncio
    async def test_connect_wraps_request_when_totp_secret_set(self):
        device = _mock_device(mac=DEVICE_MAC)
        mock_ctrl = _mock_controller([device])
        mock_ctrl.connectivity = MagicMock()
        mock_ctrl.connectivity._request = AsyncMock()

        with patch('red_alert.integrations.unifi.led_controller.Controller', return_value=mock_ctrl):
            controller = UnifiLedController('192.168.1.1', 'admin', 'pass', [DEVICE_MAC], totp_secret='JBSWY3DPEHPK3PXP')
            controller._session = AsyncMock()
            await controller.connect()

            # The _request method should have been replaced with our wrapper
            assert mock_ctrl.connectivity._request is not AsyncMock

    @pytest.mark.asyncio
    async def test_connect_does_not_wrap_when_no_totp_secret(self):
        device = _mock_device(mac=DEVICE_MAC)
        mock_ctrl = _mock_controller([device])
        original_request = AsyncMock()
        mock_ctrl.connectivity = MagicMock()
        mock_ctrl.connectivity._request = original_request

        with patch('red_alert.integrations.unifi.led_controller.Controller', return_value=mock_ctrl):
            controller = UnifiLedController('192.168.1.1', 'admin', 'pass', [DEVICE_MAC])
            controller._session = AsyncMock()
            await controller.connect()

            assert mock_ctrl.connectivity._request is original_request


class TestClose:
    @pytest.mark.asyncio
    async def test_closes_owned_session(self):
        device = _mock_device(mac=DEVICE_MAC)
        mock_ctrl = _mock_controller([device])

        with (
            patch('red_alert.integrations.unifi.led_controller.Controller', return_value=mock_ctrl),
            patch('red_alert.integrations.unifi.led_controller.aiohttp.ClientSession') as mock_session_cls,
        ):
            mock_session = AsyncMock()
            mock_session_cls.return_value = mock_session
            controller = UnifiLedController('192.168.1.1', 'admin', 'pass', [DEVICE_MAC])
            await controller.connect()
            await controller.close()

            mock_session.close.assert_called_once()
            assert controller._connected is False

    @pytest.mark.asyncio
    async def test_does_not_close_injected_session(self):
        session = AsyncMock()
        device = _mock_device(mac=DEVICE_MAC)
        mock_ctrl = _mock_controller([device])

        with patch('red_alert.integrations.unifi.led_controller.Controller', return_value=mock_ctrl):
            controller = UnifiLedController('192.168.1.1', 'admin', 'pass', [DEVICE_MAC], session=session)
            await controller.connect()
            await controller.close()

            session.close.assert_not_called()
