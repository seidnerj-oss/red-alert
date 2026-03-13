"""
Cell Broadcast (CBS/ETWS/CMAS) message parser for qmicli --wms-monitor output.

Parses the structured text output from qmicli's WMS monitor, reassembles
multi-page CBS messages, and decodes UCS-2 content into text.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger('red_alert.cbs')


@dataclass
class CbsPage:
    """A single CBS page as parsed from qmicli output."""

    serial_number: int
    message_id: int
    dcs: int
    page_number: int
    total_pages: int
    raw_hex: str

    @property
    def geographic_scope(self) -> int:
        return (self.serial_number >> 14) & 0x03

    @property
    def message_code(self) -> int:
        return (self.serial_number >> 4) & 0x03FF

    @property
    def update_number(self) -> int:
        return self.serial_number & 0x0F


@dataclass
class CbsMessage:
    """A fully reassembled multi-page CBS message."""

    serial_number: int
    message_id: int
    dcs: int
    total_pages: int
    pages: dict[int, str] = field(default_factory=dict)

    @property
    def message_code(self) -> int:
        return (self.serial_number >> 4) & 0x03FF

    @property
    def is_complete(self) -> bool:
        return len(self.pages) == self.total_pages

    @property
    def text(self) -> str:
        """Decode and concatenate all pages into a single string."""
        parts = []
        for p in range(1, self.total_pages + 1):
            if p in self.pages:
                parts.append(decode_ucs2_hex(self.pages[p]))
        full = ''.join(parts)
        return full.rstrip('\r\n\x00\x0d')


def decode_ucs2_hex(hex_string: str) -> str:
    """Decode a hex string of UCS-2 Big Endian bytes to a Python string."""
    cleaned = re.sub(r'[^0-9a-fA-F]', '', hex_string)
    if len(cleaned) % 4 != 0:
        cleaned = cleaned[: len(cleaned) - (len(cleaned) % 4)]
    if not cleaned:
        return ''
    raw = bytes.fromhex(cleaned)
    return raw.decode('utf-16-be', errors='replace')


# Regex patterns for parsing qmicli --wms-monitor output
_RE_SERIAL = re.compile(r'Serial Number:\s+0x([0-9a-fA-F]+)')
_RE_MSG_ID = re.compile(r'Message ID:\s+(\d+)')
_RE_DCS = re.compile(r'DCS:\s+0x([0-9a-fA-F]+)')
_RE_PAGE = re.compile(r'Page:\s+(\d+)\s+of\s+(\d+)')
_RE_HEX_LINE = re.compile(r'^\s+([0-9a-f]{2}(?:\s+[0-9a-f]{2})+)\s*$')


class CbsPageParser:
    """Line-by-line parser for qmicli --wms-monitor output.

    Feed lines one at a time via ``feed_line()``. When a complete CBS page
    block has been parsed, it is returned. Otherwise returns None.
    """

    def __init__(self):
        self._in_block = False
        self._serial: int | None = None
        self._msg_id: int | None = None
        self._dcs: int | None = None
        self._page_num: int | None = None
        self._total_pages: int | None = None
        self._raw_hex_lines: list[str] = []
        self._in_raw_data = False
        self._in_cbs_header = False

    def feed_line(self, line: str) -> CbsPage | None:
        """Feed a single line. Returns a CbsPage when a complete block is parsed."""
        stripped = line.rstrip('\n')

        # Start of a new indication block
        if 'Received WMS event report indication:' in stripped:
            page = self._emit()
            self._reset()
            self._in_block = True
            return page

        if not self._in_block:
            return None

        # Detect raw data section start
        if 'Raw Data' in stripped and 'bytes' in stripped:
            self._in_raw_data = True
            self._in_cbs_header = False
            self._raw_hex_lines = []
            return None

        # Detect CBS Header section
        if 'CBS Header:' in stripped:
            self._in_raw_data = False
            self._in_cbs_header = True
            return None

        # Parse hex dump lines in Raw Data section
        if self._in_raw_data:
            m = _RE_HEX_LINE.match(stripped)
            if m:
                self._raw_hex_lines.append(m.group(1))
                return None
            self._in_raw_data = False

        # Parse CBS Header fields
        if self._in_cbs_header:
            m = _RE_SERIAL.search(stripped)
            if m:
                self._serial = int(m.group(1), 16)
                return None

            m = _RE_MSG_ID.search(stripped)
            if m:
                self._msg_id = int(m.group(1))
                return None

            m = _RE_DCS.search(stripped)
            if m:
                self._dcs = int(m.group(1), 16)
                return None

            m = _RE_PAGE.search(stripped)
            if m:
                self._page_num = int(m.group(1))
                self._total_pages = int(m.group(2))
                # Page info is the last field in CBS Header - emit the page
                page = self._emit()
                self._reset()
                return page

        return None

    def flush(self) -> CbsPage | None:
        """Flush any pending page data (call at end of stream)."""
        page = self._emit()
        self._reset()
        return page

    def _emit(self) -> CbsPage | None:
        if (
            self._serial is not None
            and self._msg_id is not None
            and self._dcs is not None
            and self._page_num is not None
            and self._total_pages is not None
        ):
            # Build raw hex from hex dump, skip 6-byte CBS header
            all_hex = ''.join(line.replace(' ', '') for line in self._raw_hex_lines)
            payload_hex = all_hex[12:]  # skip serial(2) + msgid(2) + dcs(1) + page(1)
            return CbsPage(
                serial_number=self._serial,
                message_id=self._msg_id,
                dcs=self._dcs,
                page_number=self._page_num,
                total_pages=self._total_pages,
                raw_hex=payload_hex,
            )
        return None

    def _reset(self):
        self._in_block = False
        self._serial = None
        self._msg_id = None
        self._dcs = None
        self._page_num = None
        self._total_pages = None
        self._raw_hex_lines = []
        self._in_raw_data = False
        self._in_cbs_header = False


class CbsMessageAssembler:
    """Collects CBS pages and yields complete messages.

    Pages are grouped by (serial_number, message_id) key. When all pages
    of a message have been received, the complete CbsMessage is returned.
    """

    def __init__(self):
        self._pending: dict[tuple[int, int], CbsMessage] = {}

    def add_page(self, page: CbsPage) -> CbsMessage | None:
        """Add a page. Returns a complete CbsMessage if all pages are now present."""
        key = (page.serial_number, page.message_id)

        if key not in self._pending:
            self._pending[key] = CbsMessage(
                serial_number=page.serial_number,
                message_id=page.message_id,
                dcs=page.dcs,
                total_pages=page.total_pages,
            )

        msg = self._pending[key]
        msg.pages[page.page_number] = page.raw_hex

        if msg.is_complete:
            del self._pending[key]
            return msg

        return None
