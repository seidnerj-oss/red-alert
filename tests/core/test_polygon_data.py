"""Tests for polygon data manager."""

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from red_alert.core.polygon_data import PolygonDataManager, _point_in_polygon

SQUARE_POLYGON = [
    [32.0, 34.0],
    [32.0, 35.0],
    [33.0, 35.0],
    [33.0, 34.0],
    [32.0, 34.0],
]

TRIANGLE_POLYGON = [
    [30.0, 34.0],
    [30.0, 35.0],
    [31.0, 34.5],
    [30.0, 34.0],
]


class TestPointInPolygon:
    def test_point_inside_square(self):
        assert _point_in_polygon(32.5, 34.5, SQUARE_POLYGON) is True

    def test_point_outside_square(self):
        assert _point_in_polygon(31.0, 33.0, SQUARE_POLYGON) is False

    def test_point_inside_triangle(self):
        assert _point_in_polygon(30.3, 34.5, TRIANGLE_POLYGON) is True

    def test_point_outside_triangle(self):
        assert _point_in_polygon(31.5, 34.5, TRIANGLE_POLYGON) is False

    def test_point_far_outside(self):
        assert _point_in_polygon(0.0, 0.0, SQUARE_POLYGON) is False

    def test_degenerate_polygon_too_few_points(self):
        assert _point_in_polygon(32.5, 34.5, [[32.0, 34.0], [33.0, 35.0]]) is False

    def test_empty_polygon(self):
        assert _point_in_polygon(32.5, 34.5, []) is False

    def test_complex_polygon(self):
        l_shape = [
            [32.0, 34.0],
            [32.0, 34.5],
            [32.5, 34.5],
            [32.5, 34.25],
            [33.0, 34.25],
            [33.0, 34.0],
            [32.0, 34.0],
        ]
        assert _point_in_polygon(32.25, 34.25, l_shape) is True
        assert _point_in_polygon(32.75, 34.4, l_shape) is False


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def mock_client():
    return AsyncMock(spec=httpx.AsyncClient)


@pytest.fixture
def manager(tmp_path, mock_client, mock_logger):
    cache_path = str(tmp_path / 'polygons.json')
    return PolygonDataManager(mock_client, cache_path, mock_logger)


SAMPLE_SEGMENTS = [
    {'segmentId': 1, 'name': 'תל אביב - יפו'},
    {'segmentId': 2, 'name': 'רמת גן'},
]

SAMPLE_POLYGON_1 = {
    'segmentId': 1,
    'polygonPointList': [
        [
            [32.05, 34.75],
            [32.05, 34.80],
            [32.10, 34.80],
            [32.10, 34.75],
            [32.05, 34.75],
        ]
    ],
}

SAMPLE_POLYGON_2 = {
    'segmentId': 2,
    'polygonPointList': [
        [
            [32.07, 34.80],
            [32.07, 34.83],
            [32.10, 34.83],
            [32.10, 34.80],
            [32.07, 34.80],
        ]
    ],
}


def _make_response(data, status_code=200):
    return httpx.Response(status_code=status_code, json=data, request=httpx.Request('GET', 'https://example.com'))


class TestPolygonDataManager:
    def test_not_loaded_initially(self, manager):
        assert manager.is_loaded is False

    @pytest.mark.asyncio
    async def test_load_from_api(self, manager, mock_client):
        mock_client.get = AsyncMock(
            side_effect=lambda url, **kw: _make_response(
                SAMPLE_SEGMENTS if 'segments' in url else (SAMPLE_POLYGON_1 if 'id=1' in url else SAMPLE_POLYGON_2)
            )
        )

        result = await manager.load()
        assert result is True
        assert manager.is_loaded is True

    @pytest.mark.asyncio
    async def test_find_cities_at_point(self, manager, mock_client):
        mock_client.get = AsyncMock(
            side_effect=lambda url, **kw: _make_response(
                SAMPLE_SEGMENTS if 'segments' in url else (SAMPLE_POLYGON_1 if 'id=1' in url else SAMPLE_POLYGON_2)
            )
        )
        await manager.load()

        cities = manager.find_cities_at_point(32.07, 34.77)
        assert 'תל אביב - יפו' in cities
        assert 'רמת גן' not in cities

    @pytest.mark.asyncio
    async def test_find_cities_in_both_polygons(self, manager, mock_client):
        overlapping_polygon_1 = {
            'segmentId': 1,
            'polygonPointList': [
                [
                    [32.05, 34.75],
                    [32.05, 34.82],
                    [32.10, 34.82],
                    [32.10, 34.75],
                    [32.05, 34.75],
                ]
            ],
        }

        mock_client.get = AsyncMock(
            side_effect=lambda url, **kw: _make_response(
                SAMPLE_SEGMENTS if 'segments' in url else (overlapping_polygon_1 if 'id=1' in url else SAMPLE_POLYGON_2)
            )
        )
        await manager.load()

        cities = manager.find_cities_at_point(32.08, 34.81)
        assert 'תל אביב - יפו' in cities
        assert 'רמת גן' in cities

    @pytest.mark.asyncio
    async def test_find_cities_outside_all(self, manager, mock_client):
        mock_client.get = AsyncMock(
            side_effect=lambda url, **kw: _make_response(
                SAMPLE_SEGMENTS if 'segments' in url else (SAMPLE_POLYGON_1 if 'id=1' in url else SAMPLE_POLYGON_2)
            )
        )
        await manager.load()

        cities = manager.find_cities_at_point(31.0, 34.0)
        assert cities == []

    @pytest.mark.asyncio
    async def test_returns_sorted(self, manager, mock_client):
        mock_client.get = AsyncMock(
            side_effect=lambda url, **kw: _make_response(
                SAMPLE_SEGMENTS if 'segments' in url else (SAMPLE_POLYGON_1 if 'id=1' in url else SAMPLE_POLYGON_2)
            )
        )
        await manager.load()

        cities = manager.find_cities_at_point(32.08, 34.81)
        assert cities == sorted(cities)


