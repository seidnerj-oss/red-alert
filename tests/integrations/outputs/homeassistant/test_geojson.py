from datetime import datetime
from unittest.mock import MagicMock

import pytest

from red_alert.integrations.outputs.homeassistant.geojson import generate_geojson_data


@pytest.fixture
def mock_city_data():
    mock = MagicMock()
    mock.get_city_details.side_effect = lambda std: {
        'תל אביב - מרכז העיר': {'area': 'גוש דן', 'original_name': 'תל אביב - מרכז העיר', 'lat': 32.0853, 'long': 34.7818},
        'אשקלון - דרום': {'area': 'מערב לכיש', 'original_name': 'אשקלון - דרום', 'lat': 31.67, 'long': 34.57},
    }.get(std)
    return mock


@pytest.fixture
def mock_logger():
    return MagicMock()


class TestGenerateGeojsonLatest:
    def test_generates_features_for_known_cities(self, mock_city_data, mock_logger):
        attrs = {
            'cities': ['תל אביב - מרכז העיר'],
            'title': 'ירי רקטות וטילים',
            'last_changed': datetime.now().isoformat(),
            'cat': 1,
            'desc': 'Test description',
        }

        result = generate_geojson_data(attrs, 'latest', mock_city_data, mock_logger)
        assert result['type'] == 'FeatureCollection'
        assert len(result['features']) == 1
        feature = result['features'][0]
        assert feature['type'] == 'Feature'
        assert feature['geometry']['type'] == 'Point'
        assert feature['geometry']['coordinates'] == [34.7818, 32.0853]
        assert 'תל אביב - מרכז העיר' in feature['properties']['name']

    def test_returns_empty_features_for_no_cities(self, mock_city_data, mock_logger):
        attrs = {'cities': [], 'title': 'No alerts'}
        result = generate_geojson_data(attrs, 'latest', mock_city_data, mock_logger)
        assert result['features'] == []

    def test_returns_empty_for_none_attributes(self, mock_city_data, mock_logger):
        result = generate_geojson_data(None, 'latest', mock_city_data, mock_logger)
        assert result['features'] == []

    def test_skips_unknown_cities(self, mock_city_data, mock_logger):
        attrs = {
            'cities': ['עיר לא קיימת'],
            'title': 'Test',
            'last_changed': datetime.now().isoformat(),
            'cat': 1,
            'desc': '',
        }
        result = generate_geojson_data(attrs, 'latest', mock_city_data, mock_logger)
        assert result['features'] == []

    def test_groups_cities_at_same_coordinates(self, mock_city_data, mock_logger):
        # Both cities at same location
        mock_city_data.get_city_details.side_effect = lambda std: {
            'CityA': {'lat': 32.0, 'long': 34.0, 'original_name': 'CityA'},
            'CityB': {'lat': 32.0, 'long': 34.0, 'original_name': 'CityB'},
        }.get(std)

        attrs = {
            'cities': ['CityA', 'CityB'],
            'title': 'Test',
            'last_changed': datetime.now().isoformat(),
            'cat': 1,
            'desc': '',
        }
        result = generate_geojson_data(attrs, 'latest', mock_city_data, mock_logger)
        assert len(result['features']) == 1
        assert 'CityA' in result['features'][0]['properties']['name']
        assert 'CityB' in result['features'][0]['properties']['name']


class TestGenerateGeojsonHistory:
    def test_generates_features_for_history_alerts(self, mock_city_data, mock_logger):
        attrs = {
            'last_24h_alerts': [
                {'city': 'תל אביב - מרכז העיר', 'title': 'ירי רקטות', 'time': '2024-01-15 10:30:45', 'area': 'גוש דן'},
                {'city': 'אשקלון - דרום', 'title': 'ירי רקטות', 'time': '2024-01-15 10:31:00', 'area': 'מערב לכיש'},
            ]
        }

        result = generate_geojson_data(attrs, 'history', mock_city_data, mock_logger)
        assert result['type'] == 'FeatureCollection'
        assert len(result['features']) == 2

    def test_empty_history(self, mock_city_data, mock_logger):
        result = generate_geojson_data({'last_24h_alerts': []}, 'history', mock_city_data, mock_logger)
        assert result['features'] == []

    def test_counts_alerts_at_location(self, mock_city_data, mock_logger):
        attrs = {
            'last_24h_alerts': [
                {'city': 'תל אביב - מרכז העיר', 'title': 'Alert 1', 'time': '2024-01-15 10:30:45', 'area': 'גוש דן'},
                {'city': 'תל אביב - מרכז העיר', 'title': 'Alert 2', 'time': '2024-01-15 11:30:45', 'area': 'גוש דן'},
            ]
        }

        result = generate_geojson_data(attrs, 'history', mock_city_data, mock_logger)
        assert len(result['features']) == 1
        assert result['features'][0]['properties']['alert_count_at_location'] == 2


class TestUnknownDuration:
    def test_unknown_duration_logs_warning(self, mock_city_data, mock_logger):
        result = generate_geojson_data({}, 'unknown', mock_city_data, mock_logger)
        assert result['features'] == []
        mock_logger.assert_called()
