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
            mock_time.monotonic.return_value = tracker._state_entered_time + 1801.0
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
        """ALL_CLEAR is held for default 300s (5min), then transitions to ROUTINE."""
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
            mock_time.monotonic.return_value = tracker._state_entered_time + 301.0
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

    def test_all_clear_by_title_event_ended(self):
        """'האירוע הסתיים' (event ended) triggers ALL_CLEAR regardless of category."""
        tracker = AlertStateTracker()
        result = tracker.update({'cat': '10', 'data': ['City A'], 'title': 'האירוע הסתיים'})
        assert result == AlertState.ALL_CLEAR

    def test_all_clear_by_title_with_active_category(self):
        """Title-based all-clear overrides active alert category."""
        tracker = AlertStateTracker()
        result = tracker.update({'cat': '1', 'data': ['City A'], 'title': 'האירוע הסתיים'})
        assert result == AlertState.ALL_CLEAR

    def test_all_clear_by_title_overrides_areas(self):
        """Title-based all-clear applies globally like category-based all-clear."""
        tracker = AlertStateTracker(areas_of_interest=['כפר סבא'])
        result = tracker.update({'cat': '10', 'data': ['מטולה'], 'title': 'האירוע הסתיים'})
        assert result == AlertState.ALL_CLEAR

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

    def test_default_hold_seconds(self):
        assert DEFAULT_HOLD_SECONDS == {'alert': 1800, 'pre_alert': 1800, 'all_clear': 300}

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

    def test_area_filtered_only_logged_once(self):
        """Alert filtered should only log on first occurrence, not every poll."""
        tracker, log = self._make_tracker(areas_of_interest=['City X'])
        tracker.update({'cat': '1', 'data': ['City A']})
        tracker.update({'cat': '1', 'data': ['City A']})
        tracker.update({'cat': '1', 'data': ['City A']})
        filtered_msgs = [call.args[0] for call in log.call_args_list if 'Alert filtered' in call.args[0]]
        assert len(filtered_msgs) == 1

    def test_area_match_only_logged_once(self):
        """Area match should only log on first occurrence, not every poll."""
        tracker, log = self._make_tracker(areas_of_interest=['City A'])
        tracker.update({'cat': '1', 'data': ['City A']})
        tracker.update({'cat': '1', 'data': ['City A']})
        tracker.update({'cat': '1', 'data': ['City A']})
        match_msgs = [call.args[0] for call in log.call_args_list if 'Area match' in call.args[0]]
        assert len(match_msgs) == 1

    def test_area_log_resets_after_empty(self):
        """After API goes empty and returns, area result should be logged again."""
        tracker, log = self._make_tracker(areas_of_interest=['City X'])
        tracker.update({'cat': '1', 'data': ['City A']})  # filtered, logged
        tracker.update(None)  # empty, resets
        tracker.update({'cat': '1', 'data': ['City A']})  # filtered again, should log again
        filtered_msgs = [call.args[0] for call in log.call_args_list if 'Alert filtered' in call.args[0]]
        assert len(filtered_msgs) == 2


