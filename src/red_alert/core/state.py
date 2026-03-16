"""
Alert state classification for the Home Front Command API.

Four states:
    ROUTINE    - No active alerts for configured areas
    PRE_ALERT  - Category 14 or title phrases ("בדקות הקרובות", "עדכון", etc.)
    ALERT      - Active alert (categories 1-7, 10) for configured areas
    ALL_CLEAR  - Category 13 or title phrase ("האירוע הסתיים")

Classification: ALL_CLEAR title/category takes priority over category-based alert
classification. All states (including ALL_CLEAR) are filtered by areas of interest
when configured - if the alert cities don't match your areas, the state won't change.

The HFC live alerts API sends brief pulses (typically 30-60 seconds), not persistent
state. Each alert type - pre-alert, active alert, and all-clear - appears as a short
pulse and then disappears. There can be significant gaps between pulses (e.g., 14
minutes observed between a pre-alert disappearing and the all-clear arriving). Hold
timers bridge these gaps so downstream consumers maintain the correct state.
"""

import enum
import logging
import time

from red_alert.core.utils import standardize_name

# Alert categories that represent active threats (not drills, not pre-alerts).
# See constants.py for full category code documentation.
ACTIVE_ALERT_CATEGORIES = {1, 2, 3, 4, 5, 6, 7, 10}
PRE_ALERT_CATEGORY = 14  # "בדקות הקרובות צפויות להתקבל התרעות באזורך"
ALL_CLEAR_CATEGORY = 13  # "האירוע הסתיים" (event ended)
DRILL_CATEGORY_MIN = 100  # Drills use 100 + threat code (e.g. 101 = missile drill)

# Hebrew title phrases that indicate a pre-alert regardless of category code
PRE_ALERT_TITLE_PHRASES = ('בדקות הקרובות', 'עדכון', 'שהייה בסמיכות למרחב מוגן')

# Hebrew title phrases that indicate all-clear regardless of category code
ALL_CLEAR_TITLE_PHRASES = ('האירוע הסתיים',)


class AlertState(enum.Enum):
    ROUTINE = 'routine'
    PRE_ALERT = 'pre_alert'
    ALERT = 'alert'
    ALL_CLEAR = 'all_clear'


# Hold durations: how long to maintain a non-ROUTINE state after the API goes empty.
# The HFC API sends brief pulses (~30-60s), so holds bridge the gap until the next
# signal arrives. Observed real-world gap between pre-alert pulse and all-clear pulse
# was ~14 minutes, so alert/pre_alert use 30 minutes. All-clear is a terminal state
# that just needs a short display window before returning to routine.
DEFAULT_HOLD_SECONDS: dict[str, float] = {
    'alert': 1800,
    'pre_alert': 1800,
    'all_clear': 300,
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
        logger: logging.Logger | None = None,
    ):
        """
        Args:
            areas_of_interest: City/area names to monitor. If empty/None, all areas match.
            hold_seconds: Per-state hold duration in seconds. Keys are state names
                ('alert', 'pre_alert', 'all_clear'). When the API goes empty, the
                current state is held for this duration before returning to ROUTINE.
                Default: alert/pre_alert=1800 (30min), all_clear=300 (5min). ROUTINE never has a hold.
            logger: Optional logger instance.
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
        self._logger = logger
        self._last_area_result: bool | None = None

    def _log(self, msg: str) -> None:
        if self._logger:
            self._logger.info(msg)

    def update(self, data: dict | None, alert_time: float | None = None) -> AlertState:
        """Classify alert data and return the new state.

        Args:
            data: Parsed JSON from the alerts API, or None if no alert.
            alert_time: Optional monotonic timestamp of when the alert originally occurred.
                When provided (e.g., from history on startup), the hold timer is anchored
                to this time instead of now. If the hold has already expired, the state
                transitions directly to ROUTINE.

        Returns:
            The current AlertState after classification.
        """
        old_state = self.state

        if not data or not isinstance(data, dict):
            self._handle_empty()
            self._last_area_result = None
        else:
            self._classify(data)
            if alert_time is not None and self.state != AlertState.ROUTINE:
                self._state_entered_time = alert_time

        if self.state != old_state:
            self._log_transition(old_state, data)

        # If seeded with a past alert_time, the hold may already be expired
        if alert_time is not None and self.state != AlertState.ROUTINE:
            self._handle_empty()

        return self.state

    def _classify(self, data: dict) -> None:
        """Classify non-empty alert data and update internal state."""
        cat = self._parse_category(data.get('cat'))
        title = data.get('title', '')
        is_all_clear = cat == ALL_CLEAR_CATEGORY or self._has_all_clear_title(title)

        cities = data.get('data', [])
        if not cities:
            # All-clear without city data is only relevant when no area filter is active
            if is_all_clear and not self._areas:
                self._set_state(AlertState.ALL_CLEAR, data)
                return
            self._handle_empty()
            return

        # Check if alert is relevant to our areas of interest
        if not self._matches_areas(cities):
            self._handle_empty()
            return

        # Classify by type (all-clear takes priority over category-based classification)
        if is_all_clear:
            self._set_state(AlertState.ALL_CLEAR, data)
        elif cat == PRE_ALERT_CATEGORY or self._has_pre_alert_title(title):
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
            self._log(f'State: {old_state.value} -> routine')
        elif data and isinstance(data, dict):
            cat = data.get('cat', '?')
            title = data.get('title', '')
            cities = data.get('data', [])
            city_preview = ', '.join(str(c) for c in cities[:5])
            if len(cities) > 5:
                city_preview += f' ... (+{len(cities) - 5} more)'
            self._log(f"State: {old_state.value} -> {self.state.value} (cat={cat}, title='{title}', cities=[{city_preview}])")
        else:
            self._log(f'State: {old_state.value} -> {self.state.value}')

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
                self._log(f'Hold expired for {self.state.value} after {elapsed:.1f}s')

        self.state = AlertState.ROUTINE
        self.alert_data = None
        self._state_entered_time = None

    def _matches_areas(self, cities: list) -> bool:
        """Check if any alert city/area matches configured areas of interest."""
        if not self._areas:
            return True  # No filter = all areas
        alert_names = [standardize_name(c) for c in cities]
        matched = any(area in alert_names for area in self._areas)
        if matched != self._last_area_result:
            if matched:
                matching = [c for c, n in zip(cities, alert_names) if n in self._areas]
                self._log(f'Area match: {matching}')
            else:
                self._log(f'Alert filtered: cities {cities[:10]} did not match configured areas')
            self._last_area_result = matched
        return matched

    @staticmethod
    def _has_pre_alert_title(title: str) -> bool:
        """Check if the alert title contains Hebrew pre-alert phrases."""
        if not isinstance(title, str) or not title:
            return False
        return any(phrase in title for phrase in PRE_ALERT_TITLE_PHRASES)

    @staticmethod
    def _has_all_clear_title(title: str) -> bool:
        """Check if the alert title contains Hebrew all-clear phrases."""
        if not isinstance(title, str) or not title:
            return False
        return any(phrase in title for phrase in ALL_CLEAR_TITLE_PHRASES)

    @staticmethod
    def _parse_category(cat) -> int:
        """Parse category from string or int."""
        try:
            return int(cat)
        except (TypeError, ValueError):
            return 0
