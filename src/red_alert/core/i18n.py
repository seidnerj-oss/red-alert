import gettext
import os
from collections.abc import Callable

_LOCALE_DIR = os.path.join(os.path.dirname(__file__), '..', 'locale')


def get_translator(language: str = 'en') -> Callable[[str], str]:
    """Return a gettext translation function for the given language."""
    try:
        t = gettext.translation('messages', localedir=_LOCALE_DIR, languages=[language])
        return t.gettext
    except FileNotFoundError:
        return gettext.gettext  # fallback: return untranslated (English)
