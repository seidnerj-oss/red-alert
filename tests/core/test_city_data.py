import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from red_alert.core.city_data import CityDataManager


SAMPLE_CITY_DATA = {
    'areas': {
        'גוש דן': {
            'תל אביב - מרכז העיר': {'lat': 32.0853, 'long': 34.7818},
            'רמת גן - מזרח': {'lat': 32.08, 'long': 34.81},
        },
        'שרון': {
            'נתניה - מזרח': {'lat': 32.3215, 'long': 34.8532},
        },
    }
}


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def mock_api_client():
    return AsyncMock()


@pytest.fixture
def manager(tmp_path, mock_api_client, mock_logger):
    file_path = str(tmp_path / 'city_data.json')
    mgr = CityDataManager(file_path, 'https://example.com/city_data.json', mock_api_client, mock_logger)
    # Clear LRU cache between tests
    mgr.get_city_details.cache_clear()
    return mgr


class TestLoadData:
    @pytest.mark.asyncio
    async def test_loads_from_local_file(self, manager, tmp_path, mock_api_client):
        file_path = tmp_path / 'city_data.json'
        file_path.write_text(json.dumps(SAMPLE_CITY_DATA), encoding='utf-8-sig')

        result = await manager.load_data()
        assert result is True
        mock_api_client.download_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_downloads_when_local_missing(self, manager, mock_api_client):
        mock_api_client.download_file.return_value = json.dumps(SAMPLE_CITY_DATA)

        result = await manager.load_data()
        assert result is True
        mock_api_client.download_file.assert_called_once()

    @pytest.mark.asyncio
    async def test_downloads_when_local_invalid(self, manager, tmp_path, mock_api_client):
        file_path = tmp_path / 'city_data.json'
        file_path.write_text('not json', encoding='utf-8')
        mock_api_client.download_file.return_value = json.dumps(SAMPLE_CITY_DATA)

        result = await manager.load_data()
        assert result is True

    @pytest.mark.asyncio
    async def test_fails_when_both_sources_fail(self, manager, mock_api_client):
        mock_api_client.download_file.return_value = None

        result = await manager.load_data()
        assert result is False

    @pytest.mark.asyncio
    async def test_force_download(self, manager, tmp_path, mock_api_client):
        file_path = tmp_path / 'city_data.json'
        file_path.write_text(json.dumps(SAMPLE_CITY_DATA), encoding='utf-8-sig')
        mock_api_client.download_file.return_value = json.dumps(SAMPLE_CITY_DATA)

        result = await manager.load_data(force_download=True)
        assert result is True
        mock_api_client.download_file.assert_called_once()


class TestGetCityDetails:
    @pytest.mark.asyncio
    async def test_returns_details_for_known_city(self, manager, tmp_path):
        file_path = tmp_path / 'city_data.json'
        file_path.write_text(json.dumps(SAMPLE_CITY_DATA), encoding='utf-8-sig')
        await manager.load_data()

        details = manager.get_city_details('תל אביב - מרכז העיר')
        assert details is not None
        assert details['area'] == 'גוש דן'
        assert 'lat' in details
        assert 'long' in details
        assert details['lat'] == 32.0853

    @pytest.mark.asyncio
    async def test_returns_none_for_unknown_city(self, manager, tmp_path):
        file_path = tmp_path / 'city_data.json'
        file_path.write_text(json.dumps(SAMPLE_CITY_DATA), encoding='utf-8-sig')
        await manager.load_data()

        details = manager.get_city_details('עיר לא קיימת')
        assert details is None

    def test_returns_none_for_empty_string(self, manager):
        assert manager.get_city_details('') is None

    def test_returns_none_for_none(self, manager):
        assert manager.get_city_details(None) is None


class TestProcessCityData:
    @pytest.mark.asyncio
    async def test_handles_missing_areas_key(self, manager, mock_api_client, mock_logger):
        mock_api_client.download_file.return_value = json.dumps({'no_areas': {}})
        result = await manager.load_data()
        assert result is False

    @pytest.mark.asyncio
    async def test_handles_partial_coordinates(self, manager, tmp_path):
        data = {
            'areas': {
                'test': {
                    'city1': {'lat': 32.0},
                }
            }
        }
        file_path = tmp_path / 'city_data.json'
        file_path.write_text(json.dumps(data), encoding='utf-8-sig')
        await manager.load_data()

        details = manager.get_city_details('city1')
        assert details is not None
        assert 'lat' not in details

    @pytest.mark.asyncio
    async def test_handles_non_dict_city_details(self, manager, tmp_path, mock_logger):
        data = {
            'areas': {
                'test': {
                    'city1': 'not a dict',
                }
            }
        }
        file_path = tmp_path / 'city_data.json'
        file_path.write_text(json.dumps(data), encoding='utf-8-sig')
        await manager.load_data()

        details = manager.get_city_details('city1')
        assert details is None
