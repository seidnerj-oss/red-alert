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
        mock_logger.error.assert_called()

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
        msgs = [call.args[0] for call in mock_logger.info.call_args_list]
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


class TestGetExtendedAlertHistory:
    @pytest.mark.asyncio
    async def test_returns_list_on_success(self, api_urls, mock_logger):
        history_data = [
            {
                'data': 'כפר סבא',
                'date': '14.03.2026',
                'time': '02:30:30',
                'alertDate': '2026-03-14T02:30:00',
                'category': 14,
                'category_desc': 'בדקות הקרובות צפויות להתקבל התרעות באזורך',
                'matrix_id': 10,
                'rid': 479300,
            }
        ]
        resp = _make_response(json.dumps(history_data).encode('utf-8'))
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.get_extended_alert_history()
        assert result == history_data
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_response(self, api_urls, mock_logger):
        resp = _make_response(b'\r\n')
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.get_extended_alert_history()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_non_list(self, api_urls, mock_logger):
        resp = _make_response(json.dumps({'not': 'a list'}).encode('utf-8'))
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.get_extended_alert_history()
        assert result is None

    @pytest.mark.asyncio
    async def test_sends_referer_header(self, api_urls, mock_logger):
        resp = _make_response(json.dumps([]).encode('utf-8'))
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        await api.get_extended_alert_history()
        call_kwargs = client.get.call_args
        assert call_kwargs.kwargs.get('headers', {}).get('Referer') == 'https://alerts-history.oref.org.il/'

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self, api_urls, mock_logger):
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                'Server Error',
                request=httpx.Request('GET', 'https://example.com'),
                response=httpx.Response(500),
            )
        )
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.get_extended_alert_history()
        assert result is None


class TestGetAlertHistoryFallback:
    @pytest.mark.asyncio
    async def test_prefers_extended_endpoint(self, api_urls, mock_logger):
        extended_data = [{'alertDate': '2026-03-14T02:30:00', 'category_desc': 'Test', 'data': 'City'}]
        resp = _make_response(json.dumps(extended_data).encode('utf-8'))
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.get_alert_history()
        assert result == extended_data

    @pytest.mark.asyncio
    async def test_falls_back_to_24h_on_extended_failure(self, api_urls, mock_logger):
        history_24h = [{'alertDate': '2024-01-15', 'title': 'Rockets', 'data': 'City A'}]

        call_count = 0

        async def side_effect(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Extended endpoint fails
                raise httpx.TransportError('Connection refused')
            # 24h endpoint succeeds
            return _make_response(json.dumps(history_24h).encode('utf-8'))

        client = AsyncMock()
        client.get = AsyncMock(side_effect=side_effect)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.get_alert_history()
        assert result == history_24h


class TestGetDistricts:
    @pytest.mark.asyncio
    async def test_returns_list_on_success(self, api_urls, mock_logger):
        districts = [
            {'label': 'כפר סבא', 'label_he': 'כפר סבא', 'value': 'ABC', 'id': '840', 'areaid': 27, 'areaname': 'שרון', 'migun_time': 90},
        ]
        resp = _make_response(json.dumps(districts).encode('utf-8'))
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.get_districts()
        assert result == districts

    @pytest.mark.asyncio
    async def test_returns_none_on_empty(self, api_urls, mock_logger):
        resp = _make_response(b'')
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.get_districts()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_non_list(self, api_urls, mock_logger):
        resp = _make_response(json.dumps({'not': 'a list'}).encode('utf-8'))
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.get_districts()
        assert result is None

    @pytest.mark.asyncio
    async def test_sends_referer_header(self, api_urls, mock_logger):
        resp = _make_response(json.dumps([]).encode('utf-8'))
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        await api.get_districts()
        call_kwargs = client.get.call_args
        assert call_kwargs.kwargs.get('headers', {}).get('Referer') == 'https://alerts-history.oref.org.il/'

    @pytest.mark.asyncio
    async def test_supports_lang_parameter(self, api_urls, mock_logger):
        resp = _make_response(json.dumps([]).encode('utf-8'))
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        await api.get_districts(lang='en')
        call_url = client.get.call_args.args[0]
        assert 'lang=en' in call_url

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self, api_urls, mock_logger):
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                'Not Found',
                request=httpx.Request('GET', 'https://example.com'),
                response=httpx.Response(404),
            )
        )
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.get_districts()
        assert result is None

    @pytest.mark.asyncio
    async def test_handles_bom_in_response(self, api_urls, mock_logger):
        districts = [{'label_he': 'Test', 'areaname': 'Area', 'migun_time': 30}]
        bom_json = b'\xef\xbb\xbf' + json.dumps(districts).encode('utf-8')
        resp = _make_response(bom_json)
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.get_districts()
        assert result == districts


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


