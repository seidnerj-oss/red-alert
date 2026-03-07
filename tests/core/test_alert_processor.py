from unittest.mock import MagicMock

import pytest

from red_alert.core.alert_processor import AlertProcessor
from red_alert.core.constants import DEFAULT_UNKNOWN_AREA, ICONS_AND_EMOJIS


@pytest.fixture
def mock_city_data():
    mock = MagicMock()
    mock.get_city_details.side_effect = lambda std: {
        'תל אביב - מרכז העיר': {'area': 'גוש דן', 'original_name': 'תל אביב - מרכז העיר', 'lat': 32.08, 'long': 34.78},
        'רמת גן - מזרח': {'area': 'גוש דן', 'original_name': 'רמת גן - מזרח', 'lat': 32.08, 'long': 34.81},
        'נתניה - מזרח': {'area': 'שרון', 'original_name': 'נתניה - מזרח', 'lat': 32.32, 'long': 34.85},
        'אשקלון - דרום': {'area': 'מערב לכיש', 'original_name': 'אשקלון - דרום', 'lat': 31.67, 'long': 34.57},
    }.get(std)
    return mock


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def processor(mock_city_data, mock_logger):
    return AlertProcessor(mock_city_data, ICONS_AND_EMOJIS, mock_logger, language='en')


class TestExtractDurationFromDesc:
    def test_extracts_minutes(self, processor):
        assert processor.extract_duration_from_desc('היכנסו למרחב המוגן ושהו בו 10 דקות') == 600

    def test_single_minute(self, processor):
        assert processor.extract_duration_from_desc('שהו בו 1 דקה') == 60

    def test_no_duration(self, processor):
        assert processor.extract_duration_from_desc('no duration here') == 0

    def test_empty_string(self, processor):
        assert processor.extract_duration_from_desc('') == 0

    def test_none_input(self, processor):
        assert processor.extract_duration_from_desc(None) == 0

    def test_non_string_input(self, processor):
        assert processor.extract_duration_from_desc(123) == 0


class TestProcessAlertWindowData:
    def test_single_city_alert(self, processor):
        result = processor.process_alert_window_data(
            category=1,
            title='ירי רקטות וטילים',
            description='היכנסו למרחב המוגן ושהו בו 10 דקות',
            window_std_cities={'תל אביב - מרכז העיר'},
            window_alerts_grouped={'ירי רקטות וטילים': {'גוש דן': {'תל אביב - מרכז העיר'}}},
        )

        assert result['data_count'] == 1
        assert result['areas_alert_str'] == 'גוש דן'
        assert 'תל אביב - מרכז העיר' in result['cities_list_sorted']
        assert result['icon_alert'] == 'mdi:rocket-launch'
        assert result['icon_emoji'] == '🚀'
        assert result['duration'] == 600

    def test_multiple_cities_same_area(self, processor):
        result = processor.process_alert_window_data(
            category=1,
            title='ירי רקטות וטילים',
            description='10 דקות',
            window_std_cities={'תל אביב - מרכז העיר', 'רמת גן - מזרח'},
            window_alerts_grouped={'ירי רקטות וטילים': {'גוש דן': {'תל אביב - מרכז העיר', 'רמת גן - מזרח'}}},
        )

        assert result['data_count'] == 2
        assert result['areas_alert_str'] == 'גוש דן'

    def test_multiple_areas(self, processor):
        result = processor.process_alert_window_data(
            category=1,
            title='ירי רקטות וטילים',
            description='10 דקות',
            window_std_cities={'תל אביב - מרכז העיר', 'נתניה - מזרח'},
            window_alerts_grouped={
                'ירי רקטות וטילים': {
                    'גוש דן': {'תל אביב - מרכז העיר'},
                    'שרון': {'נתניה - מזרח'},
                }
            },
        )

        assert result['data_count'] == 2
        assert 'גוש דן' in result['areas_alert_str']
        assert 'שרון' in result['areas_alert_str']

    def test_empty_cities_returns_default(self, processor):
        result = processor.process_alert_window_data(
            category=1,
            title='ירי רקטות וטילים',
            description='10 דקות',
            window_std_cities=set(),
            window_alerts_grouped={},
        )

        assert result['data_count'] == 0
        assert result['areas_alert_str'] == ''
        assert result['cities_list_sorted'] == []

    def test_unknown_city_uses_default_area(self, processor):
        result = processor.process_alert_window_data(
            category=1,
            title='ירי רקטות וטילים',
            description='10 דקות',
            window_std_cities={'עיר לא קיימת'},
            window_alerts_grouped={'ירי רקטות וטילים': {DEFAULT_UNKNOWN_AREA: {'עיר לא קיימת'}}},
        )

        assert result['data_count'] == 1
        assert DEFAULT_UNKNOWN_AREA in result['areas_alert_str']

    def test_multiple_alert_types(self, processor):
        result = processor.process_alert_window_data(
            category=1,
            title='ירי רקטות וטילים',
            description='10 דקות',
            window_std_cities={'תל אביב - מרכז העיר', 'אשקלון - דרום'},
            window_alerts_grouped={
                'ירי רקטות וטילים': {'גוש דן': {'תל אביב - מרכז העיר'}},
                'חדירת כלי טיס עוין': {'מערב לכיש': {'אשקלון - דרום'}},
            },
        )

        assert result['data_count'] == 2
        assert 'Active alerts (2 types)' in result['text_wa_grouped'] or '2' in result['text_wa_grouped']

    def test_whatsapp_message_generated(self, processor):
        result = processor.process_alert_window_data(
            category=1,
            title='ירי רקטות וטילים',
            description='10 דקות',
            window_std_cities={'תל אביב - מרכז העיר'},
            window_alerts_grouped={'ירי רקטות וטילים': {'גוש דן': {'תל אביב - מרכז העיר'}}},
        )

        assert result['text_wa_grouped']
        assert '🚀' in result['text_wa_grouped']

    def test_telegram_message_generated(self, processor):
        result = processor.process_alert_window_data(
            category=1,
            title='ירי רקטות וטילים',
            description='10 דקות',
            window_std_cities={'תל אביב - מרכז העיר'},
            window_alerts_grouped={'ירי רקטות וטילים': {'גוש דן': {'תל אביב - מרכז העיר'}}},
        )

        assert result['text_tg_grouped']
        assert '🚀' in result['text_tg_grouped']


class TestCheckLen:
    def test_short_text_passes_through(self, processor):
        result = processor._check_len('short text', 1, 'area', 100)
        assert result == 'short text'

    def test_long_text_gets_truncated(self, processor):
        long_text = 'x' * 1000
        result = processor._check_len(long_text, 5, 'גוש דן', 100)
        assert len(result) < 1000
        assert '5' in result

    def test_none_input_returns_empty(self, processor):
        result = processor._check_len(None, 0, '', 100)
        assert result == ''


class TestHebrew:
    def test_hebrew_language_processor(self, mock_city_data, mock_logger):
        processor = AlertProcessor(mock_city_data, ICONS_AND_EMOJIS, mock_logger, language='he')

        result = processor.process_alert_window_data(
            category=1,
            title='ירי רקטות וטילים',
            description='10 דקות',
            window_std_cities=set(),
            window_alerts_grouped={},
        )

        assert result['input_text_state'] == 'ירי רקטות וטילים'
