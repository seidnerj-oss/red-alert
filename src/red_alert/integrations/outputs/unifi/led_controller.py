"""
UniFi AP LED controller.

Controls LED color, brightness, on/off state, and locate (blink) mode
on UniFi access points through the UniFi Network controller REST API.

Supports two backends:
- aiounifi (default) - the library used by Home Assistant's UniFi integration
- pyunifiapi - native 2FA support, no aiohttp dependency

Uses a local controller account with optional TOTP-based 2FA.
"""

# pyright: reportMissingImports=false, reportPossiblyUnboundVariable=false

import asyncio
import functools
import logging
import re
from collections.abc import Callable, Mapping
from typing import Any

logger = logging.getLogger('red_alert.unifi')

HEX_COLOR_PATTERN = re.compile(r'^#(?:[0-9a-fA-F]{3}){1,2}$')

# Backend availability
_HAS_AIOUNIFI = False
_HAS_PYUNIFIAPI = False

try:
    import aiohttp
    from aiounifi.controller import Controller as AioController
    from aiounifi.models.configuration import Configuration as AioConfiguration
    from aiounifi.models.device import DeviceLocateRequest as AioDeviceLocateRequest
    from aiounifi.models.device import DeviceSetLedStatus as AioDeviceSetLedStatus

    _HAS_AIOUNIFI = True
except ImportError:
    pass

try:
    import pyunifiapi as _pyunifiapi_check  # noqa: F401

    _HAS_PYUNIFIAPI = True
except ImportError:
    pass


def _wrap_request_with_2fa(original_request, totp_secret: str, session: Any):
    """Wrap aiounifi's internal _request to inject TOTP 2FA token into login POST requests.

    Supports two auth flows:
    - Local accounts: single request with ``ubic_2fa_token`` field.
    - SSO/cloud accounts (UniFi OS gateways): two-step flow where the first
      login returns HTTP 499 with an MFA cookie, and a second login sends the
      TOTP code in the ``token`` field alongside that cookie.
    """
    import json as json_mod

    import pyotp

    @functools.wraps(original_request)
    async def _request_with_2fa(
        method: str,
        url: str,
        json: Mapping[str, Any] | None = None,
        allow_redirects: bool = True,
    ):
        if not (method == 'post' and json and 'username' in json):
            return await original_request(method, url, json=json, allow_redirects=allow_redirects)

        # First attempt: send login without any token to detect SSO vs local 2FA
        try:
            response, bytes_data = await original_request(method, url, json=json, allow_redirects=allow_redirects)
        except Exception:
            # Local 2FA: first request without token was rejected - retry with ubic_2fa_token
            token = pyotp.TOTP(totp_secret).now()
            return await original_request(method, url, json={**json, 'ubic_2fa_token': token}, allow_redirects=allow_redirects)

        if response.status != 499:
            # Not an SSO MFA challenge - return as-is (login succeeded or failed for other reasons)
            return response, bytes_data

        # SSO two-step flow: extract MFA cookie from 499 response body
        logger.debug('SSO MFA challenge received, performing two-step auth')
        try:
            body = json_mod.loads(bytes_data)
            mfa_cookie_str = body.get('data', {}).get('mfaCookie', '')
        except (ValueError, AttributeError):
            return response, bytes_data

        if not mfa_cookie_str or '=' not in mfa_cookie_str:
            return response, bytes_data

        # Set the MFA cookie on the aiohttp session
        cookie_name, cookie_val = mfa_cookie_str.split('=', 1)
        session.cookie_jar.update_cookies({cookie_name: cookie_val})

        # Re-login with the TOTP in the 'token' field (SSO expects this, not ubic_2fa_token)
        token = pyotp.TOTP(totp_secret).now()
        return await original_request(method, url, json={**json, 'token': token}, allow_redirects=allow_redirects)

    return _request_with_2fa


def rgb_to_hex(r: int, g: int, b: int) -> str:
    """Convert RGB values (0-255) to a hex color string like '#FF0000'."""
    return f'#{r:02X}{g:02X}{b:02X}'


