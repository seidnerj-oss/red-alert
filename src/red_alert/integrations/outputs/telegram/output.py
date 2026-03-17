"""
Telegram alert output for the orchestrator.

Receives AlertEvents from any input and sends Telegram notifications
on state transitions. Maintains its own MultiSourceStateTracker for
area filtering and per-source hold timers.
"""

from __future__ import annotations

import logging

from red_alert.core.orchestrator import AlertEvent, AlertOutput, MultiSourceStateTracker
from red_alert.core.state import AlertState
from red_alert.integrations.outputs.telegram.bot import TelegramBot
from red_alert.integrations.outputs.telegram.server import format_alert_message, format_all_clear_message, format_alert_ended_message

logger = logging.getLogger('red_alert.output.telegram')


class TelegramOutput(AlertOutput):
    """Orchestrator output that sends Telegram notifications on state changes."""

    def __init__(self, config: dict):
        self._config = config
        self._bot: TelegramBot | None = None
        self._tracker: MultiSourceStateTracker | None = None
        self._previous_state = AlertState.ROUTINE

    @property
    def name(self) -> str:
        return 'telegram'

    async def start(self) -> None:
        cfg = self._config
        if not cfg.get('bot_token'):
            raise ValueError('Telegram bot_token is required')
        if not cfg.get('chat_id'):
            raise ValueError('Telegram chat_id is required')

        self._bot = TelegramBot(token=cfg['bot_token'], chat_id=cfg['chat_id'])
        self._tracker = MultiSourceStateTracker(
            areas_of_interest=cfg.get('areas_of_interest'),
            hold_seconds=cfg.get('hold_seconds'),
            logger=logger,
        )

        logger.info('Telegram output started: chat_id=%s, areas=%s', cfg['chat_id'], cfg.get('areas_of_interest') or 'all')

    async def handle_event(self, event: AlertEvent) -> None:
        if not self._tracker or not self._bot:
            return

        old_merged, new_merged = self._tracker.update(event)

        if new_merged != self._previous_state:
            await self._on_state_change(self._previous_state, new_merged, self._tracker.alert_data)
            self._previous_state = new_merged

    async def _on_state_change(self, old: AlertState, new: AlertState, alert_data: dict | None) -> None:
        assert self._bot is not None

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

    async def stop(self) -> None:
        if self._bot:
            await self._bot.close()
