"""
Telegram alert notification monitor.

Polls the Home Front Command API and sends Telegram messages
on alert state transitions:
    - ROUTINE -> ALERT:     alert notification with cities
    - ROUTINE -> PRE_ALERT: pre-alert warning
    - ALERT -> ALL_CLEAR:   explicit all-clear
    - ALERT -> ROUTINE:     alert ended (hold expired)
    - PRE_ALERT -> ALERT:   alert escalation

Usage:
    python -m red_alert.integrations.outputs.telegram --config config.json
"""

import asyncio
import logging

import httpx

from red_alert.integrations.inputs.hfc.api_client import HomeFrontCommandApiClient
from red_alert.core.constants import ICONS_AND_EMOJIS
from red_alert.core.state import AlertState, AlertStateTracker
from red_alert.integrations.outputs.telegram.bot import TelegramBot, escape_html

logger = logging.getLogger('red_alert.telegram')

API_URLS = {
    'live': 'https://www.oref.org.il/WarningMessages/alert/alerts.json',
    'history': 'https://www.oref.org.il/WarningMessages/alert/History/AlertsHistory.json',
}

SESSION_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (compatible; red-alert/4.0; Telegram)',
    'Referer': 'https://www.oref.org.il/',
    'X-Requested-With': 'XMLHttpRequest',
    'Accept': 'application/json',
    'Accept-Encoding': 'gzip, deflate, br',
    'Accept-Language': 'he,en;q=0.9',
    'Connection': 'keep-alive',
    'Pragma': 'no-cache',
    'Cache-Control': 'no-cache',
}

DEFAULT_CONFIG: dict = {
    'interval': 1,
    'hold_seconds': {},
    'areas_of_interest': [],
    'bot_token': None,
    'chat_id': None,
}


def format_alert_message(data: dict, state: AlertState) -> str:
    """Format an alert notification as Telegram HTML."""
    cat = 0
    try:
        cat = int(data.get('cat', 0))
    except (TypeError, ValueError):
        pass

    _, emoji = ICONS_AND_EMOJIS.get(cat, ('mdi:alert', '❗'))
    title = data.get('title', 'Alert')
    desc = data.get('desc', '')
    cities = data.get('data', [])

    lines = [f'{emoji} <b>{escape_html(str(title))}</b>']
    if cities:
        city_names = [escape_html(str(c)) for c in cities]
        lines.append(', '.join(city_names))
    if desc:
        lines.append(f'<i>{escape_html(str(desc))}</i>')

    return '\n'.join(lines)


def format_all_clear_message() -> str:
    return '✅ <b>All clear</b>'


def format_alert_ended_message() -> str:
    return '✅ <b>Alert ended</b>'


class TelegramAlertMonitor:
    """Polls the Home Front Command API and sends Telegram messages on state changes."""

    def __init__(
        self,
        api_client: HomeFrontCommandApiClient,
        bot: TelegramBot,
        state_tracker: AlertStateTracker,
    ):
        self._api_client = api_client
        self._bot = bot
        self._state = state_tracker
        self._previous_state: AlertState = AlertState.ROUTINE

    @property
    def alert_state(self) -> AlertState:
        return self._state.state

    async def poll(self) -> AlertState:
        """Poll the API, update state, and send a message if state changed."""
        data = await self._api_client.get_live_alerts()
        new_state = self._state.update(data)

        if new_state != self._previous_state:
            await self._on_state_change(self._previous_state, new_state, self._state.alert_data)
            self._previous_state = new_state

        return new_state

    async def _on_state_change(self, old: AlertState, new: AlertState, alert_data: dict | None):
        """Send a Telegram notification based on the state transition."""
        message: str | None = None

        if new in (AlertState.ALERT, AlertState.PRE_ALERT) and alert_data:
            message = format_alert_message(alert_data, new)
        elif new == AlertState.ALL_CLEAR:
            message = format_all_clear_message()
        elif new == AlertState.ROUTINE and old in (AlertState.ALERT, AlertState.PRE_ALERT):
            message = format_alert_ended_message()

        if message:
            logger.info('State changed: %s -> %s, sending notification', old.value, new.value)
            await self._bot.send_message(message)


async def run_monitor(config: dict):
    """Main loop: create components and poll indefinitely."""
    cfg = {**DEFAULT_CONFIG, **config}

    if not cfg['bot_token']:
        logger.error('No bot token configured. Set "bot_token" in config.')
        return

    if not cfg['chat_id']:
        logger.error('No chat ID configured. Set "chat_id" in config.')
        return

    http_client = httpx.AsyncClient(headers=SESSION_HEADERS, timeout=15.0)
    api_client = HomeFrontCommandApiClient(http_client, API_URLS, logger)
    state_tracker = AlertStateTracker(areas_of_interest=cfg.get('areas_of_interest'), hold_seconds=cfg.get('hold_seconds'), logger=logger)
    bot = TelegramBot(token=cfg['bot_token'], chat_id=cfg['chat_id'])

    monitor = TelegramAlertMonitor(api_client, bot, state_tracker)
    interval = cfg['interval']

    logger.info(
        'Starting Telegram monitor: chat_id=%s, polling every %ss, areas=%s',
        cfg['chat_id'],
        interval,
        cfg.get('areas_of_interest') or 'all',
    )

    try:
        while True:
            try:
                state = await monitor.poll()
                logger.debug('State: %s', state.value)
            except Exception:
                logger.exception('Error during poll cycle')
            await asyncio.sleep(interval)
    finally:
        await bot.close()
        await http_client.aclose()