class TestRealWorldScenarios:
    """End-to-end scenarios based on observed HFC API behavior.

    The HFC live alerts API sends brief pulses (~30-60s) for each event phase.
    These tests simulate realistic timelines observed in production logs:
    - Pre-alert (cat=14) pulse, gap, then all-clear (cat=13) pulse
    - Rocket alert (cat=1) pulse, ~10min gap, then all-clear (cat=13) pulse
    - All-clear may arrive with cat=10 + title "האירוע הסתיים" on the live endpoint
      (history endpoint normalizes to cat=13)
    """

    def test_kfar_saba_pre_alert_to_all_clear(self):
        """Real scenario: כפר סבא got pre-alert at 02:30, all-clear at 02:45 (~15min gap).

        The API goes empty between the pre-alert pulse and the all-clear pulse.
        The 30-minute hold bridges this gap so the state stays PRE_ALERT until
        the all-clear arrives.
        """
        tracker = AlertStateTracker(areas_of_interest=['כפר סבא'])

        # 02:30 - Pre-alert pulse arrives (669 cities including כפר סבא)
        result = tracker.update(
            {
                'cat': '14',
                'title': 'בדקות הקרובות צפויות להתקבל התרעות באזורך',
                'data': ['כפר סבא', 'רעננה', 'הוד השרון'],
            }
        )
        assert result == AlertState.PRE_ALERT
        entered_time = tracker._state_entered_time

        # 02:31 - API goes empty (pulse ended after ~30-60s)
        with patch('red_alert.core.state.time') as mock_time:
            mock_time.monotonic.return_value = entered_time + 60.0
            result = tracker.update(None)
        assert result == AlertState.PRE_ALERT  # held by 30min hold

        # 02:34-02:35 - Rocket alerts for other cities (not כפר סבא)
        with patch('red_alert.core.state.time') as mock_time:
            mock_time.monotonic.return_value = entered_time + 240.0
            result = tracker.update({'cat': '1', 'title': 'ירי רקטות וטילים', 'data': ['מטולה', 'כפר יובל']})
        assert result == AlertState.PRE_ALERT  # rockets didn't match our area, hold continues

        # 02:40 - API empty again
        with patch('red_alert.core.state.time') as mock_time:
            mock_time.monotonic.return_value = entered_time + 600.0
            result = tracker.update(None)
        assert result == AlertState.PRE_ALERT  # still within 30min hold

        # 02:45 - All-clear arrives (cat=13, "האירוע הסתיים")
        result = tracker.update({'cat': '13', 'title': 'האירוע הסתיים', 'data': ['כפר סבא', 'רעננה']})
        assert result == AlertState.ALL_CLEAR

    def test_metula_full_alert_cycle(self):
        """Real scenario: מטולה got rockets at 03:45, all-clear at 03:56 (~10min gap)."""
        tracker = AlertStateTracker(areas_of_interest=['מטולה'])

        # 03:45 - Rocket alert
        result = tracker.update({'cat': '1', 'title': 'ירי רקטות וטילים', 'data': ['מטולה']})
        assert result == AlertState.ALERT
        entered_time = tracker._state_entered_time

        # 03:46 - API empty (pulse ended)
        with patch('red_alert.core.state.time') as mock_time:
            mock_time.monotonic.return_value = entered_time + 60.0
            result = tracker.update(None)
        assert result == AlertState.ALERT  # held by 30min hold

        # 03:55 - Still empty, 10 minutes later
        with patch('red_alert.core.state.time') as mock_time:
            mock_time.monotonic.return_value = entered_time + 600.0
            result = tracker.update(None)
        assert result == AlertState.ALERT  # still within 30min hold

        # 03:56 - All-clear
        result = tracker.update({'cat': '13', 'title': 'האירוע הסתיים', 'data': ['מטולה']})
        assert result == AlertState.ALL_CLEAR

    def test_all_clear_with_cat_10_and_title(self):
        """Real scenario: live API sends all-clear as cat=10 with title 'האירוע הסתיים'.

        The history endpoint normalizes this to cat=13, but the live endpoint may
        use the matrix_id (10) as the category. Title-based detection handles this.
        """
        tracker = AlertStateTracker()

        # Active alert
        tracker.update({'cat': '1', 'title': 'ירי רקטות וטילים', 'data': ['מטולה']})
        assert tracker.state == AlertState.ALERT

        # All-clear arrives with cat=10 (matrix_id) instead of cat=13
        result = tracker.update({'cat': '10', 'title': 'האירוע הסתיים', 'data': ['מטולה']})
        assert result == AlertState.ALL_CLEAR

    def test_hostile_aircraft_intrusion(self):
        """Real scenario: hostile aircraft (cat=2, "חדירת כלי טיס עוין") observed in history."""
        tracker = AlertStateTracker()
        result = tracker.update({'cat': '2', 'title': 'חדירת כלי טיס עוין', 'data': ['City A']})
        assert result == AlertState.ALERT

    def test_pre_alert_hold_survives_unrelated_rockets(self):
        """Pre-alert for one area should hold even when rockets hit other areas.

        During a real event, the API may show rocket alerts for northern cities
        while central Israel is in pre-alert. These rockets don't match our area
        so the pre-alert hold should continue.
        """
        tracker = AlertStateTracker(areas_of_interest=['כפר סבא'])

        # Pre-alert for our area
        tracker.update(
            {
                'cat': '14',
                'title': 'בדקות הקרובות צפויות להתקבל התרעות באזורך',
                'data': ['כפר סבא'],
            }
        )
        assert tracker.state == AlertState.PRE_ALERT
        entered_time = tracker._state_entered_time

        # Rockets hit north (not our area) - should NOT change state
        with patch('red_alert.core.state.time') as mock_time:
            mock_time.monotonic.return_value = entered_time + 300.0
            result = tracker.update({'cat': '1', 'title': 'ירי רקטות וטילים', 'data': ['מטולה', 'כפר גלעדי']})
        assert result == AlertState.PRE_ALERT

    def test_multiple_salvos_with_all_clear_between(self):
        """Real scenario: מטולה had two rocket salvos with all-clear between.

        03:45 alert -> 03:56 all-clear -> 04:22 new alert -> 04:32 all-clear.
        The all-clear should transition properly, and a new alert during
        all-clear hold should transition immediately.
        """
        tracker = AlertStateTracker(areas_of_interest=['מטולה'])

        # First salvo
        tracker.update({'cat': '1', 'title': 'ירי רקטות וטילים', 'data': ['מטולה']})
        assert tracker.state == AlertState.ALERT

        # First all-clear
        tracker.update({'cat': '13', 'title': 'האירוע הסתיים', 'data': ['מטולה']})
        assert tracker.state == AlertState.ALL_CLEAR
        all_clear_time = tracker._state_entered_time

        # During all-clear hold, second salvo arrives
        with patch('red_alert.core.state.time') as mock_time:
            mock_time.monotonic.return_value = all_clear_time + 120.0  # 2 min into all-clear hold
            result = tracker.update({'cat': '1', 'title': 'ירי רקטות וטילים', 'data': ['מטולה']})
        assert result == AlertState.ALERT  # immediate transition

        # Second all-clear
        tracker.update({'cat': '13', 'title': 'האירוע הסתיים', 'data': ['מטולה']})
        assert tracker.state == AlertState.ALL_CLEAR

    def test_hold_expires_without_all_clear(self):
        """If no all-clear arrives within 30 minutes, state returns to ROUTINE."""
        tracker = AlertStateTracker()
        tracker.update({'cat': '1', 'title': 'ירי רקטות וטילים', 'data': ['מטולה']})
        entered_time = tracker._state_entered_time

        # 29 minutes - still held
        with patch('red_alert.core.state.time') as mock_time:
            mock_time.monotonic.return_value = entered_time + 1740.0
            result = tracker.update(None)
        assert result == AlertState.ALERT

        # 31 minutes - hold expired
        with patch('red_alert.core.state.time') as mock_time:
            mock_time.monotonic.return_value = entered_time + 1860.0
            result = tracker.update(None)
        assert result == AlertState.ROUTINE

    def test_all_clear_hold_expires_to_routine(self):
        """All-clear hold (5min) returns to ROUTINE after expiry."""
        tracker = AlertStateTracker()
        tracker.update({'cat': '13', 'title': 'האירוע הסתיים', 'data': ['מטולה']})
        entered_time = tracker._state_entered_time

        # 4 minutes - still held
        with patch('red_alert.core.state.time') as mock_time:
            mock_time.monotonic.return_value = entered_time + 240.0
            result = tracker.update(None)
        assert result == AlertState.ALL_CLEAR

        # 6 minutes - expired
        with patch('red_alert.core.state.time') as mock_time:
            mock_time.monotonic.return_value = entered_time + 360.0
            result = tracker.update(None)
        assert result == AlertState.ROUTINE
