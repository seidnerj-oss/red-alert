"""
Philips Hue light controller.

Changes the color of Hue lights/groups based on alert state using the
Hue Bridge local REST API (v1/CLIP).

Requires:
    - Hue Bridge on the local network
    - API key obtained via bridge link button registration
    - httpx package installed
"""

import asyncio
import colorsys
import logging

import httpx

logger = logging.getLogger('red_alert.hue')


def rgb_to_hue_state(r: int, g: int, b: int) -> dict:
    """Convert RGB (0-255) to Hue Bridge API light state parameters."""
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    state = {'on': True, 'bri': max(1, int(v * 254))}
    if s > 0.01:
        state['hue'] = int(h * 65535)
        state['sat'] = int(s * 254)
    else:
        state['sat'] = 0
    return state


class HueLightController:
    """Controls the color of one or more Philips Hue lights and/or groups."""

    def __init__(
        self,
        bridge_ip: str,
        api_key: str,
        lights: list[int | str] | None = None,
        groups: list[int | str] | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        """
        Args:
            bridge_ip: IP address of the Hue Bridge.
            api_key: API key obtained via bridge registration.
            lights: List of individual light IDs to control.
            groups: List of group IDs to control (more efficient for multiple lights in a room).
            client: Optional httpx client. If not provided, one is created on first use.
        """
        self._bridge_ip = bridge_ip
        self._api_key = api_key
        self._lights = [str(lid) for lid in (lights or [])]
        self._groups = [str(gid) for gid in (groups or [])]
        self._client = client
        self._owns_client = client is None
        self._current_color: tuple[int, int, int] | None = None
        self._base_url = f'http://{bridge_ip}/api/{api_key}'

    async def set_color(self, r: int, g: int, b: int):
        """Set color on all configured lights and groups.

        Skips the update if the color hasn't changed since the last call.
        """
        color = (r, g, b)
        if color == self._current_color:
            return

        if self._client is None:
            self._client = httpx.AsyncClient()

        state = rgb_to_hue_state(r, g, b)
        tasks = []
        for lid in self._lights:
            tasks.append(self._put(f'{self._base_url}/lights/{lid}/state', state, f'light {lid}'))
        for gid in self._groups:
            tasks.append(self._put(f'{self._base_url}/groups/{gid}/action', state, f'group {gid}'))
        await asyncio.gather(*tasks, return_exceptions=True)
        self._current_color = color

    async def _put(self, url: str, payload: dict, label: str):
        try:
            resp = await self._client.put(url, json=payload)
            if resp.status_code != 200:
                logger.error('Hue API error for %s: %s %s', label, resp.status_code, resp.text)
            else:
                logger.debug('Set %s', label)
        except OSError as e:
            logger.error('Connection error setting %s: %s', label, e)

    async def close(self):
        """Close the HTTP client if owned by this controller."""
        if self._owns_client and self._client:
            await self._client.aclose()
            self._client = None
