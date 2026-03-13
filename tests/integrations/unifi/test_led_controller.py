import sys
from contextlib import contextmanager
from types import ModuleType
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
    """Create a mock Device object compatible with both backends."""
    device = MagicMock()
    device.mac = mac
    device.id = device_id
    device.supports_led_ring = supports_led_ring
    return device


def _mock_controller(devices=None):
    """Create a mock controller with devices (compatible with both backends)."""
    controller = AsyncMock()
    controller.login = AsyncMock()
    controller.request = AsyncMock()
    controller.execute = AsyncMock()
    controller.connect = AsyncMock()
    controller.initialize = AsyncMock()
    controller.disconnect = AsyncMock()

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


# --- aiounifi backend tests ---


class TestConnectAiounifi:
    @pytest.mark.asyncio
    async def test_connect_logs_in_and_loads_devices(self):
        device = _mock_device(mac=DEVICE_MAC)
        mock_ctrl = _mock_controller([device])

        with patch('red_alert.integrations.unifi.led_controller.AioController', return_value=mock_ctrl):
            controller = UnifiLedController('192.168.1.1', 'admin', 'pass', [DEVICE_MAC], backend='aiounifi')
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
            patch('red_alert.integrations.unifi.led_controller.AioController', return_value=mock_ctrl),
            patch('red_alert.integrations.unifi.led_controller.aiohttp.ClientSession') as mock_session_cls,
        ):
            mock_session_cls.return_value = AsyncMock()
            controller = UnifiLedController('192.168.1.1', 'admin', 'pass', [DEVICE_MAC], backend='aiounifi')
            await controller.connect()

            mock_session_cls.assert_called_once()
            assert controller._owns_session is True


# --- pyunifiapi backend tests ---


@contextmanager
def _fake_pyunifiapi(mock_config_cls=None, mock_ctrl_cls=None, mock_led_status_cls=None, mock_locate_cls=None):
    """Inject fake pyunifiapi modules into sys.modules so lazy imports resolve to mocks."""
    mock_config_cls = mock_config_cls or MagicMock()
    mock_ctrl_cls = mock_ctrl_cls or MagicMock()
    mock_led_status_cls = mock_led_status_cls or MagicMock()
    mock_locate_cls = mock_locate_cls or MagicMock()

    pkg = ModuleType('pyunifiapi')
    pkg.ControllerConfig = mock_config_cls

    ctrl_mod = ModuleType('pyunifiapi.controller')
    ctrl_mod.Controller = mock_ctrl_cls

    models_mod = ModuleType('pyunifiapi.models')
    device_mod = ModuleType('pyunifiapi.models.device')
    device_mod.DeviceSetLedStatus = mock_led_status_cls
    device_mod.DeviceLocateRequest = mock_locate_cls

    saved = {k: sys.modules.get(k) for k in ('pyunifiapi', 'pyunifiapi.controller', 'pyunifiapi.models', 'pyunifiapi.models.device')}
    sys.modules['pyunifiapi'] = pkg
    sys.modules['pyunifiapi.controller'] = ctrl_mod
    sys.modules['pyunifiapi.models'] = models_mod
    sys.modules['pyunifiapi.models.device'] = device_mod

    with patch('red_alert.integrations.unifi.led_controller._HAS_PYUNIFIAPI', True):
        try:
            yield
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v


