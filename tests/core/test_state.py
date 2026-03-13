from unittest.mock import MagicMock, patch

from red_alert.core.state import ACTIVE_ALERT_CATEGORIES, ALL_CLEAR_CATEGORY, DEFAULT_HOLD_SECONDS, AlertState, AlertStateTracker


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

    def test_returns_to_routine_after_alert_hold_expires(self):
        tracker = AlertStateTracker()
        tracker.update({'cat': '1', 'data': ['City A']})
        assert tracker.state == AlertState.ALERT

        # Within hold period - stays ALERT
        with patch('red_alert.core.state.time') as mock_time:
            mock_time.monotonic.return_value = tracker._state_entered_time + 30.0
            result = tracker.update(None)
        assert result == AlertState.ALERT

        # After hold period - returns to ROUTINE
        with patch('red_alert.core.state.time') as mock_time:
            mock_time.monotonic.return_value = tracker._state_entered_time + 61.0
            result = tracker.update(None)
        assert result == AlertState.ROUTINE
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

    def test_all_clear_transitions_to_routine_after_hold(self):
        """ALL_CLEAR is held for default 60s, then transitions to ROUTINE."""
        tracker = AlertStateTracker()
        tracker.update({'cat': '13', 'data': ['City A']})
        assert tracker.state == AlertState.ALL_CLEAR

        # Within hold period - stays ALL_CLEAR
        with patch('red_alert.core.state.time') as mock_time:
            mock_time.monotonic.return_value = tracker._state_entered_time + 30.0
            result = tracker.update(None)
        assert result == AlertState.ALL_CLEAR

        # After hold period - returns to ROUTINE
        with patch('red_alert.core.state.time') as mock_time:
            mock_time.monotonic.return_value = tracker._state_entered_time + 61.0
            result = tracker.update(None)
        assert result == AlertState.ROUTINE

    def test_all_clear_without_data_field(self):
        """All-clear with empty data still triggers ALL_CLEAR (checked before data filtering)."""
        tracker = AlertStateTracker()
        # Cat 13 without data - still parsed before city check
        result = tracker.update({'cat': '13'})
        assert result == AlertState.ALL_CLEAR

    def test_all_clear_not_in_active_categories(self):
        assert ALL_CLEAR_CATEGORY not in ACTIVE_ALERT_CATEGORIES

    def test_all_clear_hold_resets_on_repeat(self):
        """Repeated all-clear signals reset the hold timer."""
        tracker = AlertStateTracker()
        tracker.update({'cat': '13', 'data': ['City A']})
        first_time = tracker._state_entered_time

        tracker.update({'cat': '13', 'data': ['City A']})
        assert tracker._state_entered_time >= first_time
        assert tracker.state == AlertState.ALL_CLEAR

    def test_all_clear_interrupted_by_alert(self):
        """A new alert during all-clear hold transitions immediately."""
        tracker = AlertStateTracker()
        tracker.update({'cat': '13', 'data': ['City A']})
        assert tracker.state == AlertState.ALL_CLEAR

        result = tracker.update({'cat': '1', 'data': ['City A']})
        assert result == AlertState.ALERT


