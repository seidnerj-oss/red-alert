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


# --- HFC Districts data ---

SAMPLE_HFC_DISTRICTS = [
    {'label': 'כפר סבא', 'label_he': 'כפר סבא', 'value': 'ABC123', 'id': '840', 'areaid': 27, 'areaname': 'שרון', 'migun_time': 90},
    {'label': 'רעננה', 'label_he': 'רעננה', 'value': 'DEF456', 'id': '841', 'areaid': 27, 'areaname': 'שרון', 'migun_time': 90},
    {
        'label': 'תל אביב - מרכז העיר',
        'label_he': 'תל אביב - מרכז העיר',
        'value': 'GHI789',
        'id': '100',
        'areaid': 8,
        'areaname': 'דן',
        'migun_time': 90,
    },
    {'label': 'מטולה', 'label_he': 'מטולה', 'value': 'JKL012', 'id': '200', 'areaid': 6, 'areaname': 'גליל עליון', 'migun_time': 0},
]


class TestHfcDistricts:
    @pytest.mark.asyncio
    async def test_loads_from_hfc_districts(self, manager, mock_api_client):
        mock_api_client.get_districts.return_value = SAMPLE_HFC_DISTRICTS
        mock_api_client.download_file.return_value = None

        result = await manager.load_data()
        assert result is True

        details = manager.get_city_details('כפר סבא')
        assert details is not None
        assert details['area'] == 'שרון'
        assert details['migun_time'] == 90
        assert details['original_name'] == 'כפר סבא'

    @pytest.mark.asyncio
    async def test_hfc_districts_with_icbs_overlay(self, manager, tmp_path, mock_api_client):
        mock_api_client.get_districts.return_value = SAMPLE_HFC_DISTRICTS

        # ICBS has coordinates for תל אביב but uses different area name
        icbs_data = {
            'areas': {
                'גוש דן': {
                    'תל אביב - מרכז העיר': {'lat': 32.0853, 'long': 34.7818},
                }
            }
        }
        file_path = tmp_path / 'city_data.json'
        file_path.write_text(json.dumps(icbs_data), encoding='utf-8-sig')

        result = await manager.load_data()
        assert result is True

        # Area should come from HFC (דן), not ICBS (גוש דן)
        details = manager.get_city_details('תל אביב - מרכז העיר')
        assert details is not None
        assert details['area'] == 'דן'
        assert details['migun_time'] == 90
        # Coordinates should come from ICBS overlay
        assert details['lat'] == 32.0853
        assert details['long'] == 34.7818

    @pytest.mark.asyncio
    async def test_hfc_districts_without_icbs(self, manager, mock_api_client):
        mock_api_client.get_districts.return_value = SAMPLE_HFC_DISTRICTS
        mock_api_client.download_file.return_value = None

        result = await manager.load_data()
        assert result is True

        # Should work but without coordinates
        details = manager.get_city_details('מטולה')
        assert details is not None
        assert details['area'] == 'גליל עליון'
        assert details['migun_time'] == 0
        assert 'lat' not in details
        assert 'long' not in details

    @pytest.mark.asyncio
    async def test_hfc_failure_falls_back_to_icbs(self, manager, tmp_path, mock_api_client, mock_logger):
        mock_api_client.get_districts.return_value = None

        file_path = tmp_path / 'city_data.json'
        file_path.write_text(json.dumps(SAMPLE_CITY_DATA), encoding='utf-8-sig')

        result = await manager.load_data()
        assert result is True

        # Should use ICBS data
        details = manager.get_city_details('תל אביב - מרכז העיר')
        assert details is not None
        assert details['area'] == 'גוש דן'  # ICBS area name
        assert details['lat'] == 32.0853
        assert 'migun_time' not in details

        # Verify fallback warning logged
        log_msgs = [call.args[0] for call in mock_logger.call_args_list]
        assert any('ICBS city data as fallback' in msg for msg in log_msgs)

    @pytest.mark.asyncio
    async def test_hfc_exception_falls_back_to_icbs(self, manager, tmp_path, mock_api_client):
        mock_api_client.get_districts.side_effect = Exception('Network error')

        file_path = tmp_path / 'city_data.json'
        file_path.write_text(json.dumps(SAMPLE_CITY_DATA), encoding='utf-8-sig')

        result = await manager.load_data()
        assert result is True

        details = manager.get_city_details('נתניה - מזרח')
        assert details is not None
        assert details['area'] == 'שרון'

    @pytest.mark.asyncio
    async def test_hfc_empty_list_falls_back_to_icbs(self, manager, tmp_path, mock_api_client):
        mock_api_client.get_districts.return_value = []

        file_path = tmp_path / 'city_data.json'
        file_path.write_text(json.dumps(SAMPLE_CITY_DATA), encoding='utf-8-sig')

        result = await manager.load_data()
        assert result is True

    @pytest.mark.asyncio
    async def test_hfc_districts_skips_invalid_entries(self, manager, mock_api_client):
        districts = [
            {'label_he': 'כפר סבא', 'areaname': 'שרון', 'migun_time': 90},  # valid
            {'label_he': '', 'areaname': 'שרון'},  # empty city name
            {'label_he': 'נתניה', 'areaname': ''},  # empty area name
            'not a dict',  # wrong type
            {'label_he': 'רעננה', 'areaname': 'שרון', 'migun_time': 60},  # valid
        ]
        mock_api_client.get_districts.return_value = districts
        mock_api_client.download_file.return_value = None

        result = await manager.load_data()
        assert result is True

        assert manager.get_city_details('כפר סבא') is not None
        assert manager.get_city_details('רעננה') is not None
        assert manager.get_city_details('נתניה') is None

    @pytest.mark.asyncio
    async def test_migun_time_zero_preserved(self, manager, mock_api_client):
        districts = [
            {'label_he': 'כפר גלעדי', 'areaname': 'גליל עליון', 'migun_time': 0},
        ]
        mock_api_client.get_districts.return_value = districts
        mock_api_client.download_file.return_value = None

        await manager.load_data()
        details = manager.get_city_details('כפר גלעדי')
        assert details is not None
        assert details['migun_time'] == 0

    @pytest.mark.asyncio
    async def test_both_hfc_and_icbs_fail(self, manager, mock_api_client, mock_logger):
        mock_api_client.get_districts.return_value = None
        mock_api_client.download_file.return_value = None

        result = await manager.load_data()
        assert result is False

        log_msgs = [call.args[0] for call in mock_logger.call_args_list]
        assert any('CRITICAL' in msg for msg in log_msgs)
