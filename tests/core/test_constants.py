from red_alert.core.constants import CLEAN_NAME_REGEX, DAY_NAMES, DEFAULT_UNKNOWN_AREA, HISTORY_CATEGORY_TO_LIVE, ICONS_AND_EMOJIS


class TestConstants:
    def test_icons_and_emojis_has_all_categories(self):
        for cat in range(16):
            assert cat in ICONS_AND_EMOJIS
            icon, emoji = ICONS_AND_EMOJIS[cat]
            assert icon.startswith('mdi:')
            assert isinstance(emoji, str)
            assert len(emoji) > 0

    def test_day_names_has_all_days(self):
        expected_days = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
        for day in expected_days:
            assert day in DAY_NAMES
            assert isinstance(DAY_NAMES[day], str)
            assert len(DAY_NAMES[day]) > 0

    def test_default_unknown_area(self):
        assert isinstance(DEFAULT_UNKNOWN_AREA, str)
        assert len(DEFAULT_UNKNOWN_AREA) > 0

    def test_clean_name_regex_removes_parens(self):
        assert CLEAN_NAME_REGEX.sub('', 'hello(world)') == 'helloworld'

    def test_clean_name_regex_removes_quotes(self):
        assert CLEAN_NAME_REGEX.sub('', "hello'world'") == 'helloworld'


class TestHistoryCategoryMapping:
    def test_covers_all_26_history_categories(self):
        for i in range(1, 27):
            assert i in HISTORY_CATEGORY_TO_LIVE, f'History category {i} not mapped'

    def test_threat_categories_match_live(self):
        assert HISTORY_CATEGORY_TO_LIVE[1] == 1  # Missiles
        assert HISTORY_CATEGORY_TO_LIVE[2] == 2  # Hostile aircraft
        assert HISTORY_CATEGORY_TO_LIVE[3] == 3  # Earthquake
        assert HISTORY_CATEGORY_TO_LIVE[7] == 7  # Hazmat

    def test_flash_alert_maps_to_10(self):
        assert HISTORY_CATEGORY_TO_LIVE[8] == 10

    def test_all_clear_maps_to_13(self):
        assert HISTORY_CATEGORY_TO_LIVE[9] == 13

    def test_pre_alert_maps_to_14(self):
        assert HISTORY_CATEGORY_TO_LIVE[10] == 14

    def test_drills_map_to_100_plus(self):
        for hist_cat in range(11, 27):
            live_cat = HISTORY_CATEGORY_TO_LIVE[hist_cat]
            assert live_cat >= 100, f'History drill category {hist_cat} mapped to {live_cat}, expected >= 100'