class TestHold:
    """Per-state hold durations after API goes empty."""

    def test_default_hold_all_states_60s(self):
        assert DEFAULT_HOLD_SECONDS == {'alert': 60, 'pre_alert': 60, 'all_clear': 60}

    def test_no_hold_immediate_routine(self):
        """Without hold, alert state drops to routine on empty API."""
        tracker = AlertStateTracker(hold_seconds={'alert': 0, 'pre_alert': 0, 'all_clear': 0})
        tracker.update({'cat': '1', 'data': ['City A']})
        assert tracker.state == AlertState.ALERT

        tracker.update(None)
        assert tracker.state == AlertState.ROUTINE

    def test_alert_hold_keeps_alert_active(self):
        tracker = AlertStateTracker(hold_seconds={'alert': 30})
        tracker.update({'cat': '1', 'data': ['City A']})
        assert tracker.state == AlertState.ALERT

        # Simulate empty API while still within hold window
        with patch('red_alert.core.state.time') as mock_time:
            mock_time.monotonic.return_value = tracker._state_entered_time + 10.0
            result = tracker.update(None)
        assert result == AlertState.ALERT
        assert tracker.state == AlertState.ALERT

    def test_alert_hold_expires_to_routine(self):
        tracker = AlertStateTracker(hold_seconds={'alert': 30})
        tracker.update({'cat': '1', 'data': ['City A']})
        assert tracker.state == AlertState.ALERT

        # Simulate empty API after hold has expired
        with patch('red_alert.core.state.time') as mock_time:
            mock_time.monotonic.return_value = tracker._state_entered_time + 31.0
            result = tracker.update(None)
        assert result == AlertState.ROUTINE

    def test_pre_alert_hold(self):
        tracker = AlertStateTracker(hold_seconds={'pre_alert': 20})
        tracker.update({'cat': '14', 'data': ['City A']})
        assert tracker.state == AlertState.PRE_ALERT

        with patch('red_alert.core.state.time') as mock_time:
            mock_time.monotonic.return_value = tracker._state_entered_time + 10.0
            result = tracker.update(None)
        assert result == AlertState.PRE_ALERT

    def test_all_clear_hold(self):
        tracker = AlertStateTracker(hold_seconds={'all_clear': 120})
        tracker.update({'cat': '13', 'data': ['City A']})
        assert tracker.state == AlertState.ALL_CLEAR

        with patch('red_alert.core.state.time') as mock_time:
            mock_time.monotonic.return_value = tracker._state_entered_time + 60.0
            result = tracker.update(None)
        assert result == AlertState.ALL_CLEAR

        with patch('red_alert.core.state.time') as mock_time:
            mock_time.monotonic.return_value = tracker._state_entered_time + 121.0
            result = tracker.update(None)
        assert result == AlertState.ROUTINE

    def test_hold_resets_on_same_state(self):
        """Hold timer resets when API confirms same state is still active."""
        tracker = AlertStateTracker(hold_seconds={'alert': 30})
        tracker.update({'cat': '1', 'data': ['City A']})
        first_time = tracker._state_entered_time

        # New alert during hold resets the timer
        tracker.update({'cat': '1', 'data': ['City A']})
        assert tracker._state_entered_time >= first_time

    def test_hold_does_not_block_state_transition(self):
        """State transitions happen immediately regardless of active hold."""
        tracker = AlertStateTracker(hold_seconds={'alert': 30, 'pre_alert': 30})
        tracker.update({'cat': '1', 'data': ['City A']})
        assert tracker.state == AlertState.ALERT

        # All-clear transitions immediately despite alert hold
        result = tracker.update({'cat': '13', 'data': ['City A']})
        assert result == AlertState.ALL_CLEAR

    def test_alert_hold_then_pre_alert(self):
        """Pre-alert during alert hold transitions immediately."""
        tracker = AlertStateTracker(hold_seconds={'alert': 30})
        tracker.update({'cat': '1', 'data': ['City A']})
        assert tracker.state == AlertState.ALERT

        result = tracker.update({'cat': '14', 'data': ['City A']})
        assert result == AlertState.PRE_ALERT

    def test_hold_does_not_affect_routine(self):
        """ROUTINE has no hold - setting it is ignored."""
        tracker = AlertStateTracker(hold_seconds={'routine': 30})
        tracker.update(None)
        assert tracker.state == AlertState.ROUTINE

        tracker.update(None)
        assert tracker.state == AlertState.ROUTINE

    def test_multiple_states_with_different_holds(self):
        tracker = AlertStateTracker(hold_seconds={'alert': 10, 'pre_alert': 20, 'all_clear': 30})

        # Alert with 10s hold
        tracker.update({'cat': '1', 'data': ['City A']})
        assert tracker.state == AlertState.ALERT
        with patch('red_alert.core.state.time') as mock_time:
            mock_time.monotonic.return_value = tracker._state_entered_time + 11.0
            result = tracker.update(None)
        assert result == AlertState.ROUTINE

        # Pre-alert with 20s hold
        tracker.update({'cat': '14', 'data': ['City A']})
        assert tracker.state == AlertState.PRE_ALERT
        with patch('red_alert.core.state.time') as mock_time:
            mock_time.monotonic.return_value = tracker._state_entered_time + 15.0
            result = tracker.update(None)
        assert result == AlertState.PRE_ALERT

    def test_all_clear_then_empty_respects_hold(self):
        """After alert -> all-clear, the all-clear hold applies."""
        tracker = AlertStateTracker(hold_seconds={'alert': 30, 'all_clear': 60})
        tracker.update({'cat': '1', 'data': ['City A']})
        tracker.update({'cat': '13', 'data': ['City A']})
        assert tracker.state == AlertState.ALL_CLEAR

        # All-clear hold keeps state
        with patch('red_alert.core.state.time') as mock_time:
            mock_time.monotonic.return_value = tracker._state_entered_time + 30.0
            result = tracker.update(None)
        assert result == AlertState.ALL_CLEAR

        # All-clear hold expires
        with patch('red_alert.core.state.time') as mock_time:
            mock_time.monotonic.return_value = tracker._state_entered_time + 61.0
            result = tracker.update(None)
        assert result == AlertState.ROUTINE


