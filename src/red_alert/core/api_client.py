import asyncio
import json
import random

import aiohttp

from red_alert.core.utils import check_bom


class HomeFrontCommandApiClient:
    """Client for fetching alerts from the Israeli Home Front Command (Pikud HaOref) API."""

    def __init__(self, session: aiohttp.ClientSession, urls: dict, logger):
        self._session = session
        self._urls = urls
        self._log = logger

    async def _fetch_with_retries(self, fetch_func, retries: int = 2):
        """Retry on network errors with exponential backoff."""
        for attempt in range(retries + 1):
            try:
                return await fetch_func()
            except (aiohttp.ClientError, asyncio.TimeoutError):
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
                async with self._session.get(url) as resp:
                    resp.raise_for_status()
                    if 'application/json' not in resp.headers.get('Content-Type', ''):
                        self._log(f'Warning: Expected JSON content type, got {resp.headers.get("Content-Type")}', level='WARNING')
                    raw_data = await resp.read()
                    try:
                        return raw_data.decode('utf-8-sig')
                    except UnicodeDecodeError:
                        self._log('Failed decoding with utf-8-sig, trying utf-8.', level='DEBUG')
                        return raw_data.decode('utf-8')

            text = await self._fetch_with_retries(_do_fetch)

            if not text or not text.strip():
                return None

            try:
                text = check_bom(text)
                return json.loads(text)
            except json.JSONDecodeError as e:
                log_text_preview = text[:1000].replace('\n', '\\n').replace('\r', '\\r')
                if 'Expecting value: line 1 column 1 (char 0)' not in str(e):
                    self._log(f"Invalid JSON in live alerts: {e}. Raw text preview: '{log_text_preview}...'", level='WARNING')
                return None

        except aiohttp.ClientResponseError as e:
            self._log(f'HTTP error fetching live alerts: Status {e.status}, Message: {e.message}', level='WARNING')
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
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
                async with self._session.get(url) as resp:
                    resp.raise_for_status()
                    raw_data = await resp.read()
                    try:
                        return raw_data.decode('utf-8-sig')
                    except UnicodeDecodeError:
                        return raw_data.decode('utf-8')

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
        except aiohttp.ClientResponseError as e:
            self._log(f'HTTP error fetching history: Status {e.status}, Message: {e.message}', level='WARNING')
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            self._log(f'Network/Timeout error fetching history: {e}', level='WARNING')
        except Exception as e:
            self._log(f'Unexpected error fetching history: {e}', level='ERROR', exc_info=True)
        return None

    async def download_file(self, url: str):
        """Download text content (e.g. city data), return str or None."""
        try:

            async def _do_fetch():
                async with self._session.get(url) as resp:
                    resp.raise_for_status()
                    raw_data = await resp.read()
                    try:
                        return raw_data.decode('utf-8-sig')
                    except UnicodeDecodeError:
                        return raw_data.decode('utf-8')

            text = await self._fetch_with_retries(_do_fetch)
            text = check_bom(text)
            return text
        except aiohttp.ClientResponseError as e:
            self._log(f'HTTP error downloading file {url}: Status {e.status}, Message: {e.message}', level='ERROR')
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            self._log(f'Network/Timeout error downloading file {url}: {e}', level='ERROR')
        except Exception as e:
            self._log(f'Unexpected error downloading file {url}: {e}', level='ERROR', exc_info=True)
        return None
