"""Synthetic sessions and mock QQ Music responses; no real credentials."""
import base64
import io
import json
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch

from auto_ekey import (AutoFetcher, AutoKeyError, LoginSession, extract_sessions,
                       read_account, request_ekey, ENDPOINT)
from converter import Cancelled, QMC_CORE, QMCv2, NCM_CORE
from libtakiyasha.qmc._qmckeyciphers import QMCv2KeyEncryptV1

INFO = {'resource_name': 'F0M000150F7f2ztQel.mflac', 'media_mid': '001t1RpP3ZmrwI'}
FAKE_TOKEN = 'SyntheticSessionOnly0123456789ABCDEF=='


class Opener:
    def __init__(self, payload):
        self.payload = payload
        self.requests = []

    def open(self, req, timeout):
        self.requests.append(req)
        return io.BytesIO(json.dumps(self.payload).encode())


class AutoKeyTests(unittest.TestCase):
    def test_named_field_only_utf8_utf16_escaped(self):
        data = json.dumps({'comm': {'uin': '12345678', 'authst': FAKE_TOKEN, 'tmeLoginType': '1'}})
        for encoded in [data.encode(), data.encode('utf-16le'), ('x' + data).encode('utf-16le'),
                        data.replace('"', '\\"').encode()]:
            result = list(extract_sessions(encoded, None))
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].uin, '12345678')
            self.assertEqual(result[0].authst, FAKE_TOKEN)
            self.assertNotIn(FAKE_TOKEN, repr(result[0]))
        self.assertEqual(list(extract_sessions(FAKE_TOKEN.encode(), '12345678')), [])

    def test_ini_encodings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for encoding in ['utf-8-sig', 'utf-16']:
                (root / 'QQMusicServiceConfig.ini').write_text('[Account]\nUin=12345678\n', encoding=encoding)
                self.assertEqual(read_account(root), '12345678')

    def test_api_exact_resource_and_fixed_https_destination(self):
        response = {'code': 0, 'req_1': {'code': 0, 'data': {'midurlinfo': [
            {'filename': INFO['resource_name'], 'songmid': INFO['media_mid'],
             'result': 0, 'ekey': 'SyntheticEkeyForTestsOnly'}]}}}
        opener = Opener(response)
        key = request_ekey(INFO, LoginSession('12345678', FAKE_TOKEN), '1', opener=opener)
        self.assertEqual(key, 'SyntheticEkeyForTestsOnly')
        req = opener.requests[0]
        self.assertEqual(req.full_url, ENDPOINT)
        body = json.loads(req.data)
        self.assertEqual(body['req_1']['param']['filename'], [INFO['resource_name']])
        self.assertEqual(body['req_1']['param']['songmid'], [INFO['media_mid']])
        self.assertEqual(body['comm']['tmeLoginType'], '1')

    def test_error_response_cannot_leak_credentials(self):
        for payload in [{'debug': FAKE_TOKEN}, {'code': 1}, {'code': 0, 'req_1': {'code': 0, 'data': {'midurlinfo': []}}}]:
            with self.assertRaises(AutoKeyError) as raised:
                request_ekey(INFO, LoginSession('12345678', FAKE_TOKEN), '1', opener=Opener(payload))
            self.assertNotIn(FAKE_TOKEN, str(raised.exception))

    def test_wrong_resource_response_rejected(self):
        opener = Opener({'code': 0, 'req_1': {'code': 0, 'data': {'midurlinfo': [
            {'filename': 'different.mflac', 'result': 0, 'ekey': 'SyntheticEkeyForTestsOnly'}]}}})
        with self.assertRaises(AutoKeyError):
            request_ekey(INFO, LoginSession('12345678', FAKE_TOKEN), '1', opener=opener)

    def test_login_fallback_cache_and_cancel(self):
        sessions = [LoginSession('12345678', FAKE_TOKEN)]
        attempts, scans = [], []
        def provider(cancel, deadline):
            scans.append(1)
            yield from sessions
        def requester(info, session, login_type, timeout):
            attempts.append(login_type)
            if login_type != '3':
                raise AutoKeyError('登录类型不匹配')
            return 'SyntheticEkeyForTestsOnly'
        fetcher = AutoFetcher(provider, requester)
        self.assertEqual(fetcher.fetch(INFO), 'SyntheticEkeyForTestsOnly')
        self.assertEqual(attempts, ['1', '3'])
        fetcher.fetch(INFO)
        self.assertEqual(len(scans), 1)
        stop = threading.Event();stop.set()
        with self.assertRaises(Cancelled):
            fetcher.fetch(INFO, stop)
        fetcher.clear()
        self.assertIsNone(fetcher.session)

    def test_invalid_resource_never_reads_credentials(self):
        def provider(*args):
            self.fail('invalid resource must not read credentials')
        fetcher = AutoFetcher(provider)
        with self.assertRaises(AutoKeyError):
            fetcher.fetch({'resource_name': '../bad.mflac', 'media_mid': 'validMID123'})

    def test_automatic_fetch_to_decrypt_to_mp3(self):
        import shutil, subprocess, time
        from server import State
        from converter import musicex_info
        if not shutil.which('ffmpeg'):
            self.skipTest('需要 FFmpeg')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plain = root / 'source.flac'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=duration=0.25', str(plain)], check=True)
            audio = QMCv2.new('rc4');audio.write(plain.read_bytes())
            ekey = base64.b64encode(QMCv2KeyEncryptV1(QMC_CORE).encrypt(audio.master_key)).decode()
            footer = bytearray(192);footer[:12] = struct.pack('<III', 1, 2, 5)
            footer[12:40] = INFO['media_mid'].encode('utf-16le')
            name = INFO['resource_name'].encode('utf-16le');footer[72:72+len(name)] = name
            footer[-16:] = struct.pack('<II', 192, 1) + b'musicex\0'
            source = root / '中文.mflac'
            source.write_bytes(audio.getvalue(nocryptlayer=True) + footer)
            response = {'code': 0, 'req_1': {'code': 0, 'data': {'midurlinfo': [
                {'filename': INFO['resource_name'], 'songmid': INFO['media_mid'], 'result': 0, 'ekey': ekey}]}}}
            opener = Opener(response)
            fetcher = AutoFetcher(lambda *_: iter([LoginSession('12345678', FAKE_TOKEN, '1')]),
                lambda info, session, kind, timeout: request_ekey(info, session, kind, timeout, opener))
            state = State(root / 'out', shutil.which('ffmpeg'), fetcher)
            try:
                state.jobs['test'] = {'id':'test','name':source.name,'source':source,'status':'queued',
                    'message':'','info':musicex_info(source),'bitrate':320,'keys':{},'auto':True,'cancel':threading.Event()}
                state.pending.put('test')
                for _ in range(200):
                    if state.jobs['test']['status'] in ('done','failed'):
                        break
                    time.sleep(.025)
                job = state.jobs['test']
                self.assertEqual(job['status'],'done',job.get('message'))
                self.assertEqual(job['keys'],{})
                serialized = json.dumps(state.snapshot())
                self.assertNotIn(FAKE_TOKEN,serialized)
                self.assertNotIn(ekey,serialized)
                subprocess.run(['ffmpeg','-v','error','-xerror','-i',str(job['target']),'-f','null','-'],check=True)
            finally:
                state.close()
                self.assertIsNone(fetcher.session)


if __name__ == '__main__':
    unittest.main()
