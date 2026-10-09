"""Local audio conversion core. No network access, original files stay untouched."""
from __future__ import annotations

import argparse
import base64
import json
import io
import os
from pathlib import Path
import shutil
import hashlib
import re
import struct
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'vendor'))
from libtakiyasha import NCM, QMCv1, QMCv2
from libtakiyasha.qmc import probe_qmc
from libtakiyasha.qmc._qmckeyciphers import make_core_key
from libtakiyasha.qmc._qmckeyciphers import QMCv2KeyEncryptV1, QMCv2KeyEncryptV2
from libtakiyasha.qmc._qmcdataciphers import Mask128, HardenedRC4

NCM_CORE = b'hzHRAmso5kInbaxW'
QMC_CORE = make_core_key(106, 8)
QMC_GARBLE = [b'386ZJY!@#*$%^&)(', b'**#!(#$%&^a1cZ,T']
PLAIN = {'.mp3', '.flac', '.wav', '.ogg', '.m4a', '.aac', '.opus', '.wma', '.aiff', '.ape'}
ENCRYPTED = {'.ncm', '.mflac', '.mgg', '.qmcflac', '.qmcogg', '.qmcra'}
ENCRYPTED.update(f'.{prefix}{n}' for prefix in ('mflac', 'mgg', 'qmc') for n in range(10))


class ConversionError(Exception):
    pass


class Cancelled(ConversionError):
    pass


def checkpoint(cancel):
    if cancel and cancel.is_set():
        raise Cancelled('已取消')


def find_ffmpeg(explicit=None):
    candidate = explicit or os.environ.get('FFMPEG_PATH')
    if not candidate:
        name = 'ffmpeg.exe' if os.name == 'nt' else 'ffmpeg'
        local = ROOT / 'bin' / name
        candidate = str(local) if local.is_file() else shutil.which('ffmpeg')
    if not candidate:
        raise ConversionError('未找到 FFmpeg。请安装并加入 PATH，或在界面中选择 ffmpeg 程序。')
    resolved = shutil.which(str(candidate)) or str(Path(candidate).expanduser().resolve())
    if not Path(resolved).is_file():
        raise ConversionError('FFmpeg 程序路径不存在。')
    return resolved


def find_flac():
    name = 'flac.exe' if os.name == 'nt' else 'flac'
    local = ROOT / 'bin' / name
    candidate = os.environ.get('FLAC_PATH') or (str(local) if local.is_file() else shutil.which('flac'))
    if not candidate or not Path(candidate).is_file():
        raise ConversionError('缺少专用 FLAC 解码器，请完整解压新版懒人包（包含 bin/flac.exe 和 libFLAC.dll）。')
    return str(Path(candidate).resolve())


def run_checked_audio_command(cmd, log_path, cancel, label):
    """File-backed logging, cancellation and a strict exit check for both codecs."""
    with log_path.open('w+b') as log:
        try:
            process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        except OSError as e:
            raise ConversionError(f'无法运行{label}：{e}') from e
        try:
            while process.poll() is None:
                checkpoint(cancel)
                (cancel or threading.Event()).wait(0.1)
            checkpoint(cancel)
        except BaseException:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            raise
        if process.returncode:
            log.seek(0)
            detail = log.read(4096).decode('utf-8', errors='replace').strip()
            raise ConversionError(f'{label}失败：\n{detail}')


