from red_alert.core.i18n import get_translator


class TestGetTranslator:
    def test_english_returns_english(self):
        _ = get_translator('en')
        assert _('No alerts') == 'No alerts'
        assert _('Routine') == 'Routine'

    def test_hebrew_returns_hebrew(self):
        _ = get_translator('he')
        assert _('No alerts') == 'אין התרעות'
        assert _('Routine') == 'שגרה'
        assert _('Loading...') == 'טוען...'
        assert _('Unknown') == 'לא ידוע'

    def test_hebrew_format_strings(self):
        _ = get_translator('he')
        result = _('Widespread attack on {count} cities in: {areas}').format(count=5, areas='גוש דן')
        assert '5' in result
        assert 'גוש דן' in result

    def test_unknown_language_falls_back_to_english(self):
        _ = get_translator('xx')
        assert _('No alerts') == 'No alerts'

    def test_default_language_is_english(self):
        _ = get_translator()
        assert _('No alerts') == 'No alerts'

    def test_hebrew_day_names(self):
        _ = get_translator('he')
        assert _('Sunday') == 'יום ראשון'
        assert _('Saturday') == 'יום שבת'
