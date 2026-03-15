import datetime
from unittest.mock import AsyncMock, patch

import pytest

from red_alert.core.state import AlertState
from red_alert.integrations.outputs.unifi.server import (
    NAMED_COLORS,
    UnifiAlertMonitor,
    _build_device_led_states,
    _build_device_schedules,
    _build_led_states,
    _normalize_config,
    _parse_schedule,
    _resolve_color,
    _resolve_led_state,
    _schedule_active,
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


class TestNormalizeConfig:
    def test_flat_config_backward_compat(self):
        """Flat config (no monitors key) produces a single controller group with one monitor."""
        config = {
            'host': '192.168.1.1',
            'username': 'admin',
            'password': 'pass',
            'device_macs': ['aa:bb:cc:dd:ee:ff'],
            'areas_of_interest': ['tel aviv'],
        }
        groups = _normalize_config(config)

        assert len(groups) == 1
        connection, monitors = groups[0]
        assert connection['host'] == '192.168.1.1'
        assert connection['username'] == 'admin'
        assert len(monitors) == 1
        assert monitors[0]['device_macs'] == ['aa:bb:cc:dd:ee:ff']
        assert monitors[0]['areas_of_interest'] == ['tel aviv']

    def test_flat_config_defaults(self):
        """Flat config fills in defaults from DEFAULT_CONFIG."""
        groups = _normalize_config({'host': '1.2.3.4', 'username': 'a', 'password': 'b', 'device_macs': ['aa:bb:cc:dd:ee:ff']})
        connection, _ = groups[0]

        assert connection['interval'] == 1
        assert connection['port'] == 443
        assert connection['backend'] is None
        assert connection['connection'] == 'local'

    def test_multi_monitor_splits_correctly(self):
        """monitors key produces multiple monitor entries with shared connection."""
        config = {
            'host': '192.168.1.1',
            'username': 'admin',
            'password': 'pass',
            'monitors': [
                {
                    'name': 'Home',
                    'device_macs': ['aa:bb:cc:dd:ee:ff'],
                    'areas_of_interest': ['kfar saba'],
                },
                {
                    'name': 'Bedroom',
                    'device_macs': ['11:22:33:44:55:66'],
                    'areas_of_interest': ['tel aviv'],
                },
            ],
        }
        groups = _normalize_config(config)

        assert len(groups) == 1
        connection, monitors = groups[0]
        assert connection['host'] == '192.168.1.1'
        assert len(monitors) == 2
        assert monitors[0]['name'] == 'Home'
        assert monitors[0]['device_macs'] == ['aa:bb:cc:dd:ee:ff']
        assert monitors[0]['areas_of_interest'] == ['kfar saba']
        assert monitors[1]['name'] == 'Bedroom'
        assert monitors[1]['device_macs'] == ['11:22:33:44:55:66']
        assert monitors[1]['areas_of_interest'] == ['tel aviv']

    def test_hold_seconds_inheritance(self):
        """Top-level hold_seconds is inherited by monitors, with per-monitor overrides."""
        config = {
            'host': '1.2.3.4',
            'username': 'a',
            'password': 'b',
            'hold_seconds': {'alert': 120, 'pre_alert': 60},
            'monitors': [
                {
                    'name': 'A',
                    'device_macs': ['aa:bb:cc:dd:ee:ff'],
                },
                {
                    'name': 'B',
                    'device_macs': ['11:22:33:44:55:66'],
                    'hold_seconds': {'alert': 300},
                },
            ],
        }
        groups = _normalize_config(config)
        _, monitors = groups[0]

        # Monitor A inherits top-level hold_seconds
        assert monitors[0]['hold_seconds'] == {'alert': 120, 'pre_alert': 60}
        # Monitor B overrides alert but inherits pre_alert
        assert monitors[1]['hold_seconds'] == {'alert': 300, 'pre_alert': 60}

    def test_monitor_led_states_preserved(self):
        """Per-monitor led_states are passed through."""
        config = {
            'host': '1.2.3.4',
            'username': 'a',
            'password': 'b',
            'monitors': [
                {
                    'name': 'Dim',
                    'device_macs': ['aa:bb:cc:dd:ee:ff'],
                    'led_states': {'routine': {'brightness': 20}},
                },
            ],
        }
        groups = _normalize_config(config)
        _, monitors = groups[0]

        assert monitors[0]['led_states'] == {'routine': {'brightness': 20}}

    def test_connection_keys_not_in_monitors(self):
        """Connection-level keys like host/username are not leaked into monitor configs."""
        config = {
            'host': '1.2.3.4',
            'username': 'a',
            'password': 'b',
            'monitors': [
                {'name': 'M', 'device_macs': ['aa:bb:cc:dd:ee:ff']},
            ],
        }
        groups = _normalize_config(config)
        _, monitors = groups[0]

        assert 'host' not in monitors[0]
        assert 'username' not in monitors[0]
        assert 'password' not in monitors[0]

    def test_cloud_multi_controller(self):
        """controllers key produces multiple controller groups for cloud mode."""
        config = {
            'username': 'sso@email.com',
            'password': 'pass',
            'totp_secret': 'SECRET',
            'controllers': [
                {
                    'name': 'Home',
                    'device_id': 'ctrl-home-123',
                    'site': 'default',
                    'monitors': [
                        {'name': 'Living Room', 'device_macs': ['aa:bb:cc:dd:ee:ff'], 'areas_of_interest': ['kfar saba']},
                    ],
                },
                {
                    'name': 'Parents',
                    'device_id': 'ctrl-parents-456',
                    'monitors': [
                        {'name': 'Hallway', 'device_macs': ['11:22:33:44:55:66'], 'areas_of_interest': ['herzliya']},
                    ],
                },
            ],
        }
        groups = _normalize_config(config)

        assert len(groups) == 2

        # First controller
        conn1, mons1 = groups[0]
        assert conn1['connection'] == 'cloud'
        assert conn1['backend'] == 'pyunifiapi'
        assert conn1['device_id'] == 'ctrl-home-123'
        assert conn1['site'] == 'default'
        assert conn1['username'] == 'sso@email.com'
        assert len(mons1) == 1
        assert mons1[0]['name'] == 'Living Room'
        assert mons1[0]['areas_of_interest'] == ['kfar saba']

        # Second controller
        conn2, mons2 = groups[1]
        assert conn2['device_id'] == 'ctrl-parents-456'
        assert conn2['username'] == 'sso@email.com'
        assert len(mons2) == 1
        assert mons2[0]['name'] == 'Hallway'

    def test_cloud_controllers_inherit_shared_credentials(self):
        """Each controller in a cloud config inherits shared credentials."""
        config = {
            'username': 'user@email.com',
            'password': 'pass',
            'totp_secret': 'TOTP123',
            'controllers': [
                {'device_id': 'c1', 'monitors': [{'device_macs': ['aa:bb:cc:dd:ee:ff']}]},
                {'device_id': 'c2', 'monitors': [{'device_macs': ['11:22:33:44:55:66']}]},
            ],
        }
        groups = _normalize_config(config)

        for conn, _ in groups:
            assert conn['username'] == 'user@email.com'
            assert conn['password'] == 'pass'
            assert conn['totp_secret'] == 'TOTP123'
            assert conn['backend'] == 'pyunifiapi'
            assert conn['connection'] == 'cloud'

    def test_cloud_hold_seconds_inheritance(self):
        """Top-level hold_seconds is inherited by monitors in cloud controllers."""
        config = {
            'username': 'u',
            'password': 'p',
            'hold_seconds': {'alert': 120},
            'controllers': [
                {
                    'device_id': 'c1',
                    'monitors': [
                        {'device_macs': ['aa:bb:cc:dd:ee:ff']},
                        {'device_macs': ['11:22:33:44:55:66'], 'hold_seconds': {'alert': 300}},
                    ],
                },
            ],
        }
        groups = _normalize_config(config)
        _, monitors = groups[0]

        assert monitors[0]['hold_seconds'] == {'alert': 120}
        assert monitors[1]['hold_seconds'] == {'alert': 300}

    def test_mixed_local_and_cloud_controllers(self):
        """controllers list with both local (host) and cloud (device_id) controllers."""
        config = {
            'username': 'sso@email.com',
            'password': 'pass',
            'totp_secret': 'SECRET',
            'controllers': [
                {
                    'name': 'Home',
                    'host': '172.16.1.1',
                    'site': 'default',
                    'monitors': [
                        {'name': 'Study', 'device_macs': ['aa:bb:cc:dd:ee:ff']},
                    ],
                },
                {
                    'name': 'Parents',
                    'device_id': 'ctrl-parents-456',
                    'monitors': [
                        {'name': 'Hallway', 'device_macs': ['11:22:33:44:55:66']},
                    ],
                },
            ],
        }
        groups = _normalize_config(config)

        assert len(groups) == 2

        # Home controller: local mode
        conn_home, mons_home = groups[0]
        assert conn_home['host'] == '172.16.1.1'
        assert conn_home['connection'] == 'local'
        assert conn_home.get('device_id') is None
        assert len(mons_home) == 1

        # Parents controller: cloud mode
        conn_parents, mons_parents = groups[1]
        assert conn_parents['device_id'] == 'ctrl-parents-456'
        assert conn_parents['connection'] == 'cloud'
        assert conn_parents['backend'] == 'pyunifiapi'
        assert len(mons_parents) == 1

    def test_local_controller_preserves_backend(self):
        """A local controller in the controllers list can use aiounifi backend."""
        config = {
            'username': 'admin',
            'password': 'pass',
            'backend': 'aiounifi',
            'controllers': [
                {
                    'name': 'Home',
                    'host': '192.168.1.1',
                    'monitors': [{'device_macs': ['aa:bb:cc:dd:ee:ff']}],
                },
            ],
        }
        groups = _normalize_config(config)
        conn, _ = groups[0]

        assert conn['connection'] == 'local'
        assert conn['backend'] == 'aiounifi'

    def test_controller_can_override_port(self):
        """Per-controller port overrides the top-level default."""
        config = {
            'username': 'u',
            'password': 'p',
            'port': 443,
            'controllers': [
                {
                    'host': '10.0.0.1',
                    'port': 8443,
                    'monitors': [{'device_macs': ['aa:bb:cc:dd:ee:ff']}],
                },
            ],
        }
        groups = _normalize_config(config)
        conn, _ = groups[0]

        assert conn['port'] == 8443


class TestUpdate:
    """Tests for the update() method (receives pre-fetched data)."""

    def _make_monitor(self, led_states=None):
        api_client = AsyncMock()
        led_controller = AsyncMock()
        state_tracker = AsyncMock()
        monitor = UnifiAlertMonitor(api_client, led_controller, state_tracker, led_states)
        return monitor, api_client, led_controller, state_tracker

    @pytest.mark.asyncio
    async def test_update_alert_sends_red(self):
        monitor, _, led, state_tracker = self._make_monitor()
        state_tracker.update = lambda data: AlertState.ALERT
        state = await monitor.update({'cat': '1', 'data': ['city']})
        assert state == AlertState.ALERT
        led.set_led.assert_called_once_with(on=True, color_hex='#FF0000', brightness=100)

    @pytest.mark.asyncio
    async def test_update_routine_sends_white(self):
        monitor, _, led, state_tracker = self._make_monitor()
        state_tracker.update = lambda data: AlertState.ROUTINE
        state = await monitor.update(None)
        assert state == AlertState.ROUTINE
        led.set_led.assert_called_once_with(on=True, color_hex='#FFFFFF', brightness=100)

    @pytest.mark.asyncio
    async def test_update_no_api_call(self):
        """update() must not call get_live_alerts - data is pre-fetched."""
        monitor, api_client, _, state_tracker = self._make_monitor()
        state_tracker.update = lambda data: AlertState.ROUTINE
        await monitor.update(None)
        api_client.get_live_alerts.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_no_change_skips_led(self):
        monitor, _, led, state_tracker = self._make_monitor()
        state_tracker.update = lambda data: AlertState.ROUTINE
        await monitor.update(None)
        await monitor.update(None)
        led.set_led.assert_called_once()

    @pytest.mark.asyncio
    async def test_poll_delegates_to_update(self):
        """poll() fetches data then calls update() logic."""
        monitor, api_client, led, state_tracker = self._make_monitor()
        api_client.get_live_alerts = AsyncMock(return_value={'data': []})
        state_tracker.update = lambda data: AlertState.ALERT
        state = await monitor.poll()
        assert state == AlertState.ALERT
        api_client.get_live_alerts.assert_called_once()
        led.set_led.assert_called_once()


class TestMultiMonitor:
    """Tests for multi-monitor mode with per-area device groups."""

    def _make_multi_monitor(self, monitors_cfg):
        """Create multiple monitors sharing one LED controller."""
        api_client = AsyncMock()
        led_controller = AsyncMock()
        monitors = []
        state_trackers = []

        for cfg in monitors_cfg:
            state_tracker = AsyncMock()
            led_states = _build_led_states(cfg.get('led_states', {}))
            device_macs = cfg.get('device_macs', [])
            device_overrides = cfg.get('device_overrides', {})
            device_led_states = _build_device_led_states(led_states, device_macs, device_overrides) if device_overrides else None

            monitor = UnifiAlertMonitor(
                api_client=api_client,
                led_controller=led_controller,
                state_tracker=state_tracker,
                led_states=led_states,
                device_led_states=device_led_states,
                device_macs=device_macs,
                name=cfg.get('name'),
            )
            monitors.append(monitor)
            state_trackers.append(state_tracker)

        return monitors, api_client, led_controller, state_trackers

    @pytest.mark.asyncio
    async def test_independent_state_tracking(self):
        """Two monitors with different areas track state independently."""
        monitors, _, led, trackers = self._make_multi_monitor(
            [
                {'name': 'Home', 'device_macs': ['aa:bb:cc:dd:ee:ff']},
                {'name': 'Bedroom', 'device_macs': ['11:22:33:44:55:66']},
            ]
        )

        # Home sees alert, Bedroom sees routine
        trackers[0].update = lambda data: AlertState.ALERT
        trackers[1].update = lambda data: AlertState.ROUTINE

        data = {'cat': '1', 'data': ['kfar saba']}
        state_home = await monitors[0].update(data)
        state_bedroom = await monitors[1].update(data)

        assert state_home == AlertState.ALERT
        assert state_bedroom == AlertState.ROUTINE

    @pytest.mark.asyncio
    async def test_each_monitor_controls_own_macs(self):
        """Each monitor only sends LED updates to its own device MACs."""
        monitors, _, led, trackers = self._make_multi_monitor(
            [
                {'name': 'Home', 'device_macs': ['aa:bb:cc:dd:ee:ff']},
                {'name': 'Bedroom', 'device_macs': ['11:22:33:44:55:66']},
            ]
        )

        trackers[0].update = lambda data: AlertState.ALERT
        trackers[1].update = lambda data: AlertState.ROUTINE

        await monitors[0].update(None)
        await monitors[1].update(None)

        calls = led.set_device_led.call_args_list
        assert len(calls) == 2

        # Home monitor sets alert (red) on its MAC
        home_call = calls[0]
        assert home_call.args[0] == 'aa:bb:cc:dd:ee:ff'
        assert home_call.kwargs['color_hex'] == '#FF0000'

        # Bedroom monitor sets routine (white) on its MAC
        bedroom_call = calls[1]
        assert bedroom_call.args[0] == '11:22:33:44:55:66'
        assert bedroom_call.kwargs['color_hex'] == '#FFFFFF'

    @pytest.mark.asyncio
    async def test_per_monitor_led_states(self):
        """Each monitor can have its own LED state config."""
        monitors, _, led, trackers = self._make_multi_monitor(
            [
                {
                    'name': 'Home',
                    'device_macs': ['aa:bb:cc:dd:ee:ff'],
                    'led_states': {'routine': {'brightness': 20}},
                },
                {
                    'name': 'Bedroom',
                    'device_macs': ['11:22:33:44:55:66'],
                    'led_states': {'routine': {'brightness': 5}},
                },
            ]
        )

        trackers[0].update = lambda data: AlertState.ROUTINE
        trackers[1].update = lambda data: AlertState.ROUTINE

        await monitors[0].update(None)
        await monitors[1].update(None)

        calls = {call.args[0]: call for call in led.set_device_led.call_args_list}
        assert calls['aa:bb:cc:dd:ee:ff'].kwargs['brightness'] == 20
        assert calls['11:22:33:44:55:66'].kwargs['brightness'] == 5

    @pytest.mark.asyncio
    async def test_multi_monitor_locate_per_device(self):
        """In multi-monitor mode, blink uses locate_device per MAC, not bulk locate."""
        monitors, _, led, trackers = self._make_multi_monitor(
            [
                {
                    'name': 'Home',
                    'device_macs': ['aa:bb:cc:dd:ee:ff'],
                    'led_states': {'alert': {'blink': True}},
                },
                {
                    'name': 'Bedroom',
                    'device_macs': ['11:22:33:44:55:66'],
                },
            ]
        )

        trackers[0].update = lambda data: AlertState.ALERT
        trackers[1].update = lambda data: AlertState.ROUTINE

        await monitors[0].update(None)
        await monitors[1].update(None)

        # Home should use locate_device (per-device), not locate (bulk)
        led.locate_device.assert_called_once_with('aa:bb:cc:dd:ee:ff', True)
        led.locate.assert_not_called()

    @pytest.mark.asyncio
    async def test_multi_monitor_no_cross_contamination(self):
        """State change in one monitor does not affect the other."""
        monitors, _, led, trackers = self._make_multi_monitor(
            [
                {'name': 'Home', 'device_macs': ['aa:bb:cc:dd:ee:ff']},
                {'name': 'Bedroom', 'device_macs': ['11:22:33:44:55:66']},
            ]
        )

        # Both start routine
        trackers[0].update = lambda data: AlertState.ROUTINE
        trackers[1].update = lambda data: AlertState.ROUTINE
        await monitors[0].update(None)
        await monitors[1].update(None)
        led.set_device_led.reset_mock()

        # Only Home goes to alert
        trackers[0].update = lambda data: AlertState.ALERT
        await monitors[0].update(None)
        await monitors[1].update(None)

        # Only Home's MAC should get an update (alert/red)
        assert led.set_device_led.call_count == 1
        call = led.set_device_led.call_args_list[0]
        assert call.args[0] == 'aa:bb:cc:dd:ee:ff'
        assert call.kwargs['color_hex'] == '#FF0000'

    @pytest.mark.asyncio
    async def test_multi_monitor_multiple_macs_per_monitor(self):
        """A single monitor can control multiple devices."""
        monitors, _, led, trackers = self._make_multi_monitor(
            [
                {'name': 'Home', 'device_macs': ['aa:bb:cc:dd:ee:ff', '11:22:33:44:55:66']},
            ]
        )

        trackers[0].update = lambda data: AlertState.ALERT
        await monitors[0].update(None)

        assert led.set_device_led.call_count == 2
        macs_called = {call.args[0] for call in led.set_device_led.call_args_list}
        assert macs_called == {'aa:bb:cc:dd:ee:ff', '11:22:33:44:55:66'}


class TestSchedule:
    def test_parse_schedule(self):
        s = _parse_schedule({'time': '08:00-20:00', 'timezone': 'Asia/Jerusalem', 'led_states': {'routine': {'brightness': 5}}})
        assert s['start'] == datetime.time(8, 0)
        assert s['end'] == datetime.time(20, 0)
        assert s['led_states'] == {'routine': {'brightness': 5}}

    def test_parse_schedule_missing_timezone_raises(self):
        with pytest.raises(ValueError, match='missing required "timezone"'):
            _parse_schedule({'time': '08:00-20:00'})

    def test_parse_schedule_custom_timezone(self):
        s = _parse_schedule({'time': '08:00-20:00', 'timezone': 'UTC'})
        assert str(s['tz']) == 'UTC'

    def test_schedule_active_within_range(self):
        s = _parse_schedule({'time': '08:00-20:00', 'timezone': 'UTC'})
        with patch('red_alert.integrations.outputs.unifi.server.datetime') as mock_dt:
            mock_dt.datetime.now.return_value.time.return_value = datetime.time(12, 0)
            mock_dt.time = datetime.time
            assert _schedule_active(s) is True

    def test_schedule_inactive_outside_range(self):
        s = _parse_schedule({'time': '08:00-20:00', 'timezone': 'UTC'})
        with patch('red_alert.integrations.outputs.unifi.server.datetime') as mock_dt:
            mock_dt.datetime.now.return_value.time.return_value = datetime.time(22, 0)
            mock_dt.time = datetime.time
            assert _schedule_active(s) is False

    def test_schedule_active_crosses_midnight(self):
        s = _parse_schedule({'time': '20:00-08:00', 'timezone': 'UTC'})
        with patch('red_alert.integrations.outputs.unifi.server.datetime') as mock_dt:
            mock_dt.datetime.now.return_value.time.return_value = datetime.time(23, 0)
            mock_dt.time = datetime.time
            assert _schedule_active(s) is True

    def test_schedule_inactive_crosses_midnight_daytime(self):
        s = _parse_schedule({'time': '20:00-08:00', 'timezone': 'UTC'})
        with patch('red_alert.integrations.outputs.unifi.server.datetime') as mock_dt:
            mock_dt.datetime.now.return_value.time.return_value = datetime.time(12, 0)
            mock_dt.time = datetime.time
            assert _schedule_active(s) is False

    def test_build_device_schedules(self):
        overrides = {
            'AA:BB:CC:DD:EE:FF': {
                'led_states': {'routine': {'brightness': 100}},
                'schedules': [{'time': '08:00-20:00', 'timezone': 'Asia/Jerusalem', 'led_states': {'routine': {'brightness': 5}}}],
            }
        }
        result = _build_device_schedules(overrides)
        assert 'aa:bb:cc:dd:ee:ff' in result
        assert len(result['aa:bb:cc:dd:ee:ff']) == 1

    def test_state_cfg_applies_active_schedule(self):
        base = _build_led_states({})
        device_overrides = {
            'aa:bb:cc:dd:ee:ff': {
                'led_states': {'routine': {'brightness': 100}},
                'schedules': [{'time': '08:00-20:00', 'timezone': 'Asia/Jerusalem', 'led_states': {'routine': {'brightness': 5}}}],
            }
        }
        device_led = _build_device_led_states(base, ['aa:bb:cc:dd:ee:ff'], device_overrides)
        device_schedules = _build_device_schedules(device_overrides)

        monitor = UnifiAlertMonitor(
            api_client=AsyncMock(),
            led_controller=AsyncMock(),
            state_tracker=AsyncMock(),
            led_states=base,
            device_led_states=device_led,
            device_macs=['aa:bb:cc:dd:ee:ff'],
            device_schedules=device_schedules,
            name='test',
        )

        with patch('red_alert.integrations.outputs.unifi.server.datetime') as mock_dt:
            mock_dt.datetime.now.return_value.time.return_value = datetime.time(12, 0)
            mock_dt.time = datetime.time
            cfg = monitor._state_cfg(AlertState.ROUTINE, 'aa:bb:cc:dd:ee:ff')
            assert cfg['brightness'] == 5

    def test_state_cfg_inactive_schedule_uses_base(self):
        base = _build_led_states({})
        device_overrides = {
            'aa:bb:cc:dd:ee:ff': {
                'led_states': {'routine': {'brightness': 100}},
                'schedules': [{'time': '08:00-20:00', 'timezone': 'Asia/Jerusalem', 'led_states': {'routine': {'brightness': 5}}}],
            }
        }
        device_led = _build_device_led_states(base, ['aa:bb:cc:dd:ee:ff'], device_overrides)
        device_schedules = _build_device_schedules(device_overrides)

        monitor = UnifiAlertMonitor(
            api_client=AsyncMock(),
            led_controller=AsyncMock(),
            state_tracker=AsyncMock(),
            led_states=base,
            device_led_states=device_led,
            device_macs=['aa:bb:cc:dd:ee:ff'],
            device_schedules=device_schedules,
            name='test',
        )

        with patch('red_alert.integrations.outputs.unifi.server.datetime') as mock_dt:
            mock_dt.datetime.now.return_value.time.return_value = datetime.time(22, 0)
            mock_dt.time = datetime.time
            cfg = monitor._state_cfg(AlertState.ROUTINE, 'aa:bb:cc:dd:ee:ff')
            assert cfg['brightness'] == 100
