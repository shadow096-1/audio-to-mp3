"""QQ Music ekey acquisition for the user's own running Windows client.

Reads only named QQ Music login fields, uses read-only process handles,
and sends credentials only to QQ Music over HTTPS. No dumps or token logs.
Protocol reference: ownlight6/qmc-decoder TECHNICAL_DETAILS.md.
"""
from __future__ import annotations
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, HTTPRedirectHandler, build_opener

from converter import ConversionError, checkpoint

ENDPOINT = 'https://u.y.qq.com/cgi-bin/musicu.fcg'
TOKEN = re.compile(r'"(?:authst|strAuthst)"\s*:\s*"([A-Za-z0-9_+/=\-]{16,2048})"')
UIN = re.compile(r'"(?:uin|nUserId)"\s*:\s*"?(\d{5,20})"?')
LOGIN = re.compile(r'"(?:tmeLoginType|loginType)"\s*:\s*"?([13])"?')


class AutoKeyError(ConversionError):
    pass


@dataclass(frozen=True, repr=False)
class LoginSession:
    uin: str
    authst: str
    login_type: str | None = None

    def __repr__(self):
        return '<QQ Music login session; credentials redacted>'


def extract_sessions(data, fallback_uin):
    """Use explicitly named authst fields, never guess arbitrary Base64 blobs."""
    texts = [data.decode('utf-8', errors='ignore')]
    if b'a\0u\0t\0h\0s\0t\0' in data or b's\0t\0r\0A\0u\0t\0h' in data:
        texts.extend(data[start:].decode('utf-16le', errors='ignore') for start in (0, 1))
    seen = set()
    for text in texts:
        text = text.replace('\\"', '"')
        for match in TOKEN.finditer(text):
            # Restrict surrounding identity fields to the same nearby JSON object.
            left = text.rfind('{', max(0, match.start() - 4096), match.start())
            right = text.find('}', match.end(), match.end() + 4096)
            nearby = text[max(left, match.start() - 256):right if right >= 0 else match.end() + 256]
            uin_match = UIN.search(nearby)
            uin = uin_match.group(1) if uin_match else fallback_uin
            if not uin or not re.fullmatch(r'\d{5,20}', uin) or int(uin) == 0:
                continue
            token = match.group(1)
            if len(set(token)) < 4:
                continue
            login = LOGIN.search(nearby)
            session = LoginSession(uin, token, login.group(1) if login else None)
            if (uin, token) not in seen:
                seen.add((uin, token))
                yield session


def read_account(base):
    path = base / 'QQMusicServiceConfig.ini'
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        content = raw.decode('utf-16', errors='ignore')
    else:
        content = raw.decode('utf-8-sig', errors='ignore')
    match = re.search(r'^\s*uin\s*=\s*(\d{5,20})\s*$', content, re.I | re.M)
    return match.group(1) if match and int(match.group(1)) else None


