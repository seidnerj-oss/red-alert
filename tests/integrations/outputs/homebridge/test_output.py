import pytest

from red_alert.core.orchestrator import AlertEvent
from red_alert.core.state import AlertState
from red_alert.integrations.outputs.homebridge.output import HomebridgeOutput


class TestHomebridgeOutput:
    def test_name(self):
        out = HomebridgeOutput(config={})
        assert out.name == 'homebridge'

    @pytest.mark.asyncio
    async def test_start_and_stop(self):
        out = HomebridgeOutput(config={'host': '127.0.0.1', 'port': 0})
        await out.start()
        assert out._tracker is not None
        assert out._runner is not None
        await out.stop()

    @pytest.mark.asyncio
    async def test_handle_event_updates_state(self):
        out = HomebridgeOutput(
            config={
                'host': '127.0.0.1',
                'port': 0,
                'hold_seconds': {'alert': 0, 'pre_alert': 0, 'all_clear': 0},
            }
        )
        await out.start()

        alert_data = {'cat': '1', 'title': 'Test', 'data': ['City'], 'desc': ''}
        event = AlertEvent(source='hfc', state=AlertState.ALERT, data=alert_data)
        await out.handle_event(event)

        assert out._tracker.state == AlertState.ALERT
        assert out._active is True

        empty_event = AlertEvent(source='hfc', state=AlertState.ROUTINE, data=None)
        await out.handle_event(empty_event)
        assert out._active is False

        await out.stop()

    @pytest.mark.asyncio
    async def test_area_filtering(self):
        out = HomebridgeOutput(
            config={
                'host': '127.0.0.1',
                'port': 0,
                'areas_of_interest': ['כפר סבא'],
                'hold_seconds': {'alert': 0, 'pre_alert': 0, 'all_clear': 0},
            }
        )
        await out.start()

        event = AlertEvent(source='hfc', state=AlertState.ALERT, data={'cat': '1', 'title': 'Test', 'data': ['תל אביב'], 'desc': ''})
        await out.handle_event(event)
        assert out._active is False

        event = AlertEvent(source='hfc', state=AlertState.ALERT, data={'cat': '1', 'title': 'Test', 'data': ['כפר סבא'], 'desc': ''})
        await out.handle_event(event)
        assert out._active is True

        await out.stop()

    @pytest.mark.asyncio
    async def test_handle_event_before_start(self):
        out = HomebridgeOutput(config={})
        event = AlertEvent(source='hfc', state=AlertState.ALERT)
        await out.handle_event(event)

    @pytest.mark.asyncio
    async def test_stop_without_start(self):
        out = HomebridgeOutput(config={})
        await out.stop()
