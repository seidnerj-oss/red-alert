from red_alert.core.state import ACTIVE_ALERT_CATEGORIES, AlertState, AlertStateTracker


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
