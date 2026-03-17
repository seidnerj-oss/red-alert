from red_alert.core.state import AlertState
from red_alert.integrations.inputs.cbs.input import CBS_STATE_TO_CAT, CBS_STATE_TO_TITLE, _cbs_to_alert_dict


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