class TestConnectPyunifiapi:
    @pytest.mark.asyncio
    async def test_connect_and_initialize(self):
        device = _mock_device(mac=DEVICE_MAC)
        mock_ctrl_instance = _mock_controller([device])
        mock_ctrl_cls = MagicMock(return_value=mock_ctrl_instance)

        with _fake_pyunifiapi(mock_ctrl_cls=mock_ctrl_cls):
            controller = UnifiLedController('192.168.1.1', 'admin', 'pass', [DEVICE_MAC], backend='pyunifiapi')
            await controller.connect()

            mock_ctrl_cls.assert_called_once()
            mock_ctrl_instance.connect.assert_called_once()
            mock_ctrl_instance.initialize.assert_called_once()
            assert controller._connected is True

    @pytest.mark.asyncio
    async def test_uses_execute_for_requests(self):
        device = _mock_device(mac=DEVICE_MAC)
        mock_ctrl_instance = _mock_controller([device])
        mock_ctrl_cls = MagicMock(return_value=mock_ctrl_instance)
        mock_led_cls = MagicMock()
        mock_led_cls.create = MagicMock(return_value=MagicMock())

        with _fake_pyunifiapi(mock_ctrl_cls=mock_ctrl_cls, mock_led_status_cls=mock_led_cls):
            controller = UnifiLedController('192.168.1.1', 'admin', 'pass', [DEVICE_MAC], backend='pyunifiapi')
            await controller.connect()
            await controller.set_led(on=True, color_hex='#FF0000', brightness=100)

            mock_ctrl_instance.execute.assert_called_once()
            mock_ctrl_instance.request.assert_not_called()

    @pytest.mark.asyncio
    async def test_close_disconnects(self):
        device = _mock_device(mac=DEVICE_MAC)
        mock_ctrl_instance = _mock_controller([device])
        mock_ctrl_cls = MagicMock(return_value=mock_ctrl_instance)

        with _fake_pyunifiapi(mock_ctrl_cls=mock_ctrl_cls):
            controller = UnifiLedController('192.168.1.1', 'admin', 'pass', [DEVICE_MAC], backend='pyunifiapi')
            await controller.connect()
            await controller.close()

            mock_ctrl_instance.disconnect.assert_called_once()
            assert controller._connected is False

    @pytest.mark.asyncio
    async def test_passes_totp_secret_natively(self):
        device = _mock_device(mac=DEVICE_MAC)
        mock_ctrl_instance = _mock_controller([device])
        mock_ctrl_cls = MagicMock(return_value=mock_ctrl_instance)
        mock_config_cls = MagicMock()

        with _fake_pyunifiapi(mock_config_cls=mock_config_cls, mock_ctrl_cls=mock_ctrl_cls):
            controller = UnifiLedController('192.168.1.1', 'admin', 'pass', [DEVICE_MAC], backend='pyunifiapi', totp_secret='JBSWY3DPEHPK3PXP')
            await controller.connect()

            mock_config_cls.assert_called_once_with(
                host='192.168.1.1',
                username='admin',
                password='pass',
                port=443,
                site='default',
                totp_secret='JBSWY3DPEHPK3PXP',
            )


# --- Backend-agnostic LED tests ---


class TestSetLed:
    @pytest.mark.asyncio
    async def test_sets_led_on_device(self):
        device = _mock_device(mac=DEVICE_MAC)
        mock_ctrl = _mock_controller([device])

        with patch('red_alert.integrations.unifi.led_controller.AioController', return_value=mock_ctrl):
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

        with patch('red_alert.integrations.unifi.led_controller.AioController', return_value=mock_ctrl):
            controller = UnifiLedController('192.168.1.1', 'admin', 'pass', [DEVICE_MAC, DEVICE_MAC_2])
            controller._session = AsyncMock()
            await controller.connect()
            await controller.set_led(on=True, color_hex='#00FF00', brightness=75)

            assert mock_ctrl.request.call_count == 2

    @pytest.mark.asyncio
    async def test_skips_if_state_unchanged(self):
        device = _mock_device(mac=DEVICE_MAC)
        mock_ctrl = _mock_controller([device])

        with patch('red_alert.integrations.unifi.led_controller.AioController', return_value=mock_ctrl):
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

        with patch('red_alert.integrations.unifi.led_controller.AioController', return_value=mock_ctrl):
            controller = UnifiLedController('192.168.1.1', 'admin', 'pass', [DEVICE_MAC])
            controller._session = AsyncMock()
            await controller.connect()

            await controller.set_led(on=True, color_hex='#FF0000', brightness=100)
            await controller.set_led(on=True, color_hex='#00FF00', brightness=50)
            assert mock_ctrl.request.call_count == 2

    @pytest.mark.asyncio
    async def test_skips_missing_device(self):
        mock_ctrl = _mock_controller([])  # no devices found

        with patch('red_alert.integrations.unifi.led_controller.AioController', return_value=mock_ctrl):
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
            patch('red_alert.integrations.unifi.led_controller.AioController', return_value=mock_ctrl),
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
            patch('red_alert.integrations.unifi.led_controller.AioController', return_value=mock_ctrl),
            patch('red_alert.integrations.unifi.led_controller.AioDeviceSetLedStatus') as mock_request_cls,
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
            patch('red_alert.integrations.unifi.led_controller.AioController', return_value=mock_ctrl),
            patch('red_alert.integrations.unifi.led_controller.AioDeviceLocateRequest') as mock_locate_cls,
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
            patch('red_alert.integrations.unifi.led_controller.AioController', return_value=mock_ctrl),
            patch('red_alert.integrations.unifi.led_controller.AioDeviceLocateRequest') as mock_locate_cls,
        ):
            mock_locate_cls.create = MagicMock(return_value=MagicMock())
            controller = UnifiLedController('192.168.1.1', 'admin', 'pass', [DEVICE_MAC])
            controller._session = AsyncMock()
            await controller.connect()
            await controller.locate(enable=False)

            mock_locate_cls.create.assert_called_once_with(DEVICE_MAC, locate=False)


