"""Loopback interface; QQ Music ekey requests are the only remote operation."""
from __future__ import annotations
import argparse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import secrets
import shutil
import sys
import tempfile
import threading
from urllib.parse import parse_qs, urlsplit
import webbrowser

from converter import (ROOT, PLAIN, ENCRYPTED, Cancelled, ConversionError,
                       convert_one, find_ffmpeg, musicex_info, checkpoint)
from auto_ekey import AutoFetcher


class State:
    def __init__(self, output=None, ffmpeg=None, fetcher=None):
        self.output = Path(output or ROOT / 'MP3输出').resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.ffmpeg = find_ffmpeg(ffmpeg)
        self.temp = tempfile.TemporaryDirectory(prefix='audio-convert-')
        self.lock = threading.RLock()
        self.jobs = {}
        self.pending = queue.Queue()
        self.stop = threading.Event()
        self.token = secrets.token_urlsafe(32)
        self.fetcher = fetcher or AutoFetcher()
        self.worker = threading.Thread(target=self.run, daemon=True)
        self.worker.start()

    def snapshot(self):
        with self.lock:
            return {'output': str(self.output), 'jobs': [
                {k: v for k, v in job.items() if k in
                 ('id', 'name', 'status', 'message', 'info', 'result', 'bitrate')}
                for job in self.jobs.values()]}

    def run(self):
        while not self.stop.is_set():
            try:
                ident = self.pending.get(timeout=0.3)
            except queue.Empty:
                continue
            with self.lock:
                job = self.jobs[ident]
                job['status'] = 'converting'
            try:
                checkpoint(job['cancel'])
                if job['info'] and not job['keys'] and job.get('auto', True):
                    with self.lock:
                        job.update(status='fetching', message='正在通过当前 QQ 音乐登录状态自动获取 ekey…')
                    key = self.fetcher.fetch(job['info'], job['cancel'])
                    checkpoint(job['cancel'])
                    job['keys'] = {'files': {job['name']: {'ekey': key}}}
                    del key
                    with self.lock:
                        job.update(status='converting', message='已获取密钥，正在解密并转码…')
                result = convert_one(job['source'], self.output, job['bitrate'],
                                     self.ffmpeg, job['keys'], job['cancel'])
                with self.lock:
                    job['keys'] = {}
                    job.update(status='done', message='转换完成', result=result.name, target=result)
                job['source'].unlink(missing_ok=True)
            except Cancelled:
                with self.lock:
                    job['keys'] = {}
                    job.update(status='cancelled', message='已取消')
            except ConversionError as e:
                with self.lock:
                    job['keys'] = {}
                    job.update(status='failed', message=str(e))
            except Exception:
                with self.lock:
                    job['keys'] = {}
                    job.update(status='failed', message='自动取钥或转换遇到异常。请重新登录 QQ 音乐后重试；若仍失败，可填写 ekey。')
            finally:
                self.pending.task_done()

    def close(self):
        self.stop.set()
        with self.lock:
            for job in self.jobs.values():
                job['cancel'].set()
        self.worker.join()
        self.fetcher.clear()
        self.temp.cleanup()


