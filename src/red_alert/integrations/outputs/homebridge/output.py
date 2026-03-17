"""
Homebridge HTTP server alert output for the orchestrator.

Receives AlertEvents and exposes alert state via HTTP endpoints
for Homebridge HTTP-based plugins.
"""

from __future__ import annotations

import datetime
import logging

from aiohttp import web

from red_alert.core.orchestrator import AlertEvent, AlertOutput, MultiSourceStateTracker
from red_alert.core.state import AlertState

logger = logging.getLogger('red_alert.output.homebridge')

DEFAULT_CONFIG: dict = {
    'host': '0.0.0.0',
    'port': 8512,
    'hold_seconds': {},
    'areas_of_interest': [],
}


class HomebridgeOutput(AlertOutput):
    """Orchestrator output that serves alert state via HTTP for Homebridge."""

    def __init__(self, config: dict):
        self._config = config
        self._tracker: MultiSourceStateTracker | None = None
        self._runner: web.AppRunner | None = None
        self._last_update: str | None = None

    @property
    def name(self) -> str:
        return 'homebridge'

    async def start(self) -> None:
        cfg = {**DEFAULT_CONFIG, **self._config}

        self._tracker = MultiSourceStateTracker(
            areas_of_interest=cfg.get('areas_of_interest'),
            hold_seconds=cfg.get('hold_seconds'),
            logger=logger,
        )

        app = web.Application()
        app['output'] = self
        app.router.add_get('/status', self._handle_status)
        app.router.add_get('/contact', self._handle_contact)
        app.router.add_get('/city', self._handle_city_contact)
        app.router.add_get('/state', self._handle_state)
        app.router.add_get('/health', self._handle_health)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, cfg['host'], cfg['port'])
        await site.start()

        logger.info(
            'Homebridge output started on %s:%s, areas=%s',
            cfg['host'],
            cfg['port'],
            cfg.get('areas_of_interest') or 'all',
        )

    async def handle_event(self, event: AlertEvent) -> None:
        if not self._tracker:
            return
        self._tracker.update(event)
        self._last_update = datetime.datetime.now(datetime.timezone.utc).isoformat()

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()

    @property
    def _active(self) -> bool:
        if not self._tracker:
            return False
        return self._tracker.state in (AlertState.ALERT, AlertState.PRE_ALERT)

    async def _handle_status(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                'active': self._active,
                'city_active': self._active,
                'state': self._tracker.state.value if self._tracker else 'routine',
                'alert': self._tracker.alert_data if self._tracker else None,
                'last_update': self._last_update,
            }
        )

    async def _handle_contact(self, request: web.Request) -> web.Response:
        return web.Response(text='1' if self._active else '0')

    async def _handle_city_contact(self, request: web.Request) -> web.Response:
        return web.Response(text='1' if self._active else '0')

    async def _handle_state(self, request: web.Request) -> web.Response:
        return web.Response(text=self._tracker.state.value if self._tracker else 'routine')

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({'status': 'ok'})
