from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from red_alert.core.constants import DEFAULT_UNKNOWN_AREA
from red_alert.core.history import HistoryManager


@pytest.fixture
def mock_city_data():
    mock = MagicMock()
    mock.get_city_details.side_effect = lambda std: {
        'תל אביב - מרכז העיר': {'area': 'גוש דן', 'original_name': 'תל אביב - מרכז העיר'},
        'רמת גן - מזרח': {'area': 'גוש דן', 'original_name': 'רמת גן - מזרח'},
        'אשקלון - דרום': {'area': 'מערב לכיש', 'original_name': 'אשקלון - דרום'},
    }.get(std)
    return mock


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def history_manager(mock_city_data, mock_logger):
    return HistoryManager(
        hours_to_show=4,
        city_data_manager=mock_city_data,
        logger=mock_logger,
        timer_duration_seconds=120,
        language='en',
    )


class TestInit:
    def test_default_values(self, history_manager):
        assert history_manager._hours_to_show == 4
        assert history_manager._timer_duration_seconds == 120
        assert history_manager._history_list == []

    def test_invalid_timer_uses_default(self, mock_city_data, mock_logger):
        hm = HistoryManager(4, mock_city_data, mock_logger, -1)
        assert hm._timer_duration_seconds == 120

    def test_invalid_hours_uses_default(self, mock_city_data, mock_logger):
        hm = HistoryManager(0, mock_city_data, mock_logger, 120)
        assert hm._hours_to_show == 4


class TestClearPollTracker:
    def test_clears_tracker(self, history_manager):
        history_manager._added_in_current_poll.add(('test', 'city', 'area'))
        history_manager.clear_poll_tracker()
        assert len(history_manager._added_in_current_poll) == 0


