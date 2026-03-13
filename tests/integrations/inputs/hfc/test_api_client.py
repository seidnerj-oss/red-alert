import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from red_alert.integrations.inputs.hfc.api_client import HomeFrontCommandApiClient


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def api_urls():
    return {
        'live': 'https://www.oref.org.il/WarningMessages/alert/alerts.json',
        'history': 'https://www.oref.org.il/WarningMessages/alert/History/AlertsHistory.json',
    }


def _make_mock_client(response: httpx.Response):
    """Create a mock httpx.AsyncClient that returns the given response."""
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    return client


def _make_response(data: bytes, status_code=200, content_type='application/json'):
    return httpx.Response(
        status_code=status_code,
        content=data,
        headers={'Content-Type': content_type},
        request=httpx.Request('GET', 'https://example.com'),
    )


class TestGetLiveAlerts:
    @pytest.mark.asyncio
    async def test_returns_parsed_json_on_success(self, api_urls, mock_logger):
        alert_data = {'id': '123', 'cat': '1', 'title': 'Rocket fire', 'data': ['City A'], 'desc': 'Take cover'}
        resp = _make_response(json.dumps(alert_data).encode('utf-8'))
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.get_live_alerts()
        assert result == alert_data

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_response(self, api_urls, mock_logger):
        resp = _make_response(b'')
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.get_live_alerts()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_invalid_json(self, api_urls, mock_logger):
        resp = _make_response(b'not json')
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.get_live_alerts()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_url_not_configured(self, mock_logger):
        client = AsyncMock()
        api = HomeFrontCommandApiClient(client, {}, mock_logger)
        result = await api.get_live_alerts()
        assert result is None
        mock_logger.assert_called()

    @pytest.mark.asyncio
    async def test_handles_bom_in_response(self, api_urls, mock_logger):
        alert_data = {'id': '456', 'title': 'Test'}
        bom_json = b'\xef\xbb\xbf' + json.dumps(alert_data).encode('utf-8')
        resp = _make_response(bom_json)
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.get_live_alerts()
        assert result == alert_data

    @pytest.mark.asyncio
    async def test_returns_none_on_whitespace_response(self, api_urls, mock_logger):
        resp = _make_response(b'   \n  ')
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.get_live_alerts()
        assert result is None


class TestGetAlertHistory:
    @pytest.mark.asyncio
    async def test_returns_list_on_success(self, api_urls, mock_logger):
        history_data = [{'alertDate': '2024-01-15', 'title': 'Rockets', 'data': 'City A'}]
        resp = _make_response(json.dumps(history_data).encode('utf-8'))
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.get_alert_history()
        assert result == history_data

    @pytest.mark.asyncio
    async def test_returns_none_if_not_list(self, api_urls, mock_logger):
        resp = _make_response(json.dumps({'not': 'a list'}).encode('utf-8'))
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.get_alert_history()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_empty(self, api_urls, mock_logger):
        resp = _make_response(b'')
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.get_alert_history()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_url_not_configured(self, mock_logger):
        client = AsyncMock()
        api = HomeFrontCommandApiClient(client, {}, mock_logger)
        result = await api.get_alert_history()
        assert result is None


class TestAlertReceivedLogging:
    @pytest.mark.asyncio
    async def test_logs_alert_summary_on_dict_response(self, api_urls, mock_logger):
        alert_data = {'cat': '1', 'title': 'Rocket fire', 'data': ['City A', 'City B']}
        resp = _make_response(json.dumps(alert_data).encode('utf-8'))
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        await api.get_live_alerts()
        msgs = [call.args[0] for call in mock_logger.call_args_list]
        assert any('Alert received' in m and 'cat=1' in m and '2 cities' in m for m in msgs)

    @pytest.mark.asyncio
    async def test_no_alert_log_on_empty_response(self, api_urls, mock_logger):
        resp = _make_response(b'')
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        await api.get_live_alerts()
        msgs = [call.args[0] for call in mock_logger.call_args_list] if mock_logger.call_args_list else []
        assert not any('Alert received' in m for m in msgs)

    @pytest.mark.asyncio
    async def test_no_alert_log_on_list_response(self, api_urls, mock_logger):
        resp = _make_response(json.dumps([1, 2, 3]).encode('utf-8'))
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        await api.get_live_alerts()
        msgs = [call.args[0] for call in mock_logger.call_args_list] if mock_logger.call_args_list else []
        assert not any('Alert received' in m for m in msgs)


class TestDownloadFile:
    @pytest.mark.asyncio
    async def test_returns_text_on_success(self, api_urls, mock_logger):
        content = '{"areas": {"test": {}}}'
        resp = _make_response(content.encode('utf-8'))
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.download_file('https://example.com/data.json')
        assert result == content

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self, api_urls, mock_logger):
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                'Not Found',
                request=httpx.Request('GET', 'https://example.com/notfound'),
                response=httpx.Response(404),
            )
        )
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.download_file('https://example.com/notfound')
        assert result is None
