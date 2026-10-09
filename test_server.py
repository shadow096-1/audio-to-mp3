"""HTTP integration checks for the offline portable interface."""
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from server import State, make_handler


class ServerTests(unittest.TestCase):
    def test_local_http_conversion_and_access_rules(self):
        if not shutil.which('ffmpeg'):
            self.skipTest('需要 FFmpeg')
        import subprocess
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / '原始.wav'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=duration=0.25', str(source)], check=True)
            state = State(output=root / 'out', ffmpeg=shutil.which('ffmpeg'))
            server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(state))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f'http://127.0.0.1:{server.server_port}'
            try:
                def request(path, body=None, token=True, origin=None):
                    headers = {'X-App-Token': state.token} if token else {}
                    if origin:
                        headers['Origin'] = origin
                    return urlopen(Request(base + path, data=body, headers=headers), timeout=10)
                with self.assertRaises(HTTPError) as denied:
                    request('/status', token=False)
                self.assertEqual(denied.exception.code, 403)
                with self.assertRaises(HTTPError) as denied:
                    request('/status', origin='https://example.invalid')
                self.assertEqual(denied.exception.code, 403)
                html = request('/').read().decode()
                self.assertIn('拖入音乐', html)
                self.assertNotIn('__APP_TOKEN__', html)
                response = json.load(request('/upload?name=' + quote('测试 中文.wav') + '&bitrate=192', source.read_bytes()))
                ident = response['id']
                for _ in range(100):
                    snapshot = json.load(request('/status'))
                    job = snapshot['jobs'][0]
                    if job['status'] in ('done', 'failed'):
                        break
                    time.sleep(.05)
                self.assertEqual(job['status'], 'done', job)
                data = request('/download?id=' + ident).read()
                self.assertGreater(len(data), 100)
                self.assertNotIn('source', job)
                self.assertNotIn('keys', job)
                with self.assertRaises(HTTPError):
                    request('/upload?name=..%2Fescape.wav', b'bad')
                self.assertFalse(state.jobs[ident]['source'].exists())
            finally:
                server.shutdown()
                server.server_close()
                state.close()


if __name__ == '__main__':
    unittest.main()
