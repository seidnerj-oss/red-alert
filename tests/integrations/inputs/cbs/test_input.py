from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from red_alert.core.state import AlertState
from red_alert.integrations.inputs.cbs.input import CBS_STATE_TO_CAT, CBS_STATE_TO_TITLE, CbsInput, _cbs_to_alert_dict
from red_alert.integrations.inputs.cbs.server import HEALTH_CHECK_FAILURES_BEFORE_REBUILD, QMICLI_STOP_TIMEOUT, CbsAlertMonitor


class TestCbsInputHealthCheck:
    @pytest.mark.asyncio
    async def test_health_check_gets_the_monitor_so_it_can_rebuild_the_bridge(self, tmp_path):
        mock_bridge = MagicMock()
        mock_bridge.lte_host = '192.168.1.100'
        mock_bridge.bridge_port = 18222
        mock_bridge.ensure_bridge = AsyncMock(return_value=True)
        mock_bridge.configure_cbs = AsyncMock(return_value=True)
        mock_bridge.close = AsyncMock()

        cbs_input = CbsInput(
            {
                'areas_of_interest': ['כפר סבא'],
                'lte_host': '192.168.1.100',
                'history_path': str(tmp_path / 'cbs_history.json'),
                'health_check_interval': 300,
            }
        )

        with (
            patch('red_alert.integrations.inputs.cbs.input._resolve_location', new_callable=AsyncMock, return_value=['כפר סבא']),
            patch('red_alert.integrations.inputs.cbs.input._create_bridge', return_value=mock_bridge),
            patch('red_alert.integrations.inputs.cbs.input._periodic_health_check', new_callable=AsyncMock) as mock_health,
            patch.object(CbsAlertMonitor, 'run_subprocess', new_callable=AsyncMock, side_effect=KeyboardInterrupt),
            pytest.raises(KeyboardInterrupt),
        ):
            await cbs_input.run(AsyncMock())

        mock_health.assert_called_once_with(mock_bridge, cbs_input._monitor, 300, HEALTH_CHECK_FAILURES_BEFORE_REBUILD, QMICLI_STOP_TIMEOUT)
        mock_bridge.close.assert_awaited_once()


class TestCbsToAlertDict:
    def test_routine_returns_none(self):
        assert _cbs_to_alert_dict(AlertState.ROUTINE, ['כפר סבא']) is None

    def test_pre_alert(self):
        result = _cbs_to_alert_dict(AlertState.PRE_ALERT, ['כפר סבא', 'תל אביב'])
        assert result == {
            'cat': '14',
            'title': 'CBS pre-alert',
            'data': ['כפר סבא', 'תל אביב'],
            'desc': '',
        }

    def test_alert(self):
        result = _cbs_to_alert_dict(AlertState.ALERT, ['כפר סבא'])
        assert result == {
            'cat': '1',
            'title': 'CBS alert',
            'data': ['כפר סבא'],
            'desc': '',
        }

    def test_all_clear(self):
        result = _cbs_to_alert_dict(AlertState.ALL_CLEAR, ['כפר סבא'])
        assert result == {
            'cat': '13',
            'title': 'האירוע הסתיים',
            'data': ['כפר סבא'],
            'desc': '',
        }

    def test_empty_areas(self):
        result = _cbs_to_alert_dict(AlertState.PRE_ALERT, [])
        assert result['data'] == []


class TestCbsStateMapping:
    def test_cat_mapping_complete(self):
        assert CBS_STATE_TO_CAT[AlertState.PRE_ALERT] == '14'
        assert CBS_STATE_TO_CAT[AlertState.ALERT] == '1'
        assert CBS_STATE_TO_CAT[AlertState.ALL_CLEAR] == '13'

    def test_title_mapping_complete(self):
        assert CBS_STATE_TO_TITLE[AlertState.PRE_ALERT] == 'CBS pre-alert'
        assert CBS_STATE_TO_TITLE[AlertState.ALERT] == 'CBS alert'
        assert CBS_STATE_TO_TITLE[AlertState.ALL_CLEAR] == 'האירוע הסתיים'
