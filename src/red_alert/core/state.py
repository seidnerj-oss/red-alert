"""
Alert state classification for the Home Front Command API.

Four states:
    ROUTINE    - No active alerts for configured areas
    PRE_ALERT  - Category 14: imminent alert warning ("בדקות הקרובות")
    ALERT      - Active alert (categories 1-7, 10) for configured areas
    ALL_CLEAR  - Explicit all-clear signal (category 13) from the API
"""

import enum
import time
from collections.abc import Callable

from red_alert.core.utils import standardize_name

# Alert categories that represent active threats (not drills, not pre-alerts)
ACTIVE_ALERT_CATEGORIES = {1, 2, 3, 4, 5, 6, 7, 10}
PRE_ALERT_CATEGORY = 14
ALL_CLEAR_CATEGORY = 13
DRILL_CATEGORY_MIN = 100

# Hebrew title phrases that indicate a pre-alert regardless of category code
PRE_ALERT_TITLE_PHRASES = ('בדקות הקרובות', 'עדכון', 'שהייה בסמיכות למרחב מוגן')


class AlertState(enum.Enum):
    ROUTINE = 'routine'
    PRE_ALERT = 'pre_alert'
    ALERT = 'alert'
    ALL_CLEAR = 'all_clear'


DEFAULT_HOLD_SECONDS: dict[str, float] = {
    'alert': 60,
    'pre_alert': 60,
    'all_clear': 60,
}


class AlertStateTracker:
    """Classifies alert data into one of four states, filtered by areas of interest.

    Supports per-state hold durations: when the API goes empty after being in a
    non-ROUTINE state, that state is held for the configured number of seconds
    before returning to ROUTINE. Hold timers reset each time the API confirms
    the same state is still active. State transitions to a different state
    happen immediately regardless of any active hold.
    """

    def __init__(
        self,
        areas_of_interest: list[str] | None = None,
        hold_seconds: dict[str, float] | None = None,
        logger: Callable | None = None,
    ):
        """
        Args:
            areas_of_interest: City/area names to monitor. If empty/None, all areas match.
            hold_seconds: Per-state hold duration in seconds. Keys are state names
                ('alert', 'pre_alert', 'all_clear'). When the API goes empty, the
                current state is held for this duration before returning to ROUTINE.
                Default: {'all_clear': 60}. ROUTINE never has a hold.
            logger: Optional logging callback with signature ``logger(msg, level='INFO')``.
        """
        self._areas = [standardize_name(a) for a in (areas_of_interest or [])]
        merged = {**DEFAULT_HOLD_SECONDS, **(hold_seconds or {})}
        self._hold: dict[AlertState, float] = {}
        for key, seconds in merged.items():
            state = AlertState(key)
            if state != AlertState.ROUTINE and seconds:
                self._hold[state] = seconds
        self._state_entered_time: float | None = None
        self.state = AlertState.ROUTINE
        self.alert_data: dict | None = None
        self._log = logger

    def _emit(self, msg: str, level: str = 'INFO') -> None:
        """Emit a log message via the optional logger callback."""
        if self._log:
            self._log(msg, level=level)

    def update(self, data: dict | None) -> AlertState:
        """Classify alert data and return the new state.

        Args:
            data: Parsed JSON from the alerts API, or None if no alert.

        Returns:
            The current AlertState after classification.
        """
        old_state = self.state

        if not data or not isinstance(data, dict):
            self._handle_empty()
        else:
            self._classify(data)

        if self.state != old_state:
            self._log_transition(old_state, data)

        return self.state

    def _classify(self, data: dict) -> None:
        """Classify non-empty alert data and update internal state."""
        cat = self._parse_category(data.get('cat'))

        # All-clear overrides everything (regardless of areas of interest)
        if cat == ALL_CLEAR_CATEGORY:
            self._set_state(AlertState.ALL_CLEAR, data)
            return

        cities = data.get('data', [])
        if not cities:
            self._handle_empty()
            return

        # Check if alert is relevant to our areas of interest
        if not self._matches_areas(cities):
            self._handle_empty()
            return

        is_pre_alert = cat == PRE_ALERT_CATEGORY or self._has_pre_alert_title(data.get('title', ''))

        if is_pre_alert:
            self._set_state(AlertState.PRE_ALERT, data)
        elif cat in ACTIVE_ALERT_CATEGORIES:
            self._set_state(AlertState.ALERT, data)
        else:
            # Drills, updates, etc. - don't change state to alert
            self.state = AlertState.ROUTINE
            self.alert_data = None
            self._state_entered_time = None

    def _log_transition(self, old_state: AlertState, data: dict | None) -> None:
        """Log a state transition with relevant context."""
        if self.state == AlertState.ROUTINE:
            self._emit(f'State: {old_state.value} -> routine')
        elif data and isinstance(data, dict):
            cat = data.get('cat', '?')
            title = data.get('title', '')
            cities = data.get('data', [])
            city_preview = ', '.join(str(c) for c in cities[:5])
            if len(cities) > 5:
                city_preview += f' ... (+{len(cities) - 5} more)'
            self._emit(f"State: {old_state.value} -> {self.state.value} (cat={cat}, title='{title}', cities=[{city_preview}])")
        else:
            self._emit(f'State: {old_state.value} -> {self.state.value}')

    def _set_state(self, state: AlertState, data: dict):
        """Set a non-ROUTINE state and record/reset the hold timer."""
        self.state = state
        self.alert_data = data
        self._state_entered_time = time.monotonic()

    def _handle_empty(self) -> None:
        """Handle empty/None API response, respecting per-state hold durations."""
        if self.state != AlertState.ROUTINE and self._state_entered_time:
            hold = self._hold.get(self.state)
            if hold:
                elapsed = time.monotonic() - self._state_entered_time
                if elapsed < hold:
                    return
                self._emit(f'Hold expired for {self.state.value} after {elapsed:.1f}s')

        self.state = AlertState.ROUTINE
        self.alert_data = None
        self._state_entered_time = None

    def _matches_areas(self, cities: list) -> bool:
        """Check if any alert city/area matches configured areas of interest."""
        if not self._areas:
            return True  # No filter = all areas
        alert_names = [standardize_name(c) for c in cities]
        matched = any(area in alert_names for area in self._areas)
        if matched:
            matching = [c for c, n in zip(cities, alert_names) if n in self._areas]
            self._emit(f'Area match: {matching}')
        else:
            self._emit(f'Alert filtered: cities {cities[:10]} did not match configured areas')
        return matched

    @staticmethod
    def _has_pre_alert_title(title: str) -> bool:
        """Check if the alert title contains Hebrew pre-alert phrases."""
        if not isinstance(title, str) or not title:
            return False
        return any(phrase in title for phrase in PRE_ALERT_TITLE_PHRASES)

    @staticmethod
    def _parse_category(cat) -> int:
        """Parse category from string or int."""
        try:
            return int(cat)
        except (TypeError, ValueError):
            return 0
