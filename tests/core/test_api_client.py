import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from red_alert.core.api_client import HomeFrontCommandApiClient


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def api_urls():
    return {
        'live': 'https://www.oref.org.il/WarningMessages/alert/alerts.json',
        'history': 'https://www.oref.org.il/WarningMessages/alert/History/AlertsHistory.json',
    }


def _make_mock_session(response_mock):
    """Create a mock aiohttp session with proper async context manager support."""
    session = MagicMock()
    ctx_manager = AsyncMock()
    ctx_manager.__aenter__.return_value = response_mock
    ctx_manager.__aexit__.return_value = False
    session.get.return_value = ctx_manager
    return session


def _make_response(data: bytes, content_type='application/json'):
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.headers = {'Content-Type': content_type}
    response.read = AsyncMock(return_value=data)
    return response


class TestGetLiveAlerts:
    @pytest.mark.asyncio
    async def test_returns_parsed_json_on_success(self, api_urls, mock_logger):
        alert_data = {'id': '123', 'cat': '1', 'title': 'Rocket fire', 'data': ['City A'], 'desc': 'Take cover'}
        resp = _make_response(json.dumps(alert_data).encode('utf-8'))
        session = _make_mock_session(resp)
        client = HomeFrontCommandApiClient(session, api_urls, mock_logger)

        result = await client.get_live_alerts()
        assert result == alert_data

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_response(self, api_urls, mock_logger):
        resp = _make_response(b'')
        session = _make_mock_session(resp)
        client = HomeFrontCommandApiClient(session, api_urls, mock_logger)

        result = await client.get_live_alerts()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_invalid_json(self, api_urls, mock_logger):
        resp = _make_response(b'not json')
        session = _make_mock_session(resp)
        client = HomeFrontCommandApiClient(session, api_urls, mock_logger)

        result = await client.get_live_alerts()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_url_not_configured(self, mock_logger):
        session = MagicMock()
        client = HomeFrontCommandApiClient(session, {}, mock_logger)
        result = await client.get_live_alerts()
        assert result is None
        mock_logger.assert_called()

    @pytest.mark.asyncio
    async def test_handles_bom_in_response(self, api_urls, mock_logger):
        alert_data = {'id': '456', 'title': 'Test'}
        bom_json = b'\xef\xbb\xbf' + json.dumps(alert_data).encode('utf-8')
        resp = _make_response(bom_json)
        session = _make_mock_session(resp)
        client = HomeFrontCommandApiClient(session, api_urls, mock_logger)

        result = await client.get_live_alerts()
        assert result == alert_data

    @pytest.mark.asyncio
    async def test_returns_none_on_whitespace_response(self, api_urls, mock_logger):
        resp = _make_response(b'   \n  ')
        session = _make_mock_session(resp)
        client = HomeFrontCommandApiClient(session, api_urls, mock_logger)

        result = await client.get_live_alerts()
        assert result is None


class TestGetAlertHistory:
    @pytest.mark.asyncio
    async def test_returns_list_on_success(self, api_urls, mock_logger):
        history_data = [{'alertDate': '2024-01-15', 'title': 'Rockets', 'data': 'City A'}]
        resp = _make_response(json.dumps(history_data).encode('utf-8'))
        session = _make_mock_session(resp)
        client = HomeFrontCommandApiClient(session, api_urls, mock_logger)

        result = await client.get_alert_history()
        assert result == history_data

    @pytest.mark.asyncio
    async def test_returns_none_if_not_list(self, api_urls, mock_logger):
        resp = _make_response(json.dumps({'not': 'a list'}).encode('utf-8'))
        session = _make_mock_session(resp)
        client = HomeFrontCommandApiClient(session, api_urls, mock_logger)

        result = await client.get_alert_history()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_empty(self, api_urls, mock_logger):
        resp = _make_response(b'')
        session = _make_mock_session(resp)
        client = HomeFrontCommandApiClient(session, api_urls, mock_logger)

        result = await client.get_alert_history()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_url_not_configured(self, mock_logger):
        session = MagicMock()
        client = HomeFrontCommandApiClient(session, {}, mock_logger)
        result = await client.get_alert_history()
        assert result is None


class TestDownloadFile:
    @pytest.mark.asyncio
    async def test_returns_text_on_success(self, api_urls, mock_logger):
        content = '{"areas": {"test": {}}}'
        resp = _make_response(content.encode('utf-8'))
        session = _make_mock_session(resp)
        client = HomeFrontCommandApiClient(session, api_urls, mock_logger)

        result = await client.download_file('https://example.com/data.json')
        assert result == content

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self, api_urls, mock_logger):
        import aiohttp

        session = MagicMock()
        ctx_manager = AsyncMock()
        ctx_manager.__aenter__.side_effect = aiohttp.ClientResponseError(request_info=MagicMock(), history=(), status=404, message='Not Found')
        ctx_manager.__aexit__.return_value = False
        session.get.return_value = ctx_manager
        client = HomeFrontCommandApiClient(session, api_urls, mock_logger)

        result = await client.download_file('https://example.com/notfound')
        assert result is None
