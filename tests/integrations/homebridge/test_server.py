from unittest.mock import AsyncMock

import pytest

from red_alert.integrations.homebridge.server import AlertMonitor


@pytest.fixture
def mock_api_client():
    return AsyncMock()


@pytest.fixture
def monitor(mock_api_client):
    return AlertMonitor(mock_api_client)


@pytest.fixture
def monitor_with_cities(mock_api_client):
    return AlertMonitor(mock_api_client, city_names=['תל אביב - מרכז העיר', 'חיפה'])


SAMPLE_ALERT = {
    'id': '133456',
    'cat': '1',
    'title': 'ירי רקטות וטילים',
    'data': ['תל אביב - מרכז העיר', 'רמת גן - מזרח'],
    'desc': 'היכנסו למרחב המוגן',
}


class TestAlertMonitor:
    @pytest.mark.asyncio
    async def test_initial_state(self, monitor):
        assert monitor.active is False
        assert monitor.city_active is False
        assert monitor.alert_data is None

    @pytest.mark.asyncio
    async def test_poll_with_active_alert(self, monitor, mock_api_client):
        mock_api_client.get_live_alerts.return_value = SAMPLE_ALERT

        await monitor.poll()

        assert monitor.active is True
        assert monitor.city_active is True  # no city filter = follows active
        assert monitor.alert_data['id'] == '133456'
        assert monitor.alert_data['cities'] == SAMPLE_ALERT['data']
        assert monitor.last_update is not None

    @pytest.mark.asyncio
    async def test_poll_with_no_alert(self, monitor, mock_api_client):
        mock_api_client.get_live_alerts.return_value = None

        await monitor.poll()

        assert monitor.active is False
        assert monitor.city_active is False
        assert monitor.alert_data is None

    @pytest.mark.asyncio
    async def test_poll_with_empty_data(self, monitor, mock_api_client):
        mock_api_client.get_live_alerts.return_value = {'id': '1', 'cat': '1', 'title': 'Test', 'data': [], 'desc': ''}

        await monitor.poll()

        assert monitor.active is False

    @pytest.mark.asyncio
    async def test_poll_city_filter_match(self, monitor_with_cities, mock_api_client):
        mock_api_client.get_live_alerts.return_value = SAMPLE_ALERT

        await monitor_with_cities.poll()

        assert monitor_with_cities.active is True
        assert monitor_with_cities.city_active is True  # tel aviv is in the alert

    @pytest.mark.asyncio
    async def test_poll_city_filter_no_match(self, monitor_with_cities, mock_api_client):
        alert = {**SAMPLE_ALERT, 'data': ['באר שבע - מזרח']}
        mock_api_client.get_live_alerts.return_value = alert

        await monitor_with_cities.poll()

        assert monitor_with_cities.active is True
        assert monitor_with_cities.city_active is False

    @pytest.mark.asyncio
    async def test_poll_clears_previous_alert(self, monitor, mock_api_client):
        mock_api_client.get_live_alerts.return_value = SAMPLE_ALERT
        await monitor.poll()
        assert monitor.active is True

        mock_api_client.get_live_alerts.return_value = None
        await monitor.poll()
        assert monitor.active is False
        assert monitor.alert_data is None

    @pytest.mark.asyncio
    async def test_status_property(self, monitor, mock_api_client):
        mock_api_client.get_live_alerts.return_value = SAMPLE_ALERT
        await monitor.poll()

        status = monitor.status
        assert status['active'] is True
        assert status['city_active'] is True
        assert status['alert']['id'] == '133456'
        assert status['last_update'] is not None


class TestHTTPEndpoints:
    @pytest.fixture
    def app(self):
        """Create a minimal app with a mock monitor (no real session or polling)."""
        from aiohttp import web
        from red_alert.integrations.homebridge.server import handle_contact, handle_city_contact, handle_health, handle_status

        mock_client = AsyncMock()
        monitor = AlertMonitor(mock_client)

        app = web.Application()
        app['monitor'] = monitor
        app.router.add_get('/status', handle_status)
        app.router.add_get('/contact', handle_contact)
        app.router.add_get('/city', handle_city_contact)
        app.router.add_get('/health', handle_health)
        return app

    @pytest.fixture
    async def client(self, app, aiohttp_client):
        return await aiohttp_client(app)

    @pytest.mark.asyncio
    async def test_health(self, client):
        resp = await client.get('/health')
        assert resp.status == 200
        data = await resp.json()
        assert data['status'] == 'ok'

    @pytest.mark.asyncio
    async def test_contact_no_alert(self, client):
        resp = await client.get('/contact')
        assert resp.status == 200
        text = await resp.text()
        assert text == '0'

    @pytest.mark.asyncio
    async def test_contact_with_alert(self, client):
        client.app['monitor'].active = True
        resp = await client.get('/contact')
        text = await resp.text()
        assert text == '1'

    @pytest.mark.asyncio
    async def test_city_no_alert(self, client):
        resp = await client.get('/city')
        text = await resp.text()
        assert text == '0'

    @pytest.mark.asyncio
    async def test_city_with_alert(self, client):
        client.app['monitor'].city_active = True
        resp = await client.get('/city')
        text = await resp.text()
        assert text == '1'

    @pytest.mark.asyncio
    async def test_status_json(self, client):
        client.app['monitor'].active = True
        client.app['monitor'].alert_data = {'id': '123', 'cat': '1', 'title': 'Test', 'cities': ['A'], 'desc': 'D'}
        client.app['monitor'].last_update = '2024-01-01T00:00:00+00:00'
        client.app['monitor'].city_active = False

        resp = await client.get('/status')
        assert resp.status == 200
        data = await resp.json()
        assert data['active'] is True
        assert data['city_active'] is False
        assert data['alert']['id'] == '123'
        assert data['last_update'] == '2024-01-01T00:00:00+00:00'

    @pytest.mark.asyncio
    async def test_status_no_alert(self, client):
        resp = await client.get('/status')
        data = await resp.json()
        assert data['active'] is False
        assert data['alert'] is None
