import json
import os
from unittest.mock import MagicMock

import pytest

from red_alert.integrations.homeassistant.file_manager import FileManager


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def file_manager(tmp_path, mock_logger):
    paths = {
        'json_backup': str(tmp_path / 'backup.json'),
        'txt_history': str(tmp_path / 'history.txt'),
        'csv': str(tmp_path / 'history.csv'),
    }
    day_names = {
        'Sunday': 'Sunday',
        'Monday': 'Monday',
        'Tuesday': 'Tuesday',
        'Wednesday': 'Wednesday',
        'Thursday': 'Thursday',
        'Friday': 'Friday',
        'Saturday': 'Saturday',
    }
    return FileManager(paths, save_enabled=True, day_names_map=day_names, timer_duration=120, logger=mock_logger, language='en')


class TestGetFromJson:
    def test_loads_valid_json(self, file_manager, tmp_path):
        data = {'id': 123, 'title': 'Test Alert'}
        backup_path = tmp_path / 'backup.json'
        backup_path.write_text(json.dumps(data), encoding='utf-8-sig')

        result = file_manager.get_from_json()
        assert result == data

    def test_returns_none_when_file_missing(self, file_manager):
        result = file_manager.get_from_json()
        assert result is None

    def test_returns_none_when_save_disabled(self, tmp_path, mock_logger):
        paths = {'json_backup': str(tmp_path / 'backup.json')}
        fm = FileManager(paths, save_enabled=False, day_names_map={}, timer_duration=120, logger=mock_logger)
        result = fm.get_from_json()
        assert result is None

    def test_returns_none_on_invalid_json(self, file_manager, tmp_path):
        backup_path = tmp_path / 'backup.json'
        backup_path.write_text('not json', encoding='utf-8-sig')

        result = file_manager.get_from_json()
        assert result is None


class TestSaveJsonBackup:
    def test_saves_data(self, file_manager, tmp_path):
        data = {'id': 456, 'title': 'Saved Alert'}
        file_manager.save_json_backup(data)

        backup_path = tmp_path / 'backup.json'
        assert backup_path.exists()
        loaded = json.loads(backup_path.read_text(encoding='utf-8-sig'))
        assert loaded == data

    def test_skips_when_save_disabled(self, tmp_path, mock_logger):
        paths = {'json_backup': str(tmp_path / 'backup.json')}
        fm = FileManager(paths, save_enabled=False, day_names_map={}, timer_duration=120, logger=mock_logger)
        fm.save_json_backup({'test': True})
        assert not (tmp_path / 'backup.json').exists()


class TestCreateCsvHeader:
    def test_creates_csv_with_header(self, file_manager, tmp_path):
        file_manager.create_csv_header_if_needed()

        csv_path = tmp_path / 'history.csv'
        assert csv_path.exists()
        content = csv_path.read_text(encoding='utf-8-sig')
        assert 'ID' in content
        assert 'TITLE' in content

    def test_does_not_overwrite_existing(self, file_manager, tmp_path):
        csv_path = tmp_path / 'history.csv'
        csv_path.write_text('existing content', encoding='utf-8-sig')

        file_manager.create_csv_header_if_needed()
        assert csv_path.read_text(encoding='utf-8-sig') == 'existing content'


class TestSaveGeojsonFile:
    def test_saves_valid_geojson(self, file_manager, tmp_path):
        geojson = {'type': 'FeatureCollection', 'features': []}
        path = str(tmp_path / 'test.geojson')

        file_manager.save_geojson_file(geojson, path)

        assert os.path.exists(path)
        loaded = json.loads(open(path, encoding='utf-8-sig').read())
        assert loaded == geojson

    def test_skips_invalid_data(self, file_manager, tmp_path, mock_logger):
        path = str(tmp_path / 'test.geojson')
        file_manager.save_geojson_file({'no_features': True}, path)
        assert not os.path.exists(path)

    def test_skips_when_path_missing(self, file_manager, mock_logger):
        file_manager.save_geojson_file({'type': 'FeatureCollection', 'features': []}, None)
        mock_logger.assert_called()

    def test_skips_when_save_disabled(self, tmp_path, mock_logger):
        paths = {}
        fm = FileManager(paths, save_enabled=False, day_names_map={}, timer_duration=120, logger=mock_logger)
        path = str(tmp_path / 'test.geojson')
        fm.save_geojson_file({'type': 'FeatureCollection', 'features': []}, path)
        assert not os.path.exists(path)


class TestSaveHistoryFiles:
    def test_saves_history_to_txt_and_csv(self, file_manager, tmp_path):
        attrs = {
            'id': 1,
            'last_changed': '2024-01-15T10:30:45.000000',
            'full_message_str': 'Test alert message',
            'prev_title': 'Test Title',
            'prev_data_count': 3,
            'prev_areas_alert_str': 'Area 1',
            'prev_alerts_cities_str': 'City A, City B',
            'prev_desc': 'Test description',
            'prev_alerts_count': 5,
        }

        file_manager.save_history_files(attrs)

        txt_path = tmp_path / 'history.txt'
        csv_path = tmp_path / 'history.csv'
        assert txt_path.exists()
        assert csv_path.exists()
        assert 'Test alert message' in txt_path.read_text(encoding='utf-8-sig')

    def test_does_not_save_duplicate_id(self, file_manager, tmp_path):
        attrs = {
            'id': 42,
            'last_changed': '2024-01-15T10:30:45.000000',
            'full_message_str': 'Alert 1',
            'prev_title': 'T1',
            'prev_data_count': 1,
            'prev_areas_alert_str': '',
            'prev_alerts_cities_str': '',
            'prev_desc': '',
            'prev_alerts_count': 1,
        }

        file_manager.save_history_files(attrs)
        file_manager.save_history_files(attrs)

        txt_path = tmp_path / 'history.txt'
        content = txt_path.read_text(encoding='utf-8-sig')
        assert content.count('Alert 1') == 1

    def test_clear_last_saved_id(self, file_manager):
        file_manager._last_saved_alert_id = 42
        file_manager.clear_last_saved_id()
        assert file_manager._last_saved_alert_id is None
