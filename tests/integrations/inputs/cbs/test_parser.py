"""Tests for CBS stream parser."""

from pathlib import Path

import pytest

from red_alert.integrations.inputs.cbs.parser import CbsMessageAssembler, CbsPage, CbsPageParser, decode_ucs2_hex

FIXTURES_DIR = Path(__file__).parent / 'fixtures'


class TestDecodeUcs2Hex:
    def test_hebrew_text(self):
        # "בדקות" (bet dalet qof vav tav)
        hex_str = '05d105d305e705d505ea'
        assert decode_ucs2_hex(hex_str) == 'בדקות'

    def test_english_text(self):
        # "Alert" in UCS-2 BE
        hex_str = '0041006c0065007200740073'
        assert decode_ucs2_hex(hex_str) == 'Alerts'

    def test_arabic_text(self):
        # "في" (fa ya)
        hex_str = '064106 4a'
        assert decode_ucs2_hex(hex_str) == 'في'

    def test_russian_text(self):
        # "В" (capital Ve)
        hex_str = '0412'
        assert decode_ucs2_hex(hex_str) == 'В'

    def test_empty_string(self):
        assert decode_ucs2_hex('') == ''

    def test_strips_non_hex_chars(self):
        assert decode_ucs2_hex('00 41 00 42') == 'AB'

    def test_odd_length_truncated(self):
        # 5 hex chars -> truncated to 4
        assert decode_ucs2_hex('00410') == 'A'


class TestCbsPage:
    def test_serial_number_fields(self):
        # Serial 0x59c0: GS=1, Message Code=412, Update=0
        page = CbsPage(serial_number=0x59C0, message_id=4370, dcs=0x59, page_number=1, total_pages=15, raw_hex='')
        assert page.geographic_scope == 1
        assert page.message_code == 412
        assert page.update_number == 0

    def test_different_serial(self):
        # Serial 0x57e0: GS=1, Message Code=382, Update=0
        page = CbsPage(serial_number=0x57E0, message_id=4373, dcs=0x59, page_number=1, total_pages=13, raw_hex='')
        assert page.geographic_scope == 1
        assert page.message_code == 382
        assert page.update_number == 0


class TestCbsPageParser:
    def test_parse_single_page(self):
        lines = [
            '[/dev/cdc-wdm0] Received WMS event report indication:',
            '  Transfer Route MT Message:',
            '    Ack Indicator:  do-not-send',
            '    Transaction ID: 4294967295',
            '    Format:         gsm-wcdma-broadcast',
            '    Raw Data (88 bytes):',
            '      59 c0 11 12 59 1f 05 d1 05 d3 05 e7 05 d5 05 ea ',
            '      00 20 05 d4 05 e7 05 e8 05 d5 05 d1 05 d5 05 ea ',
            '      00 20 05 e6 05 e4 05 d5 05 d9 05 d5 05 ea 00 20 ',
            '      05 dc 05 d4 05 ea 05 e7 05 d1 05 dc 00 20 05 d4 ',
            '      05 ea 05 e8 05 e2 05 d5 05 ea 00 20 05 d1 05 d0 ',
            '      05 d6 05 d5 05 e8 05 da ',
            '    CBS Header:',
            '      Serial Number: 0x59c0 (GS: 1, Message Code: 412, Update: 0)',
            '      Message ID:    4370 (0x1112)',
            '      DCS:           0x59',
            '      Page:          1 of 15',
        ]
        parser = CbsPageParser()
        pages = []
        for line in lines:
            page = parser.feed_line(line)
            if page:
                pages.append(page)

        assert len(pages) == 1
        assert pages[0].serial_number == 0x59C0
        assert pages[0].message_id == 4370
        assert pages[0].dcs == 0x59
        assert pages[0].page_number == 1
        assert pages[0].total_pages == 15
        assert len(pages[0].raw_hex) > 0

    def test_ignores_monitoring_header(self):
        parser = CbsPageParser()
        result = parser.feed_line('[/dev/cdc-wdm0] WMS event reporting enabled, monitoring for messages...')
        assert result is None

    def test_two_consecutive_pages(self):
        lines = [
            '[/dev/cdc-wdm0] Received WMS event report indication:',
            '  Transfer Route MT Message:',
            '    Raw Data (88 bytes):',
            '      59 c0 11 12 59 1f 00 41 00 42 00 43 00 44 00 45 00 46 ',
            '    CBS Header:',
            '      Serial Number: 0x59c0 (GS: 1, Message Code: 412, Update: 0)',
            '      Message ID:    4370 (0x1112)',
            '      DCS:           0x59',
            '      Page:          1 of 2',
            '[/dev/cdc-wdm0] Received WMS event report indication:',
            '  Transfer Route MT Message:',
            '    Raw Data (88 bytes):',
            '      59 c0 11 12 59 2f 00 47 00 48 00 49 00 4a 00 4b 00 4c ',
            '    CBS Header:',
            '      Serial Number: 0x59c0 (GS: 1, Message Code: 412, Update: 0)',
            '      Message ID:    4370 (0x1112)',
            '      DCS:           0x59',
            '      Page:          2 of 2',
        ]
        parser = CbsPageParser()
        pages = []
        for line in lines:
            page = parser.feed_line(line)
            if page:
                pages.append(page)

        assert len(pages) == 2
        assert pages[0].page_number == 1
        assert pages[1].page_number == 2

    def test_flush_pending(self):
        parser = CbsPageParser()
        # Feed a partial block then flush - should get nothing since Page line triggers emit
        parser.feed_line('[/dev/cdc-wdm0] Received WMS event report indication:')
        parser.feed_line('  Transfer Route MT Message:')
        result = parser.flush()
        assert result is None


