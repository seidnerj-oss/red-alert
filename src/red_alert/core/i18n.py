import gettext
import os

_LOCALE_DIR = os.path.join(os.path.dirname(__file__), '..', 'locale')


def get_translator(language: str = 'en') -> callable:
    """Return a gettext translation function for the given language."""
    try:
        t = gettext.translation('messages', localedir=_LOCALE_DIR, languages=[language])
        return t.gettext
    except FileNotFoundError:
        return gettext.gettext  # fallback: return untranslated (English)
