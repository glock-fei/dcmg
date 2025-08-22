import gettext
import locale
import logging
import os

logger = logging.getLogger(__name__)


def _get_preferences_language():
    """
    Get the language from the environment variable PREFER_LANG.
    If not set, return "zh_CN" as default.
    """
    lang = os.getenv("PREFER_LANG")
    if lang:
        return lang

    return "zh_CN"


current_lang = _get_preferences_language()
logger.info("preferences language: %s", current_lang)

tr = gettext.translation('messages', localedir='locales', languages=[current_lang])
tr.install()

gettext_lazy = tr.gettext