class TestCacheOperations:
    @pytest.mark.asyncio
    async def test_cache_saved_on_successful_load(self, manager, mock_client, tmp_path):
        mock_client.get = AsyncMock(
            side_effect=lambda url, **kw: _make_response(
                SAMPLE_SEGMENTS if 'segments' in url else (SAMPLE_POLYGON_1 if 'id=1' in url else SAMPLE_POLYGON_2)
            )
        )
        await manager.load()

        cache_file = tmp_path / 'polygons.json'
        assert cache_file.exists()
        data = json.loads(cache_file.read_text(encoding='utf-8'))
        assert 'תל אביב - יפו' in data
        assert 'רמת גן' in data

    @pytest.mark.asyncio
    async def test_loads_from_cache_on_api_failure(self, manager, mock_client, tmp_path):
        cache_data = {
            'חיפה': [
                [
                    [32.80, 34.95],
                    [32.80, 35.00],
                    [32.85, 35.00],
                    [32.85, 34.95],
                    [32.80, 34.95],
                ]
            ]
        }
        cache_file = tmp_path / 'polygons.json'
        cache_file.write_text(json.dumps(cache_data, ensure_ascii=False), encoding='utf-8')

        mock_client.get = AsyncMock(side_effect=httpx.TransportError('Connection refused'))

        result = await manager.load()
        assert result is True
        assert manager.is_loaded is True
        assert manager.find_cities_at_point(32.82, 34.97) == ['חיפה']

    @pytest.mark.asyncio
    async def test_fails_when_both_api_and_cache_unavailable(self, manager, mock_client):
        mock_client.get = AsyncMock(side_effect=httpx.TransportError('Connection refused'))

        result = await manager.load()
        assert result is False
        assert manager.is_loaded is False


class TestRefresh:
    @pytest.mark.asyncio
    async def test_refresh_updates_data(self, manager, mock_client, tmp_path):
        mock_client.get = AsyncMock(
            side_effect=lambda url, **kw: _make_response(
                SAMPLE_SEGMENTS if 'segments' in url else (SAMPLE_POLYGON_1 if 'id=1' in url else SAMPLE_POLYGON_2)
            )
        )

        result = await manager.refresh()
        assert result is True
        assert manager.is_loaded is True

    @pytest.mark.asyncio
    async def test_refresh_failure_keeps_existing_data(self, manager, mock_client):
        manager._polygons = {'existing': [SQUARE_POLYGON]}

        mock_client.get = AsyncMock(side_effect=httpx.TransportError('Connection refused'))

        result = await manager.refresh()
        assert result is False
        assert manager.is_loaded is True
        assert 'existing' in manager._polygons


class TestApiEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_segments_list(self, manager, mock_client):
        mock_client.get = AsyncMock(side_effect=lambda url, **kw: _make_response([]))

        result = await manager.load()
        assert result is False

    @pytest.mark.asyncio
    async def test_segments_without_id(self, manager, mock_client):
        segments = [{'name': 'no id'}]
        mock_client.get = AsyncMock(side_effect=lambda url, **kw: _make_response(segments if 'segments' in url else {}))

        result = await manager.load()
        assert result is False

    @pytest.mark.asyncio
    async def test_polygon_fetch_failure_skipped(self, manager, mock_client):
        call_count = 0

        def side_effect(url, **kw):
            nonlocal call_count
            if 'segments' in url:
                return _make_response(SAMPLE_SEGMENTS)
            call_count += 1
            if call_count == 1:
                raise httpx.TransportError('timeout')
            return _make_response(SAMPLE_POLYGON_2)

        mock_client.get = AsyncMock(side_effect=side_effect)

        result = await manager.load()
        assert result is True
        assert 'רמת גן' in manager._polygons
        assert 'תל אביב - יפו' not in manager._polygons

    @pytest.mark.asyncio
    async def test_segments_with_alternate_id_field(self, manager, mock_client):
        segments = [{'id': 99, 'name': 'alt id city'}]
        polygon = {'polygonPointList': [SQUARE_POLYGON]}

        mock_client.get = AsyncMock(side_effect=lambda url, **kw: _make_response(segments if 'segments' in url else polygon))

        result = await manager.load()
        assert result is True
        assert 'alt id city' in manager._polygons

    @pytest.mark.asyncio
    async def test_empty_polygon_point_list(self, manager, mock_client):
        segments = [{'segmentId': 1, 'name': 'empty polygon'}]
        polygon = {'polygonPointList': []}

        mock_client.get = AsyncMock(side_effect=lambda url, **kw: _make_response(segments if 'segments' in url else polygon))

        result = await manager.load()
        assert result is False

    @pytest.mark.asyncio
    async def test_http_error_on_segments(self, manager, mock_client):
        mock_client.get = AsyncMock(return_value=httpx.Response(status_code=403, request=httpx.Request('GET', 'https://example.com')))

        result = await manager.load()
        assert result is False