class TestGetAlertCategories:
    @pytest.mark.asyncio
    async def test_returns_list_on_success(self, api_urls, mock_logger):
        categories = [
            {'id': 1, 'category': 'missilealert', 'matrix_id': 1, 'priority': 120, 'queue': False},
            {'id': 2, 'category': 'uav', 'matrix_id': 6, 'priority': 130, 'queue': False},
        ]
        resp = _make_response(json.dumps(categories).encode('utf-8'))
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.get_alert_categories()
        assert result == categories

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_response(self, api_urls, mock_logger):
        resp = _make_response(b'')
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.get_alert_categories()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_non_list(self, api_urls, mock_logger):
        resp = _make_response(json.dumps({'not': 'a list'}).encode('utf-8'))
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.get_alert_categories()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_http_404(self, api_urls, mock_logger):
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                'Not Found',
                request=httpx.Request('GET', 'https://example.com'),
                response=httpx.Response(404),
            )
        )
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.get_alert_categories()
        assert result is None

    @pytest.mark.asyncio
    async def test_uses_correct_url(self, api_urls, mock_logger):
        resp = _make_response(json.dumps([]).encode('utf-8'))
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        await api.get_alert_categories()
        call_url = client.get.call_args.args[0]
        assert '/alerts/alertCategories.json' in call_url

    @pytest.mark.asyncio
    async def test_sends_referer_header(self, api_urls, mock_logger):
        resp = _make_response(json.dumps([]).encode('utf-8'))
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        await api.get_alert_categories()
        call_kwargs = client.get.call_args
        assert call_kwargs.kwargs.get('headers', {}).get('Referer') == 'https://www.oref.org.il/'


class TestGetAlertTranslations:
    @pytest.mark.asyncio
    async def test_returns_list_on_success(self, api_urls, mock_logger):
        translations = [
            {
                'heb': 'היכנסו למרחב המוגן',
                'eng': 'Enter the Protected Space',
                'rus': 'Войдите в убежище',
                'arb': 'ادخلوا الحيز المحمي',
                'catId': 1,
                'matrixCatId': 1,
                'hebTitle': 'ירי רקטות וטילים',
                'engTitle': 'Rocket and Missile Attack',
                'rusTitle': 'Ракетный обстрел',
                'arbTitle': 'إطلاق قذائف وصواريخ',
                'updateType': '-',
            },
        ]
        resp = _make_response(json.dumps(translations).encode('utf-8'))
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.get_alert_translations()
        assert result == translations

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_response(self, api_urls, mock_logger):
        resp = _make_response(b'')
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.get_alert_translations()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_non_list(self, api_urls, mock_logger):
        resp = _make_response(json.dumps({'not': 'a list'}).encode('utf-8'))
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.get_alert_translations()
        assert result is None

    @pytest.mark.asyncio
    async def test_uses_correct_url(self, api_urls, mock_logger):
        resp = _make_response(json.dumps([]).encode('utf-8'))
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        await api.get_alert_translations()
        call_url = client.get.call_args.args[0]
        assert '/alerts/alertsTranslation.json' in call_url

    @pytest.mark.asyncio
    async def test_sends_referer_header(self, api_urls, mock_logger):
        resp = _make_response(json.dumps([]).encode('utf-8'))
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        await api.get_alert_translations()
        call_kwargs = client.get.call_args
        assert call_kwargs.kwargs.get('headers', {}).get('Referer') == 'https://www.oref.org.il/'


class TestGetAlertDisplayConfig:
    @pytest.mark.asyncio
    async def test_returns_list_on_success(self, api_urls, mock_logger):
        config = [
            {
                'title': 'ירי רקטות וטילים',
                'cat': 'missilealert',
                'instructions': 'היכנסו למרחב המוגן',
                'eventManagementLink': None,
                'lifeSavingGuidelinesLink': None,
                'ttlInMinutes': 10,
                'updateType': '0',
            },
        ]
        resp = _make_response(json.dumps(config).encode('utf-8'))
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.get_alert_display_config()
        assert result == config
        assert result[0]['ttlInMinutes'] == 10

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_response(self, api_urls, mock_logger):
        resp = _make_response(b'')
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.get_alert_display_config()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_non_list(self, api_urls, mock_logger):
        resp = _make_response(json.dumps({'not': 'a list'}).encode('utf-8'))
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.get_alert_display_config()
        assert result is None

    @pytest.mark.asyncio
    async def test_uses_correct_url(self, api_urls, mock_logger):
        resp = _make_response(json.dumps([]).encode('utf-8'))
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        await api.get_alert_display_config()
        call_url = client.get.call_args.args[0]
        assert '/alerts/RemainderConfig_heb.json' in call_url

    @pytest.mark.asyncio
    async def test_sends_referer_header(self, api_urls, mock_logger):
        resp = _make_response(json.dumps([]).encode('utf-8'))
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        await api.get_alert_display_config()
        call_kwargs = client.get.call_args
        assert call_kwargs.kwargs.get('headers', {}).get('Referer') == 'https://www.oref.org.il/'


