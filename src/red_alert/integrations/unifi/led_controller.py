"""
UniFi AP LED controller via aiounifi.

Controls LED color, brightness, on/off state, and locate (blink) mode
on UniFi access points through the UniFi Network controller REST API.

Uses aiounifi for authentication, session management, and API calls.
Requires a local controller account (not cloud/SSO). 2FA is not supported.
"""

import asyncio
import logging
import re
import ssl

import aiohttp

from aiounifi import Controller
from aiounifi.models.configuration import Configuration
from aiounifi.models.device import DeviceLocateRequest, DeviceSetLedStatus

logger = logging.getLogger('red_alert.unifi')

HEX_COLOR_PATTERN = re.compile(r'^#(?:[0-9a-fA-F]{3}){1,2}$')


def rgb_to_hex(r: int, g: int, b: int) -> str:
    """Convert RGB values (0-255) to a hex color string like '#FF0000'."""
    return f'#{r:02X}{g:02X}{b:02X}'


class UnifiLedController:
    """Controls LED color/brightness/state on UniFi APs via aiounifi."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        device_macs: list[str],
        port: int = 443,
        site: str = 'default',
        session: aiohttp.ClientSession | None = None,
    ):
        """
        Args:
            host: Hostname or IP of the UniFi controller (e.g., '192.168.1.1').
            username: Controller login username (local account, not cloud/SSO).
            password: Controller login password.
            device_macs: List of device MAC addresses to control.
            port: Controller port (default: 443 for UniFi OS).
            site: UniFi site name (default: 'default').
            session: Optional aiohttp.ClientSession. If not provided, one is created internally.
        """
        self._device_macs = [mac.lower() for mac in device_macs]
        self._session = session
        self._owns_session = session is None
        self._controller: Controller | None = None
        self._connected = False
        self._current_state: tuple | None = None

        self._host = host
        self._username = username
        self._password = password
        self._port = port
        self._site = site

    async def connect(self):
        """Authenticate with the controller and load device list."""
        if self._session is None:
            self._session = aiohttp.ClientSession()

        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        config = Configuration(
            session=self._session,
            host=self._host,
            username=self._username,
            password=self._password,
            port=self._port,
            site=self._site,
            ssl_context=ssl_context,
        )
        self._controller = Controller(config)
        await self._controller.login()
        await self._controller.devices.update()

        found = [mac for mac in self._device_macs if mac in self._controller.devices]
        missing = [mac for mac in self._device_macs if mac not in self._controller.devices]

        if missing:
            logger.warning('Devices not found on controller: %s', ', '.join(missing))
        logger.info('Connected to UniFi controller, %d/%d device(s) found', len(found), len(self._device_macs))
        self._connected = True

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

        tasks = [self._set_device_led(mac, on, color_hex, brightness) for mac in self._device_macs]
        await asyncio.gather(*tasks, return_exceptions=True)
        self._current_state = state

    async def _set_device_led(self, mac: str, on: bool, color_hex: str, brightness: int):
        """Set LED state on a single device."""
        device = self._controller.devices.get(mac)
        if device is None:
            logger.warning('Device %s not found, skipping LED update', mac)
            return

        try:
            status = 'on' if on else 'off'
            request = DeviceSetLedStatus.create(
                device,
                status=status,
                brightness=brightness if device.supports_led_ring else None,
                color=color_hex if device.supports_led_ring else None,
            )
            await self._controller.request(request)
            logger.debug('LED set on %s: on=%s, color=%s, brightness=%d', mac, on, color_hex, brightness)
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
        try:
            request = DeviceLocateRequest.create(mac, locate=enable)
            await self._controller.request(request)
            logger.debug('Locate %s on %s', 'enabled' if enable else 'disabled', mac)
        except Exception as e:
            logger.error('Error setting locate on %s: %s', mac, e)

    async def close(self):
        """Close the HTTP session if we own it."""
        if self._owns_session and self._session:
            await self._session.close()
            self._session = None
        self._connected = False
