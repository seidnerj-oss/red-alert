from unittest.mock import AsyncMock, patch

import pytest

from red_alert.core.orchestrator import AlertEvent
from red_alert.core.state import AlertState
from red_alert.integrations.outputs.unifi.output import UnifiOutput, _MultiSourceMonitor


class TestMultiSourceMonitor:
    def _make_monitor(self, areas=None, hold=None):
        raw_monitor = AsyncMock()
        raw_monitor.update = AsyncMock(return_value=AlertState.ROUTINE)
        raw_monitor.check_schedule = AsyncMock(return_value=False)
        ms = _MultiSourceMonitor(
            monitor=raw_monitor,
            areas_of_interest=areas,
            hold_seconds=hold or {'alert': 0, 'pre_alert': 0, 'all_clear': 0},
            name='test-monitor',
        )
        return ms

    def _make_event(self, source='hfc', state=AlertState.ALERT, cat='1', cities=None):
        data = {'cat': cat, 'title': 'Test', 'data': cities or ['כפר סבא'], 'desc': ''}
        return AlertEvent(source=source, state=state, data=data)

    @pytest.mark.asyncio
    async def test_first_alert_triggers_update(self):
        ms = self._make_monitor()
        event = self._make_event()
        await ms.handle_event(event)
        ms.monitor.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_first_event_always_triggers_update(self):
        ms = self._make_monitor()

        event1 = AlertEvent(source='hfc', state=AlertState.ROUTINE, data=None)
        await ms.handle_event(event1)
        ms.monitor.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_update_when_state_unchanged_after_first(self):
        ms = self._make_monitor()

        event1 = AlertEvent(source='hfc', state=AlertState.ROUTINE, data=None)
        await ms.handle_event(event1)
        ms.monitor.update.reset_mock()

        event2 = AlertEvent(source='hfc', state=AlertState.ROUTINE, data=None)
        await ms.handle_event(event2)
        ms.monitor.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_multi_source_escalation(self):
        ms = self._make_monitor()

        cbs_event = self._make_event(source='cbs', state=AlertState.PRE_ALERT, cat='14')
        await ms.handle_event(cbs_event)
        assert ms.tracker.state == AlertState.PRE_ALERT
        ms.monitor.update.assert_called_once()

        ms.monitor.update.reset_mock()
        hfc_event = self._make_event(source='hfc', state=AlertState.ALERT, cat='1')
        await ms.handle_event(hfc_event)
        assert ms.tracker.state == AlertState.ALERT
        ms.monitor.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_area_filtering(self):
        ms = self._make_monitor(areas=['כפר סבא'])

        event = self._make_event(cities=['תל אביב'])
        await ms.handle_event(event)
        assert ms.tracker.state == AlertState.ROUTINE
        ms.monitor.update.reset_mock()

        event = self._make_event(cities=['כפר סבא'])
        await ms.handle_event(event)
        assert ms.tracker.state == AlertState.ALERT
        ms.monitor.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_cbs_clear_doesnt_override_hfc_alert(self):
        ms = self._make_monitor(hold={'alert': 1800, 'pre_alert': 1800, 'all_clear': 300})

        hfc = self._make_event(source='hfc', state=AlertState.ALERT, cat='1')
        await ms.handle_event(hfc)
        assert ms.tracker.state == AlertState.ALERT

        cbs_clear = AlertEvent(source='cbs', state=AlertState.ROUTINE, data=None)
        await ms.handle_event(cbs_clear)
        assert ms.tracker.state == AlertState.ALERT


class TestUnifiOutput:
    def test_name(self):
        out = UnifiOutput(config={})
        assert out.name == 'unifi'

    @pytest.mark.asyncio
    async def test_start_requires_credentials(self):
        out = UnifiOutput(config={'host': '1.2.3.4', 'device_macs': ['aa:bb:cc:dd:ee:ff']})
        with pytest.raises(ValueError, match='credentials'):
            await out.start()

    @pytest.mark.asyncio
    async def test_stop_without_start(self):
        out = UnifiOutput(config={})
        await out.stop()


class TestUnifiOutputStartFailure:
    """Failed start() must clean up after itself so the orchestrator retry loop can call it again safely."""

    def _config(self):
        return {
            'host': '172.16.1.1',
            'username': 'user',
            'password': 'pass',
            'backend': 'pyunifiapi',
            'device_macs': ['aa:bb:cc:dd:ee:ff'],
        }

    def _multi_config(self):
        return {
            'username': 'user',
            'password': 'pass',
            'backend': 'pyunifiapi',
            'controllers': [
                {'host': '172.16.1.1', 'monitors': [{'device_macs': ['aa:bb:cc:dd:ee:01']}]},
                {'host': '172.16.1.2', 'monitors': [{'device_macs': ['aa:bb:cc:dd:ee:02']}]},
            ],
        }

    def _patch_led_controller(self, instances, connect_side_effect=None):
        def make_ctrl(*args, **kwargs):
            ctrl = AsyncMock()
            if connect_side_effect is not None:
                ctrl.connect = AsyncMock(side_effect=connect_side_effect)
            instances.append(ctrl)
            return ctrl

        return patch('red_alert.integrations.outputs.unifi.output.UnifiLedController', side_effect=make_ctrl)

    @pytest.mark.asyncio
    async def test_failed_start_does_not_accumulate_state(self):
        out = UnifiOutput(config=self._config())
        instances = []

        with self._patch_led_controller(instances, connect_side_effect=ConnectionError('boom')):
            for _ in range(2):
                with pytest.raises(ConnectionError):
                    await out.start()

        assert out._led_controllers == []
        assert out._monitors == []
        assert out._all_raw_monitors == []
        for ctrl in instances:
            assert ctrl.connect.await_count <= 1

    @pytest.mark.asyncio
    async def test_failed_start_closes_resources(self):
        out = UnifiOutput(config=self._config())
        instances = []

        with self._patch_led_controller(instances, connect_side_effect=ConnectionError('boom')):
            with pytest.raises(ConnectionError):
                await out.start()

        assert out._http_client is None
        instances[0].close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_partial_connect_failure_closes_connected_controllers(self):
        out = UnifiOutput(config=self._multi_config())
        instances = []

        def make_ctrl(*args, **kwargs):
            ctrl = AsyncMock()
            if len(instances) == 1:
                ctrl.connect = AsyncMock(side_effect=ConnectionError('boom'))
            instances.append(ctrl)
            return ctrl

        with patch('red_alert.integrations.outputs.unifi.output.UnifiLedController', side_effect=make_ctrl):
            with pytest.raises(ConnectionError):
                await out.start()

        assert len(instances) == 2
        for ctrl in instances:
            ctrl.close.assert_awaited_once()
        assert out._led_controllers == []
        assert out._http_client is None

    @pytest.mark.asyncio
    async def test_retry_after_failure_connects_single_controller(self):
        out = UnifiOutput(config=self._config())
        instances = []
        fail = {'value': True}

        def make_ctrl(*args, **kwargs):
            ctrl = AsyncMock()
            if fail['value']:
                ctrl.connect = AsyncMock(side_effect=ConnectionError('boom'))
            instances.append(ctrl)
            return ctrl

        with patch('red_alert.integrations.outputs.unifi.output.UnifiLedController', side_effect=make_ctrl):
            with pytest.raises(ConnectionError):
                await out.start()
            fail['value'] = False
            await out.start()

            assert len(out._led_controllers) == 1
            assert sum(ctrl.connect.await_count for ctrl in instances) == 2

            await out.stop()