class WindowsClient:
    def __init__(self):
        if os.name != 'nt':
            raise AutoKeyError('自动获取目前支持 Windows。请使用 Windows 懒人包，并运行、登录 QQ 音乐。')
        import ctypes as c
        from ctypes import wintypes as w
        self.c = c
        self.k = c.WinDLL('kernel32', use_last_error=True)
        self.a = c.WinDLL('advapi32', use_last_error=True)
        H, D, P, S = w.HANDLE, w.DWORD, c.c_void_p, c.c_size_t
        definitions = {
            'CreateToolhelp32Snapshot': ([D, D], H),
            'Process32FirstW': ([H, P], w.BOOL), 'Process32NextW': ([H, P], w.BOOL),
            'OpenProcess': ([D, w.BOOL, D], H), 'CloseHandle': ([H], w.BOOL),
            'GetCurrentProcess': ([], H),
            'QueryFullProcessImageNameW': ([H, D, w.LPWSTR, c.POINTER(D)], w.BOOL),
            'VirtualQueryEx': ([H, P, P, S], S),
            'ReadProcessMemory': ([H, P, P, S, c.POINTER(S)], w.BOOL),
        }
        for name, (args, result) in definitions.items():
            function = getattr(self.k, name)
            function.argtypes, function.restype = args, result
        for name, args, result in [
            ('OpenProcessToken', [H, D, c.POINTER(H)], w.BOOL),
            ('GetTokenInformation', [H, D, P, D, c.POINTER(D)], w.BOOL),
            ('GetLengthSid', [P], D),
        ]:
            function = getattr(self.a, name)
            function.argtypes, function.restype = args, result

        class Entry(c.Structure):
            _fields_ = [('dwSize', D), ('cntUsage', D), ('pid', D), ('heap', S),
                        ('module', D), ('threads', D), ('parent', D),
                        ('priority', w.LONG), ('flags', D), ('name', w.WCHAR * 260)]

        class Region(c.Structure):
            _fields_ = [('base', P), ('allocation', P), ('allocation_protect', D),
                        ('partition', w.WORD), ('padding', w.WORD), ('size', S),
                        ('state', D), ('protect', D), ('kind', D)]
        self.Entry, self.Region = Entry, Region
        self.owner = self.owner_sid(self.k.GetCurrentProcess())
        if not self.owner:
            raise AutoKeyError('无法确认当前 Windows 用户，未读取 QQ 音乐登录状态。')

    def owner_sid(self, handle):
        c = self.c
        token = c.c_void_p()
        if not self.a.OpenProcessToken(handle, 0x0008, c.byref(token)):
            return None
        try:
            needed = c.c_uint32()
            self.a.GetTokenInformation(token, 1, None, 0, c.byref(needed))
            if not 1 <= needed.value <= 4096:
                return None
            buffer = c.create_string_buffer(needed.value)
            if not self.a.GetTokenInformation(token, 1, buffer, needed.value, c.byref(needed)):
                return None
            sid = c.cast(buffer, c.POINTER(c.c_void_p))[0]
            length = self.a.GetLengthSid(sid)
            return c.string_at(sid, length) if 8 <= length <= 68 else None
        finally:
            self.k.CloseHandle(token)

    def processes(self):
        c = self.c
        snapshot = self.k.CreateToolhelp32Snapshot(2, 0)
        if snapshot in (None, c.c_void_p(-1).value):
            raise AutoKeyError('无法检测 QQ 音乐进程。')
        entries = []
        try:
            entry = self.Entry()
            entry.dwSize = c.sizeof(entry)
            ok = self.k.Process32FirstW(snapshot, c.byref(entry))
            while ok:
                if entry.name.lower() in ('qqmusic.exe', 'qmbrowser.exe'):
                    entries.append((int(entry.pid), entry.name.lower()))
                ok = self.k.Process32NextW(snapshot, c.byref(entry))
        finally:
            self.k.CloseHandle(snapshot)
        accepted = []
        for pid, name in entries:
            handle = self.k.OpenProcess(0x1000, False, pid)
            if not handle:
                continue
            try:
                if self.owner_sid(handle) != self.owner:
                    continue
                size = c.c_uint32(32768)
                path = c.create_unicode_buffer(size.value)
                if self.k.QueryFullProcessImageNameW(handle, 0, path, c.byref(size)):
                    accepted.append((pid, name, Path(path.value)))
            finally:
                self.k.CloseHandle(handle)
        roots = [path.parent for _, name, path in accepted if name == 'qqmusic.exe']
        # qmbrowser is accepted only from the running QQ Music installation.
        return [(pid, name) for pid, name, path in accepted
                if name == 'qqmusic.exe' or any(path.is_relative_to(root) for root in roots)]

    def scan(self, pid, fallback_uin, cancel, deadline):
        c = self.c
        handle = self.k.OpenProcess(0x0400 | 0x0010, False, pid)
        if not handle:
            return
        try:
            if self.owner_sid(handle) != self.owner:
                return
            address, scanned = 0, 0
            region = self.Region()
            buffer = c.create_string_buffer(1024 * 1024)
            while address < 0x7fffffff0000 and scanned < 1024**3 and time.monotonic() < deadline:
                checkpoint(cancel)
                if not self.k.VirtualQueryEx(handle, address, c.byref(region), c.sizeof(region)):
                    break
                base, size = int(region.base or 0), int(region.size)
                following = base + size
                if following <= address:
                    break
                readable = region.protect & 0xff in (2, 4, 8, 0x20, 0x40, 0x80)
                if region.state == 0x1000 and not region.protect & 0x100 and readable:
                    offset, overlap = 0, b''
                    while offset < size and time.monotonic() < deadline and scanned < 1024**3:
                        checkpoint(cancel)
                        count = min(size - offset, len(buffer))
                        read = c.c_size_t()
                        self.k.ReadProcessMemory(handle, base + offset, buffer, count, c.byref(read))
                        if read.value:
                            data = overlap + buffer.raw[:read.value]
                            yield from extract_sessions(data, fallback_uin)
                            overlap = data[-8192:]
                        else:
                            overlap = b''
                        offset += count
                        scanned += count
                address = following
        finally:
            self.k.CloseHandle(handle)


