import asyncio
import json
import random
from datetime import datetime, timedelta

import httpx

from red_alert.core.utils import check_bom

ALERTS_HISTORY_REFERER = 'https://alerts-history.oref.org.il/'
ALERTS_HISTORY_BASE = 'https://alerts-history.oref.org.il'


class HomeFrontCommandApiClient:
    """Client for fetching alerts from the Israeli Home Front Command (Pikud Ha-Oref) API.

    HFC API endpoints:
        Live alerts:     /WarningMessages/alert/alerts.json
        24h history:     /WarningMessages/alert/History/AlertsHistory.json
        Extended history (alerts-history.oref.org.il):
            /Shared/Ajax/GetAlarmsHistory.aspx?lang=he&fromDate=DD.MM.YYYY&toDate=DD.MM.YYYY&mode=0
        City/district data (alerts-history.oref.org.il):
            /Shared/Ajax/GetDistricts.aspx?lang=he  (includes migun_time - shelter time in seconds)
            /Shared/Ajax/GetCities.aspx?lang=he      (includes areaid, mixname)

    Live alerts response format:
        {"cat": "1", "title": "ירי רקטות וטילים", "data": ["city1", "city2"], "desc": "..."}
        The API sends brief pulses (~30-60s), then returns empty. See state.py for hold logic.

    IMPORTANT: The live endpoint may send all-clear with cat=10 (matrix_id) instead of cat=13.
    The history endpoint normalizes to category=13 with matrix_id=10.
    """

    def __init__(self, client: httpx.AsyncClient, urls: dict, logger):
        self._client = client
        self._urls = urls
        self._log = logger
        self._last_alert_id: str | None = None

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
                    alert_id = f"{data.get('cat', '?')}:{data.get('title', '')}:{len(data.get('data', []))}"
                    if alert_id != self._last_alert_id:
                        cat = data.get('cat', '?')
                        title = data.get('title', '')
                        cities = data.get('data', [])
                        self._log(f"Alert received: cat={cat}, title='{title}', {len(cities)} cities")
                        self._last_alert_id = alert_id
                elif self._last_alert_id is not None:
                    self._last_alert_id = None
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
        """Fetch alert history from the extended history endpoint.

        Uses the alerts-history.oref.org.il extended endpoint which is more reliable
        than the 24h endpoint on www.oref.org.il (which returns empty even when alerts
        occurred recently). Falls back to the 24h endpoint if the extended one fails.

        Returns a list of dicts with at least 'alertDate', 'title'/'category_desc', and 'data' keys,
        or None on failure.
        """
        data = await self.get_extended_alert_history()
        if data is not None:
            return data

        self._log('Extended history unavailable, falling back to 24h endpoint.', level='WARNING')
        return await self._get_24h_alert_history()

    async def get_extended_alert_history(self, hours_back: int = 24):
        """Fetch alert history from the extended alerts-history.oref.org.il endpoint.

        This endpoint is more reliable than the 24h endpoint and returns richer data
        including category, category_desc, matrix_id, and alertDate in ISO format.
        """
        now = datetime.now()
        from_date = (now - timedelta(hours=hours_back)).strftime('%d.%m.%Y')
        to_date = now.strftime('%d.%m.%Y')
        url = f'{ALERTS_HISTORY_BASE}/Shared/Ajax/GetAlarmsHistory.aspx?lang=he&fromDate={from_date}&toDate={to_date}&mode=0'
        try:

            async def _do_fetch():
                resp = await self._client.get(url, headers={'Referer': ALERTS_HISTORY_REFERER})
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
                    self._log(f'Extended history: loaded {len(data)} entries for past {hours_back}h.')
                    return data
                self._log('Extended history response is not a list.', level='WARNING')
                return None
            except json.JSONDecodeError as e:
                self._log(f'Invalid JSON in extended history: {e}', level='WARNING')
                return None
        except httpx.HTTPStatusError as e:
            self._log(f'HTTP error fetching extended history: Status {e.response.status_code}', level='WARNING')
        except httpx.TransportError as e:
            self._log(f'Network/Timeout error fetching extended history: {e}', level='WARNING')
        except Exception as e:
            self._log(f'Unexpected error fetching extended history: {e.__class__.__name__} - {e}', level='ERROR', exc_info=True)
        return None

    async def _get_24h_alert_history(self):
        """Fetch from the legacy 24h endpoint. Known to be unreliable - may return empty
        even when alerts occurred in the past 24 hours. Use get_alert_history() which
        prefers the extended endpoint."""
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

    async def get_districts(self, lang: str = 'he'):
        """Fetch city/district data from the HFC API.

        Returns the authoritative HFC city-to-area mapping with shelter times (migun_time).
        This is preferable to static city data files because it uses the HFC's own area
        groupings that match the live alert system.

        Returns a list of dicts with keys: label, label_he, value, id, areaid, areaname, migun_time.
        Returns None on failure.
        """
        url = f'{ALERTS_HISTORY_BASE}/Shared/Ajax/GetDistricts.aspx?lang={lang}'
        try:

            async def _do_fetch():
                resp = await self._client.get(url, headers={'Referer': ALERTS_HISTORY_REFERER})
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
                    self._log(f'Districts: loaded {len(data)} entries.')
                    return data
                self._log('Districts response is not a list.', level='WARNING')
                return None
            except json.JSONDecodeError as e:
                self._log(f'Invalid JSON in districts: {e}', level='WARNING')
                return None
        except httpx.HTTPStatusError as e:
            self._log(f'HTTP error fetching districts: Status {e.response.status_code}', level='WARNING')
        except httpx.TransportError as e:
            self._log(f'Network/Timeout error fetching districts: {e}', level='WARNING')
        except Exception as e:
            self._log(f'Unexpected error fetching districts: {e.__class__.__name__} - {e}', level='ERROR', exc_info=True)
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