class UnifiLedController:
    """Controls LED color/brightness/state on UniFi APs.

    Supports two backends:
    - ``'aiounifi'`` (default): uses aiounifi + aiohttp. 2FA via monkey-patch.
    - ``'pyunifiapi'``: uses pyunifiapi + httpx. Native 2FA support.
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        device_macs: list[str],
        port: int = 443,
        site: str | None = None,
        session: Any | None = None,
        totp_secret: str | None = None,
        backend: str = 'aiounifi',
    ):
        """
        Args:
            host: Hostname or IP of the UniFi controller.
            username: Controller login username.
            password: Controller login password.
            device_macs: List of device MAC addresses to control.
            port: Controller port (default: 443).
            site: UniFi site name. Required when the controller has multiple sites.
                  If None, auto-detected when only one site exists.
            session: Optional aiohttp.ClientSession (aiounifi backend only). Ignored by pyunifiapi.
            totp_secret: Optional TOTP secret (base32) for 2FA.
            backend: Backend library to use: 'aiounifi' (default) or 'pyunifiapi'.
        """
        self._device_macs = [mac.lower() for mac in device_macs]
        self._session = session
        self._owns_session = session is None
        self._controller: Any = None
        self._connected = False
        self._current_state: tuple | None = None

        self._host = host
        self._username = username
        self._password = password
        self._port = port
        self._site = site
        self._totp_secret = totp_secret
        self._backend = backend

        # Resolved at connect time based on backend
        self._send_request: Callable | None = None
        self._DeviceSetLedStatus: Any = None
        self._DeviceLocateRequest: Any = None

    async def connect(self):
        """Authenticate with the controller and load device list."""
        if self._backend == 'pyunifiapi':
            await self._connect_pyunifiapi()
        else:
            await self._connect_aiounifi()

    async def _connect_aiounifi(self):
        """Connect using aiounifi backend."""
        if not _HAS_AIOUNIFI:
            raise ImportError('aiounifi is not installed. Install with: pip install "red-alert[unifi]"')

        import ssl

        if self._session is None:
            self._session = aiohttp.ClientSession()

        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        # Use configured site or 'default' for initial connection
        config = AioConfiguration(
            session=self._session,
            host=self._host,
            username=self._username,
            password=self._password,
            port=self._port,
            site=self._site or 'default',
            ssl_context=ssl_context,
        )
        self._controller = AioController(config)

        if self._totp_secret:
            self._controller.connectivity._request = _wrap_request_with_2fa(self._controller.connectivity._request, self._totp_secret, self._session)

        await self._controller.login()
        await self._controller.sites.update()
        self._validate_site()
        await self._controller.devices.update()

        self._send_request = self._controller.request
        self._DeviceSetLedStatus = AioDeviceSetLedStatus
        self._DeviceLocateRequest = AioDeviceLocateRequest

        self._log_device_discovery()
        self._connected = True

    def _validate_site(self):
        """Validate the configured site against available sites on the controller.

        Must be called after sites have been fetched (aiounifi: sites.update(), pyunifiapi: initialize()).
        Raises ValueError if multiple sites exist and no site was explicitly configured.
        """
        sites = list(self._controller.sites.values())

        if not sites:
            logger.warning('No sites returned by controller')
            return

        site_info = [(s.name, s.description) for s in sites]

        if len(sites) == 1:
            site = sites[0]
            logger.info('Using site "%s" (%s)', site.name, site.description)
            return

        # Multiple sites - require explicit configuration
        site_list = ', '.join(f'"{name}" ({desc})' for name, desc in site_info)
        if self._site is None:
            raise ValueError(f'Multiple UniFi sites found: {site_list}. Set "site" in your config to specify which one to use.')

        site_names = [name for name, _ in site_info]
        if self._site not in site_names:
            raise ValueError(f'Configured site "{self._site}" not found. Available sites: {site_list}')

        logger.info('Using site "%s" (%d sites available)', self._site, len(sites))

    async def _connect_pyunifiapi(self):
        """Connect using pyunifiapi backend."""
        if not _HAS_PYUNIFIAPI:
            raise ImportError('pyunifiapi is not installed. Install with: pip install pyunifiapi')

        from pyunifiapi import ControllerConfig as PyControllerConfig
        from pyunifiapi.controller import Controller as PyController
        from pyunifiapi.models.device import DeviceLocateRequest as PyDeviceLocateRequest
        from pyunifiapi.models.device import DeviceSetLedStatus as PyDeviceSetLedStatus

        config = PyControllerConfig(
            host=self._host,
            username=self._username,
            password=self._password,
            port=self._port,
            site=self._site or 'default',
            totp_secret=self._totp_secret,
        )
        self._controller = PyController(config)
        await self._controller.connect()
        await self._controller.initialize()  # fetches sites among other data
        self._validate_site()

        self._send_request = self._controller.execute
        self._DeviceSetLedStatus = PyDeviceSetLedStatus
        self._DeviceLocateRequest = PyDeviceLocateRequest

        self._log_device_discovery()
        self._connected = True

    def _log_device_discovery(self):
        """Log which configured devices were found on the controller."""
        found = [mac for mac in self._device_macs if mac in self._controller.devices]
        missing = [mac for mac in self._device_macs if mac not in self._controller.devices]
        if missing:
            logger.warning('Devices not found on controller: %s', ', '.join(missing))
        logger.info('Connected to UniFi controller (%s), %d/%d device(s) found', self._backend, len(found), len(self._device_macs))

    async def _ensure_connected(self):
        if not self._connected:
            await self.connect()

    async def set_led(self, on: bool = True, color_hex: str = '#FFFFFF', brightness: int = 100):
        """Set LED state on all configured devices.

        Skips the update if the state hasn't changed since the last call.

        Args:
            on: Whether the LED should be on.
            color_hex: Hex color string (e.g., '#FF0000').
            brightness: Brightness percentage (0-100).
        """
        state = (on, color_hex, brightness)
        if state == self._current_state:
            return

        await self._ensure_connected()

        tasks = [self._set_device_led_inner(mac, on, color_hex, brightness) for mac in self._device_macs]
        await asyncio.gather(*tasks, return_exceptions=True)
        self._current_state = state

    async def set_device_led(self, mac: str, on: bool = True, color_hex: str = '#FFFFFF', brightness: int = 100):
        """Set LED state on a single device.

        Args:
            mac: Device MAC address.
            on: Whether the LED should be on.
            color_hex: Hex color string (e.g., '#FF0000').
            brightness: Brightness percentage (0-100).
        """
        await self._ensure_connected()
        await self._set_device_led_inner(mac, on, color_hex, brightness)

    async def _set_device_led_inner(self, mac: str, on: bool, color_hex: str, brightness: int):
        """Set LED state on a single device (internal, no connect check)."""
        assert self._controller is not None
        assert self._send_request is not None
        device = self._controller.devices.get(mac)
        if device is None:
            logger.warning('Device %s not found, skipping LED update', mac)
            return

        try:
            status = 'on' if on else 'off'
            request = self._DeviceSetLedStatus.create(
                device,
                status=status,
                brightness=brightness if device.supports_led_ring else None,
                color=color_hex if device.supports_led_ring else None,
            )
            await self._send_request(request)
            logger.info('LED set on %s: on=%s, color=%s, brightness=%d', mac, on, color_hex, brightness)
        except Exception as e:
            logger.error('Error setting LED on %s: %s', mac, e)

    async def locate(self, enable: bool = True):
        """Enable or disable locate mode (blinking) on all configured devices.

        Args:
            enable: True to start blinking, False to stop.
        """
        await self._ensure_connected()

        tasks = [self._locate_device(mac, enable) for mac in self._device_macs]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _locate_device(self, mac: str, enable: bool):
        """Enable or disable locate mode on a single device."""
        assert self._controller is not None
        assert self._send_request is not None
        try:
            request = self._DeviceLocateRequest.create(mac, locate=enable)
            await self._send_request(request)
            logger.info('Locate %s on %s', 'enabled' if enable else 'disabled', mac)
        except Exception as e:
            logger.error('Error setting locate on %s: %s', mac, e)

    async def close(self):
        """Close the connection and release resources."""
        if self._backend == 'pyunifiapi' and self._controller:
            await self._controller.disconnect()
        elif self._owns_session and self._session:
            await self._session.close()
            self._session = None
        self._connected = False
