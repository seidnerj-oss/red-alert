"""
UniFi AP LED controller via SSH.

Changes the RGB LED color on UniFi U6/U7 access points by writing
to /proc/ubnt_ledbar/custom_color over SSH.

Requires:
    - SSH key-based auth configured on each AP
    - Device SSH Authentication enabled in UniFi Network settings
    - asyncssh package installed
"""

import asyncio
import logging

import asyncssh

logger = logging.getLogger('red_alert.unifi')

LED_PATH = '/proc/ubnt_ledbar/custom_color'


class UnifiLedController:
    """Controls the LED color on one or more UniFi access points via SSH."""

    def __init__(
        self,
        devices: list[dict],
        ssh_username: str = 'admin',
        ssh_key_path: str | None = None,
        known_hosts: str | None = None,
    ):
        """
        Args:
            devices: List of dicts with 'host' (required) and optional 'port' (default 22).
            ssh_username: SSH username for AP login.
            ssh_key_path: Path to SSH private key file. None uses default keys.
            known_hosts: Path to known_hosts file. None disables host key checking.
        """
        self._devices = devices
        self._username = ssh_username
        self._key_path = ssh_key_path
        self._known_hosts = known_hosts
        self._current_color: tuple[int, int, int] | None = None

    async def set_color(self, r: int, g: int, b: int):
        """Set LED color on all configured devices.

        Skips the update if the color hasn't changed since the last call.
        """
        color = (r, g, b)
        if color == self._current_color:
            return

        tasks = [self._set_device_color(dev, r, g, b) for dev in self._devices]
        await asyncio.gather(*tasks, return_exceptions=True)
        self._current_color = color

    async def _set_device_color(self, device: dict, r: int, g: int, b: int):
        """Set LED color on a single device."""
        host = device['host']
        port = device.get('port', 22)
        cmd = f'echo -n {r},{g},{b} > {LED_PATH}'

        connect_kwargs = {
            'host': host,
            'port': port,
            'username': self._username,
            'known_hosts': self._known_hosts,
        }
        if self._key_path:
            connect_kwargs['client_keys'] = [self._key_path]

        try:
            async with asyncssh.connect(**connect_kwargs) as conn:
                result = await conn.run(cmd, check=True)
                if result.stderr:
                    logger.warning('LED command stderr on %s: %s', host, result.stderr.strip())
                else:
                    logger.debug('LED set to %d,%d,%d on %s', r, g, b, host)
        except asyncssh.Error as e:
            logger.error('SSH error setting LED on %s:%d: %s', host, port, e)
        except OSError as e:
            logger.error('Connection error to %s:%d: %s', host, port, e)
