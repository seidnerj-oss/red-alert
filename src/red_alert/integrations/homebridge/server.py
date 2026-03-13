"""
Lightweight HTTP server exposing Home Front Command alert state
for Homebridge HTTP-based plugins (e.g. homebridge-http-contact-sensor).

Endpoints:
    GET /status  - Full JSON alert status
    GET /contact - 0 (no alert) or 1 (alert active) for contact sensor
    GET /city    - 0/1 filtered by configured cities
    GET /health  - Health check

Usage:
    python -m red_alert.integrations.homebridge --port 8512
    python -m red_alert.integrations.homebridge --config config.json
"""

import asyncio
import datetime
import logging

import httpx
from aiohttp import web

from red_alert.core.api_client import HomeFrontCommandApiClient
from red_alert.core.state import AlertState, AlertStateTracker

logger = logging.getLogger('red_alert.homebridge')

API_URLS = {
    'live': 'https://www.oref.org.il/WarningMessages/alert/alerts.json',
    'history': 'https://www.oref.org.il/WarningMessages/alert/History/AlertsHistory.json',
}

SESSION_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (compatible; red-alert/4.0; Homebridge)',
    'Referer': 'https://www.oref.org.il/',
    'X-Requested-With': 'XMLHttpRequest',
    'Accept': 'application/json',
    'Accept-Encoding': 'gzip, deflate, br',
    'Accept-Language': 'he,en;q=0.9',
    'Connection': 'keep-alive',
    'Pragma': 'no-cache',
    'Cache-Control': 'no-cache',
}

DEFAULT_CONFIG = {
    'host': '0.0.0.0',
    'port': 8512,
    'interval': 1,
    'hold_seconds': {},
    'areas_of_interest': [],
}


def _log_adapter(msg, level='INFO', **kwargs):
    """Adapt Python logging to the core logger interface (accepts level as keyword)."""
    getattr(logger, level.lower(), logger.info)(msg)


class AlertMonitor:
    """Polls the Home Front Command API and tracks current alert state."""

    def __init__(
        self, api_client: HomeFrontCommandApiClient, areas_of_interest: list[str] | None = None, hold_seconds: dict[str, float] | None = None
    ):
        self._api_client = api_client
        self._state = AlertStateTracker(areas_of_interest=areas_of_interest, hold_seconds=hold_seconds)
        self.last_update = None

    @property
    def active(self) -> bool:
        return self._state.state in (AlertState.ALERT, AlertState.PRE_ALERT)

    @property
    def city_active(self) -> bool:
        """True when an alert/pre-alert matches configured areas (or any area if none configured)."""
        return self.active

    @property
    def alert_state(self) -> AlertState:
        return self._state.state

    @property
    def alert_data(self) -> dict | None:
        return self._state.alert_data

    @property
    def status(self) -> dict:
        return {
            'active': self.active,
            'city_active': self.city_active,
            'state': self._state.state.value,
            'alert': self.alert_data,
            'last_update': self.last_update,
        }

    async def poll(self):
        """Poll the API once and update state."""
        data = await self._api_client.get_live_alerts()
        self.last_update = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self._state.update(data)


# --- HTTP Handlers ---


async def handle_status(request: web.Request) -> web.Response:
    """Full JSON status."""
    monitor: AlertMonitor = request.app['monitor']
    return web.json_response(monitor.status)


async def handle_contact(request: web.Request) -> web.Response:
    """0/1 for contact sensor. 1 = alert active (contact open), 0 = routine (contact closed)."""
    monitor: AlertMonitor = request.app['monitor']
    return web.Response(text='1' if monitor.active else '0')


async def handle_city_contact(request: web.Request) -> web.Response:
    """0/1 filtered by configured cities."""
    monitor: AlertMonitor = request.app['monitor']
    return web.Response(text='1' if monitor.city_active else '0')


async def handle_state(request: web.Request) -> web.Response:
    """Alert state as text: 'routine', 'pre_alert', or 'alert'."""
    monitor: AlertMonitor = request.app['monitor']
    return web.Response(text=monitor.alert_state.value)


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({'status': 'ok'})


# --- Background polling ---


async def _poll_loop(app: web.Application):
    """Background task that polls the API at the configured interval."""
    monitor: AlertMonitor = app['monitor']
    interval: float = app['config']['interval']
    while True:
        try:
            await monitor.poll()
        except Exception:
            logger.exception('Error polling alerts')
        await asyncio.sleep(interval)


async def _on_startup(app: web.Application) -> None:
    app['poll_task'] = asyncio.create_task(_poll_loop(app))
    logger.info(
        'Server started on %s:%s (polling every %ss, cities=%s)',
        app['config']['host'],
        app['config']['port'],
        app['config']['interval'],
        app['config'].get('areas_of_interest', []) or 'all',
    )


async def _on_cleanup(app: web.Application) -> None:
    app['poll_task'].cancel()
    try:
        await app['poll_task']
    except asyncio.CancelledError:
        pass
    await app['http_client'].aclose()


# --- App factory ---


def create_app(config: dict | None = None) -> web.Application:
    """Create the aiohttp web application."""
    cfg = {**DEFAULT_CONFIG, **(config or {})}

    http_client = httpx.AsyncClient(headers=SESSION_HEADERS, timeout=15.0)

    api_client = HomeFrontCommandApiClient(http_client, API_URLS, _log_adapter)
    monitor = AlertMonitor(api_client, areas_of_interest=cfg.get('areas_of_interest'), hold_seconds=cfg.get('hold_seconds'))

    app = web.Application()
    app['monitor'] = monitor
    app['http_client'] = http_client
    app['config'] = cfg

    app.router.add_get('/status', handle_status)
    app.router.add_get('/contact', handle_contact)
    app.router.add_get('/city', handle_city_contact)
    app.router.add_get('/state', handle_state)
    app.router.add_get('/health', handle_health)

    app.on_startup.append(_on_startup)  # type: ignore[arg-type]
    app.on_cleanup.append(_on_cleanup)  # type: ignore[arg-type]

    return app
