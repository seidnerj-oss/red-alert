from unittest.mock import AsyncMock

import httpx
import pytest

from red_alert.integrations.hue.light_controller import HueLightController, rgb_to_hue_state


def _mock_hue_client(status_code=200):
    """Create a mock httpx.AsyncClient for Hue Bridge API calls."""
    mock_client = AsyncMock()
    mock_resp = httpx.Response(status_code=status_code, request=httpx.Request('PUT', 'http://test'))
    mock_client.put = AsyncMock(return_value=mock_resp)
    return mock_client


class TestRgbToHueState:
    def test_red(self):
        state = rgb_to_hue_state(255, 0, 0)
        assert state['on'] is True
        assert state['hue'] == 0
        assert state['sat'] == 254
        assert state['bri'] == 254

    def test_green(self):
        state = rgb_to_hue_state(0, 255, 0)
        assert state['on'] is True
        assert state['sat'] == 254
        assert state['bri'] == 254
        assert 21000 < state['hue'] < 22000  # ~120 degrees

    def test_yellow(self):
        state = rgb_to_hue_state(255, 255, 0)
        assert state['on'] is True
        assert state['sat'] == 254
        assert 10000 < state['hue'] < 11500  # ~60 degrees

    def test_white(self):
        state = rgb_to_hue_state(255, 255, 255)
        assert state['on'] is True
        assert state['sat'] == 0
        assert state['bri'] == 254
        assert 'hue' not in state

    def test_dim_red(self):
        state = rgb_to_hue_state(128, 0, 0)
        assert state['bri'] == 127  # half brightness


class TestSetColor:
    @pytest.mark.asyncio
    async def test_sets_color_on_all_lights(self):
        client = _mock_hue_client()
        controller = HueLightController('192.168.1.50', 'key', lights=[1, 2, 3], client=client)

        await controller.set_color(255, 0, 0)

        assert client.put.call_count == 3
        urls = [c.args[0] for c in client.put.call_args_list]
        assert 'http://192.168.1.50/api/key/lights/1/state' in urls
        assert 'http://192.168.1.50/api/key/lights/2/state' in urls
        assert 'http://192.168.1.50/api/key/lights/3/state' in urls

    @pytest.mark.asyncio
    async def test_sets_color_on_groups(self):
        client = _mock_hue_client()
        controller = HueLightController('192.168.1.50', 'key', groups=[0, 1], client=client)

        await controller.set_color(255, 0, 0)

        assert client.put.call_count == 2
        urls = [c.args[0] for c in client.put.call_args_list]
        assert 'http://192.168.1.50/api/key/groups/0/action' in urls
        assert 'http://192.168.1.50/api/key/groups/1/action' in urls

    @pytest.mark.asyncio
    async def test_sets_both_lights_and_groups(self):
        client = _mock_hue_client()
        controller = HueLightController('192.168.1.50', 'key', lights=[1], groups=[0], client=client)

        await controller.set_color(0, 255, 0)

        assert client.put.call_count == 2

    @pytest.mark.asyncio
    async def test_skips_if_same_color(self):
        client = _mock_hue_client()
        controller = HueLightController('192.168.1.50', 'key', lights=[1], client=client)

        await controller.set_color(255, 0, 0)
        assert client.put.call_count == 1

        client.put.reset_mock()
        await controller.set_color(255, 0, 0)
        assert client.put.call_count == 0

    @pytest.mark.asyncio
    async def test_updates_on_different_color(self):
        client = _mock_hue_client()
        controller = HueLightController('192.168.1.50', 'key', lights=[1], client=client)

        await controller.set_color(255, 0, 0)
        client.put.reset_mock()
        await controller.set_color(0, 255, 0)
        assert client.put.call_count == 1

    @pytest.mark.asyncio
    async def test_sends_correct_hue_state(self):
        client = _mock_hue_client()
        controller = HueLightController('192.168.1.50', 'key', lights=[1], client=client)

        await controller.set_color(255, 0, 0)

        call_kwargs = client.put.call_args_list[0].kwargs
        payload = call_kwargs['json']
        assert payload['on'] is True
        assert payload['hue'] == 0
        assert payload['sat'] == 254

    @pytest.mark.asyncio
    async def test_handles_connection_error(self):
        client = AsyncMock()
        client.put = AsyncMock(side_effect=OSError('Connection refused'))
        controller = HueLightController('192.168.1.50', 'key', lights=[1], client=client)

        # Should not raise
        await controller.set_color(255, 0, 0)

    @pytest.mark.asyncio
    async def test_empty_lights_and_groups_does_nothing(self):
        client = _mock_hue_client()
        controller = HueLightController('192.168.1.50', 'key', client=client)

        await controller.set_color(255, 0, 0)
        assert client.put.call_count == 0

    @pytest.mark.asyncio
    async def test_light_ids_converted_to_strings(self):
        client = _mock_hue_client()
        controller = HueLightController('192.168.1.50', 'key', lights=[1, '2'], groups=[0], client=client)

        await controller.set_color(255, 0, 0)

        urls = [c.args[0] for c in client.put.call_args_list]
        assert 'http://192.168.1.50/api/key/lights/1/state' in urls
        assert 'http://192.168.1.50/api/key/lights/2/state' in urls
        assert 'http://192.168.1.50/api/key/groups/0/action' in urls