class TestCbsMessageAssembler:
    def test_single_page_message(self):
        assembler = CbsMessageAssembler()
        page = CbsPage(serial_number=0x1234, message_id=4370, dcs=0x59, page_number=1, total_pages=1, raw_hex='00410042')
        msg = assembler.add_page(page)
        assert msg is not None
        assert msg.is_complete
        assert msg.text == 'AB'

    def test_multi_page_assembly(self):
        assembler = CbsMessageAssembler()
        page1 = CbsPage(serial_number=0x1234, message_id=4370, dcs=0x59, page_number=1, total_pages=2, raw_hex='00410042')
        page2 = CbsPage(serial_number=0x1234, message_id=4370, dcs=0x59, page_number=2, total_pages=2, raw_hex='00430044')

        assert assembler.add_page(page1) is None
        msg = assembler.add_page(page2)
        assert msg is not None
        assert msg.text == 'ABCD'

    def test_interleaved_messages(self):
        assembler = CbsMessageAssembler()

        # Two different messages arriving interleaved
        p1_m1 = CbsPage(serial_number=0x1111, message_id=4370, dcs=0x59, page_number=1, total_pages=2, raw_hex='00410042')
        p1_m2 = CbsPage(serial_number=0x2222, message_id=4373, dcs=0x59, page_number=1, total_pages=2, raw_hex='00580059')
        p2_m1 = CbsPage(serial_number=0x1111, message_id=4370, dcs=0x59, page_number=2, total_pages=2, raw_hex='00430044')
        p2_m2 = CbsPage(serial_number=0x2222, message_id=4373, dcs=0x59, page_number=2, total_pages=2, raw_hex='005A0021')

        assert assembler.add_page(p1_m1) is None
        assert assembler.add_page(p1_m2) is None
        msg1 = assembler.add_page(p2_m1)
        assert msg1 is not None
        assert msg1.message_id == 4370
        assert msg1.text == 'ABCD'

        msg2 = assembler.add_page(p2_m2)
        assert msg2 is not None
        assert msg2.message_id == 4373
        assert msg2.text == 'XYZ!'

    def test_duplicate_page_ignored(self):
        assembler = CbsMessageAssembler()
        page1 = CbsPage(serial_number=0x1234, message_id=4370, dcs=0x59, page_number=1, total_pages=2, raw_hex='00410042')

        assert assembler.add_page(page1) is None
        # Adding same page again - still pending
        assert assembler.add_page(page1) is None


class TestFixtureParsing:
    """Integration test using the real CBS log fixture."""

    def test_parse_full_fixture(self):
        fixture_path = FIXTURES_DIR / 'cbs_sample.log'
        if not fixture_path.exists():
            pytest.skip('CBS fixture not available')

        parser = CbsPageParser()
        assembler = CbsMessageAssembler()
        messages = []

        with open(fixture_path) as f:
            for line in f:
                page = parser.feed_line(line)
                if page:
                    msg = assembler.add_page(page)
                    if msg:
                        messages.append(msg)

        assert len(messages) >= 2

        # Check message IDs present
        msg_ids = [m.message_id for m in messages]
        assert 4370 in msg_ids
        assert 4373 in msg_ids

    def test_fixture_message_content(self):
        fixture_path = FIXTURES_DIR / 'cbs_sample.log'
        if not fixture_path.exists():
            pytest.skip('CBS fixture not available')

        parser = CbsPageParser()
        assembler = CbsMessageAssembler()
        messages = []

        with open(fixture_path) as f:
            for line in f:
                page = parser.feed_line(line)
                if page:
                    msg = assembler.add_page(page)
                    if msg:
                        messages.append(msg)

        # Find a pre-alert (4370) message
        pre_alerts = [m for m in messages if m.message_id == 4370]
        assert len(pre_alerts) >= 1
        text = pre_alerts[0].text
        assert 'בדקות הקרובות' in text
        assert 'Alerts are expected' in text

        # Find an all-clear (4373) message
        all_clears = [m for m in messages if m.message_id == 4373]
        assert len(all_clears) >= 1
        text = all_clears[0].text
        assert 'האירוע הסתיים' in text
        assert 'event has ended' in text

    def test_fixture_multilingual(self):
        fixture_path = FIXTURES_DIR / 'cbs_sample.log'
        if not fixture_path.exists():
            pytest.skip('CBS fixture not available')

        parser = CbsPageParser()
        assembler = CbsMessageAssembler()
        messages = []

        with open(fixture_path) as f:
            for line in f:
                page = parser.feed_line(line)
                if page:
                    msg = assembler.add_page(page)
                    if msg:
                        messages.append(msg)

        # Each message should contain 4 languages
        msg = messages[0]
        text = msg.text
        assert any('\u0590' <= c <= '\u05ff' for c in text), 'Missing Hebrew'
        assert any('a' <= c <= 'z' for c in text), 'Missing English'
        assert any('\u0600' <= c <= '\u06ff' for c in text), 'Missing Arabic'
        assert any('\u0400' <= c <= '\u04ff' for c in text), 'Missing Russian/Cyrillic'
