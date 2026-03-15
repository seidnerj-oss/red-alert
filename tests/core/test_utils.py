from datetime import datetime

from red_alert.core.utils import check_bom, detect_and_decode, parse_datetime_str, standardize_name


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

    def test_strips_nul_characters(self):
        assert check_bom('hel\x00lo') == 'hello'

    def test_strips_bom_and_nul(self):
        assert check_bom('\ufeffhel\x00lo') == 'hello'

    def test_no_nul_passthrough(self):
        assert check_bom('hello') == 'hello'


class TestDetectAndDecode:
    def test_utf8_plain(self):
        assert detect_and_decode(b'hello') == 'hello'

    def test_utf8_bom(self):
        assert detect_and_decode(b'\xef\xbb\xbfhello') == 'hello'

    def test_utf16_le_bom(self):
        data = b'\xff\xfeh\x00e\x00l\x00l\x00o\x00'
        assert detect_and_decode(data) == 'hello'

    def test_utf16_be_bom(self):
        data = b'\xfe\xff\x00h\x00e\x00l\x00l\x00o'
        assert detect_and_decode(data) == 'hello'

    def test_utf8_hebrew(self):
        text = 'שלום'
        assert detect_and_decode(text.encode('utf-8')) == text

    def test_utf8_bom_hebrew(self):
        text = 'שלום'
        data = b'\xef\xbb\xbf' + text.encode('utf-8')
        assert detect_and_decode(data) == text

    def test_latin1_fallback(self):
        data = bytes([0xC0, 0xC1, 0xFE])
        result = detect_and_decode(data)
        assert isinstance(result, str)


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
