import asyncio
from unittest.mock import AsyncMock

import pytest

from red_alert.core.state import AlertState
from red_alert.integrations.inputs.hfc.input import HfcInput, _classify_for_event


class TestClassifyForEvent:
    def test_none_data(self):
        assert _classify_for_event(None) == AlertState.ROUTINE

    def test_empty_dict(self):
        assert _classify_for_event({}) == AlertState.ROUTINE

    def test_not_dict(self):
        assert _classify_for_event('string') == AlertState.ROUTINE

    def test_missile_alert(self):
        assert _classify_for_event({'cat': '1', 'title': 'ירי רקטות', 'data': ['City']}) == AlertState.ALERT

    def test_earthquake(self):
        assert _classify_for_event({'cat': '3', 'title': 'רעידת אדמה', 'data': ['City']}) == AlertState.ALERT

    def test_flash_alert(self):
        assert _classify_for_event({'cat': '10', 'title': 'Flash', 'data': ['City']}) == AlertState.ALERT

    def test_pre_alert_by_category(self):
        assert _classify_for_event({'cat': '14', 'title': 'Test', 'data': ['City']}) == AlertState.PRE_ALERT

    def test_pre_alert_by_title(self):
        assert _classify_for_event({'cat': '1', 'title': 'בדקות הקרובות צפויות התרעות'}) == AlertState.PRE_ALERT

    def test_all_clear_by_category(self):
        assert _classify_for_event({'cat': '13', 'title': 'Test'}) == AlertState.ALL_CLEAR

    def test_all_clear_by_title(self):
        assert _classify_for_event({'cat': '10', 'title': 'האירוע הסתיים'}) == AlertState.ALL_CLEAR

    def test_drill_category(self):
        assert _classify_for_event({'cat': '101', 'title': 'Drill'}) == AlertState.ROUTINE

    def test_invalid_category(self):
        assert _classify_for_event({'cat': 'abc', 'title': 'Test'}) == AlertState.ROUTINE

    def test_hostile_aircraft(self):
        assert _classify_for_event({'cat': '2', 'title': 'Test'}) == AlertState.ALERT

    def test_all_active_categories(self):
        for cat in (1, 2, 3, 4, 5, 6, 7, 10):
            assert _classify_for_event({'cat': str(cat), 'title': 'Test'}) == AlertState.ALERT


class TestHfcInput:
    def _make_input(self, **kwargs):
        api_client = AsyncMock()
        api_client.get_live_alerts = AsyncMock(return_value=None)
        api_client.get_recent_alerts_from_history = AsyncMock(return_value=[])
        return HfcInput(api_client=api_client, **kwargs), api_client

    @pytest.mark.asyncio
    async def test_name(self):
        hfc_input, _ = self._make_input()
        assert hfc_input.name == 'hfc'

    @pytest.mark.asyncio
    async def test_emits_routine_on_empty(self):
        hfc_input, api_client = self._make_input(poll_interval=0.01)
        api_client.get_live_alerts.return_value = None

        events = []

        async def emit(event):
            events.append(event)
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await hfc_input.run(emit)

        assert len(events) == 1
        assert events[0].source == 'hfc'
        assert events[0].state == AlertState.ROUTINE
        assert events[0].data is None

    @pytest.mark.asyncio
    async def test_emits_alert_on_data(self):
        hfc_input, api_client = self._make_input(poll_interval=0.01)
        alert_data = {'cat': '1', 'title': 'Rockets', 'data': ['City A'], 'desc': 'Shelter'}
        api_client.get_live_alerts.return_value = alert_data

        events = []

        async def emit(event):
            events.append(event)
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await hfc_input.run(emit)

        assert events[0].source == 'hfc'
        assert events[0].state == AlertState.ALERT
        assert events[0].data == alert_data

    @pytest.mark.asyncio
    async def test_seeds_from_history(self):
        hfc_input, api_client = self._make_input()
        history_data = [{'cat': '1', 'title': 'Rockets', 'data': ['City A'], 'alertDate': '2025-01-15T10:30:00'}]
        api_client.get_recent_alerts_from_history.return_value = history_data

        call_count = 0
        events = []

        async def emit(event):
            nonlocal call_count
            events.append(event)
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError()

        api_client.get_live_alerts.return_value = None

        with pytest.raises(asyncio.CancelledError):
            await hfc_input.run(emit)

        assert events[0].source == 'hfc'
        assert events[0].data == history_data[0]
        assert events[0].state == AlertState.ALERT

    @pytest.mark.asyncio
    async def test_history_failure_doesnt_prevent_polling(self):
        hfc_input, api_client = self._make_input(poll_interval=0.01)
        api_client.get_recent_alerts_from_history.side_effect = RuntimeError('Network error')
        api_client.get_live_alerts.return_value = None

        events = []

        async def emit(event):
            events.append(event)
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await hfc_input.run(emit)

        assert len(events) == 1
        assert events[0].source == 'hfc'

    @pytest.mark.asyncio
    async def test_poll_error_continues(self):
        hfc_input, api_client = self._make_input(poll_interval=0.01)
        call_count = 0
        events = []

        async def failing_then_ok():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError('Network error')
            return None

        api_client.get_live_alerts = failing_then_ok

        async def emit(event):
            events.append(event)
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await hfc_input.run(emit)

        assert len(events) == 1