def make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *_):
            pass

        def send_bytes(self, data, content_type='application/json; charset=utf-8', code=200):
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(data)
            self.close_connection = True

        def respond(self, data, code=200):
            self.send_bytes(json.dumps(data, ensure_ascii=False).encode('utf-8'), code=code)

        def authenticated(self, params):
            host = f'127.0.0.1:{self.server.server_port}'
            origin = self.headers.get('Origin')
            if self.headers.get('Host') != host or (origin and origin != 'http://' + host):
                return False
            supplied = self.headers.get('X-App-Token') or params.get('token', [''])[0]
            return secrets.compare_digest(supplied, state.token)

        def do_GET(self):
            parts = urlsplit(self.path)
            params = parse_qs(parts.query)
            if not self.authenticated(params):
                self.respond({'error': '请从启动程序打开此页面。'}, 403)
                return
            if parts.path == '/':
                data = (ROOT / 'webui.html').read_bytes().replace(b'__APP_TOKEN__', state.token.encode('ascii'))
                self.send_bytes(data, 'text/html; charset=utf-8')
            elif parts.path == '/status':
                self.respond(state.snapshot())
            elif parts.path == '/download':
                with state.lock:
                    job = state.jobs.get(params.get('id', [''])[0])
                    target = job.get('target') if job and job['status'] == 'done' else None
                if not target or not target.is_file():
                    self.respond({'error': '没有可下载的 MP3。'}, 404)
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'audio/mpeg')
                self.send_header('Content-Length', str(target.stat().st_size))
                from urllib.parse import quote
                self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + quote(target.name))
                self.send_header('Connection', 'close')
                self.end_headers()
                try:
                    with target.open('rb') as f:
                        shutil.copyfileobj(f, self.wfile)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                self.close_connection = True
            else:
                self.respond({'error': '页面不存在'}, 404)

        def do_POST(self):
            parts = urlsplit(self.path)
            params = parse_qs(parts.query)
            if not self.authenticated(params):
                self.respond({'error': '请求未授权。'}, 403)
                return
            try:
                self.connection.settimeout(120)
                if parts.path == '/upload':
                    self.upload(params)
                    return
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 <= length <= 65536:
                    raise ConversionError('请求过大。')
                data = json.loads(self.rfile.read(length) or b'{}')
                if not isinstance(data, dict):
                    raise ConversionError('请求无效。')
                if parts.path == '/cancel':
                    with state.lock:
                        for job in state.jobs.values():
                            if job['status'] in ('queued', 'converting', 'fetching'):
                                job['cancel'].set()
                    self.respond({'ok': True})
                elif parts.path == '/retry':
                    self.retry(data)
                elif parts.path == '/folder':
                    if os.name == 'nt':
                        os.startfile(str(state.output))
                    self.respond({'ok': True, 'output': str(state.output)})
                elif parts.path == '/shutdown':
                    self.respond({'ok': True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                else:
                    self.respond({'error': '操作不存在。'}, 404)
            except (ConversionError, ValueError, OSError, KeyError) as e:
                self.respond({'error': str(e)}, 400)

        def upload(self, params):
            name = params.get('name', [''])[0]
            if not name or any(c in name for c in '/\\\x00') or name in ('.', '..'):
                raise ConversionError('文件名无效。')
            if Path(name).suffix.lower() not in PLAIN | ENCRYPTED:
                raise ConversionError('暂不支持此扩展名。')
            length = int(self.headers.get('Content-Length', '0'))
            if not 1 <= length <= 2 * 1024**3:
                raise ConversionError('文件为空或超过 2 GB。')
            bitrate = int(params.get('bitrate', ['320'])[0])
            if bitrate not in (128, 192, 256, 320):
                raise ConversionError('码率无效。')
            ident = secrets.token_hex(12)
            folder = Path(state.temp.name) / ident
            folder.mkdir()
            source = folder / name
            try:
                remaining = length
                with source.open('wb') as dest:
                    while remaining:
                        block = self.rfile.read(min(1024 * 1024, remaining))
                        if not block:
                            raise ConversionError('文件传入中断。')
                        dest.write(block)
                        remaining -= len(block)
                info = musicex_info(source) if source.suffix.lower() in ENCRYPTED else None
                job = {'id': ident, 'name': name, 'source': source, 'status': 'queued',
                       'message': '等待转换', 'info': info, 'bitrate': bitrate,
                       'keys': {}, 'cancel': threading.Event(),
                       'auto': params.get('auto', ['1'])[0] == '1'}
                if info and not job['auto']:
                    job.update(status='failed', message='新版 musicex 文件不带歌曲密钥。点击“填写 ekey”后重试。')
                with state.lock:
                    state.jobs[ident] = job
                if not info or job['auto']:
                    state.pending.put(ident)
                self.respond({'id': ident})
            except BaseException:
                if ident not in state.jobs:
                    shutil.rmtree(folder, ignore_errors=True)
                raise

        def retry(self, data):
            with state.lock:
                job = state.jobs[data['id']]
                if job['status'] not in ('failed', 'cancelled') or not job['source'].is_file():
                    raise ConversionError('当前文件不可重试。')
                key = data.get('ekey', '').strip()
                if job['info'] and not key and data.get('auto') is not True:
                    raise ConversionError('请填写该资源的 ekey，或选择自动重试。')
                job['auto'] = data.get('auto') is True
                job['keys'] = {'files': {job['name']: {'ekey': key}}} if key else {}
                job['cancel'].clear()
                job.update(status='queued', message='等待转换')
                state.pending.put(job['id'])
            self.respond({'ok': True})
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--output')
    parser.add_argument('--ffmpeg')
    args = parser.parse_args()
    state = State(args.output, args.ffmpeg)
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(state))
    server.daemon_threads = True
    url = f'http://127.0.0.1:{server.server_port}/?token={state.token}'
    print('AudioToMP3 is running locally. Keep this window open.', flush=True)
    print('Open this address if the browser did not open:', flush=True)
    print(url, flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        state.close()


if __name__ == '__main__':
    main()
