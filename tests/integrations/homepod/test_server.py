from unittest.mock import AsyncMock

import pytest

from red_alert.core.state import AlertState, AlertStateTracker
from red_alert.integrations.homepod.server import (
    DeviceAction,
    HomepodAlertMonitor,
    _build_device_actions,
    _parse_action,
)


class TestParseAction:
    def test_none_returns_none(self):
        assert _parse_action(None) is None

    def test_empty_dict(self):
        action = _parse_action({})
        assert action.audio is None
        assert action.volume is None
        assert action.loop is False

    def test_full_config(self):
        action = _parse_action({'audio': '/path/to/siren.mp3', 'volume': 80, 'loop': True})
        assert action.audio == '/path/to/siren.mp3'
        assert action.volume == 80
        assert action.loop is True

    def test_partial_config(self):
        action = _parse_action({'audio': '/siren.mp3'})
        assert action.audio == '/siren.mp3'
        assert action.volume is None
        assert action.loop is False

    def test_volume_only(self):
        action = _parse_action({'volume': 30})
        assert action.audio is None
        assert action.volume == 30


class TestBuildDeviceActions:
    def test_empty_config(self):
        actions = _build_device_actions({})
        assert AlertState.ALERT in actions
        assert AlertState.PRE_ALERT in actions
        assert AlertState.ALL_CLEAR in actions
        assert AlertState.ROUTINE in actions
        for state in AlertState:
            assert actions[state] is None

    def test_alert_only(self):
        actions = _build_device_actions(
            {
                'alert': {'audio': '/siren.mp3', 'volume': 100},
            }
        )
        assert actions[AlertState.ALERT].audio == '/siren.mp3'
        assert actions[AlertState.ALERT].volume == 100
        assert actions[AlertState.ROUTINE] is None

    def test_all_states(self):
        actions = _build_device_actions(
            {
                'alert': {'audio': '/siren.mp3', 'volume': 100, 'loop': True},
                'pre_alert': {'audio': '/warning.mp3', 'volume': 70},
                'all_clear': {'audio': '/clear.mp3', 'volume': 50},
                'routine': {},
            }
        )
        assert actions[AlertState.ALERT].audio == '/siren.mp3'
        assert actions[AlertState.ALERT].loop is True
        assert actions[AlertState.PRE_ALERT].audio == '/warning.mp3'
        assert actions[AlertState.ALL_CLEAR].audio == '/clear.mp3'
        assert actions[AlertState.ROUTINE].audio is None


