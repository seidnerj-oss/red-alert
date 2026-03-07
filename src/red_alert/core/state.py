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


class AlertStateTracker:
    """Classifies alert data into one of four states, filtered by areas of interest.

    Supports an optional cooldown: when the API goes empty after an alert,
    the alert state is held for ``cooldown_seconds`` before returning to ROUTINE.
    An explicit all-clear (category 13) always resets immediately.
    """

    def __init__(self, areas_of_interest: list[str] | None = None, cooldown_seconds: float | None = None):
        """
        Args:
            areas_of_interest: City/area names to monitor. If empty/None, all areas match.
            cooldown_seconds: Seconds to hold alert state after API goes empty. None = no cooldown.
        """
        self._areas = [standardize_name(a) for a in (areas_of_interest or [])]
        self._cooldown = cooldown_seconds
        self._last_alert_time: float | None = None
        self.state = AlertState.ROUTINE
        self.alert_data: dict | None = None

    def update(self, data: dict | None) -> AlertState:
        """Classify alert data and return the new state.

        Args:
            data: Parsed JSON from the alerts API, or None if no alert.

        Returns:
            The current AlertState after classification.
        """
        if not data or not isinstance(data, dict):
            return self._handle_empty()

        cat = self._parse_category(data.get('cat'))

        # All-clear overrides everything (regardless of areas of interest)
        if cat == ALL_CLEAR_CATEGORY:
            self.state = AlertState.ALL_CLEAR
            self.alert_data = data
            self._last_alert_time = None
            return self.state

        cities = data.get('data', [])
        if not cities:
            return self._handle_empty()

        # Check if alert is relevant to our areas of interest
        if not self._matches_areas(cities):
            return self._handle_empty()

        is_pre_alert = cat == PRE_ALERT_CATEGORY or self._has_pre_alert_title(data.get('title', ''))

        if is_pre_alert:
            self.state = AlertState.PRE_ALERT
            self.alert_data = data
            self._last_alert_time = time.monotonic()
        elif cat in ACTIVE_ALERT_CATEGORIES:
            self.state = AlertState.ALERT
            self.alert_data = data
            self._last_alert_time = time.monotonic()
        else:
            # Drills, updates, etc. - don't change state to alert
            self.state = AlertState.ROUTINE
            self.alert_data = None

        return self.state

    def _handle_empty(self) -> AlertState:
        """Handle empty/None API response, respecting cooldown if configured."""
        if self._cooldown and self._last_alert_time and self.state in (AlertState.ALERT, AlertState.PRE_ALERT):
            elapsed = time.monotonic() - self._last_alert_time
            if elapsed < self._cooldown:
                return self.state
        self.state = AlertState.ROUTINE
        self.alert_data = None
        self._last_alert_time = None
        return self.state

    def _matches_areas(self, cities: list) -> bool:
        """Check if any alert city/area matches configured areas of interest."""
        if not self._areas:
            return True  # No filter = all areas
        alert_names = [standardize_name(c) for c in cities]
        return any(area in alert_names for area in self._areas)

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