def windows_sessions(cancel=None, deadline=None):
    deadline = deadline or time.monotonic() + 20
    client = WindowsClient()
    processes = client.processes()
    if not any(name == 'qqmusic.exe' for _, name in processes):
        raise AutoKeyError('未检测到当前用户运行的 QQ 音乐。请打开并登录 QQ 音乐，然后点击“自动重试”。若 QQ 音乐以管理员运行，请以相同权限启动本软件。')
    appdata = os.environ.get('APPDATA')
    fallback = None
    if appdata:
        base = Path(appdata) / 'Tencent' / 'QQMusic'
        fallback = read_account(base)
        for name in ('SetCookie.dat', '_SetCookie.dat'):
            checkpoint(cancel)
            path = base / name
            try:
                if path.stat().st_size <= 4 * 1024 * 1024:
                    yield from extract_sessions(path.read_bytes(), fallback)
            except OSError:
                pass
    for pid, _ in sorted(processes, key=lambda item: item[1] != 'qqmusic.exe'):
        yield from client.scan(pid, fallback, cancel, deadline)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def request_ekey(info, session, login_type, timeout=7, opener=None):
    body = {
        'comm': {'authst': session.authst, 'uin': session.uin, 'ct': '19', 'cv': '1859',
                 'tmeLoginType': login_type},
        'req_1': {'module': 'music.vkey.GetEVkey', 'method': 'CgiGetEVkey',
                  'param': {'filename': [info['resource_name']], 'guid': '10000',
                            'songmid': [info['media_mid']], 'songtype': [1],
                            'uin': session.uin, 'loginflag': 1, 'platform': '27', 'ctx': 1}},
    }
    req = Request(ENDPOINT, data=json.dumps(body).encode('utf-8'), method='POST', headers={
        'Content-Type': 'application/json', 'Referer': 'https://y.qq.com/',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36'})
    try:
        with (opener or build_opener(NoRedirect())).open(req, timeout=timeout) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise AutoKeyError('QQ 音乐响应过大，已停止取钥。')
        data = json.loads(raw)
    except (HTTPError, URLError, OSError, ValueError):
        # Never include response bodies or request arguments in the error.
        raise AutoKeyError('连接 QQ 音乐服务器失败。请检查网络连接后自动重试。') from None
    try:
        rows = data['req_1']['data']['midurlinfo']
        if data.get('code', 0) != 0 or data['req_1'].get('code') != 0:
            raise AutoKeyError('QQ 音乐登录会话失效或请求被拒绝。请重新登录 QQ 音乐后自动重试。')
        for row in rows:
            if row.get('filename') and row['filename'] != info['resource_name']:
                continue
            if row.get('songmid') and row['songmid'] != info['media_mid']:
                continue
            key = row.get('ekey')
            if row.get('result') == 0 and isinstance(key, str) and 16 <= len(key) <= 16384:
                return key
    except (KeyError, TypeError, AttributeError):
        pass
    raise AutoKeyError('QQ 音乐未返回该资源的密钥。请确认当前账号有该歌曲的播放/下载权限，并尝试重新登录后自动重试。')


class AutoFetcher:
    def __init__(self, provider=windows_sessions, requester=request_ekey):
        self.provider = provider
        self.requester = requester
        self.session = None
        self.session_time = 0

    def clear(self):
        self.session = None
        self.session_time = 0

    def fetch(self, info, cancel=None):
        if not re.fullmatch(r'[A-Za-z0-9_.-]{8,128}\.(?:mflac\d?|mgg\d?)', info.get('resource_name', ''), re.I):
            raise AutoKeyError('musicex 资源名无效，未发送请求。')
        if not re.fullmatch(r'[A-Za-z0-9]{8,64}', info.get('media_mid', '')):
            raise AutoKeyError('musicex Media MID 无效，未发送请求。')
        deadline = time.monotonic() + 30
        seen, attempts = set(), 0
        last_error = None

        def candidates():
            if self.session and time.monotonic() - self.session_time < 60:
                yield self.session
            yield from self.provider(cancel, deadline)

        for session in candidates():
            checkpoint(cancel)
            if (session.uin, session.authst) in seen:
                continue
            seen.add((session.uin, session.authst))
            for login_type in ([session.login_type] if session.login_type else ['1', '3']):
                remaining = deadline - time.monotonic()
                if remaining <= 0 or attempts >= 8:
                    break
                attempts += 1
                try:
                    key = self.requester(info, session, login_type, timeout=min(7, remaining))
                    checkpoint(cancel)
                    self.session, self.session_time = session, time.monotonic()
                    return key
                except AutoKeyError as e:
                    last_error = e
            if time.monotonic() >= deadline or attempts >= 8:
                break
        self.clear()
        if last_error:
            raise last_error
        raise AutoKeyError('未读取到 QQ 音乐的有效登录状态。请重新登录，播放一次该歌曲，再点击“自动重试”。若 QQ 音乐以管理员运行，请以相同权限启动本软件。')
