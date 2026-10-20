"""Offline translation catalogs shared by the local UI, API, and CLI.

Only application-owned message fields are translated. Paths, resource IDs,
filenames, key configuration and raw third-party diagnostics stay untouched.
"""
from functools import lru_cache
import json
from pathlib import Path
import re

CATALOG = json.loads(Path(__file__).with_name('locales.json').read_text(encoding='utf-8'))
LANGUAGES = tuple(CATALOG)


def normalize_language(value, default='en'):
    value = str(value or '').strip().lower().replace('_', '-')
    if value in ('zh-tw', 'zh-hk', 'zh-mo') or value.startswith('zh-hant'):
        return 'zh-TW'
    if value == 'zh' or value.startswith(('zh-cn', 'zh-sg', 'zh-hans')):
        return 'zh-CN'
    if value == 'en' or value.startswith('en-'):
        return 'en'
    if value == 'ja' or value.startswith('ja-'):
        return 'ja'
    return default


@lru_cache(maxsize=4)
def _pattern(language):
    messages = CATALOG[language]['messages']
    return re.compile('|'.join(re.escape(k) for k in sorted(messages, key=len, reverse=True)))


def translate(text, language='zh-CN'):
    """Replace known canonical messages in one pass; retain decoder output."""
    language = normalize_language(language, 'zh-CN')
    if language == 'zh-CN' or not isinstance(text, str):
        return text
    messages = CATALOG[language]['messages']
    return _pattern(language).sub(lambda m: messages[m.group()], text)


def localize_payload(value, language):
    """Never translate names, output paths, resource IDs or other data fields."""
    if isinstance(value, list):
        return [localize_payload(v, language) for v in value]
    if isinstance(value, dict):
        return {k: translate(v, language) if k in ('message', 'error') and isinstance(v, str)
                else localize_payload(v, language) for k, v in value.items()}
    return value
