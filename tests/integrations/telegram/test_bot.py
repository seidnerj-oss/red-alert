from unittest.mock import AsyncMock, patch

import httpx
import pytest

from red_alert.integrations.telegram.bot import TelegramBot, escape_html


class TestEscapeHtml:
    def test_escapes_ampersand(self):
        assert escape_html('A & B') == 'A &amp; B'

    def test_escapes_angle_brackets(self):
        assert escape_html('<b>text</b>') == '&lt;b&gt;text&lt;/b&gt;'

    def test_no_escaping_needed(self):
        assert escape_html('plain text') == 'plain text'

    def test_empty_string(self):
        assert escape_html('') == ''

    def test_hebrew_text(self):
        text = 'ירי רקטות וטילים'
        assert escape_html(text) == text

    def test_combined_escaping(self):
        assert escape_html('A & B < C > D') == 'A &amp; B &lt; C &gt; D'


class TestTelegramBot:
    def test_init(self):
        bot = TelegramBot(token='123:ABC', chat_id='-100')
        assert bot._chat_id == '-100'
        assert '123:ABC' in bot._base_url

    def test_chat_id_converted_to_string(self):
        bot = TelegramBot(token='123:ABC', chat_id=12345)
        assert bot._chat_id == '12345'

    @pytest.mark.asyncio
    async def test_send_message_success(self):
        bot = TelegramBot(token='123:ABC', chat_id='-100')
        mock_client = AsyncMock()
        mock_client.post.return_value = httpx.Response(200, json={'ok': True})
        bot._client = mock_client

        result = await bot.send_message('Hello')
        assert result is True

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert '/sendMessage' in call_args.args[0]
        assert call_args.kwargs['json']['chat_id'] == '-100'
        assert call_args.kwargs['json']['text'] == 'Hello'
        assert call_args.kwargs['json']['parse_mode'] == 'HTML'

    @pytest.mark.asyncio
    async def test_send_message_api_error(self):
        bot = TelegramBot(token='123:ABC', chat_id='-100')
        mock_client = AsyncMock()
        mock_client.post.return_value = httpx.Response(400, json={'ok': False, 'description': 'Bad Request'})
        bot._client = mock_client

        result = await bot.send_message('Hello')
        assert result is False

    @pytest.mark.asyncio
    async def test_send_message_network_error(self):
        bot = TelegramBot(token='123:ABC', chat_id='-100')
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.ConnectError('Connection refused')
        bot._client = mock_client

        result = await bot.send_message('Hello')
        assert result is False

    @pytest.mark.asyncio
    async def test_send_message_creates_client_if_none(self):
        bot = TelegramBot(token='123:ABC', chat_id='-100')
        assert bot._client is None

        with patch('red_alert.integrations.telegram.bot.httpx.AsyncClient') as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.post.return_value = httpx.Response(200, json={'ok': True})
            mock_cls.return_value = mock_instance

            await bot.send_message('Hello')
            mock_cls.assert_called_once()
            assert bot._client is mock_instance

    @pytest.mark.asyncio
    async def test_close(self):
        bot = TelegramBot(token='123:ABC', chat_id='-100')
        mock_client = AsyncMock()
        bot._client = mock_client

        await bot.close()
        mock_client.aclose.assert_called_once()
        assert bot._client is None

    @pytest.mark.asyncio
    async def test_close_when_no_client(self):
        bot = TelegramBot(token='123:ABC', chat_id='-100')
        await bot.close()  # Should not raise
