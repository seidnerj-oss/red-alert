import asyncio
import json
import random

import httpx

from red_alert.core.utils import check_bom


class HomeFrontCommandApiClient:
    """Client for fetching alerts from the Israeli Home Front Command (Pikud Ha-Oref) API."""

    def __init__(self, client: httpx.AsyncClient, urls: dict, logger):
        self._client = client
        self._urls = urls
        self._log = logger

    async def _fetch_with_retries(self, fetch_func, retries: int = 2):
        """Retry on network errors with exponential backoff."""
        for attempt in range(retries + 1):
            try:
                return await fetch_func()
            except httpx.TransportError:
                if attempt == retries:
                    self._log(f'Network error after {retries + 1} attempts.', level='WARNING')
                    raise
                wait = 0.5 * (2**attempt) + random.uniform(0, 0.5)
                self._log(f'Network error (attempt {attempt + 1}/{retries + 1}). Retrying in {wait:.2f}s.', level='DEBUG')
                await asyncio.sleep(wait)

    async def get_live_alerts(self):
        """Fetch live alerts, return dict or None."""
        url = self._urls.get('live')
        if not url:
            self._log('Live alerts URL not configured.', level='ERROR')
            return None
        try:

            async def _do_fetch():
                resp = await self._client.get(url)
                resp.raise_for_status()
                content_type = resp.headers.get('Content-Type', '')
                if 'application/json' not in content_type:
                    self._log(f'Warning: Expected JSON content type, got {content_type}', level='WARNING')
                try:
                    return resp.content.decode('utf-8-sig')
                except UnicodeDecodeError:
                    self._log('Failed decoding with utf-8-sig, trying utf-8.', level='DEBUG')
                    return resp.content.decode('utf-8')

            text = await self._fetch_with_retries(_do_fetch)

            if not text or not text.strip():
                return None

            try:
                text = check_bom(text)
                data = json.loads(text)
                if data and isinstance(data, dict):
                    cat = data.get('cat', '?')
                    title = data.get('title', '')
                    cities = data.get('data', [])
                    self._log(f"Alert received: cat={cat}, title='{title}', {len(cities)} cities")
                return data
            except json.JSONDecodeError as e:
                log_text_preview = text[:1000].replace('\n', '\\n').replace('\r', '\\r')
                if 'Expecting value: line 1 column 1 (char 0)' not in str(e):
                    self._log(f"Invalid JSON in live alerts: {e}. Raw text preview: '{log_text_preview}...'", level='WARNING')
                return None

        except httpx.HTTPStatusError as e:
            self._log(f'HTTP error fetching live alerts: Status {e.response.status_code}', level='WARNING')
        except httpx.TransportError as e:
            self._log(f'Network/Timeout error fetching live alerts: {e}', level='WARNING')
        except Exception as e:
            self._log(f'Unexpected error fetching live alerts: {e.__class__.__name__} - {e}', level='ERROR', exc_info=True)

        return None

    async def get_alert_history(self):
        """Fetch alert history, return list or None."""
        url = self._urls.get('history')
        if not url:
            self._log('History alerts URL not configured.', level='ERROR')
            return None
        try:

            async def _do_fetch():
                resp = await self._client.get(url)
                resp.raise_for_status()
                try:
                    return resp.content.decode('utf-8-sig')
                except UnicodeDecodeError:
                    return resp.content.decode('utf-8')

            text = await self._fetch_with_retries(_do_fetch)
            if not text or not text.strip():
                return None
            try:
                text = check_bom(text)
                data = json.loads(text)
                if isinstance(data, list):
                    return data
                self._log('History response is not a list', level='WARNING')
                return None
            except json.JSONDecodeError as e:
                log_text_preview = text[:5500].replace('\n', '\\n').replace('\r', '\\r')
                self._log(f"Invalid JSON in history alerts: {e}. Raw text preview: '{log_text_preview}...'", level='WARNING')
                return None
        except httpx.HTTPStatusError as e:
            self._log(f'HTTP error fetching history: Status {e.response.status_code}', level='WARNING')
        except httpx.TransportError as e:
            self._log(f'Network/Timeout error fetching history: {e}', level='WARNING')
        except Exception as e:
            self._log(f'Unexpected error fetching history: {e}', level='ERROR', exc_info=True)
        return None

    async def download_file(self, url: str):
        """Download text content (e.g. city data), return str or None."""
        try:

            async def _do_fetch():
                resp = await self._client.get(url)
                resp.raise_for_status()
                try:
                    return resp.content.decode('utf-8-sig')
                except UnicodeDecodeError:
                    return resp.content.decode('utf-8')

            text = await self._fetch_with_retries(_do_fetch)
            if text is not None:
                text = check_bom(text)
            return text
        except httpx.HTTPStatusError as e:
            self._log(f'HTTP error downloading file {url}: Status {e.response.status_code}', level='ERROR')
        except httpx.TransportError as e:
            self._log(f'Network/Timeout error downloading file {url}: {e}', level='ERROR')
        except Exception as e:
            self._log(f'Unexpected error downloading file {url}: {e}', level='ERROR', exc_info=True)
        return None