class TestGetGlobalConfig:
    @pytest.mark.asyncio
    async def test_returns_dict_on_success(self, api_urls, mock_logger):
        config = {
            'alertsTimeout': 10,
            'isSettlementStatusNeeded': False,
            'feedbackForm': {'articles': True},
            'defaultOgImage': '/media/test.jpg',
        }
        resp = _make_response(json.dumps(config).encode('utf-8'))
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.get_global_config()
        assert result == config
        assert result['alertsTimeout'] == 10

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_response(self, api_urls, mock_logger):
        resp = _make_response(b'')
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.get_global_config()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_non_dict(self, api_urls, mock_logger):
        resp = _make_response(json.dumps([1, 2, 3]).encode('utf-8'))
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.get_global_config()
        assert result is None

    @pytest.mark.asyncio
    async def test_uses_correct_url(self, api_urls, mock_logger):
        resp = _make_response(json.dumps({}).encode('utf-8'))
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        await api.get_global_config()
        call_url = client.get.call_args.args[0]
        assert 'api.oref.org.il/api/v1/global' in call_url

    @pytest.mark.asyncio
    async def test_sends_referer_header(self, api_urls, mock_logger):
        resp = _make_response(json.dumps({}).encode('utf-8'))
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        await api.get_global_config()
        call_kwargs = client.get.call_args
        assert call_kwargs.kwargs.get('headers', {}).get('Referer') == 'https://www.oref.org.il/'

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self, api_urls, mock_logger):
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                'Server Error',
                request=httpx.Request('GET', 'https://example.com'),
                response=httpx.Response(500),
            )
        )
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.get_global_config()
        assert result is None


class TestGetRecentAlertsFromHistory:
    @pytest.mark.asyncio
    async def test_groups_history_entries_into_live_format(self, api_urls, mock_logger):
        from datetime import datetime

        now = datetime.now()
        recent_date = now.strftime('%Y-%m-%dT%H:%M:%S')
        history = [
            {'alertDate': recent_date, 'category': 1, 'category_desc': 'ירי רקטות וטילים', 'data': 'תל אביב', 'matrix_id': 1},
            {'alertDate': recent_date, 'category': 1, 'category_desc': 'ירי רקטות וטילים', 'data': 'רמת גן', 'matrix_id': 1},
        ]
        resp = _make_response(json.dumps(history).encode('utf-8'))
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.get_recent_alerts_from_history(max_age_seconds=300)
        assert len(result) == 1
        assert result[0]['cat'] == '1'
        assert 'תל אביב' in result[0]['data']
        assert 'רמת גן' in result[0]['data']
        assert result[0]['title'] == 'ירי רקטות וטילים'

    @pytest.mark.asyncio
    async def test_filters_old_entries(self, api_urls, mock_logger):
        history = [
            {'alertDate': '2020-01-01T00:00:00', 'category': 1, 'category_desc': 'Old alert', 'data': 'City', 'matrix_id': 1},
        ]
        resp = _make_response(json.dumps(history).encode('utf-8'))
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.get_recent_alerts_from_history(max_age_seconds=120)
        assert result == []

    @pytest.mark.asyncio
    async def test_uses_matrix_id_over_category(self, api_urls, mock_logger):
        from datetime import datetime

        now = datetime.now()
        recent_date = now.strftime('%Y-%m-%dT%H:%M:%S')
        history = [
            {'alertDate': recent_date, 'category': 8, 'category_desc': 'Flash', 'data': 'City', 'matrix_id': 10},
        ]
        resp = _make_response(json.dumps(history).encode('utf-8'))
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.get_recent_alerts_from_history(max_age_seconds=300)
        assert len(result) == 1
        assert result[0]['cat'] == '10'

    @pytest.mark.asyncio
    async def test_falls_back_to_category_mapping(self, api_urls, mock_logger):
        from datetime import datetime

        now = datetime.now()
        recent_date = now.strftime('%Y-%m-%dT%H:%M:%S')
        history = [
            {'alertDate': recent_date, 'category': 8, 'category_desc': 'Flash', 'data': 'City'},
        ]
        resp = _make_response(json.dumps(history).encode('utf-8'))
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.get_recent_alerts_from_history(max_age_seconds=300)
        assert len(result) == 1
        assert result[0]['cat'] == '10'

    @pytest.mark.asyncio
    async def test_returns_empty_on_api_failure(self, api_urls, mock_logger):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.TransportError('timeout'))
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.get_recent_alerts_from_history()
        assert result == []

    @pytest.mark.asyncio
    async def test_deduplicates_cities_in_group(self, api_urls, mock_logger):
        from datetime import datetime

        now = datetime.now()
        recent_date = now.strftime('%Y-%m-%dT%H:%M:%S')
        history = [
            {'alertDate': recent_date, 'category': 1, 'category_desc': 'Missiles', 'data': 'City A', 'matrix_id': 1},
            {'alertDate': recent_date, 'category': 1, 'category_desc': 'Missiles', 'data': 'City A', 'matrix_id': 1},
        ]
        resp = _make_response(json.dumps(history).encode('utf-8'))
        client = _make_mock_client(resp)
        api = HomeFrontCommandApiClient(client, api_urls, mock_logger)

        result = await api.get_recent_alerts_from_history(max_age_seconds=300)
        assert len(result) == 1
        assert result[0]['data'].count('City A') == 1
