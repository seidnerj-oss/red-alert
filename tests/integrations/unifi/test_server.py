from unittest.mock import AsyncMock

import pytest

from red_alert.core.state import AlertState
from red_alert.integrations.unifi.server import (
    NAMED_COLORS,
    UnifiAlertMonitor,
    _build_device_led_states,
    _build_led_states,
    _resolve_color,
    _resolve_led_state,
)


class TestResolveColor:
    def test_named_color(self):
        assert _resolve_color('red') == (255, 0, 0)

    def test_named_color_white(self):
        assert _resolve_color('white') == (255, 255, 255)

    def test_unknown_name_falls_back_to_white(self):
        assert _resolve_color('magenta') == NAMED_COLORS['white']

    def test_rgb_list(self):
        assert _resolve_color([128, 64, 32]) == (128, 64, 32)

    def test_rgb_tuple(self):
        assert _resolve_color((10, 20, 30)) == (10, 20, 30)

    def test_hex_string(self):
        assert _resolve_color('#821E1E') == (130, 30, 30)

    def test_hex_string_red(self):
        assert _resolve_color('#FF0000') == (255, 0, 0)


class TestResolveLedState:
    def test_defaults(self):
        result = _resolve_led_state({})
        assert result == {'on': True, 'color': (255, 255, 255), 'brightness': 100, 'blink': False}

    def test_explicit_values(self):
        result = _resolve_led_state({'on': False, 'color': 'red', 'brightness': 50, 'blink': True})
        assert result == {'on': False, 'color': (255, 0, 0), 'brightness': 50, 'blink': True}

    def test_brightness_clamped_to_0(self):
        result = _resolve_led_state({'brightness': -10})
        assert result['brightness'] == 0

    def test_brightness_clamped_to_100(self):
        result = _resolve_led_state({'brightness': 200})
        assert result['brightness'] == 100


class TestBuildLedStates:
    def test_defaults(self):
        states = _build_led_states({})
        assert states[AlertState.ALERT] == {'on': True, 'color': (255, 0, 0), 'brightness': 100, 'blink': False}
        assert states[AlertState.PRE_ALERT] == {'on': True, 'color': (255, 255, 0), 'brightness': 100, 'blink': False}
        assert states[AlertState.ROUTINE] == {'on': True, 'color': (255, 255, 255), 'brightness': 100, 'blink': False}

    def test_partial_override(self):
        states = _build_led_states({'routine': {'brightness': 30}})
        assert states[AlertState.ROUTINE]['brightness'] == 30
        assert states[AlertState.ROUTINE]['color'] == (255, 255, 255)
        assert states[AlertState.ROUTINE]['on'] is True

    def test_turn_off_routine(self):
        states = _build_led_states({'routine': {'on': False}})
        assert states[AlertState.ROUTINE]['on'] is False
        assert states[AlertState.ALERT]['on'] is True
        assert states[AlertState.PRE_ALERT]['on'] is True

    def test_custom_color_override(self):
        states = _build_led_states({'alert': {'color': [255, 128, 0]}})
        assert states[AlertState.ALERT]['color'] == (255, 128, 0)

    def test_hex_color_override(self):
        states = _build_led_states({'alert': {'color': '#821E1E'}})
        assert states[AlertState.ALERT]['color'] == (130, 30, 30)

    def test_blink_override(self):
        states = _build_led_states({'alert': {'blink': True}})
        assert states[AlertState.ALERT]['blink'] is True
        assert states[AlertState.ROUTINE]['blink'] is False

    def test_full_custom_config(self):
        user_cfg = {
            'alert': {'on': True, 'color': 'red', 'brightness': 100, 'blink': True},
            'pre_alert': {'on': True, 'color': 'yellow', 'brightness': 75},
            'routine': {'on': False},
        }
        states = _build_led_states(user_cfg)
        assert states[AlertState.ALERT]['blink'] is True
        assert states[AlertState.PRE_ALERT]['brightness'] == 75
        assert states[AlertState.ROUTINE]['on'] is False


