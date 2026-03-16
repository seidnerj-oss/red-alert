"""CBS message history - local persistence for startup state recovery.

CBS messages are ephemeral push events with no external history endpoint.
This module persists received messages to a local JSON file so the CBS
monitor can recover its state after a restart.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone

from red_alert.core.state import AlertState
from red_alert.integrations.inputs.cbs.parser import CbsMessage

logger = logging.getLogger('red_alert.cbs.history')

DEFAULT_MAX_AGE_SECONDS = 3600


class CbsHistory:
    """Persists CBS messages to a local JSON file for state recovery on startup."""

    def __init__(self, path: str, max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS):
        self._path = path
        self._max_age = max_age_seconds
        self._entries: list[dict] = []
        self._load()

    def _load(self) -> None:
        """Load existing history from disk.

        Tries the main file first, then falls back to .prev (in case a crash
        happened between the two renames in _save).
        """
        for path in (self._path, self._path + '.prev'):
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self._entries = data
                    self._prune()
                    logger.info('Loaded %d CBS history entries from %s', len(self._entries), path)
                    return
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                continue
        self._entries = []

    def _save(self) -> None:
        """Write history to disk using atomic rename for crash safety.

        Writes to a temporary file first, then renames into place.
        This ensures the history file is never partially written.
        """
        try:
            dir_path = os.path.dirname(self._path)
            os.makedirs(dir_path, exist_ok=True)

            next_path = self._path + '.next'
            prev_path = self._path + '.prev'

            with open(next_path, 'w', encoding='utf-8') as f:
                json.dump(self._entries, f, ensure_ascii=False, indent=2)

            if os.path.exists(self._path):
                os.replace(self._path, prev_path)
            os.replace(next_path, self._path)

            if os.path.exists(prev_path):
                os.remove(prev_path)
        except OSError as e:
            logger.warning('Failed to save CBS history: %s', e)

    def _prune(self) -> None:
        """Remove entries older than max_age_seconds."""
        cutoff = time.time() - self._max_age
        before = len(self._entries)
        self._entries = [e for e in self._entries if e.get('timestamp', 0) > cutoff]
        pruned = before - len(self._entries)
        if pruned > 0:
            logger.debug('Pruned %d expired CBS history entries', pruned)

    def record(self, message: CbsMessage, state: AlertState) -> None:
        """Record a CBS message to history."""
        now = time.time()
        entry = {
            'timestamp': now,
            'datetime': datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
            'message_id': message.message_id,
            'serial_number': message.serial_number,
            'message_code': message.message_code,
            'total_pages': message.total_pages,
            'text': message.text,
            'state': state.value,
        }
        self._entries.append(entry)
        self._prune()
        self._save()

    def get_recent(self, max_age_seconds: int | None = None) -> list[dict]:
        """Get recent history entries within the specified age window.

        Args:
            max_age_seconds: Maximum age in seconds. Defaults to the configured max_age.

        Returns:
            List of history entries, most recent first.
        """
        cutoff = time.time() - (max_age_seconds if max_age_seconds is not None else self._max_age)
        recent = [e for e in self._entries if e.get('timestamp', 0) > cutoff]
        recent.sort(key=lambda e: e.get('timestamp', 0), reverse=True)
        return recent

    def get_latest_state(self, max_age_seconds: int | None = None) -> tuple[AlertState, float] | None:
        """Get the most recent alert state from history.

        Returns:
            Tuple of (AlertState, unix_timestamp) for the most recent entry,
            or None if no recent history.
        """
        recent = self.get_recent(max_age_seconds)
        if not recent:
            return None
        entry = recent[0]
        try:
            state = AlertState(entry['state'])
            return (state, entry['timestamp'])
        except (KeyError, ValueError):
            return None