def _mock_session():
    """Create a mock aiohttp session with cookie jar."""
    session = MagicMock()
    session.cookie_jar = MagicMock()
    session.cookie_jar.update_cookies = MagicMock()
    return session


def _mock_response(status=200):
    """Create a mock aiohttp response with the given status."""
    resp = MagicMock()
    resp.status = status
    return resp


class TestWrapRequestWith2faLocal:
    """Tests for local account 2FA (single-step with ubic_2fa_token)."""

    @pytest.mark.asyncio
    async def test_local_2fa_on_first_request_failure(self):
        """When the first request (no token) raises, retries with ubic_2fa_token."""
        from aiounifi.errors import Forbidden

        session = _mock_session()
        call_count = 0

        async def fake_request(method, url, json=None, allow_redirects=True):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call without token - local controller rejects
                raise Forbidden('403 Forbidden')
            return (_mock_response(200), b'{"meta":{"rc":"ok"}}')

        wrapped = _wrap_request_with_2fa(fake_request, 'JBSWY3DPEHPK3PXP', session)
        await wrapped('post', 'https://host/api/auth/login', json={'username': 'admin', 'password': 'pass'})

        assert call_count == 2

    @pytest.mark.asyncio
    async def test_local_2fa_injects_ubic_2fa_token(self):
        """The retry request includes ubic_2fa_token with a valid 6-digit TOTP."""
        from aiounifi.errors import Forbidden

        session = _mock_session()
        captured_json = {}

        async def fake_request(method, url, json=None, allow_redirects=True):
            if 'ubic_2fa_token' not in (json or {}):
                raise Forbidden('403 Forbidden')
            captured_json.update(json)
            return (_mock_response(200), b'{"meta":{"rc":"ok"}}')

        wrapped = _wrap_request_with_2fa(fake_request, 'JBSWY3DPEHPK3PXP', session)
        await wrapped('post', 'https://host/api/auth/login', json={'username': 'admin', 'password': 'pass'})

        token = captured_json['ubic_2fa_token']
        assert len(token) == 6
        assert token.isdigit()
        assert captured_json['username'] == 'admin'
        assert captured_json['password'] == 'pass'