def verify_complete_flac_pcm(flac_path, wav_path, cancel=None):
    """Verify exact sample count and STREAMINFO MD5 before accepting a trailer."""
    with flac_path.open('rb') as f:
        head = f.read(42)
    if len(head) != 42 or head[:4] != b'fLaC' or head[4:8] != b'\x00\x00\x00\x22':
        # Last-block flag is legal for files with STREAMINFO only.
        if len(head) != 42 or head[:4] != b'fLaC' or head[4:8] != b'\x80\x00\x00\x22':
            raise ConversionError('无法校验 FLAC STREAMINFO，拒绝跳过解码错误。')
    packed = int.from_bytes(head[18:26], 'big')
    samples, channels = packed & ((1 << 36) - 1), ((packed >> 41) & 7) + 1
    bits, rate = ((packed >> 36) & 31) + 1, packed >> 44
    expected_md5 = head[26:42]
    if not samples or expected_md5 == bytes(16) or bits not in (8, 16, 24, 32):
        raise ConversionError('缺少可验证的采样数或 MD5，拒绝跳过 FLAC 解码错误。')
    digest, found_fmt, found_data = hashlib.md5(), False, False
    with wav_path.open('rb') as f:
        header = f.read(12)
        if header[:4] != b'RIFF' or header[8:] != b'WAVE':
            raise ConversionError('FLAC 解码后的 WAV 结构不受支持。')
        size = wav_path.stat().st_size
        while f.tell() + 8 <= size:
            chunk = f.read(8)
            kind, length = chunk[:4], int.from_bytes(chunk[4:], 'little')
            start = f.tell()
            if start + length > size:
                raise ConversionError('FLAC 解码后的 WAV 数据截断。')
            if kind == b'fmt ':
                fmt = f.read(min(length, 40))
                if len(fmt) < 16:
                    raise ConversionError('PCM 格式头截断。')
                code, actual_channels, actual_rate = struct.unpack('<HHI', fmt[:8])
                actual_bits = int.from_bytes(fmt[14:16], 'little')
                if code not in (1, 65534) or (actual_channels, actual_rate, actual_bits) != (channels, rate, bits):
                    raise ConversionError('PCM 参数与 FLAC STREAMINFO 不一致。')
                found_fmt = True
            elif kind == b'data':
                if found_data or length != samples * channels * (bits // 8):
                    raise ConversionError('解码后的实际采样数与 FLAC STREAMINFO 不一致。')
                found_data = True
                remaining = length
                while remaining:
                    checkpoint(cancel)
                    data = f.read(min(1024 * 1024, remaining))
                    if bits == 8:
                        data = bytes(value ^ 128 for value in data)
                    digest.update(data)
                    remaining -= len(data)
            f.seek(start + length + (length & 1))
    if not found_fmt or not found_data or digest.digest() != expected_md5:
        raise ConversionError('完整音频 MD5 校验失败，拒绝输出 MP3。')
    return samples


def decode_flac_to_pcm(source, target, work, cancel):
    cmd = [find_flac(), '--decode', '--silent', '--force',
           '--output-name=' + str(target), str(source)]
    log = work / 'flac.log'
    try:
        run_checked_audio_command(cmd, log, cancel, 'FLAC 校验与解码')
    except Cancelled:
        raise
    except ConversionError as original:
        # Some supplied files have non-audio bytes AFTER the complete stream.
        # Recover only if all decoder errors occur after the advertised final
        # sample, and the entire PCM stream passes its original MD5 checksum.
        try:
            run_checked_audio_command(cmd[:1] + ['--decode-through-errors'] + cmd[1:],
                log, cancel, 'FLAC 尾部诊断解码')
            samples = verify_complete_flac_pcm(source, target, cancel)
            if log.stat().st_size > 256 * 1024:
                raise ConversionError('FLAC 解码错误过多。')
            text = log.read_text(encoding='utf-8', errors='replace')
            errors = re.findall(r'Got error code \d+:(\w+) after processing (\d+) samples', text)
            if not errors or any(kind != 'FLAC__STREAM_DECODER_ERROR_STATUS_LOST_SYNC'
                                 or int(count) != samples for kind, count in errors):
                raise ConversionError('错误位于完整音频结束之前。')
        except Cancelled:
            raise
        except ConversionError:
            raise original from None


def load_keys(path=None):
    if not path:
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        if not isinstance(data, dict):
            raise ValueError('配置必须是 JSON 对象')
        return data
    except (OSError, ValueError) as e:
        raise ConversionError(f'无法读取密钥配置：{e}') from e


def decode_key(value):
    if not isinstance(value, str):
        raise ConversionError('密钥应是以 hex: 或 base64: 开头的字符串。')
    try:
        if value.startswith('hex:'):
            return bytes.fromhex(value[4:])
        if value.startswith('base64:'):
            return base64.b64decode(value[7:], validate=True)
    except ValueError as e:
        raise ConversionError('密钥的十六进制或 Base64 编码无效。') from e
    raise ConversionError('密钥应以 hex: 或 base64: 开头。')


def musicex_info(source):
    """musicex length includes the complete footer in the supplied samples."""
    size = source.stat().st_size
    if size < 16:
        return None
    with source.open('rb') as f:
        f.seek(-16, 2)
        tail = f.read(16)
        if tail[8:] != b'musicex\0':
            return None
        length = int.from_bytes(tail[:4], 'little')
        version = int.from_bytes(tail[4:8], 'little')
        if version != 1 or length < 156 or length >= size or length > 1024 * 1024:
            raise ConversionError('musicex 尾部长度或版本不受支持。')
        f.seek(-length, 2)
        footer = f.read(length)
    return {'format': 'musicex', 'version': version, 'audio_size': size - length,
            'song_id': int.from_bytes(footer[:4], 'little'),
            'media_mid': footer[12:72].decode('utf-16le').rstrip('\0'),
            'resource_name': footer[72:140].decode('utf-16le').rstrip('\0'),
            'embedded_key': False}


def decode_ekey(value, core=QMC_CORE, garble=None):
    """Accept wrapped EncV1/EncV2 ekey, not a password or account cookie."""
    if not isinstance(value, str):
        raise ConversionError('ekey 必须是 Base64 字符串。')
    try:
        raw = base64.b64decode(value.strip().removeprefix('base64:'), validate=True)
        if raw.startswith(b'QQMusic EncV2,Key:'):
            return QMCv2KeyEncryptV2(core, garble or QMC_GARBLE).decrypt(raw[18:])
        # EncV1 lengths overlap raw-key lengths. Check TEA's seven zero
        # padding bytes before considering the raw-key fallback.
        try:
            return QMCv2KeyEncryptV1(core).decrypt(raw)
        except Exception:
            if len(raw) in (128, 256, 512):
                return raw
            raise
    except Exception as e:
        raise ConversionError('ekey 编码或封装无效，请提供该资源的完整 ekey；解密后主密钥请使用 master_key。') from e


class MusicexAudio(io.BytesIO):
    def __init__(self, data, cipher):
        super().__init__(data)
        self.cipher = cipher
        self.diagnostic = ''

    def read(self, size=-1):
        offset = self.tell()
        data = super().read(size)
        return self.cipher.decrypt(data, offset)


def flac_diagnostic(path):
    """Report structure only: no audio bytes, keys, or account fields."""
    size = path.stat().st_size
    with path.open('rb') as f:
        if f.read(4) != b'fLaC':
            return '解密结果不是原生 FLAC'
        count = 0
        while count < 128:
            header = f.read(4)
            if len(header) != 4:
                return 'FLAC 元数据头截断'
            kind = header[0] & 127
            length = int.from_bytes(header[1:], 'big')
            if (count == 0 and (kind != 0 or length != 34)) or kind == 127:
                return 'FLAC 元数据结构无效'
            if f.tell() + length > size:
                return 'FLAC 元数据长度越界'
            f.seek(length, 1)
            count += 1
            if header[0] & 128:
                offset = f.tell()
                frame = f.read(2)
                sync = len(frame) == 2 and frame[0] == 255 and (frame[1] & 254) == 248
                return f'FLAC 元数据块={count}，首帧偏移={offset}，首帧同步={"正常" if sync else "异常"}'
        return 'FLAC 元数据块过多'


def open_encrypted(source, keys):
    suffix = source.suffix.lower()
    if suffix == '.ncm':
        return NCM.open(source, core_key=NCM_CORE)
    file_keys = keys.get('files', {})
    if not isinstance(file_keys, dict):
        raise ConversionError('配置 files 必须是对象。')
    selected = file_keys.get(str(source.resolve()), file_keys.get(source.name, {}))
    if not isinstance(selected, dict):
        raise ConversionError('每个文件的密钥配置必须是对象。')
    musicex = musicex_info(source)
    if musicex:
        if selected.get('master_key'):
            master = decode_key(selected['master_key'])
        elif selected.get('ekey'):
            core = decode_key(keys['qmc_core_key']) if keys.get('qmc_core_key') else QMC_CORE
            master = decode_ekey(selected['ekey'], core)
        else:
            raise ConversionError('这是新版 musicex MFLAC/MGG，文件中没有歌曲解密密钥。请点击“填写 ekey”，或转换已有的 FLAC。')
        if len(master) == 128:
            cipher = Mask128(master)
        elif len(master) == 256:
            cipher = Mask128.from_qmcv2_key256(master)
        elif 300 < len(master) <= 4096:
            cipher = HardenedRC4(master)
        else:
            raise ConversionError(f'musicex 主密钥长度不受支持（实际 {len(master)} 字节）。')
        with source.open('rb') as f:
            data = f.read(musicex['audio_size'])
        audio = MusicexAudio(data, cipher)
        audio.diagnostic = f'主密钥长度={len(master)}，算法={type(cipher).__name__}，音频数据长度={len(data)}'
        head = audio.read(16)
        audio.seek(0)
        if not (head.startswith((b'fLaC', b'OggS', b'ID3')) or head[:1] == b'\xff'):
            audio.close()
            raise ConversionError('该歌曲密钥与音频不匹配，解密后没有可识别的音频头。')
        return audio
    _, info = probe_qmc(source)
    if info is None:
        raise ConversionError('无法识别此加密版本；可能缺少内嵌密钥、版本不受支持或文件损坏。')
    if info.version == 1:
        if not keys.get('qmc_v1_mask'):
            raise ConversionError('此 QMCv1 文件需要配置 qmc_v1_mask。')
        return QMCv1.open((source, info), mask=decode_key(keys['qmc_v1_mask']))
    # Absolute paths disambiguate duplicate names; basename is a convenience fallback.
    selected = file_keys.get(str(source.resolve()), file_keys.get(source.name, {}))
    if not isinstance(selected, dict):
        raise ConversionError('每个文件的密钥配置必须是对象。')
    master = decode_key(selected['master_key']) if selected.get('master_key') else None
    if info.master_key_encrypted is None and master is None:
        raise ConversionError('此文件未内嵌密钥（如 STag）；请在配置 files 中提供该文件的 master_key。')
    garble_values = keys.get('qmc_garble_keys')
    if garble_values is None:
        garble_values = ['hex:' + k.hex() for k in QMC_GARBLE]
    if not isinstance(garble_values, list):
        raise ConversionError('qmc_garble_keys 必须是数组。')
    garble = [decode_key(k) for k in garble_values]
    if info.master_key_encryption_ver == 2 and not garble and not master:
        raise ConversionError('此 MFLAC/MGG 使用 V2 密钥封装；请配置 qmc_garble_keys 或该歌曲的 master_key。')
    core = decode_key(keys['qmc_core_key']) if keys.get('qmc_core_key') else QMC_CORE
    return QMCv2.open((source, info), core_key=core, garble_keys=garble, master_key=master)


def discover(inputs, recursive=False):
    found = []
    seen = set()
    for item in inputs:
        path = Path(item).expanduser().resolve()
        if not path.exists():
            raise ConversionError(f'路径不存在：{path}')
        candidates = sorted(path.rglob('*') if recursive else path.iterdir()) if path.is_dir() else [path]
        for f in candidates:
            if f.is_file() and (not path.is_dir() or f.suffix.lower() in PLAIN | ENCRYPTED):
                normalized = f.resolve()
                if normalized not in seen:
                    seen.add(normalized)
                    found.append(normalized)
    return found


def publish(stage, folder, stem):
    """Exclusive create means existing MP3s are never overwritten."""
    counter = 0
    while True:
        name = f'{stem}.mp3' if counter == 0 else f'{stem} ({counter}).mp3'
        target = folder / name
        try:
            dest = target.open('xb')
        except FileExistsError:
            counter += 1
            continue
        try:
            with dest, stage.open('rb') as src:
                shutil.copyfileobj(src, dest)
            return target
        except BaseException:
            # Close before removing, also on Windows.
            dest.close()
            target.unlink(missing_ok=True)
            raise


def convert_one(source, output, bitrate=320, ffmpeg=None, keys=None, cancel=None):
    source = Path(source).expanduser().resolve()
    folder = Path(output).expanduser().resolve()
    if not source.is_file():
        raise ConversionError('输入文件不存在。')
    if bitrate not in (128, 192, 256, 320):
        raise ConversionError('码率必须是 128、192、256 或 320。')
    ffmpeg = find_ffmpeg(ffmpeg)
    if source.suffix.lower() not in PLAIN | ENCRYPTED:
        raise ConversionError(f'不支持的扩展名：{source.suffix}')
    checkpoint(cancel)
    folder.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.audio-to-mp3-', dir=folder) as tmp:
        work = Path(tmp)
        decoded = source
        metadata = {}
        diagnostic = ''
        if source.suffix.lower() in ENCRYPTED:
            try:
                with open_encrypted(source, keys or {}) as audio:
                    checkpoint(cancel)
                    decoded = work / 'decoded.audio'
                    audio.seek(0)
                    diagnostic = getattr(audio, 'diagnostic', '')
                    with decoded.open('wb') as out:
                        while True:
                            checkpoint(cancel)
                            block = audio.read(1024 * 1024)
                            if not block:
                                break
                            out.write(block)
                    if diagnostic:
                        diagnostic += '；' + flac_diagnostic(decoded)
                    if isinstance(audio, NCM):
                        tag = audio.ncm_tag
                        metadata['title'] = tag.musicName or ''
                        metadata['album'] = tag.album or ''
                        metadata['artist'] = ', '.join(str(a[0]) for a in (tag.artist or []) if a)
            except (ConversionError, Cancelled):
                raise
            except Exception as e:
                raise ConversionError(f'解密失败：{type(e).__name__}；密钥不匹配、版本不支持或文件损坏。') from e
        with decoded.open('rb') as f:
            native_flac = f.read(4) == b'fLaC'
        if native_flac:
            # Use the reference decoder for FLAC, including frame CRC and
            # STREAMINFO MD5 checks. FFmpeg receives PCM WAV, never FLAC.
            try:
                from mutagen.flac import FLAC
                tags = FLAC(decoded)
                for field in ('title', 'artist', 'album', 'date', 'tracknumber', 'genre'):
                    if field not in metadata and tags.get(field):
                        metadata[field] = '; '.join(tags[field])
            except Exception:
                pass  # The reference decoder will validate the actual stream.
            wav = work / 'decoded.wav'
            decode_flac_to_pcm(decoded, wav, work, cancel)
            if not wav.is_file() or wav.stat().st_size <= 44:
                raise ConversionError('FLAC 解码器没有生成有效的 PCM 音频。')
            decoded = wav
        stage = work / 'encoded.mp3'
        cmd = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-xerror', '-y',
               '-i', str(decoded), '-map', '0:a:0', '-vn', '-c:a', 'libmp3lame',
               '-b:a', f'{bitrate}k', '-id3v2_version', '3']
        for field, value in metadata.items():
            if value:
                cmd += ['-metadata', f'{field}={value}']
        cmd += [str(stage)]
        try:
            run_checked_audio_command(cmd, work / 'ffmpeg.log', cancel, 'MP3 编码')
        except Cancelled:
            raise
        except ConversionError as e:
            if diagnostic:
                raise ConversionError(f'{e}\n诊断 v4：{diagnostic}') from e
            raise
        if not stage.is_file() or stage.stat().st_size == 0:
            raise ConversionError('MP3 编码器没有生成有效输出。')
        checkpoint(cancel)
        return publish(stage, folder, source.stem)


