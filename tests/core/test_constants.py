from red_alert.core.constants import CLEAN_NAME_REGEX, DAY_NAMES, DEFAULT_UNKNOWN_AREA, ICONS_AND_EMOJIS


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
