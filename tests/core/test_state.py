from unittest.mock import patch

from red_alert.core.state import ACTIVE_ALERT_CATEGORIES, ALL_CLEAR_CATEGORY, AlertState, AlertStateTracker


class TestAlertStateTracker:
    def test_initial_state_is_routine(self):
        tracker = AlertStateTracker()
        assert tracker.state == AlertState.ROUTINE

    def test_none_data_is_routine(self):
        tracker = AlertStateTracker()
        assert tracker.update(None) == AlertState.ROUTINE

    def test_empty_dict_is_routine(self):
        tracker = AlertStateTracker()
        assert tracker.update({}) == AlertState.ROUTINE

    def test_empty_data_list_is_routine(self):
        tracker = AlertStateTracker()
        assert tracker.update({'cat': '1', 'data': []}) == AlertState.ROUTINE

    def test_active_alert_category_1(self):
        tracker = AlertStateTracker()
        result = tracker.update({'cat': '1', 'data': ['City A'], 'title': 'Rockets'})
        assert result == AlertState.ALERT
        assert tracker.alert_data is not None

    def test_active_alert_all_categories(self):
        tracker = AlertStateTracker()
        for cat in ACTIVE_ALERT_CATEGORIES:
            result = tracker.update({'cat': str(cat), 'data': ['City A']})
            assert result == AlertState.ALERT, f'Category {cat} should be ALERT'

    def test_pre_alert_category_14(self):
        tracker = AlertStateTracker()
        result = tracker.update({'cat': '14', 'data': ['Area X'], 'title': 'הנחיה מקדימה'})
        assert result == AlertState.PRE_ALERT
        assert tracker.alert_data is not None

    def test_drill_category_is_routine(self):
        tracker = AlertStateTracker()
        result = tracker.update({'cat': '101', 'data': ['City A']})
        assert result == AlertState.ROUTINE

    def test_unknown_category_is_routine(self):
        tracker = AlertStateTracker()
        result = tracker.update({'cat': '999', 'data': ['City A']})
        assert result == AlertState.ROUTINE

    def test_invalid_category_is_routine(self):
        tracker = AlertStateTracker()
        result = tracker.update({'cat': 'abc', 'data': ['City A']})
        assert result == AlertState.ROUTINE

    def test_category_as_int(self):
        tracker = AlertStateTracker()
        result = tracker.update({'cat': 1, 'data': ['City A']})
        assert result == AlertState.ALERT

    def test_returns_to_routine_after_alert(self):
        tracker = AlertStateTracker()
        tracker.update({'cat': '1', 'data': ['City A']})
        assert tracker.state == AlertState.ALERT

        tracker.update(None)
        assert tracker.state == AlertState.ROUTINE
        assert tracker.alert_data is None


class TestAreasOfInterest:
    def test_no_filter_matches_all(self):
        tracker = AlertStateTracker()
        result = tracker.update({'cat': '1', 'data': ['Any City']})
        assert result == AlertState.ALERT

    def test_empty_list_matches_all(self):
        tracker = AlertStateTracker(areas_of_interest=[])
        result = tracker.update({'cat': '1', 'data': ['Any City']})
        assert result == AlertState.ALERT

    def test_matching_area(self):
        tracker = AlertStateTracker(areas_of_interest=['כפר סבא'])
        result = tracker.update({'cat': '1', 'data': ['כפר סבא', 'תל אביב']})
        assert result == AlertState.ALERT

    def test_non_matching_area(self):
        tracker = AlertStateTracker(areas_of_interest=['כפר סבא'])
        result = tracker.update({'cat': '1', 'data': ['תל אביב', 'חיפה']})
        assert result == AlertState.ROUTINE

    def test_pre_alert_matching_area(self):
        tracker = AlertStateTracker(areas_of_interest=['כפר סבא'])
        result = tracker.update({'cat': '14', 'data': ['כפר סבא']})
        assert result == AlertState.PRE_ALERT

    def test_pre_alert_non_matching_area(self):
        tracker = AlertStateTracker(areas_of_interest=['כפר סבא'])
        result = tracker.update({'cat': '14', 'data': ['חיפה']})
        assert result == AlertState.ROUTINE

    def test_multiple_areas_of_interest(self):
        tracker = AlertStateTracker(areas_of_interest=['כפר סבא', 'רעננה'])
        result = tracker.update({'cat': '1', 'data': ['רעננה']})
        assert result == AlertState.ALERT

    def test_name_standardization(self):
        tracker = AlertStateTracker(areas_of_interest=['תל אביב - מרכז העיר'])
        result = tracker.update({'cat': '1', 'data': ['תל אביב - מרכז העיר']})
        assert result == AlertState.ALERT


