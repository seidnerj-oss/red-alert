from unittest.mock import AsyncMock

import pytest

from red_alert.core.state import AlertState
from red_alert.integrations.outputs.hue.server import (
    HueAlertMonitor,
    _build_light_overrides,
    _build_state_colors,
    _resolve_color,
)


class TestResolveColor:
    def test_named_color(self):
        assert _resolve_color('red') == (255, 0, 0)

    def test_named_color_warm(self):
        assert _resolve_color('warm') == (255, 180, 100)

    def test_unknown_name_falls_back_to_white(self):
        assert _resolve_color('magenta') == (255, 255, 255)

    def test_hex_string(self):
        assert _resolve_color('#FF0000') == (255, 0, 0)

    def test_rgb_list(self):
        assert _resolve_color([128, 64, 32]) == (128, 64, 32)


class TestBuildStateColors:
    def test_defaults(self):
        colors = _build_state_colors({})
        assert colors[AlertState.ALERT] == (255, 0, 0)
        assert colors[AlertState.PRE_ALERT] == (255, 255, 0)
        assert colors[AlertState.ALL_CLEAR] == (0, 255, 0)
        assert colors[AlertState.ROUTINE] == (255, 255, 255)

    def test_override_routine(self):
        colors = _build_state_colors({'routine': 'warm'})
        assert colors[AlertState.ROUTINE] == (255, 180, 100)
        assert colors[AlertState.ALERT] == (255, 0, 0)

    def test_override_with_hex(self):
        colors = _build_state_colors({'alert': '#00FF00'})
        assert colors[AlertState.ALERT] == (0, 255, 0)


class TestBuildLightOverrides:
    def test_no_overrides_returns_none(self):
        base = _build_state_colors({})
        result = _build_light_overrides(base, ['1', '2'], [], {})
        assert result is None

    def test_override_one_light(self):
        base = _build_state_colors({})
        overrides = {'1': {'state_colors': {'alert': 'blue'}}}
        result = _build_light_overrides(base, ['1', '2'], [], overrides)

        assert result['1'][AlertState.ALERT] == (0, 0, 255)
        assert result['1'][AlertState.ROUTINE] == (255, 255, 255)  # inherits base
        assert result['2'] is base  # no override

    def test_override_group(self):
        base = _build_state_colors({})
        overrides = {'0': {'state_colors': {'routine': 'warm'}}}
        result = _build_light_overrides(base, [], ['0'], overrides)

        assert result['0'][AlertState.ROUTINE] == (255, 180, 100)
        assert result['0'][AlertState.ALERT] == (255, 0, 0)

    def test_flat_override_format(self):
        """Override without 'state_colors' wrapper (flat dict of state -> color)."""
        base = _build_state_colors({})
        overrides = {'1': {'alert': 'blue', 'routine': 'warm'}}
        result = _build_light_overrides(base, ['1'], [], overrides)

        assert result['1'][AlertState.ALERT] == (0, 0, 255)
        assert result['1'][AlertState.ROUTINE] == (255, 180, 100)


class TestHueAlertMonitor:
    def _make_monitor(self, state_colors=None, light_overrides=None):
        api_client = AsyncMock()
        light_controller = AsyncMock()
        light_controller._lights = ['1', '2']
        light_controller._groups = []
        state_tracker = AsyncMock()
        monitor = HueAlertMonitor(api_client, light_controller, state_tracker, state_colors, light_overrides)
        return monitor, api_client, light_controller, state_tracker

    @pytest.mark.asyncio
    async def test_poll_alert_sends_red(self):
        monitor, api_client, lights, state_tracker = self._make_monitor()
        api_client.get_live_alerts = AsyncMock(return_value={'data': []})
        state_tracker.update = lambda data: AlertState.ALERT
        state = await monitor.poll()
        assert state == AlertState.ALERT
        lights.set_color.assert_called_once_with(255, 0, 0)

    @pytest.mark.asyncio
    async def test_poll_routine_sends_white(self):
        monitor, api_client, lights, state_tracker = self._make_monitor()
        api_client.get_live_alerts = AsyncMock(return_value={'data': []})
        state_tracker.update = lambda data: AlertState.ROUTINE
        state = await monitor.poll()
        assert state == AlertState.ROUTINE
        lights.set_color.assert_called_once_with(255, 255, 255)

    @pytest.mark.asyncio
    async def test_no_update_when_state_unchanged(self):
        monitor, api_client, lights, state_tracker = self._make_monitor()
        api_client.get_live_alerts = AsyncMock(return_value={'data': []})
        state_tracker.update = lambda data: AlertState.ROUTINE
        await monitor.poll()
        await monitor.poll()
        lights.set_color.assert_called_once()

    @pytest.mark.asyncio
    async def test_custom_state_colors(self):
        colors = _build_state_colors({'routine': 'warm'})
        monitor, api_client, lights, state_tracker = self._make_monitor(state_colors=colors)
        api_client.get_live_alerts = AsyncMock(return_value={'data': []})
        state_tracker.update = lambda data: AlertState.ROUTINE
        await monitor.poll()
        lights.set_color.assert_called_once_with(255, 180, 100)


class TestHueAlertMonitorPerLight:
    def _make_monitor_with_overrides(self, light_ids, group_ids, overrides_cfg):
        base_colors = _build_state_colors({})
        light_overrides = _build_light_overrides(base_colors, light_ids, group_ids, overrides_cfg)

        api_client = AsyncMock()
        light_controller = AsyncMock()
        light_controller._lights = light_ids
        light_controller._groups = group_ids
        state_tracker = AsyncMock()
        monitor = HueAlertMonitor(api_client, light_controller, state_tracker, base_colors, light_overrides)
        return monitor, api_client, light_controller, state_tracker

    @pytest.mark.asyncio
    async def test_per_light_different_alert_colors(self):
        overrides = {'1': {'state_colors': {'alert': 'blue'}}}
        monitor, api_client, lights, state_tracker = self._make_monitor_with_overrides(['1', '2'], [], overrides)

        api_client.get_live_alerts = AsyncMock(return_value={'data': []})
        state_tracker.update = lambda data: AlertState.ALERT
        await monitor.poll()

        assert lights.set_light_color.call_count == 2
        calls = {call.args[0]: call for call in lights.set_light_color.call_args_list}
        # Light 1 gets blue (overridden)
        assert calls['1'].args[1:] == (0, 0, 255)
        # Light 2 gets red (base)
        assert calls['2'].args[1:] == (255, 0, 0)

    @pytest.mark.asyncio
    async def test_per_light_routine_same_without_override(self):
        overrides = {'1': {'state_colors': {'alert': 'blue'}}}
        monitor, api_client, lights, state_tracker = self._make_monitor_with_overrides(['1', '2'], [], overrides)

        api_client.get_live_alerts = AsyncMock(return_value={'data': []})
        state_tracker.update = lambda data: AlertState.ROUTINE
        await monitor.poll()

        calls = {call.args[0]: call for call in lights.set_light_color.call_args_list}
        # Both get white (base routine color) since no routine override
        assert calls['1'].args[1:] == (255, 255, 255)
        assert calls['2'].args[1:] == (255, 255, 255)
