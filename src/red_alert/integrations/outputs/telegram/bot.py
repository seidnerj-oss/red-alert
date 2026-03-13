"""
Telegram Bot API client for sending alert notifications.

Uses the Telegram Bot HTTP API via httpx (no extra dependencies).
"""

import logging

import httpx

logger = logging.getLogger('red_alert.telegram')

TELEGRAM_API_BASE = 'https://api.telegram.org'


def escape_html(text: str) -> str:
    """Escape HTML special characters for Telegram HTML parse mode."""
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


class TelegramBot:
    """Sends messages to a Telegram chat via the Bot API."""

    def __init__(self, token: str, chat_id: str | int):
        self._token = token
        self._chat_id = str(chat_id)
        self._base_url = f'{TELEGRAM_API_BASE}/bot{token}'
        self._client: httpx.AsyncClient | None = None

    async def send_message(self, text: str, parse_mode: str = 'HTML') -> bool:
        """Send a message to the configured chat.

        Returns True on success, False on failure.
        """
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=15.0)

        try:
            resp = await self._client.post(
                f'{self._base_url}/sendMessage',
                json={
                    'chat_id': self._chat_id,
                    'text': text,
                    'parse_mode': parse_mode,
                },
            )
            if resp.status_code == 200:
                return True
            logger.error('Telegram API error %d: %s', resp.status_code, resp.text)
            return False
        except httpx.HTTPError as e:
            logger.error('Failed to send Telegram message: %s', e)
            return False

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None
