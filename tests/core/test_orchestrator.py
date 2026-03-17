from unittest.mock import AsyncMock

import pytest

from red_alert.core.orchestrator import (
    AlertEvent,
    AlertInput,
    AlertOutput,
    MultiSourceStateTracker,
    Orchestrator,
    STATE_SEVERITY,
)
from red_alert.core.state import AlertState


class TestAlertEvent:
    def test_defaults(self):
        event = AlertEvent(source='hfc', state=AlertState.ALERT)
        assert event.source == 'hfc'
        assert event.state == AlertState.ALERT
        assert event.data is None
        assert isinstance(event.timestamp, float)

    def test_with_data(self):
        data = {'cat': '1', 'title': 'Test', 'data': ['City A'], 'desc': ''}
        event = AlertEvent(source='hfc', state=AlertState.ALERT, data=data, timestamp=100.0)
        assert event.data == data
        assert event.timestamp == 100.0


class TestStateSeverity:
    def test_ordering(self):
        assert STATE_SEVERITY[AlertState.ROUTINE] < STATE_SEVERITY[AlertState.ALL_CLEAR]
        assert STATE_SEVERITY[AlertState.ALL_CLEAR] < STATE_SEVERITY[AlertState.PRE_ALERT]
        assert STATE_SEVERITY[AlertState.PRE_ALERT] < STATE_SEVERITY[AlertState.ALERT]


class TestMultiSourceStateTracker:
    def _make_alert_data(self, cat='1', cities=None):
        return {'cat': cat, 'title': 'Test', 'data': cities or ['כפר סבא'], 'desc': ''}

    def test_initial_state_is_routine(self):
        tracker = MultiSourceStateTracker()
        assert tracker.state == AlertState.ROUTINE

    def test_single_source_alert(self):
        tracker = MultiSourceStateTracker()
        event = AlertEvent(source='hfc', state=AlertState.ALERT, data=self._make_alert_data())
        old, new = tracker.update(event)
        assert old == AlertState.ROUTINE
        assert new == AlertState.ALERT

    def test_single_source_returns_to_routine(self):
        tracker = MultiSourceStateTracker(hold_seconds={'alert': 0, 'pre_alert': 0, 'all_clear': 0})
        event = AlertEvent(source='hfc', state=AlertState.ALERT, data=self._make_alert_data())
        tracker.update(event)

        empty_event = AlertEvent(source='hfc', state=AlertState.ROUTINE, data=None)
        old, new = tracker.update(empty_event)
        assert old == AlertState.ALERT
        assert new == AlertState.ROUTINE

    def test_two_sources_max_severity(self):
        tracker = MultiSourceStateTracker()

        cbs_event = AlertEvent(source='cbs', state=AlertState.PRE_ALERT, data=self._make_alert_data(cat='14'))
        old, new = tracker.update(cbs_event)
        assert new == AlertState.PRE_ALERT

        hfc_event = AlertEvent(source='hfc', state=AlertState.ALERT, data=self._make_alert_data(cat='1'))
        old, new = tracker.update(hfc_event)
        assert old == AlertState.PRE_ALERT
        assert new == AlertState.ALERT

    def test_one_source_clearing_doesnt_override_other(self):
        tracker = MultiSourceStateTracker(hold_seconds={'alert': 0, 'pre_alert': 0, 'all_clear': 0})

        hfc_event = AlertEvent(source='hfc', state=AlertState.ALERT, data=self._make_alert_data(cat='1'))
        tracker.update(hfc_event)

        cbs_event = AlertEvent(source='cbs', state=AlertState.PRE_ALERT, data=self._make_alert_data(cat='14'))
        tracker.update(cbs_event)
        assert tracker.state == AlertState.ALERT

        cbs_clear = AlertEvent(source='cbs', state=AlertState.ROUTINE, data=None)
        tracker.update(cbs_clear)
        assert tracker.state == AlertState.ALERT
        assert tracker.get_source_state('hfc') == AlertState.ALERT
        assert tracker.get_source_state('cbs') == AlertState.ROUTINE

    def test_all_sources_clear_returns_routine(self):
        tracker = MultiSourceStateTracker(hold_seconds={'alert': 0, 'pre_alert': 0, 'all_clear': 0})

        hfc_event = AlertEvent(source='hfc', state=AlertState.ALERT, data=self._make_alert_data())
        tracker.update(hfc_event)
        cbs_event = AlertEvent(source='cbs', state=AlertState.PRE_ALERT, data=self._make_alert_data(cat='14'))
        tracker.update(cbs_event)

        hfc_clear = AlertEvent(source='hfc', state=AlertState.ROUTINE, data=None)
        tracker.update(hfc_clear)
        assert tracker.state == AlertState.PRE_ALERT

        cbs_clear = AlertEvent(source='cbs', state=AlertState.ROUTINE, data=None)
        tracker.update(cbs_clear)
        assert tracker.state == AlertState.ROUTINE

    def test_area_filtering(self):
        tracker = MultiSourceStateTracker(
            areas_of_interest=['כפר סבא'],
            hold_seconds={'alert': 0, 'pre_alert': 0, 'all_clear': 0},
        )

        event = AlertEvent(source='hfc', state=AlertState.ALERT, data=self._make_alert_data(cities=['תל אביב']))
        tracker.update(event)
        assert tracker.state == AlertState.ROUTINE

        event = AlertEvent(source='hfc', state=AlertState.ALERT, data=self._make_alert_data(cities=['כפר סבא']))
        tracker.update(event)
        assert tracker.state == AlertState.ALERT

    def test_get_source_state_unknown_source(self):
        tracker = MultiSourceStateTracker()
        assert tracker.get_source_state('nonexistent') == AlertState.ROUTINE

    def test_alert_data_tracks_highest_severity(self):
        tracker = MultiSourceStateTracker()
        alert_data = self._make_alert_data(cat='1')
        pre_alert_data = self._make_alert_data(cat='14')

        tracker.update(AlertEvent(source='cbs', state=AlertState.PRE_ALERT, data=pre_alert_data))
        assert tracker.alert_data == pre_alert_data

        tracker.update(AlertEvent(source='hfc', state=AlertState.ALERT, data=alert_data))
        assert tracker.alert_data == alert_data

    def test_severity_escalation_cbs_then_hfc(self):
        tracker = MultiSourceStateTracker(hold_seconds={'alert': 1800, 'pre_alert': 1800, 'all_clear': 300})

        tracker.update(AlertEvent(source='cbs', state=AlertState.PRE_ALERT, data=self._make_alert_data(cat='14')))
        assert tracker.state == AlertState.PRE_ALERT

        tracker.update(AlertEvent(source='hfc', state=AlertState.ALERT, data=self._make_alert_data(cat='1')))
        assert tracker.state == AlertState.ALERT

        tracker.update(AlertEvent(source='cbs', state=AlertState.ALL_CLEAR, data=self._make_alert_data(cat='13')))
        assert tracker.state == AlertState.ALERT

    def test_creates_trackers_lazily(self):
        tracker = MultiSourceStateTracker()
        assert len(tracker._trackers) == 0
        tracker.update(AlertEvent(source='hfc', state=AlertState.ROUTINE, data=None))
        assert 'hfc' in tracker._trackers
        assert 'cbs' not in tracker._trackers