class TestWrapRequestWith2faSso:
    """Tests for SSO/cloud account 2FA (two-step with MFA cookie + token field)."""

    @pytest.mark.asyncio
    async def test_sso_two_step_flow(self):
        """On 499 MFA challenge, extracts cookie and retries with token field."""
        import json as json_mod

        session = _mock_session()
        mfa_body = json_mod.dumps(
            {
                'data': {
                    'mfaCookie': 'UBIC_2FA=eyJhbGciOiJSUzI1NiJ9.test',
                    'authenticators': [{'type': 'totp', 'id': 'abc123'}],
                }
            }
        ).encode()
        call_count = 0

        async def fake_request(method, url, json=None, allow_redirects=True):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (_mock_response(499), mfa_body)
            return (_mock_response(200), b'{"meta":{"rc":"ok"}}')

        wrapped = _wrap_request_with_2fa(fake_request, 'JBSWY3DPEHPK3PXP', session)
        await wrapped('post', 'https://host/api/auth/login', json={'username': 'admin', 'password': 'pass'})

        assert call_count == 2
        session.cookie_jar.update_cookies.assert_called_once_with({'UBIC_2FA': 'eyJhbGciOiJSUzI1NiJ9.test'})

    @pytest.mark.asyncio
    async def test_sso_retries_with_token_field(self):
        """The SSO retry uses 'token' field (not ubic_2fa_token)."""
        import json as json_mod

        session = _mock_session()
        mfa_body = json_mod.dumps(
            {
                'data': {'mfaCookie': 'UBIC_2FA=jwt_value'},
            }
        ).encode()
        captured_json = {}

        async def fake_request(method, url, json=None, allow_redirects=True):
            if 'token' in (json or {}):
                captured_json.update(json)
                return (_mock_response(200), b'{"meta":{"rc":"ok"}}')
            return (_mock_response(499), mfa_body)

        wrapped = _wrap_request_with_2fa(fake_request, 'JBSWY3DPEHPK3PXP', session)
        await wrapped('post', 'https://host/api/auth/login', json={'username': 'admin', 'password': 'pass'})

        token = captured_json['token']
        assert len(token) == 6
        assert token.isdigit()
        assert 'ubic_2fa_token' not in captured_json

    @pytest.mark.asyncio
    async def test_sso_returns_raw_on_missing_mfa_cookie(self):
        """If 499 response has no mfaCookie, returns the raw response."""
        import json as json_mod

        session = _mock_session()
        mfa_body = json_mod.dumps({'data': {}}).encode()

        async def fake_request(method, url, json=None, allow_redirects=True):
            return (_mock_response(499), mfa_body)

        wrapped = _wrap_request_with_2fa(fake_request, 'JBSWY3DPEHPK3PXP', session)
        result = await wrapped('post', 'https://host/api/auth/login', json={'username': 'admin', 'password': 'pass'})

        assert result[0].status == 499


class TestWrapRequestWith2faPassthrough:
    """Tests for requests that should not be modified."""

    @pytest.mark.asyncio
    async def test_does_not_modify_non_login_requests(self):
        session = _mock_session()
        original = AsyncMock(return_value=(_mock_response(200), b'data'))
        wrapped = _wrap_request_with_2fa(original, 'JBSWY3DPEHPK3PXP', session)

        await wrapped('get', 'https://host/api/devices', json=None)

        original.assert_called_once_with('get', 'https://host/api/devices', json=None, allow_redirects=True)

    @pytest.mark.asyncio
    async def test_does_not_modify_post_without_username(self):
        session = _mock_session()
        original = AsyncMock(return_value=(_mock_response(200), b'data'))
        wrapped = _wrap_request_with_2fa(original, 'JBSWY3DPEHPK3PXP', session)

        payload = {'led_override': 'on'}
        await wrapped('post', 'https://host/api/devices/123', json=payload)

        original.assert_called_once_with('post', 'https://host/api/devices/123', json=payload, allow_redirects=True)

    @pytest.mark.asyncio
    async def test_no_2fa_needed_passes_through(self):
        """When first request succeeds (no 2FA required), returns directly."""
        session = _mock_session()
        original = AsyncMock(return_value=(_mock_response(200), b'{"meta":{"rc":"ok"}}'))
        wrapped = _wrap_request_with_2fa(original, 'JBSWY3DPEHPK3PXP', session)

        result = await wrapped('post', 'https://host/api/auth/login', json={'username': 'admin', 'password': 'pass'})

        assert result[0].status == 200
        original.assert_called_once()  # Only one call, no retry


class TestConnectWith2fa:
    @pytest.mark.asyncio
    async def test_connect_wraps_request_when_totp_secret_set(self):
        device = _mock_device(mac=DEVICE_MAC)
        mock_ctrl = _mock_controller([device])
        mock_ctrl.connectivity = MagicMock()
        mock_ctrl.connectivity._request = AsyncMock()

        with patch('red_alert.integrations.unifi.led_controller.AioController', return_value=mock_ctrl):
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

        with patch('red_alert.integrations.unifi.led_controller.AioController', return_value=mock_ctrl):
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
            patch('red_alert.integrations.unifi.led_controller.AioController', return_value=mock_ctrl),
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

        with patch('red_alert.integrations.unifi.led_controller.AioController', return_value=mock_ctrl):
            controller = UnifiLedController('192.168.1.1', 'admin', 'pass', [DEVICE_MAC], session=session)
            await controller.connect()
            await controller.close()

            session.close.assert_not_called()
