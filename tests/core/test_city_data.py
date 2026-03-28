import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from red_alert.core.city_data import CityDataManager, _haversine_km, find_cities_near


SAMPLE_CITY_DATA = {
    'areas': {
        'דן': {
            'תל אביב - מרכז העיר': {'original_name': 'תל אביב - מרכז העיר', 'lat': 32.0853, 'long': 34.7818, 'migun_time': 90},
            'רמת גן - מזרח': {'original_name': 'רמת גן - מזרח', 'lat': 32.08, 'long': 34.81, 'migun_time': 90},
        },
        'שרון': {
            'נתניה - מזרח': {'original_name': 'נתניה - מזרח', 'lat': 32.3215, 'long': 34.8532, 'migun_time': 60},
        },
    }
}


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def mock_api_client():
    client = AsyncMock()
    client._client = AsyncMock()
    return client


@pytest.fixture
def manager(tmp_path, mock_api_client, mock_logger):
    file_path = str(tmp_path / 'city_data.json')
    mgr = CityDataManager(file_path, '', mock_api_client, mock_logger)
    mgr.get_city_details.cache_clear()
    return mgr


class TestHaversine:
    def test_same_point_is_zero(self):
        assert _haversine_km(32.0, 34.0, 32.0, 34.0) == 0.0

    def test_known_distance(self):
        dist = _haversine_km(32.0853, 34.7818, 32.7940, 34.9896)
        assert 78 < dist < 88

    def test_short_distance(self):
        dist = _haversine_km(32.0853, 34.7818, 32.0853, 34.7918)
        assert 0.5 < dist < 2.0


class TestFindCitiesNear:
    def test_finds_nearby_cities(self, tmp_path):
        data = {
            'areas': {
                'דן': {
                    'תל אביב - מרכז העיר': {'lat': 32.0853, 'long': 34.7818},
                    'רמת גן - מזרח': {'lat': 32.08, 'long': 34.81},
                },
                'שרון': {
                    'נתניה - מזרח': {'lat': 32.3215, 'long': 34.8532},
                },
            }
        }
        path = tmp_path / 'city_data.json'
        path.write_text(json.dumps(data), encoding='utf-8-sig')

        result = find_cities_near(32.0853, 34.7818, radius_km=5.0, city_data_path=str(path))
        assert 'תל אביב - מרכז העיר' in result
        assert 'רמת גן - מזרח' in result
        assert 'נתניה - מזרח' not in result

    def test_sorted_by_distance(self, tmp_path):
        data = {'areas': {'test': {'far': {'lat': 32.12, 'long': 34.78}, 'near': {'lat': 32.086, 'long': 34.782}}}}
        path = tmp_path / 'city_data.json'
        path.write_text(json.dumps(data), encoding='utf-8-sig')

        result = find_cities_near(32.0853, 34.7818, radius_km=10.0, city_data_path=str(path))
        assert result[0] == 'near'
        assert result[1] == 'far'

    def test_empty_on_no_match(self, tmp_path):
        data = {'areas': {'test': {'far_city': {'lat': 33.0, 'long': 35.0}}}}
        path = tmp_path / 'city_data.json'
        path.write_text(json.dumps(data), encoding='utf-8-sig')

        result = find_cities_near(32.0853, 34.7818, radius_km=1.0, city_data_path=str(path))
        assert result == []

    def test_missing_file_returns_empty(self, tmp_path):
        result = find_cities_near(32.0, 34.0, city_data_path=str(tmp_path / 'nonexistent.json'))
        assert result == []

    def test_skips_entries_without_coords(self, tmp_path):
        data = {'areas': {'test': {'no_coords': {'name': 'x'}, 'with_coords': {'lat': 32.085, 'long': 34.782}}}}
        path = tmp_path / 'city_data.json'
        path.write_text(json.dumps(data), encoding='utf-8-sig')

        result = find_cities_near(32.085, 34.782, radius_km=5.0, city_data_path=str(path))
        assert 'with_coords' in result
        assert 'no_coords' not in result


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


class TestLoadData:
    @pytest.mark.asyncio
    async def test_loads_from_hfc_districts(self, manager, mock_api_client):
        mock_api_client.get_districts.return_value = SAMPLE_HFC_DISTRICTS

        with patch.object(manager, '_fetch_polygons', new_callable=AsyncMock):
            result = await manager.load_data()

        assert result is True
        details = manager.get_city_details('כפר סבא')
        assert details is not None
        assert details['area'] == 'שרון'
        assert details['migun_time'] == 90

    @pytest.mark.asyncio
    async def test_falls_back_to_cached_file(self, manager, tmp_path, mock_api_client):
        mock_api_client.get_districts.return_value = None

        file_path = tmp_path / 'city_data.json'
        file_path.write_text(json.dumps(SAMPLE_CITY_DATA), encoding='utf-8-sig')

        result = await manager.load_data()
        assert result is True

        details = manager.get_city_details('תל אביב - מרכז העיר')
        assert details is not None
        assert details['area'] == 'דן'
        assert details['lat'] == 32.0853
        assert details['migun_time'] == 90

    @pytest.mark.asyncio
    async def test_hfc_exception_falls_back_to_cache(self, manager, tmp_path, mock_api_client):
        mock_api_client.get_districts.side_effect = Exception('Network error')

        file_path = tmp_path / 'city_data.json'
        file_path.write_text(json.dumps(SAMPLE_CITY_DATA), encoding='utf-8-sig')

        result = await manager.load_data()
        assert result is True

    @pytest.mark.asyncio
    async def test_fails_when_all_sources_fail(self, manager, mock_api_client, mock_logger):
        mock_api_client.get_districts.return_value = None

        result = await manager.load_data()
        assert result is False

        log_msgs = [call.args[0] for call in mock_logger.call_args_list]
        assert any('CRITICAL' in msg for msg in log_msgs)

    @pytest.mark.asyncio
    async def test_saves_cache_after_hfc_load(self, manager, tmp_path, mock_api_client):
        mock_api_client.get_districts.return_value = SAMPLE_HFC_DISTRICTS

        with patch.object(manager, '_fetch_polygons', new_callable=AsyncMock):
            await manager.load_data()

        cache_path = tmp_path / 'city_data.json'
        assert cache_path.exists()
        cached = json.loads(cache_path.read_text(encoding='utf-8'))
        assert 'areas' in cached
        assert 'שרון' in cached['areas']

    @pytest.mark.asyncio
    async def test_force_download_skips_cache(self, manager, tmp_path, mock_api_client):
        file_path = tmp_path / 'city_data.json'
        file_path.write_text(json.dumps(SAMPLE_CITY_DATA), encoding='utf-8-sig')

        mock_api_client.get_districts.return_value = None

        result = await manager.load_data(force_download=True)
        assert result is False