class TestLoadInitialHistory:
    @pytest.mark.asyncio
    async def test_loads_valid_history(self, history_manager):
        now = datetime.now()
        mock_api = AsyncMock()
        mock_api.get_alert_history.return_value = [
            {
                'alertDate': (now - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S'),
                'title': 'ירי רקטות וטילים',
                'data': 'תל אביב - מרכז העיר',
            },
            {
                'alertDate': (now - timedelta(hours=2)).strftime('%Y-%m-%d %H:%M:%S'),
                'title': 'ירי רקטות וטילים',
                'data': 'רמת גן - מזרח',
            },
        ]

        await history_manager.load_initial_history(mock_api)
        assert len(history_manager._history_list) == 2

    @pytest.mark.asyncio
    async def test_filters_old_entries(self, history_manager):
        now = datetime.now()
        mock_api = AsyncMock()
        mock_api.get_alert_history.return_value = [
            {
                'alertDate': (now - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S'),
                'title': 'Recent',
                'data': 'תל אביב - מרכז העיר',
            },
            {
                'alertDate': (now - timedelta(hours=10)).strftime('%Y-%m-%d %H:%M:%S'),
                'title': 'Old',
                'data': 'רמת גן - מזרח',
            },
        ]

        await history_manager.load_initial_history(mock_api)
        assert len(history_manager._history_list) == 1
        assert history_manager._history_list[0]['title'] == 'Recent'

    @pytest.mark.asyncio
    async def test_handles_empty_history(self, history_manager):
        mock_api = AsyncMock()
        mock_api.get_alert_history.return_value = []

        await history_manager.load_initial_history(mock_api)
        assert len(history_manager._history_list) == 0

    @pytest.mark.asyncio
    async def test_handles_none_response(self, history_manager, mock_logger):
        mock_api = AsyncMock()
        mock_api.get_alert_history.return_value = None

        await history_manager.load_initial_history(mock_api)
        assert len(history_manager._history_list) == 0
        mock_logger.assert_called()


class TestUpdateHistory:
    def test_adds_new_cities(self, history_manager):
        history_manager.update_history('ירי רקטות וטילים', {'תל אביב - מרכז העיר', 'רמת גן - מזרח'})

        assert len(history_manager._history_list) == 2
        cities = {a['city'] for a in history_manager._history_list}
        assert 'תל אביב - מרכז העיר' in cities
        assert 'רמת גן - מזרח' in cities

    def test_does_not_duplicate_within_same_poll(self, history_manager):
        history_manager.update_history('ירי רקטות וטילים', {'תל אביב - מרכז העיר'})
        history_manager.update_history('ירי רקטות וטילים', {'תל אביב - מרכז העיר'})

        assert len(history_manager._history_list) == 1

    def test_allows_same_city_after_poll_clear(self, history_manager):
        history_manager.update_history('ירי רקטות וטילים', {'תל אביב - מרכז העיר'})
        history_manager.clear_poll_tracker()
        history_manager.update_history('ירי רקטות וטילים', {'תל אביב - מרכז העיר'})

        assert len(history_manager._history_list) == 2

    def test_empty_cities_does_nothing(self, history_manager):
        history_manager.update_history('ירי רקטות וטילים', set())
        assert len(history_manager._history_list) == 0

    def test_unknown_city_gets_default_area(self, history_manager):
        history_manager.update_history('ירי רקטות וטילים', {'עיר לא ידועה'})

        assert len(history_manager._history_list) == 1
        assert history_manager._history_list[0]['area'] == DEFAULT_UNKNOWN_AREA


class TestRestructureAlerts:
    def test_groups_by_title_and_area(self, history_manager):
        alerts = [
            {'title': 'ירי רקטות', 'city': 'City A', 'area': 'Area 1', 'time': '2024-01-15 10:30:45'},
            {'title': 'ירי רקטות', 'city': 'City B', 'area': 'Area 1', 'time': '2024-01-15 10:31:00'},
            {'title': 'ירי רקטות', 'city': 'City C', 'area': 'Area 2', 'time': '2024-01-15 10:32:00'},
        ]

        result = history_manager.restructure_alerts(alerts)
        assert 'ירי רקטות' in result
        assert 'Area 1' in result['ירי רקטות']
        assert len(result['ירי רקטות']['Area 1']) == 2
        assert 'Area 2' in result['ירי רקטות']

    def test_empty_list(self, history_manager):
        result = history_manager.restructure_alerts([])
        assert result == {}

    def test_skips_non_dict_entries(self, history_manager):
        result = history_manager.restructure_alerts(['not a dict', 42])
        assert result == {}


class TestGetHistoryAttributes:
    def test_returns_expected_keys(self, history_manager):
        history_manager.update_history('ירי רקטות', {'תל אביב - מרכז העיר'})

        attrs = history_manager.get_history_attributes()
        assert 'cities_past_24h' in attrs
        assert 'last_24h_alerts' in attrs
        assert 'last_24h_alerts_group' in attrs

    def test_prunes_old_entries(self, history_manager):
        old_time = datetime.now() - timedelta(hours=10)
        history_manager._history_list = [
            {'title': 'Old', 'city': 'City A', 'area': 'Area 1', 'time': old_time},
        ]

        attrs = history_manager.get_history_attributes()
        assert len(attrs['last_24h_alerts']) == 0

    def test_merges_alerts_within_window(self, history_manager):
        now = datetime.now()
        history_manager._history_list = [
            {'title': 'Alert 1', 'city': 'City A', 'area': 'Area 1', 'time': now},
            {'title': 'Alert 2', 'city': 'City A', 'area': 'Area 1', 'time': now - timedelta(minutes=10)},
        ]

        attrs = history_manager.get_history_attributes()
        assert len(attrs['last_24h_alerts']) == 1
        assert '&' in attrs['last_24h_alerts'][0]['title']

    def test_does_not_merge_across_window(self, history_manager):
        now = datetime.now()
        history_manager._history_list = [
            {'title': 'Alert 1', 'city': 'City A', 'area': 'Area 1', 'time': now},
            {'title': 'Alert 1', 'city': 'City A', 'area': 'Area 1', 'time': now - timedelta(hours=2)},
        ]

        attrs = history_manager.get_history_attributes()
        assert len(attrs['last_24h_alerts']) == 2


class TestLoadInitialHistoryExtendedFormat:
    """Tests for loading history from the extended endpoint (GetAlarmsHistory.aspx)
    which uses 'category_desc' instead of 'title' and ISO date format."""

    @pytest.mark.asyncio
    async def test_uses_category_desc_over_title(self, history_manager):
        now = datetime.now()
        mock_api = AsyncMock()
        mock_api.get_alert_history.return_value = [
            {
                'alertDate': (now - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%S'),
                'category_desc': 'ירי רקטות וטילים',
                'data': 'תל אביב - מרכז העיר',
                'category': 1,
                'matrix_id': 1,
            },
        ]

        await history_manager.load_initial_history(mock_api)
        assert len(history_manager._history_list) == 1
        assert history_manager._history_list[0]['title'] == 'ירי רקטות וטילים'

    @pytest.mark.asyncio
    async def test_falls_back_to_title_when_no_category_desc(self, history_manager):
        now = datetime.now()
        mock_api = AsyncMock()
        mock_api.get_alert_history.return_value = [
            {
                'alertDate': (now - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S'),
                'title': 'ירי רקטות וטילים',
                'data': 'רמת גן - מזרח',
            },
        ]

        await history_manager.load_initial_history(mock_api)
        assert len(history_manager._history_list) == 1
        assert history_manager._history_list[0]['title'] == 'ירי רקטות וטילים'

    @pytest.mark.asyncio
    async def test_handles_iso_date_format(self, history_manager):
        now = datetime.now()
        mock_api = AsyncMock()
        mock_api.get_alert_history.return_value = [
            {
                'alertDate': (now - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%S'),
                'category_desc': 'Test alert',
                'data': 'תל אביב - מרכז העיר',
            },
        ]

        await history_manager.load_initial_history(mock_api)
        assert len(history_manager._history_list) == 1

    @pytest.mark.asyncio
    async def test_pre_alert_category_desc(self, history_manager):
        now = datetime.now()
        mock_api = AsyncMock()
        mock_api.get_alert_history.return_value = [
            {
                'alertDate': (now - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%S'),
                'category_desc': 'בדקות הקרובות צפויות להתקבל התרעות באזורך',
                'data': 'תל אביב - מרכז העיר',
                'category': 14,
                'matrix_id': 10,
            },
        ]

        await history_manager.load_initial_history(mock_api)
        assert len(history_manager._history_list) == 1
        assert history_manager._history_list[0]['title'] == 'בדקות הקרובות צפויות להתקבל התרעות באזורך'

    @pytest.mark.asyncio
    async def test_all_clear_category_desc(self, history_manager):
        now = datetime.now()
        mock_api = AsyncMock()
        mock_api.get_alert_history.return_value = [
            {
                'alertDate': (now - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%S'),
                'category_desc': 'האירוע הסתיים',
                'data': 'אשקלון - דרום',
                'category': 13,
                'matrix_id': 10,
            },
        ]

        await history_manager.load_initial_history(mock_api)
        assert len(history_manager._history_list) == 1
        assert history_manager._history_list[0]['title'] == 'האירוע הסתיים'