class _DummyInput(AlertInput):
    def __init__(self, name_val: str, events: list[AlertEvent] | None = None, error: Exception | None = None):
        self._name = name_val
        self._events = events or []
        self._error = error
        self.stopped = False

    @property
    def name(self) -> str:
        return self._name

    async def run(self, emit):
        if self._error:
            raise self._error
        for event in self._events:
            await emit(event)

    async def stop(self):
        self.stopped = True


class _DummyOutput(AlertOutput):
    def __init__(self, name_val: str, fail_on_event: bool = False):
        self._name = name_val
        self.started = False
        self.stopped = False
        self.events: list[AlertEvent] = []
        self._fail_on_event = fail_on_event

    @property
    def name(self) -> str:
        return self._name

    async def start(self):
        self.started = True

    async def handle_event(self, event):
        if self._fail_on_event:
            raise RuntimeError('Output failed')
        self.events.append(event)

    async def stop(self):
        self.stopped = True


class TestOrchestrator:
    @pytest.mark.asyncio
    async def test_routes_events_to_outputs(self):
        event = AlertEvent(source='hfc', state=AlertState.ALERT, data={'cat': '1', 'title': 'Test', 'data': ['City'], 'desc': ''})
        inp = _DummyInput('hfc', events=[event])
        out1 = _DummyOutput('unifi')
        out2 = _DummyOutput('telegram')

        orchestrator = Orchestrator([inp], [out1, out2])
        await orchestrator.run()

        assert out1.started
        assert out2.started
        assert len(out1.events) == 1
        assert len(out2.events) == 1
        assert out1.events[0] is event
        assert out2.events[0] is event
        assert inp.stopped
        assert out1.stopped
        assert out2.stopped

    @pytest.mark.asyncio
    async def test_output_failure_doesnt_affect_other_outputs(self):
        event = AlertEvent(source='hfc', state=AlertState.ALERT)
        inp = _DummyInput('hfc', events=[event])
        failing_out = _DummyOutput('bad', fail_on_event=True)
        good_out = _DummyOutput('good')

        orchestrator = Orchestrator([inp], [failing_out, good_out])
        await orchestrator.run()

        assert len(good_out.events) == 1

    @pytest.mark.asyncio
    async def test_start_failure_raises(self):
        inp = _DummyInput('hfc')
        out = _DummyOutput('bad')
        out.start = AsyncMock(side_effect=ValueError('Connection failed'))

        orchestrator = Orchestrator([inp], [out])
        with pytest.raises(ValueError, match='Connection failed'):
            await orchestrator.run()

    @pytest.mark.asyncio
    async def test_empty_inputs(self):
        inp = _DummyInput('hfc', events=[])
        out = _DummyOutput('unifi')
        orchestrator = Orchestrator([inp], [out])
        await orchestrator.run()
        assert out.started
        assert len(out.events) == 0

    @pytest.mark.asyncio
    async def test_input_error_stops_orchestrator(self):
        inp = _DummyInput('hfc', error=RuntimeError('Input crashed'))
        out = _DummyOutput('unifi')
        orchestrator = Orchestrator([inp], [out])
        await orchestrator.run()
        assert inp.stopped
        assert out.stopped

    @pytest.mark.asyncio
    async def test_multiple_events(self):
        events = [
            AlertEvent(source='hfc', state=AlertState.PRE_ALERT, data={'cat': '14', 'title': 'Pre', 'data': ['City'], 'desc': ''}),
            AlertEvent(source='hfc', state=AlertState.ALERT, data={'cat': '1', 'title': 'Alert', 'data': ['City'], 'desc': ''}),
            AlertEvent(source='hfc', state=AlertState.ROUTINE, data=None),
        ]
        inp = _DummyInput('hfc', events=events)
        out = _DummyOutput('unifi')
        orchestrator = Orchestrator([inp], [out])
        await orchestrator.run()
        assert len(out.events) == 3