class TestHfcDistricts:
    @pytest.mark.asyncio
    async def test_hfc_districts_without_polygons(self, manager, mock_api_client):
        mock_api_client.get_districts.return_value = SAMPLE_HFC_DISTRICTS

        with patch.object(manager, '_fetch_polygons', new_callable=AsyncMock):
            result = await manager.load_data()

        assert result is True
        details = manager.get_city_details('מטולה')
        assert details is not None
        assert details['area'] == 'גליל עליון'
        assert details['migun_time'] == 0
        assert 'lat' not in details

    @pytest.mark.asyncio
    async def test_hfc_districts_skips_invalid_entries(self, manager, mock_api_client):
        districts = [
            {'label_he': 'כפר סבא', 'areaname': 'שרון', 'migun_time': 90},
            {'label_he': '', 'areaname': 'שרון'},
            {'label_he': 'נתניה', 'areaname': ''},
            'not a dict',
            {'label_he': 'רעננה', 'areaname': 'שרון', 'migun_time': 60},
        ]
        mock_api_client.get_districts.return_value = districts

        with patch.object(manager, '_fetch_polygons', new_callable=AsyncMock):
            result = await manager.load_data()

        assert result is True
        assert manager.get_city_details('כפר סבא') is not None
        assert manager.get_city_details('רעננה') is not None
        assert manager.get_city_details('נתניה') is None

    @pytest.mark.asyncio
    async def test_migun_time_zero_preserved(self, manager, mock_api_client):
        districts = [{'label_he': 'כפר גלעדי', 'areaname': 'גליל עליון', 'migun_time': 0}]
        mock_api_client.get_districts.return_value = districts

        with patch.object(manager, '_fetch_polygons', new_callable=AsyncMock):
            await manager.load_data()

        details = manager.get_city_details('כפר גלעדי')
        assert details is not None
        assert details['migun_time'] == 0

    @pytest.mark.asyncio
    async def test_hfc_empty_list_falls_back_to_cache(self, manager, tmp_path, mock_api_client):
        mock_api_client.get_districts.return_value = []

        file_path = tmp_path / 'city_data.json'
        file_path.write_text(json.dumps(SAMPLE_CITY_DATA), encoding='utf-8-sig')

        result = await manager.load_data()
        assert result is True


class TestGetCityDetails:
    @pytest.mark.asyncio
    async def test_returns_details_for_known_city(self, manager, tmp_path):
        file_path = tmp_path / 'city_data.json'
        file_path.write_text(json.dumps(SAMPLE_CITY_DATA), encoding='utf-8-sig')
        await manager.load_data()

        details = manager.get_city_details('תל אביב - מרכז העיר')
        assert details is not None
        assert details['area'] == 'דן'
        assert details['lat'] == 32.0853
        assert details['long'] == 34.7818

    @pytest.mark.asyncio
    async def test_returns_none_for_unknown_city(self, manager, tmp_path):
        file_path = tmp_path / 'city_data.json'
        file_path.write_text(json.dumps(SAMPLE_CITY_DATA), encoding='utf-8-sig')
        await manager.load_data()

        assert manager.get_city_details('עיר לא קיימת') is None

    def test_returns_none_for_empty_string(self, manager):
        assert manager.get_city_details('') is None

    def test_returns_none_for_none(self, manager):
        assert manager.get_city_details(None) is None


class TestProcessCachedData:
    @pytest.mark.asyncio
    async def test_handles_missing_areas_key(self, manager, tmp_path, mock_api_client):
        mock_api_client.get_districts.return_value = None

        file_path = tmp_path / 'city_data.json'
        file_path.write_text(json.dumps({'no_areas': {}}), encoding='utf-8')

        result = await manager.load_data()
        assert result is False

    @pytest.mark.asyncio
    async def test_preserves_all_fields_from_cache(self, manager, tmp_path, mock_api_client):
        mock_api_client.get_districts.return_value = None

        file_path = tmp_path / 'city_data.json'
        file_path.write_text(json.dumps(SAMPLE_CITY_DATA), encoding='utf-8-sig')

        await manager.load_data()
        details = manager.get_city_details('תל אביב - מרכז העיר')
        assert details['lat'] == 32.0853
        assert details['long'] == 34.7818
        assert details['migun_time'] == 90
        assert details['original_name'] == 'תל אביב - מרכז העיר'
