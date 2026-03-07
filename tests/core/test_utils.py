from datetime import datetime

from red_alert.core.utils import check_bom, parse_datetime_str, standardize_name


class TestStandardizeName:
    def test_basic_name(self):
        assert standardize_name('תל אביב') == 'תל אביב'

    def test_strips_whitespace(self):
        assert standardize_name('  תל אביב  ') == 'תל אביב'

    def test_removes_parentheses(self):
        assert standardize_name('תל אביב (מרכז)') == 'תל אביב מרכז'

    def test_removes_quotes(self):
        assert standardize_name("תל אביב 'מרכז'") == 'תל אביב מרכז'

    def test_special_case_jat(self):
        assert standardize_name("ג'ת") == "ג'ת"

    def test_special_case_khawaled(self):
        assert standardize_name("ח'וואלד") == "ח'וואלד"

    def test_empty_string(self):
        assert standardize_name('') == ''

    def test_none_input(self):
        assert standardize_name(None) == ''

    def test_non_string_input(self):
        assert standardize_name(123) == ''

    def test_whitespace_only(self):
        assert standardize_name('   ') == ''


class TestCheckBom:
    def test_removes_bom(self):
        assert check_bom('\ufeffhello') == 'hello'

    def test_no_bom(self):
        assert check_bom('hello') == 'hello'

    def test_empty_string(self):
        assert check_bom('') == ''

    def test_multiple_bom(self):
        assert check_bom('\ufeff\ufeffhello') == 'hello'


class TestParseDatetimeStr:
    def test_iso_format_with_microseconds(self):
        result = parse_datetime_str('2024-01-15T10:30:45.123456')
        assert isinstance(result, datetime)
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 15
        assert result.hour == 10
        assert result.minute == 30
        assert result.second == 45

    def test_iso_format_no_microseconds(self):
        result = parse_datetime_str('2024-01-15T10:30:45')
        assert isinstance(result, datetime)
        assert result.hour == 10

    def test_space_separated_format(self):
        result = parse_datetime_str('2024-01-15 10:30:45')
        assert isinstance(result, datetime)
        assert result.day == 15

    def test_space_separated_with_microseconds(self):
        result = parse_datetime_str('2024-01-15 10:30:45.123456')
        assert isinstance(result, datetime)

    def test_with_timezone_plus(self):
        result = parse_datetime_str('2024-01-15T10:30:45+02:00')
        assert isinstance(result, datetime)
        assert result.hour == 10

    def test_with_timezone_z(self):
        result = parse_datetime_str('2024-01-15T10:30:45Z')
        assert isinstance(result, datetime)

    def test_with_quotes(self):
        result = parse_datetime_str('"2024-01-15T10:30:45"')
        assert isinstance(result, datetime)

    def test_empty_string(self):
        assert parse_datetime_str('') is None

    def test_none_input(self):
        assert parse_datetime_str(None) is None

    def test_non_string_input(self):
        assert parse_datetime_str(12345) is None

    def test_invalid_format(self):
        assert parse_datetime_str('not-a-date') is None

    def test_whitespace_stripped(self):
        result = parse_datetime_str('  2024-01-15T10:30:45  ')
        assert isinstance(result, datetime)