class TestHomepodAlertMonitor:
    def _make_monitor(self, device_actions=None, areas_of_interest=None, hold_seconds=None):
        api_client = AsyncMock()
        controller = AsyncMock()
        controller.name = 'Test HomePod'
        controller.play = AsyncMock()
        controller.stop = AsyncMock()
        controller.set_volume = AsyncMock()

        actions = device_actions or {
            AlertState.ALERT: DeviceAction(audio='/siren.mp3', volume=100, loop=True),
            AlertState.PRE_ALERT: DeviceAction(audio='/warning.mp3', volume=70),
            AlertState.ALL_CLEAR: DeviceAction(audio='/clear.mp3', volume=50),
            AlertState.ROUTINE: None,
        }

        state_tracker = AlertStateTracker(areas_of_interest=areas_of_interest, hold_seconds=hold_seconds)
        devices = [(controller, actions)]
        monitor = HomepodAlertMonitor(api_client, devices, state_tracker)
        return monitor, api_client, controller

    @pytest.mark.asyncio
    async def test_no_action_on_routine(self):
        monitor, api_client, controller = self._make_monitor()
        api_client.get_live_alerts.return_value = None

        await monitor.poll()

        controller.play.assert_not_called()
        controller.stop.assert_not_called()

    @pytest.mark.asyncio
    async def test_plays_on_alert(self):
        monitor, api_client, controller = self._make_monitor()
        api_client.get_live_alerts.return_value = {'cat': '1', 'data': ['City A'], 'title': 'Rockets'}

        await monitor.poll()

        controller.play.assert_called_once_with('/siren.mp3', volume=100, loop=True)

    @pytest.mark.asyncio
    async def test_plays_on_pre_alert(self):
        monitor, api_client, controller = self._make_monitor()
        api_client.get_live_alerts.return_value = {'cat': '14', 'data': ['City A'], 'title': 'Pre-alert'}

        await monitor.poll()

        controller.play.assert_called_once_with('/warning.mp3', volume=70, loop=False)

    @pytest.mark.asyncio
    async def test_plays_on_all_clear(self):
        monitor, api_client, controller = self._make_monitor()
        # First: trigger alert
        api_client.get_live_alerts.return_value = {'cat': '1', 'data': ['City A'], 'title': 'Rockets'}
        await monitor.poll()

        # Then: all-clear
        api_client.get_live_alerts.return_value = {'cat': '13', 'data': ['City A']}
        await monitor.poll()

        assert controller.play.call_count == 2
        controller.play.assert_called_with('/clear.mp3', volume=50, loop=False)

    @pytest.mark.asyncio
    async def test_stops_on_routine_after_alert(self):
        monitor, api_client, controller = self._make_monitor()
        # First: alert
        api_client.get_live_alerts.return_value = {'cat': '1', 'data': ['City A'], 'title': 'Rockets'}
        await monitor.poll()

        # Then: routine
        api_client.get_live_alerts.return_value = None
        await monitor.poll()

        controller.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_duplicate_action_same_state(self):
        monitor, api_client, controller = self._make_monitor()
        api_client.get_live_alerts.return_value = {'cat': '1', 'data': ['City A'], 'title': 'Rockets'}

        await monitor.poll()  # ROUTINE -> ALERT
        await monitor.poll()  # ALERT -> ALERT (no change)

        assert controller.play.call_count == 1

    @pytest.mark.asyncio
    async def test_areas_of_interest_filtering(self):
        monitor, api_client, controller = self._make_monitor(areas_of_interest=['כפר סבא'])

        # Non-matching area
        api_client.get_live_alerts.return_value = {'cat': '1', 'data': ['תל אביב'], 'title': 'Rockets'}
        await monitor.poll()
        controller.play.assert_not_called()

        # Matching area
        api_client.get_live_alerts.return_value = {'cat': '1', 'data': ['כפר סבא'], 'title': 'Rockets'}
        await monitor.poll()
        controller.play.assert_called_once()

    @pytest.mark.asyncio
    async def test_alert_state_property(self):
        monitor, api_client, controller = self._make_monitor()
        assert monitor.alert_state == AlertState.ROUTINE

        api_client.get_live_alerts.return_value = {'cat': '1', 'data': ['City A'], 'title': 'Rockets'}
        await monitor.poll()
        assert monitor.alert_state == AlertState.ALERT

    @pytest.mark.asyncio
    async def test_multiple_devices(self):
        api_client = AsyncMock()
        controller1 = AsyncMock()
        controller1.name = 'Living Room'
        controller2 = AsyncMock()
        controller2.name = 'Bedroom'

        actions1 = {
            AlertState.ALERT: DeviceAction(audio='/loud_siren.mp3', volume=100),
            AlertState.ROUTINE: None,
            AlertState.PRE_ALERT: None,
            AlertState.ALL_CLEAR: None,
        }
        actions2 = {
            AlertState.ALERT: DeviceAction(audio='/gentle_alarm.mp3', volume=50),
            AlertState.ROUTINE: None,
            AlertState.PRE_ALERT: None,
            AlertState.ALL_CLEAR: None,
        }

        state_tracker = AlertStateTracker()
        devices = [(controller1, actions1), (controller2, actions2)]
        monitor = HomepodAlertMonitor(api_client, devices, state_tracker)

        api_client.get_live_alerts.return_value = {'cat': '1', 'data': ['City A'], 'title': 'Rockets'}
        await monitor.poll()

        controller1.play.assert_called_once_with('/loud_siren.mp3', volume=100, loop=False)
        controller2.play.assert_called_once_with('/gentle_alarm.mp3', volume=50, loop=False)

    @pytest.mark.asyncio
    async def test_device_with_no_action_for_state_stops(self):
        actions = {
            AlertState.ALERT: DeviceAction(audio='/siren.mp3', volume=100),
            AlertState.PRE_ALERT: None,
            AlertState.ALL_CLEAR: None,
            AlertState.ROUTINE: None,
        }
        monitor, api_client, controller = self._make_monitor(device_actions=actions)

        api_client.get_live_alerts.return_value = {'cat': '14', 'data': ['City A'], 'title': 'Pre-alert'}
        await monitor.poll()

        controller.stop.assert_called_once()
        controller.play.assert_not_called()

    @pytest.mark.asyncio
    async def test_action_with_volume_only_stops_and_sets_volume(self):
        actions = {
            AlertState.ALERT: DeviceAction(audio=None, volume=30),
            AlertState.ROUTINE: None,
            AlertState.PRE_ALERT: None,
            AlertState.ALL_CLEAR: None,
        }
        monitor, api_client, controller = self._make_monitor(device_actions=actions)

        api_client.get_live_alerts.return_value = {'cat': '1', 'data': ['City A'], 'title': 'Rockets'}
        await monitor.poll()

        controller.stop.assert_called_once()
        controller.set_volume.assert_called_once_with(30)

    @pytest.mark.asyncio
    async def test_handles_device_error(self):
        monitor, api_client, controller = self._make_monitor()
        controller.play = AsyncMock(side_effect=Exception('Connection lost'))

        api_client.get_live_alerts.return_value = {'cat': '1', 'data': ['City A'], 'title': 'Rockets'}
        await monitor.poll()  # Should not raise

    @pytest.mark.asyncio
    async def test_pre_alert_to_alert_changes_audio(self):
        monitor, api_client, controller = self._make_monitor()

        # Pre-alert
        api_client.get_live_alerts.return_value = {'cat': '14', 'data': ['City A'], 'title': 'Pre-alert'}
        await monitor.poll()
        controller.play.assert_called_with('/warning.mp3', volume=70, loop=False)

        # Escalate to alert
        api_client.get_live_alerts.return_value = {'cat': '1', 'data': ['City A'], 'title': 'Rockets'}
        await monitor.poll()
        controller.play.assert_called_with('/siren.mp3', volume=100, loop=True)
        assert controller.play.call_count == 2

    @pytest.mark.asyncio
    async def test_all_clear_to_routine_no_extra_stop(self):
        """ALL_CLEAR -> ROUTINE should stop (routine action is None), but only once per transition."""
        monitor, api_client, controller = self._make_monitor()

        # Alert
        api_client.get_live_alerts.return_value = {'cat': '1', 'data': ['City A'], 'title': 'Rockets'}
        await monitor.poll()

        # All-clear
        api_client.get_live_alerts.return_value = {'cat': '13', 'data': ['City A']}
        await monitor.poll()

        # Routine
        api_client.get_live_alerts.return_value = None
        await monitor.poll()

        # play called for: alert + all_clear = 2
        assert controller.play.call_count == 2
        # stop called for: routine after all_clear = 1
        assert controller.stop.call_count == 1