def main():
    parser = argparse.ArgumentParser(description='本地批量音频转 MP3（NCM、部分 MFLAC/MGG、普通音频）')
    parser.add_argument('inputs', nargs='+', help='文件或文件夹')
    parser.add_argument('-o', '--output', required=True, help='输出文件夹')
    parser.add_argument('-r', '--recursive', action='store_true', help='扫描子文件夹')
    parser.add_argument('-b', '--bitrate', type=int, choices=(128, 192, 256, 320), default=320)
    parser.add_argument('--ffmpeg', help='FFmpeg 程序路径')
    parser.add_argument('--keys', help='可选 JSON 密钥配置')
    args = parser.parse_args()
    try:
        ffmpeg = find_ffmpeg(args.ffmpeg)
        keys = load_keys(args.keys)
        files = discover(args.inputs, args.recursive)
        if not files:
            raise ConversionError('未找到支持的音频文件。')
    except ConversionError as e:
        print(f'错误：{e}', file=sys.stderr)
        return 2
    failures = 0
    for index, source in enumerate(files, 1):
        try:
            target = convert_one(source, args.output, args.bitrate, ffmpeg, keys)
            print(f'[{index}/{len(files)}] 成功：{source.name} → {target}')
        except Exception as e:
            failures += 1
            print(f'[{index}/{len(files)}] 失败：{source.name}：{e}', file=sys.stderr)
    print(f'完成：成功 {len(files) - failures}，失败 {failures}')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
