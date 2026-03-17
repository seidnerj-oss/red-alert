"""
HFC API alert input for the orchestrator.

Polls the Home Front Command live alerts API at a configured interval
and emits AlertEvents. Also seeds initial state from history on startup.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import time
from collections.abc import Awaitable, Callable

from red_alert.core.orchestrator import AlertEvent, AlertInput
from red_alert.core.state import AlertState, ACTIVE_ALERT_CATEGORIES, PRE_ALERT_CATEGORY, ALL_CLEAR_CATEGORY
from red_alert.integrations.inputs.hfc.api_client import HomeFrontCommandApiClient

logger = logging.getLogger('red_alert.input.hfc')

SOURCE_NAME = 'hfc'


def _classify_for_event(data: dict | None) -> AlertState:
    """Lightweight classification for the event's state field.

    The authoritative classification happens in each output's AlertStateTracker
    (which handles area filtering, hold timers, etc.). This is just a hint
    so outputs can quickly filter irrelevant events.
    """
    if not data or not isinstance(data, dict):
        return AlertState.ROUTINE

    try:
        cat = int(data.get('cat', 0))
    except (TypeError, ValueError):
        cat = 0

    title = data.get('title', '')

    if cat == ALL_CLEAR_CATEGORY or (isinstance(title, str) and 'האירוע הסתיים' in title):
        return AlertState.ALL_CLEAR
    if cat == PRE_ALERT_CATEGORY or (isinstance(title, str) and any(p in title for p in ('בדקות הקרובות', 'עדכון', 'שהייה בסמיכות למרחב מוגן'))):
        return AlertState.PRE_ALERT
    if cat in ACTIVE_ALERT_CATEGORIES:
        return AlertState.ALERT

    return AlertState.ROUTINE


class HfcInput(AlertInput):
    """Polls the HFC live alerts API and emits AlertEvents."""

    def __init__(
        self,
        api_client: HomeFrontCommandApiClient,
        poll_interval: float = 1.0,
        max_hold_seconds: float = 1800,
    ):
        self._api_client = api_client
        self._poll_interval = poll_interval
        self._max_hold_seconds = max_hold_seconds

    @property
    def name(self) -> str:
        return SOURCE_NAME

    async def run(self, emit: Callable[[AlertEvent], Awaitable[None]]) -> None:
        await self._seed_from_history(emit)

        logger.info('HFC input running, polling every %ss', self._poll_interval)

        while True:
            try:
                data = await self._api_client.get_live_alerts()
                state = _classify_for_event(data)
                event = AlertEvent(source=SOURCE_NAME, state=state, data=data)
                await emit(event)
            except Exception:
                logger.exception('Error during HFC poll')
            await asyncio.sleep(self._poll_interval)

    async def _seed_from_history(self, emit: Callable[[AlertEvent], Awaitable[None]]) -> None:
        """Seed initial state from history to handle restart during active alert."""
        try:
            recent = await self._api_client.get_recent_alerts_from_history(max_age_seconds=int(self._max_hold_seconds))
            if not recent:
                return

            now_mono = time.monotonic()
            now_wall = datetime.datetime.now()

            for alert_data in recent:
                alert_time = now_mono
                alert_date_str = alert_data.get('alertDate', '')
                if alert_date_str:
                    try:
                        alert_dt = datetime.datetime.fromisoformat(alert_date_str.replace('Z', '').split('+')[0])
                        age_seconds = (now_wall - alert_dt).total_seconds()
                        if age_seconds > 0:
                            alert_time = now_mono - age_seconds
                    except (ValueError, TypeError):
                        pass

                state = _classify_for_event(alert_data)
                event = AlertEvent(source=SOURCE_NAME, state=state, data=alert_data, alert_time=alert_time)
                await emit(event)

            logger.info('Startup: seeded from %d recent history alert(s)', len(recent))
        except Exception:
            logger.debug('Startup: history check failed', exc_info=True)