class TestPreAlertTitleDetection:
    """Pre-alert detected by Hebrew title phrases, regardless of category code."""

    def test_pre_alert_by_title_bdakot(self):
        tracker = AlertStateTracker()
        result = tracker.update({'cat': '1', 'data': ['City A'], 'title': 'בדקות הקרובות צפויות להתקבל התרעות באזורך'})
        assert result == AlertState.PRE_ALERT

    def test_pre_alert_by_title_update(self):
        tracker = AlertStateTracker()
        result = tracker.update({'cat': '1', 'data': ['City A'], 'title': 'עדכון מיוחד'})
        assert result == AlertState.PRE_ALERT

    def test_pre_alert_by_title_shelter(self):
        tracker = AlertStateTracker()
        result = tracker.update({'cat': '1', 'data': ['City A'], 'title': 'שהייה בסמיכות למרחב מוגן'})
        assert result == AlertState.PRE_ALERT

    def test_non_pre_alert_title_stays_alert(self):
        tracker = AlertStateTracker()
        result = tracker.update({'cat': '1', 'data': ['City A'], 'title': 'ירי רקטות וטילים'})
        assert result == AlertState.ALERT

    def test_pre_alert_title_with_non_alert_category(self):
        tracker = AlertStateTracker()
        result = tracker.update({'cat': '999', 'data': ['City A'], 'title': 'בדקות הקרובות צפויות התרעות'})
        assert result == AlertState.PRE_ALERT

    def test_no_title_field_uses_category_only(self):
        tracker = AlertStateTracker()
        result = tracker.update({'cat': '14', 'data': ['City A']})
        assert result == AlertState.PRE_ALERT

    def test_empty_title_uses_category_only(self):
        tracker = AlertStateTracker()
        result = tracker.update({'cat': '1', 'data': ['City A'], 'title': ''})
        assert result == AlertState.ALERT


class TestAllClear:
    """Category 13 is an explicit all-clear signal from the API."""

    def test_category_13_is_all_clear(self):
        tracker = AlertStateTracker()
        result = tracker.update({'cat': '13', 'data': ['City A']})
        assert result == AlertState.ALL_CLEAR
        assert tracker.alert_data is not None

    def test_category_13_as_int(self):
        tracker = AlertStateTracker()
        result = tracker.update({'cat': 13, 'data': ['City A']})
        assert result == AlertState.ALL_CLEAR

    def test_all_clear_constant_is_13(self):
        assert ALL_CLEAR_CATEGORY == 13

    def test_all_clear_overrides_areas_of_interest(self):
        """All-clear applies globally, not filtered by configured areas."""
        tracker = AlertStateTracker(areas_of_interest=['כפר סבא'])
        result = tracker.update({'cat': '13', 'data': ['תל אביב']})
        assert result == AlertState.ALL_CLEAR

    def test_all_clear_after_alert(self):
        tracker = AlertStateTracker()
        tracker.update({'cat': '1', 'data': ['City A']})
        assert tracker.state == AlertState.ALERT

        result = tracker.update({'cat': '13', 'data': ['City A']})
        assert result == AlertState.ALL_CLEAR

    def test_all_clear_transitions_to_routine_on_empty(self):
        tracker = AlertStateTracker()
        tracker.update({'cat': '13', 'data': ['City A']})
        assert tracker.state == AlertState.ALL_CLEAR

        tracker.update(None)
        assert tracker.state == AlertState.ROUTINE

    def test_all_clear_without_data_field(self):
        """All-clear with empty data still triggers ALL_CLEAR (checked before data filtering)."""
        tracker = AlertStateTracker()
        # Cat 13 without data - still parsed before city check
        result = tracker.update({'cat': '13'})
        assert result == AlertState.ALL_CLEAR

    def test_all_clear_not_in_active_categories(self):
        assert ALL_CLEAR_CATEGORY not in ACTIVE_ALERT_CATEGORIES


