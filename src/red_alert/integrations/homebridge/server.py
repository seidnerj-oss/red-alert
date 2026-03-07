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

import aiohttp
from aiohttp import web

from red_alert.core.api_client import HomeFrontCommandApiClient
from red_alert.core.utils import standardize_name

logger = logging.getLogger('red_alert.homebridge')

API_URLS = {
    'live': 'https://www.oref.org.il/WarningMessages/alert/alerts.json',
    'history': 'https://www.oref.org.il/WarningMessages/alert/History/AlertsHistory.json',
}

SESSION_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (compatible; RedAlert/3.0; Homebridge)',
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
    'city_names': [],
}


def _log_adapter(msg, level='INFO', **kwargs):
    """Adapt Python logging to the core logger interface (accepts level as keyword)."""
    getattr(logger, level.lower(), logger.info)(msg)


class AlertMonitor:
    """Polls the Home Front Command API and tracks current alert state."""

    def __init__(self, api_client: HomeFrontCommandApiClient, city_names: list[str] | None = None):
        self._api_client = api_client
        self._city_names = [standardize_name(c) for c in (city_names or [])]
        self.active = False
        self.city_active = False
        self.alert_data = None
        self.last_update = None

    @property
    def status(self) -> dict:
        return {
            'active': self.active,
            'city_active': self.city_active,
            'alert': self.alert_data,
            'last_update': self.last_update,
        }

    async def poll(self):
        """Poll the API once and update state."""
        data = await self._api_client.get_live_alerts()
        self.last_update = datetime.datetime.now(datetime.timezone.utc).isoformat()

        if data and isinstance(data, dict) and data.get('data'):
            self.active = True
            self.alert_data = {
                'id': data.get('id'),
                'cat': data.get('cat'),
                'title': data.get('title'),
                'cities': data.get('data', []),
                'desc': data.get('desc'),
            }
            if self._city_names:
                alert_cities = [standardize_name(c) for c in data.get('data', [])]
                self.city_active = any(c in alert_cities for c in self._city_names)
            else:
                self.city_active = self.active
        else:
            self.active = False
            self.alert_data = None
            self.city_active = False


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


async def _on_startup(app: web.Application):
    app['poll_task'] = asyncio.create_task(_poll_loop(app))
    logger.info(
        'Server started on %s:%s (polling every %ss, cities=%s)',
        app['config']['host'],
        app['config']['port'],
        app['config']['interval'],
        app['config'].get('city_names', []) or 'all',
    )


async def _on_cleanup(app: web.Application):
    app['poll_task'].cancel()
    try:
        await app['poll_task']
    except asyncio.CancelledError:
        pass
    await app['session'].close()


# --- App factory ---


def create_app(config: dict | None = None) -> web.Application:
    """Create the aiohttp web application."""
    cfg = {**DEFAULT_CONFIG, **(config or {})}

    timeout = aiohttp.ClientTimeout(total=15, connect=5, sock_connect=5, sock_read=10)
    connector = aiohttp.TCPConnector(limit_per_host=5, keepalive_timeout=30, enable_cleanup_closed=True)
    session = aiohttp.ClientSession(connector=connector, timeout=timeout, headers=SESSION_HEADERS, trust_env=False)

    api_client = HomeFrontCommandApiClient(session, API_URLS, _log_adapter)
    monitor = AlertMonitor(api_client, city_names=cfg.get('city_names'))

    app = web.Application()
    app['monitor'] = monitor
    app['session'] = session
    app['config'] = cfg

    app.router.add_get('/status', handle_status)
    app.router.add_get('/contact', handle_contact)
    app.router.add_get('/city', handle_city_contact)
    app.router.add_get('/health', handle_health)

    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)

    return app