class TestLogging:
    """Verify that AlertStateTracker emits structured log messages via its logger callback."""

    def _make_tracker(self, **kwargs):
        mock_logger = MagicMock()
        tracker = AlertStateTracker(logger=mock_logger, hold_seconds={'alert': 0, 'pre_alert': 0, 'all_clear': 0}, **kwargs)
        return tracker, mock_logger

    def test_no_logger_does_not_crash(self):
        tracker = AlertStateTracker()
        tracker.update({'cat': '1', 'data': ['City A']})
        tracker.update(None)

    def test_logs_transition_to_alert(self):
        tracker, log = self._make_tracker()
        tracker.update({'cat': '1', 'data': ['City A'], 'title': 'Rockets'})
        log.assert_called()
        msgs = [call.args[0] for call in log.call_args_list]
        assert any('routine -> alert' in m for m in msgs)
        assert any('cat=1' in m and 'Rockets' in m for m in msgs)

    def test_logs_transition_to_routine(self):
        tracker, log = self._make_tracker()
        tracker.update({'cat': '1', 'data': ['City A']})
        log.reset_mock()
        tracker.update(None)
        log.assert_called()
        msgs = [call.args[0] for call in log.call_args_list]
        assert any('alert -> routine' in m for m in msgs)

    def test_logs_transition_to_pre_alert(self):
        tracker, log = self._make_tracker()
        tracker.update({'cat': '14', 'data': ['City A'], 'title': 'Warning'})
        msgs = [call.args[0] for call in log.call_args_list]
        assert any('routine -> pre_alert' in m for m in msgs)

    def test_logs_transition_to_all_clear(self):
        tracker, log = self._make_tracker()
        tracker.update({'cat': '13', 'data': ['City A']})
        msgs = [call.args[0] for call in log.call_args_list]
        assert any('routine -> all_clear' in m for m in msgs)

    def test_no_log_when_state_unchanged(self):
        tracker, log = self._make_tracker()
        tracker.update(None)  # ROUTINE -> ROUTINE
        log.assert_not_called()

    def test_logs_area_match(self):
        tracker, log = self._make_tracker(areas_of_interest=['City A'])
        tracker.update({'cat': '1', 'data': ['City A', 'City B']})
        msgs = [call.args[0] for call in log.call_args_list]
        assert any('Area match' in m and 'City A' in m for m in msgs)

    def test_logs_area_filtered(self):
        tracker, log = self._make_tracker(areas_of_interest=['City X'])
        tracker.update({'cat': '1', 'data': ['City A', 'City B']})
        msgs = [call.args[0] for call in log.call_args_list]
        assert any('Alert filtered' in m for m in msgs)

    def test_logs_hold_expiry(self):
        tracker = AlertStateTracker(logger=MagicMock(), hold_seconds={'alert': 30, 'pre_alert': 0, 'all_clear': 0})
        mock_logger = tracker._log
        tracker.update({'cat': '1', 'data': ['City A']})
        mock_logger.reset_mock()

        with patch('red_alert.core.state.time') as mock_time:
            mock_time.monotonic.return_value = tracker._state_entered_time + 31.0
            tracker.update(None)

        msgs = [call.args[0] for call in mock_logger.call_args_list]
        assert any('Hold expired' in m and 'alert' in m for m in msgs)

    def test_city_preview_truncated(self):
        tracker, log = self._make_tracker()
        cities = [f'City {i}' for i in range(10)]
        tracker.update({'cat': '1', 'data': cities, 'title': 'Rockets'})
        msgs = [call.args[0] for call in log.call_args_list]
        transition_msg = next(m for m in msgs if 'routine -> alert' in m)
        assert '+5 more' in transition_msg

    def test_alert_to_all_clear_logs_transition(self):
        tracker, log = self._make_tracker()
        tracker.update({'cat': '1', 'data': ['City A']})
        log.reset_mock()
        tracker.update({'cat': '13', 'data': ['City A']})
        msgs = [call.args[0] for call in log.call_args_list]
        assert any('alert -> all_clear' in m for m in msgs)