class TestUnifiAlertMonitor:
    def _make_monitor(self, led_states=None):
        api_client = AsyncMock()
        led_controller = AsyncMock()
        state_tracker = AsyncMock()
        monitor = UnifiAlertMonitor(api_client, led_controller, state_tracker, led_states)
        return monitor, api_client, led_controller, state_tracker

    @pytest.mark.asyncio
    async def test_poll_alert_sends_red(self):
        monitor, api_client, led, state_tracker = self._make_monitor()
        api_client.get_live_alerts = AsyncMock(return_value={'data': []})
        state_tracker.update = lambda data: AlertState.ALERT
        state = await monitor.poll()
        assert state == AlertState.ALERT
        led.set_led.assert_called_once_with(on=True, color_hex='#FF0000', brightness=100)

    @pytest.mark.asyncio
    async def test_poll_pre_alert_sends_yellow(self):
        monitor, api_client, led, state_tracker = self._make_monitor()
        api_client.get_live_alerts = AsyncMock(return_value={'data': []})
        state_tracker.update = lambda data: AlertState.PRE_ALERT
        state = await monitor.poll()
        assert state == AlertState.PRE_ALERT
        led.set_led.assert_called_once_with(on=True, color_hex='#FFFF00', brightness=100)

    @pytest.mark.asyncio
    async def test_poll_routine_sends_white(self):
        monitor, api_client, led, state_tracker = self._make_monitor()
        api_client.get_live_alerts = AsyncMock(return_value={'data': []})
        state_tracker.update = lambda data: AlertState.ROUTINE
        state = await monitor.poll()
        assert state == AlertState.ROUTINE
        led.set_led.assert_called_once_with(on=True, color_hex='#FFFFFF', brightness=100)

    @pytest.mark.asyncio
    async def test_poll_with_led_off(self):
        led_states = _build_led_states({'routine': {'on': False}})
        monitor, api_client, led, state_tracker = self._make_monitor(led_states)
        api_client.get_live_alerts = AsyncMock(return_value={'data': []})
        state_tracker.update = lambda data: AlertState.ROUTINE
        await monitor.poll()
        led.set_led.assert_called_once_with(on=False, color_hex='#FFFFFF', brightness=100)

    @pytest.mark.asyncio
    async def test_poll_with_custom_brightness(self):
        led_states = _build_led_states({'alert': {'brightness': 50}})
        monitor, api_client, led, state_tracker = self._make_monitor(led_states)
        api_client.get_live_alerts = AsyncMock(return_value={'data': []})
        state_tracker.update = lambda data: AlertState.ALERT
        await monitor.poll()
        led.set_led.assert_called_once_with(on=True, color_hex='#FF0000', brightness=50)

    @pytest.mark.asyncio
    async def test_no_update_when_state_unchanged(self):
        monitor, api_client, led, state_tracker = self._make_monitor()
        api_client.get_live_alerts = AsyncMock(return_value={'data': []})
        state_tracker.update = lambda data: AlertState.ROUTINE
        await monitor.poll()
        await monitor.poll()
        led.set_led.assert_called_once()

    @pytest.mark.asyncio
    async def test_alert_state_property(self):
        monitor, _, _, state_tracker = self._make_monitor()
        state_tracker.state = AlertState.PRE_ALERT
        assert monitor.alert_state == AlertState.PRE_ALERT

    @pytest.mark.asyncio
    async def test_blink_enables_locate(self):
        led_states = _build_led_states({'alert': {'blink': True}})
        monitor, api_client, led, state_tracker = self._make_monitor(led_states)
        api_client.get_live_alerts = AsyncMock(return_value={'data': []})
        state_tracker.update = lambda data: AlertState.ALERT
        await monitor.poll()
        led.locate.assert_called_once_with(enable=True)
        led.set_led.assert_called_once()

    @pytest.mark.asyncio
    async def test_blink_not_enabled_when_led_off(self):
        led_states = _build_led_states({'alert': {'on': False, 'blink': True}})
        monitor, api_client, led, state_tracker = self._make_monitor(led_states)
        api_client.get_live_alerts = AsyncMock(return_value={'data': []})
        state_tracker.update = lambda data: AlertState.ALERT
        await monitor.poll()
        led.locate.assert_not_called()

    @pytest.mark.asyncio
    async def test_blink_disabled_on_state_change(self):
        led_states = _build_led_states({'alert': {'blink': True}})
        monitor, api_client, led, state_tracker = self._make_monitor(led_states)
        api_client.get_live_alerts = AsyncMock(return_value={'data': []})

        # Start blinking on alert
        state_tracker.update = lambda data: AlertState.ALERT
        await monitor.poll()
        led.locate.assert_called_with(enable=True)

        # Change to routine - locate should be disabled
        state_tracker.update = lambda data: AlertState.ROUTINE
        await monitor.poll()
        led.locate.assert_called_with(enable=False)
        assert led.locate.call_count == 2


