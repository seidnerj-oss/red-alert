import functools
from datetime import datetime

from red_alert.core.constants import CLEAN_NAME_REGEX


@functools.lru_cache(maxsize=512)
def standardize_name(name: str) -> str:
    """Return a city name stripped of parentheses/quotes and extra spaces, with special handling for ג'ת."""
    if not isinstance(name, str):
        return ''

    stripped_name = name.strip()

    # Special case: If the name is exactly "ג'ת" or "ח'וואלד", return it as is
    if stripped_name == "ג'ת" or stripped_name == "ח'וואלד":
        return stripped_name

    return CLEAN_NAME_REGEX.sub('', stripped_name)


def check_bom(text: str) -> str:
    """Remove BOM and NUL characters if present."""
    if text.startswith('\ufeff'):
        text = text.lstrip('\ufeff')
    if '\x00' in text:
        text = text.replace('\x00', '')
    return text


def detect_and_decode(data: bytes) -> str:
    """Detect encoding from byte content and decode to string.

    Handles UTF-8 with BOM, UTF-16-LE with BOM, and plain UTF-8.
    Strips NUL characters that occasionally appear in HFC API responses.
    """
    if data[:3] == b'\xef\xbb\xbf':
        return data.decode('utf-8-sig')
    if data[:2] == b'\xff\xfe':
        return data[2:].decode('utf-16-le')
    if data[:2] == b'\xfe\xff':
        return data[2:].decode('utf-16-be')
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        return data.decode('latin-1')


def parse_datetime_str(ds: str) -> datetime | None:
    """Parse various datetime string formats into datetime objects."""
    if not ds or not isinstance(ds, str):
        return None
    ds = ds.strip().strip('"')
    formats = [
        '%Y-%m-%dT%H:%M:%S.%f',
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%d %H:%M:%S.%f',
        '%Y-%m-%d %H:%M:%S',
    ]
    for fmt in formats:
        try:
            return datetime.strptime(ds, fmt)
        except ValueError:
            pass
    try:
        # Handle ISO format with timezone (make naive for comparison)
        if '+' in ds:
            ds = ds.split('+')[0]
        if 'Z' in ds:
            ds = ds.split('Z')[0]
        # Try parsing again after stripping potential timezone info
        for iso_fmt in ['%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S']:
            try:
                return datetime.strptime(ds, iso_fmt)
            except ValueError:
                pass
        # Last attempt with fromisoformat
        return datetime.fromisoformat(ds)
    except ValueError:
        return None
    except Exception:
        return None