class TestCooldown:
    """Alert state persistence after API goes empty."""

    def test_no_cooldown_immediate_routine(self):
        tracker = AlertStateTracker()
        tracker.update({'cat': '1', 'data': ['City A']})
        assert tracker.state == AlertState.ALERT

        tracker.update(None)
        assert tracker.state == AlertState.ROUTINE

    def test_cooldown_keeps_alert_active(self):
        tracker = AlertStateTracker(cooldown_seconds=30.0)
        tracker.update({'cat': '1', 'data': ['City A']})
        assert tracker.state == AlertState.ALERT

        # Simulate empty API while still within cooldown window
        with patch('red_alert.core.state.time') as mock_time:
            mock_time.monotonic.return_value = tracker._last_alert_time + 10.0
            result = tracker.update(None)
        assert result == AlertState.ALERT
        assert tracker.state == AlertState.ALERT

    def test_cooldown_expires_to_routine(self):
        tracker = AlertStateTracker(cooldown_seconds=30.0)
        tracker.update({'cat': '1', 'data': ['City A']})
        assert tracker.state == AlertState.ALERT

        # Simulate empty API after cooldown has expired
        with patch('red_alert.core.state.time') as mock_time:
            mock_time.monotonic.return_value = tracker._last_alert_time + 31.0
            result = tracker.update(None)
        assert result == AlertState.ROUTINE

    def test_cooldown_keeps_pre_alert_active(self):
        tracker = AlertStateTracker(cooldown_seconds=30.0)
        tracker.update({'cat': '14', 'data': ['City A']})
        assert tracker.state == AlertState.PRE_ALERT

        with patch('red_alert.core.state.time') as mock_time:
            mock_time.monotonic.return_value = tracker._last_alert_time + 10.0
            result = tracker.update(None)
        assert result == AlertState.PRE_ALERT

    def test_all_clear_bypasses_cooldown(self):
        tracker = AlertStateTracker(cooldown_seconds=30.0)
        tracker.update({'cat': '1', 'data': ['City A']})
        assert tracker.state == AlertState.ALERT

        # All-clear resets immediately, ignoring cooldown
        result = tracker.update({'cat': '13', 'data': ['City A']})
        assert result == AlertState.ALL_CLEAR
        assert tracker._last_alert_time is None

    def test_all_clear_then_empty_goes_routine(self):
        """After all-clear resets cooldown, next empty poll goes to ROUTINE (not held by cooldown)."""
        tracker = AlertStateTracker(cooldown_seconds=30.0)
        tracker.update({'cat': '1', 'data': ['City A']})
        tracker.update({'cat': '13', 'data': ['City A']})
        assert tracker.state == AlertState.ALL_CLEAR

        result = tracker.update(None)
        assert result == AlertState.ROUTINE

    def test_cooldown_does_not_affect_non_alert_states(self):
        """Cooldown only applies to ALERT and PRE_ALERT, not ROUTINE or ALL_CLEAR."""
        tracker = AlertStateTracker(cooldown_seconds=30.0)
        # Start in ROUTINE
        tracker.update(None)
        assert tracker.state == AlertState.ROUTINE

        # Empty poll should still be ROUTINE
        tracker.update(None)
        assert tracker.state == AlertState.ROUTINE

    def test_new_alert_during_cooldown_resets_timer(self):
        tracker = AlertStateTracker(cooldown_seconds=30.0)
        tracker.update({'cat': '1', 'data': ['City A']})
        first_alert_time = tracker._last_alert_time

        # New alert during cooldown resets the timer
        tracker.update({'cat': '1', 'data': ['City A']})
        assert tracker._last_alert_time >= first_alert_time
