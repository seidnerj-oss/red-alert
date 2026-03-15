import asyncio
import json
import random
from datetime import datetime, timedelta
from typing import Literal, overload

import httpx

from red_alert.core.utils import check_bom, detect_and_decode

ALERTS_HISTORY_REFERER = 'https://alerts-history.oref.org.il/'
ALERTS_HISTORY_BASE = 'https://alerts-history.oref.org.il'
OREF_WEBSITE_BASE = 'https://www.oref.org.il'
OREF_WEBSITE_REFERER = 'https://www.oref.org.il/'
OREF_API_BASE = 'https://api.oref.org.il'


class HomeFrontCommandApiClient:
    """Client for fetching alerts from the Israeli Home Front Command (Pikud Ha-Oref) API.

    HFC API endpoints (three domains):
        www.oref.org.il:
            /WarningMessages/alert/alerts.json           - Live alerts (polled every second)
            /WarningMessages/alert/History/AlertsHistory.json - 24h history (unreliable fallback)
            /alerts/alertCategories.json                 - Alert category definitions
            /alerts/alertsTranslation.json               - Multi-language alert translations
            /alerts/RemainderConfig_heb.json             - Alert display config (TTL, instructions)
        alerts-history.oref.org.il:
            /Shared/Ajax/GetAlarmsHistory.aspx           - Extended history (primary)
            /Shared/Ajax/GetDistricts.aspx               - District/city data with shelter times
            /Shared/Ajax/GetCities.aspx                  - City data (less useful than districts)
        api.oref.org.il:
            /api/v1/global                               - Global config (polling interval)

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

    @overload
    async def _fetch_json(
        self, url: str, *, headers: dict[str, str] | None = None, expect_list: Literal[True] = True, label: str = ''
    ) -> list | None: ...

    @overload
    async def _fetch_json(
        self, url: str, *, headers: dict[str, str] | None = None, expect_list: Literal[False] = ..., label: str = ''
    ) -> dict | None: ...

    async def _fetch_json(self, url: str, *, headers: dict[str, str] | None = None, expect_list: bool = True, label: str = ''):
        """Fetch JSON from a URL with retries, BOM handling, and shape validation.

        Args:
            url: The URL to fetch.
            headers: Optional headers for the request.
            expect_list: If True, validate response is a list. If False, validate it is a dict.
            label: Descriptive label for log messages (e.g., 'districts').

        Returns the parsed JSON data (list or dict), or None on failure.
        """
        try:

            async def _do_fetch():
                resp = await self._client.get(url, headers=headers)
                resp.raise_for_status()
                return detect_and_decode(resp.content)

            text = await self._fetch_with_retries(_do_fetch)
            if not text or not text.strip():
                return None
            try:
                text = check_bom(text)
                data = json.loads(text)
                expected_type = list if expect_list else dict
                if isinstance(data, expected_type):
                    if label:
                        self._log(f'{label.capitalize()}: loaded {len(data)} entries.')
                    return data
                self._log(f'{label.capitalize() if label else "Response"} is not a {expected_type.__name__}.', level='WARNING')
                return None
            except json.JSONDecodeError as e:
                self._log(f'Invalid JSON in {label or "response"}: {e}', level='WARNING')
                return None
        except httpx.HTTPStatusError as e:
            self._log(f'HTTP error fetching {label or url}: Status {e.response.status_code}', level='WARNING')
        except httpx.TransportError as e:
            self._log(f'Network/Timeout error fetching {label or url}: {e}', level='WARNING')
        except Exception as e:
            self._log(f'Unexpected error fetching {label or url}: {e.__class__.__name__} - {e}', level='ERROR', exc_info=True)
        return None

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
                return detect_and_decode(resp.content)

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

    async def get_recent_alerts_from_history(self, max_age_seconds: int = 120) -> list[dict]:
        """Fetch recent alerts from history, grouped into live-alert-format dicts.

        This is a fallback for when the live endpoint returns empty but alerts may
        have been broadcast. The history endpoint has latency (~30-60s), so this
        catches alerts that the live endpoint pulse was too brief to capture.

        Each returned dict has the same shape as a live alert:
            {"cat": "1", "title": "...", "data": ["city1", "city2"], "alertDate": "..."}

        Args:
            max_age_seconds: Only include history entries newer than this many seconds ago.

        Returns a list of grouped alert dicts, or an empty list on failure.
        """
        from red_alert.core.constants import HISTORY_CATEGORY_TO_LIVE

        history = await self.get_extended_alert_history(hours_back=1)
        if not history:
            return []

        now = datetime.now()
        cutoff = now - timedelta(seconds=max_age_seconds)

        # Group history entries by (alertDate rounded to minute, category) into alert groups
        groups: dict[str, dict] = {}
        for entry in history:
            if not isinstance(entry, dict):
                continue
            alert_date_str = entry.get('alertDate', '')
            if not alert_date_str:
                continue

            try:
                # History dates are ISO format: "2024-01-15T10:30:45"
                alert_dt = datetime.fromisoformat(alert_date_str.replace('Z', '').split('+')[0])
            except (ValueError, TypeError):
                continue

            if alert_dt < cutoff:
                continue

            # Use matrix_id (= live cat) if available, fall back to HISTORY_CATEGORY_TO_LIVE
            matrix_id = entry.get('matrix_id')
            hist_cat = entry.get('category')
            if matrix_id is not None:
                try:
                    live_cat = int(matrix_id)
                except (ValueError, TypeError):
                    live_cat = 0
            elif hist_cat is not None:
                try:
                    live_cat = HISTORY_CATEGORY_TO_LIVE.get(int(hist_cat), 0)
                except (ValueError, TypeError):
                    live_cat = 0
            else:
                live_cat = 0

            title = entry.get('category_desc', entry.get('title', ''))
            city = entry.get('data', '')
            # Group key: minute-level timestamp + category
            group_key = f'{alert_dt.strftime("%Y-%m-%dT%H:%M")}:{live_cat}'

            if group_key not in groups:
                groups[group_key] = {
                    'cat': str(live_cat),
                    'title': title,
                    'data': [],
                    'alertDate': alert_date_str,
                }

            if city and city not in groups[group_key]['data']:
                groups[group_key]['data'].append(city)

        # Sort by alertDate descending (most recent first)
        result = sorted(groups.values(), key=lambda g: g.get('alertDate', ''), reverse=True)
        if result:
            self._log(f'History fallback: found {len(result)} recent alert group(s) within {max_age_seconds}s.')
        return result

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
                return detect_and_decode(resp.content)

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
                return detect_and_decode(resp.content)

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
        return await self._fetch_json(url, headers={'Referer': ALERTS_HISTORY_REFERER}, label='districts')

    async def download_file(self, url: str):
        """Download text content (e.g. city data), return str or None."""
        try:

            async def _do_fetch():
                resp = await self._client.get(url)
                resp.raise_for_status()
                return detect_and_decode(resp.content)

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

    async def get_alert_categories(self) -> list | None:
        """Fetch alert category definitions from the HFC website.

        Returns a list of dicts mapping category IDs to English names, matrix IDs,
        and display priorities. Covers all 28 real and drill alert types.
        Keys: id, category, matrix_id, priority, queue.

        NOTE: This endpoint may return 404 when no alerts are active.
        Returns None on failure.
        """
        url = f'{OREF_WEBSITE_BASE}/alerts/alertCategories.json'
        return await self._fetch_json(url, headers={'Referer': OREF_WEBSITE_REFERER}, label='alert categories')

    async def get_alert_translations(self) -> list | None:
        """Fetch multi-language alert translations from the HFC website.

        Returns a list of dicts with 4-language translations (Hebrew, English, Russian, Arabic)
        for all alert types including titles and instruction text.
        Keys: heb, eng, rus, arb, catId, matrixCatId, hebTitle, engTitle, rusTitle, arbTitle, updateType.
        Returns None on failure.
        """
        url = f'{OREF_WEBSITE_BASE}/alerts/alertsTranslation.json'
        return await self._fetch_json(url, headers={'Referer': OREF_WEBSITE_REFERER}, label='alert translations')

    async def get_alert_display_config(self) -> list | None:
        """Fetch alert display configuration from the HFC website.

        Returns a list of dicts with display settings for each alert type/sub-type,
        including Hebrew titles, shelter instructions, TTL in minutes, and links to
        life-saving guidelines.
        Keys: title, cat, instructions, eventManagementLink, lifeSavingGuidelinesLink, ttlInMinutes, updateType.
        Returns None on failure.
        """
        url = f'{OREF_WEBSITE_BASE}/alerts/RemainderConfig_heb.json'
        return await self._fetch_json(url, headers={'Referer': OREF_WEBSITE_REFERER}, label='alert display config')

    async def get_global_config(self) -> dict | None:
        """Fetch global site configuration from the HFC API.

        Returns a dict with site-wide settings. The alertsTimeout field indicates
        the recommended polling interval in seconds (currently 10).
        Keys: alertsTimeout, isSettlementStatusNeeded, feedbackForm, defaultOgImage.
        Returns None on failure.
        """
        url = f'{OREF_API_BASE}/api/v1/global'
        return await self._fetch_json(url, headers={'Referer': OREF_WEBSITE_REFERER}, expect_list=False, label='global config')
