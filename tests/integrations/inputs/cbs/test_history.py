"""Tests for CBS message history persistence."""

import json
import time


from red_alert.core.state import AlertState
from red_alert.integrations.inputs.cbs.history import CbsHistory
from red_alert.integrations.inputs.cbs.parser import CbsMessage


def _make_message(message_id: int = 4370, serial: int = 0x5F30, text_hex: str = '00410042') -> CbsMessage:
    return CbsMessage(serial_number=serial, message_id=message_id, dcs=0x59, total_pages=1, pages={1: text_hex})


class TestRecord:
    def test_record_persists_to_file(self, tmp_path):
        path = str(tmp_path / 'cbs_history.json')
        history = CbsHistory(path=path)
        history.record(_make_message(), AlertState.PRE_ALERT)

        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        assert len(data) == 1
        assert data[0]['message_id'] == 4370
        assert data[0]['state'] == 'pre_alert'
        assert 'timestamp' in data[0]
        assert 'datetime' in data[0]

    def test_record_appends(self, tmp_path):
        path = str(tmp_path / 'cbs_history.json')
        history = CbsHistory(path=path)
        history.record(_make_message(4370), AlertState.PRE_ALERT)
        history.record(_make_message(4373), AlertState.ALL_CLEAR)

        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        assert len(data) == 2
        assert data[0]['state'] == 'pre_alert'
        assert data[1]['state'] == 'all_clear'

    def test_record_stores_text(self, tmp_path):
        path = str(tmp_path / 'cbs_history.json')
        history = CbsHistory(path=path)
        history.record(_make_message(), AlertState.PRE_ALERT)

        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        assert data[0]['text'] == 'AB'


class TestGetRecent:
    def test_returns_empty_when_no_history(self, tmp_path):
        history = CbsHistory(path=str(tmp_path / 'cbs_history.json'))
        assert history.get_recent() == []

    def test_returns_recent_entries(self, tmp_path):
        path = str(tmp_path / 'cbs_history.json')
        history = CbsHistory(path=path)
        history.record(_make_message(4370), AlertState.PRE_ALERT)
        history.record(_make_message(4373), AlertState.ALL_CLEAR)

        recent = history.get_recent()
        assert len(recent) == 2
        assert recent[0]['state'] == 'all_clear'
        assert recent[1]['state'] == 'pre_alert'

    def test_filters_by_age(self, tmp_path):
        path = str(tmp_path / 'cbs_history.json')
        now = time.time()
        entries = [
            {'timestamp': now - 10, 'message_id': 4370, 'state': 'pre_alert'},
            {'timestamp': now - 120, 'message_id': 4373, 'state': 'all_clear'},
        ]
        with open(path, 'w') as f:
            json.dump(entries, f)

        history = CbsHistory(path=path)
        recent = history.get_recent(max_age_seconds=60)
        assert len(recent) == 1
        assert recent[0]['state'] == 'pre_alert'


class TestGetLatestState:
    def test_returns_none_when_empty(self, tmp_path):
        history = CbsHistory(path=str(tmp_path / 'cbs_history.json'))
        assert history.get_latest_state() is None

    def test_returns_latest_state(self, tmp_path):
        path = str(tmp_path / 'cbs_history.json')
        history = CbsHistory(path=path)
        history.record(_make_message(4370), AlertState.PRE_ALERT)
        history.record(_make_message(4373), AlertState.ALL_CLEAR)

        result = history.get_latest_state()
        assert result is not None
        state, timestamp = result
        assert state == AlertState.ALL_CLEAR
        assert timestamp > 0

    def test_returns_none_when_all_expired(self, tmp_path):
        path = str(tmp_path / 'cbs_history.json')
        entries = [{'timestamp': time.time() - 7200, 'message_id': 4370, 'state': 'pre_alert'}]
        with open(path, 'w') as f:
            json.dump(entries, f)

        history = CbsHistory(path=path, max_age_seconds=3600)
        assert history.get_latest_state() is None

    def test_respects_max_age_parameter(self, tmp_path):
        path = str(tmp_path / 'cbs_history.json')
        history = CbsHistory(path=path)
        history.record(_make_message(4370), AlertState.PRE_ALERT)

        assert history.get_latest_state(max_age_seconds=60) is not None
        assert history.get_latest_state(max_age_seconds=0) is None


class TestPruning:
    def test_prunes_old_entries_on_load(self, tmp_path):
        path = str(tmp_path / 'cbs_history.json')
        now = time.time()
        entries = [
            {'timestamp': now - 7200, 'message_id': 4370, 'state': 'pre_alert'},
            {'timestamp': now - 10, 'message_id': 4373, 'state': 'all_clear'},
        ]
        with open(path, 'w') as f:
            json.dump(entries, f)

        history = CbsHistory(path=path, max_age_seconds=3600)
        assert len(history.get_recent()) == 1

    def test_prunes_on_record(self, tmp_path):
        path = str(tmp_path / 'cbs_history.json')
        now = time.time()
        entries = [{'timestamp': now - 7200, 'message_id': 4370, 'state': 'pre_alert'}]
        with open(path, 'w') as f:
            json.dump(entries, f)

        history = CbsHistory(path=path, max_age_seconds=3600)
        history.record(_make_message(4373), AlertState.ALL_CLEAR)

        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        assert len(data) == 1
        assert data[0]['state'] == 'all_clear'


class TestPersistence:
    def test_survives_reload(self, tmp_path):
        path = str(tmp_path / 'cbs_history.json')
        history1 = CbsHistory(path=path)
        history1.record(_make_message(4370), AlertState.PRE_ALERT)

        history2 = CbsHistory(path=path)
        result = history2.get_latest_state()
        assert result is not None
        assert result[0] == AlertState.PRE_ALERT

    def test_handles_missing_file(self, tmp_path):
        history = CbsHistory(path=str(tmp_path / 'nonexistent.json'))
        assert history.get_recent() == []

    def test_handles_corrupt_file(self, tmp_path):
        path = str(tmp_path / 'cbs_history.json')
        with open(path, 'w') as f:
            f.write('not json')

        history = CbsHistory(path=path)
        assert history.get_recent() == []
