"""Catalog coverage, localized HTTP responses, and persistent language settings."""
import ast
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from i18n import CATALOG, LANGUAGES, localize_payload, normalize_language, translate
from server import State, make_handler

ROOT = Path(__file__).parent


class CatalogTests(unittest.TestCase):
    def test_catalog_keys_and_placeholders_match(self):
        for language in LANGUAGES:
            for section in ('ui', 'messages'):
                expected = CATALOG['zh-CN'][section]
                actual = CATALOG[language][section]
                self.assertEqual(set(expected), set(actual), language)
                for key, text in actual.items():
                    self.assertTrue(text, (language, key))
                    self.assertEqual(sorted(re.findall(r'\{\w+\}', expected[key])),
                                     sorted(re.findall(r'\{\w+\}', text)), (language, key))

    def test_english_covers_all_app_owned_chinese_messages(self):
        for filename in ('server.py', 'converter.py', 'auto_ekey.py'):
            for node in ast.walk(ast.parse((ROOT / filename).read_text(encoding='utf-8'))):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    # The output folder name is user data, not a translated label.
                    if node.value == 'MP3输出':
                        continue
                    if re.search('[\u4e00-\u9fff]', node.value):
                        self.assertNotRegex(translate(node.value, 'en'), '[\u4e00-\u9fff]',
                                            (filename, node.lineno, node.value))

    def test_language_variants_and_fallback(self):
        for value, expected in [('en-US', 'en'), ('ja_JP', 'ja'), ('zh-Hans', 'zh-CN'),
                                ('zh-Hant-HK', 'zh-TW'), ('zh-HK', 'zh-TW'), ('fr-FR', 'en')]:
            self.assertEqual(normalize_language(value), expected)
        self.assertEqual(normalize_language(None, 'zh-CN'), 'zh-CN')

    def test_payload_preserves_user_data_and_diagnostic_values(self):
        original = {'output': '/音乐/转换完成', 'name': '已取消.flac',
                    'info': {'resource_name': 'F0M0001.mflac'},
                    'message': '转换完成', 'result': '转换完成.mp3'}
        localized = localize_payload(original, 'en')
        self.assertEqual(localized['message'], 'Conversion completed')
        for key in ('output', 'name', 'info', 'result'):
            self.assertEqual(localized[key], original[key])
        diagnostic = '主密钥长度=512，算法=HardenedRC4，音频数据长度=48009337'
        self.assertEqual(translate(diagnostic, 'en'),
            'Master-key length=512, algorithm=HardenedRC4, audio data length=48009337')
        self.assertEqual(translate('FLAC 校验与解码失败：\nflac: LOST_SYNC', 'en'),
                         'FLAC verification and decoding failed: \nflac: LOST_SYNC')
        self.assertEqual(translate('FFmpeg: unknown decoder', 'ja'), 'FFmpeg: unknown decoder')

    def test_cli_explicit_languages_and_fallback(self):
        for language, expected in [('en', 'Output folder'), ('ja', '出力フォルダー'),
                                    ('zh-TW', '輸出資料夾'), ('zh-CN', '输出文件夹')]:
            result = subprocess.run([sys.executable, str(ROOT/'converter.py'), '--lang', language, '--help'],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(expected, result.stdout)


@unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg required')
class LocalizedHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.preferences = self.root / 'preferences.json'
        self.state = State(self.root/'out', preferences_path=self.preferences)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.state))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.state.close()
        self.tmp.cleanup()

    def request(self, path='/', body=None, language='en', token=True):
        headers = {'X-App-Language': language}
        if token:
            headers['X-App-Token'] = self.state.token
        if body is not None:
            body = json.dumps(body).encode()
        return urlopen(Request(self.base+path, data=body, headers=headers), timeout=10)

    def test_language_persists_across_new_server_instances(self):
        json.load(self.request('/language', {'language': 'ja'}))
        self.assertEqual(json.loads(self.preferences.read_text()), {'language': 'ja'})
        state2 = State(self.root/'out2', preferences_path=self.preferences)
        try:
            self.assertEqual(state2.language, 'ja')
        finally:
            state2.close()
        html = self.request().read().decode()
        self.assertIn('PREFERRED_LANGUAGE="ja"', html)
        self.assertNotIn('__APP_TRANSLATIONS__', html)
        self.assertNotIn('__APP_TOKEN__', html)
        match = re.search(r'<script id="localeData" type="application/json">(.*?)</script>', html, re.S)
        self.assertEqual(json.loads(match[1]), CATALOG)

    def test_unauthorized_and_invalid_preference_never_writes(self):
        for body, token in [({'language': 'en'}, False), ({'language': 'fr'}, True),
                            ({'language': ['en']}, True)]:
            with self.assertRaises(HTTPError):
                self.request('/language', body, token=token)
            self.assertFalse(self.preferences.exists())

    def test_request_languages_do_not_mutate_shared_messages(self):
        with self.state.lock:
            self.state.jobs['demo'] = {'id': 'demo', 'name': '已取消.flac', 'status': 'done',
                                     'message': '转换完成', 'result': '已取消.mp3',
                                     'cancel': threading.Event()}
        for language in LANGUAGES:
            job = json.load(self.request('/status', language=language))['jobs'][0]
            self.assertEqual(job['message'], translate('转换完成', language))
            self.assertEqual(job['message_source'], '转换完成')
            self.assertEqual(job['name'], '已取消.flac')
        self.assertEqual(self.state.jobs['demo']['message'], '转换完成')
        with self.assertRaises(HTTPError) as error:
            self.request('/missing', language='ja')
        self.assertEqual(json.load(error.exception)['error'], 'ページが見つかりません')

    def test_bad_settings_fallback_and_failed_save_do_not_change_language(self):
        self.preferences.write_text('{bad')
        state2 = State(self.root/'out2', preferences_path=self.preferences)
        try:
            self.assertIsNone(state2.language)
        finally:
            state2.close()
        self.state.preferences_path = self.root  # Replacing a directory must fail.
        with self.assertRaisesRegex(Exception, '无法保存语言设置'):
            self.state.save_language('en')
        self.assertIsNone(self.state.language)
        self.assertFalse(list(self.root.glob('.language-*')))


if __name__ == '__main__':
    unittest.main()