class TestBuildDeviceLedStates:
    def test_no_overrides_returns_base(self):
        base = _build_led_states({})
        result = _build_device_led_states(base, ['aa:bb:cc:dd:ee:ff'], {})
        assert result['aa:bb:cc:dd:ee:ff'] is base

    def test_override_brightness_for_one_device(self):
        base = _build_led_states({})
        overrides = {'aa:bb:cc:dd:ee:ff': {'led_states': {'routine': {'brightness': 5}}}}
        result = _build_device_led_states(base, ['aa:bb:cc:dd:ee:ff', '11:22:33:44:55:66'], overrides)

        assert result['aa:bb:cc:dd:ee:ff'][AlertState.ROUTINE]['brightness'] == 5
        assert result['aa:bb:cc:dd:ee:ff'][AlertState.ALERT]['brightness'] == 100
        assert result['11:22:33:44:55:66'] is base

    def test_override_color(self):
        base = _build_led_states({})
        overrides = {'aa:bb:cc:dd:ee:ff': {'led_states': {'alert': {'color': 'blue'}}}}
        result = _build_device_led_states(base, ['aa:bb:cc:dd:ee:ff'], overrides)

        assert result['aa:bb:cc:dd:ee:ff'][AlertState.ALERT]['color'] == (0, 0, 255)
        assert result['aa:bb:cc:dd:ee:ff'][AlertState.ROUTINE]['color'] == (255, 255, 255)

    def test_override_multiple_states(self):
        base = _build_led_states({})
        overrides = {
            'aa:bb:cc:dd:ee:ff': {
                'led_states': {
                    'routine': {'brightness': 5},
                    'pre_alert': {'brightness': 50},
                }
            }
        }
        result = _build_device_led_states(base, ['aa:bb:cc:dd:ee:ff'], overrides)

        assert result['aa:bb:cc:dd:ee:ff'][AlertState.ROUTINE]['brightness'] == 5
        assert result['aa:bb:cc:dd:ee:ff'][AlertState.PRE_ALERT]['brightness'] == 50
        assert result['aa:bb:cc:dd:ee:ff'][AlertState.ALERT]['brightness'] == 100

    def test_mac_case_insensitive(self):
        base = _build_led_states({})
        overrides = {'AA:BB:CC:DD:EE:FF': {'led_states': {'routine': {'brightness': 10}}}}
        result = _build_device_led_states(base, ['aa:bb:cc:dd:ee:ff'], overrides)

        assert result['aa:bb:cc:dd:ee:ff'][AlertState.ROUTINE]['brightness'] == 10


class TestUnifiAlertMonitorPerDevice:
    def _make_monitor_with_overrides(self, device_macs, device_overrides):
        base_states = _build_led_states({})
        device_led_states = _build_device_led_states(base_states, device_macs, device_overrides)

        api_client = AsyncMock()
        led_controller = AsyncMock()
        led_controller._device_macs = [m.lower() for m in device_macs]
        state_tracker = AsyncMock()
        monitor = UnifiAlertMonitor(api_client, led_controller, state_tracker, base_states, device_led_states)
        return monitor, api_client, led_controller, state_tracker

    @pytest.mark.asyncio
    async def test_per_device_routine_brightness(self):
        macs = ['aa:bb:cc:dd:ee:ff', '11:22:33:44:55:66']
        overrides = {'11:22:33:44:55:66': {'led_states': {'routine': {'brightness': 5}}}}
        monitor, api_client, led, state_tracker = self._make_monitor_with_overrides(macs, overrides)

        api_client.get_live_alerts = AsyncMock(return_value={'data': []})
        state_tracker.update = lambda data: AlertState.ROUTINE
        await monitor.poll()

        assert led.set_device_led.call_count == 2
        calls = {call.args[0]: call for call in led.set_device_led.call_args_list}
        assert calls['aa:bb:cc:dd:ee:ff'].kwargs['brightness'] == 100
        assert calls['11:22:33:44:55:66'].kwargs['brightness'] == 5

    @pytest.mark.asyncio
    async def test_per_device_alert_same_for_all(self):
        macs = ['aa:bb:cc:dd:ee:ff', '11:22:33:44:55:66']
        overrides = {'11:22:33:44:55:66': {'led_states': {'routine': {'brightness': 5}}}}
        monitor, api_client, led, state_tracker = self._make_monitor_with_overrides(macs, overrides)

        api_client.get_live_alerts = AsyncMock(return_value={'data': []})
        state_tracker.update = lambda data: AlertState.ALERT
        await monitor.poll()

        assert led.set_device_led.call_count == 2
        calls = {call.args[0]: call for call in led.set_device_led.call_args_list}
        assert calls['aa:bb:cc:dd:ee:ff'].kwargs['brightness'] == 100
        assert calls['11:22:33:44:55:66'].kwargs['brightness'] == 100
        assert calls['aa:bb:cc:dd:ee:ff'].kwargs['color_hex'] == '#FF0000'
        assert calls['11:22:33:44:55:66'].kwargs['color_hex'] == '#FF0000'
