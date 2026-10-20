"""Integration tests use generated test audio, never commercial songs."""
import hashlib
import base64
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import struct
import threading
import unittest

from converter import (Cancelled, ConversionError, NCM, NCM_CORE, QMC_CORE,
    QMCv2, HardenedRC4, decode_ekey, flac_diagnostic, convert_one, discover, load_keys, musicex_info)
from libtakiyasha.ncm import CloudMusicIdentifier
from libtakiyasha.qmc import QMCv1, QMCv2QTag, QMCv2STag
from libtakiyasha.qmc._qmckeyciphers import QMCv2KeyEncryptV1


class ConversionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            raise unittest.SkipTest('需要 FFmpeg 和 FFprobe')
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.flac = cls.root / '原始音频.flac'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
            'sine=frequency=440:duration=1.2', '-y', str(cls.flac)], check=True)
        cls.audio = cls.flac.read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(dir=self.root))
        self.out = self.work / '输出 MP3'

    def assert_mp3(self, path):
        result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
            'stream=codec_name,duration', '-of', 'json', str(path)], capture_output=True, check=True)
        stream = json.loads(result.stdout)['streams'][0]
        self.assertEqual(stream['codec_name'], 'mp3')
        self.assertGreater(float(stream['duration']), 1)
        subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-f', 'null', '-'], check=True)

    def ncm(self, path):
        audio = NCM.new()
        audio.write(self.audio)
        # LibTakiyasha's property setter does not assign in this release;
        # mutate the existing tag object instead.
        audio.ncm_tag.format = 'flac'
        audio.ncm_tag.musicName = '测试标题'
        audio.ncm_tag.artist = [['测试歌手', 1]]
        audio.ncm_tag.album = '测试专辑'
        audio.save(NCM_CORE, filething=path)
        audio.close()

    def qmc(self, path, method='map', garble=None, stag=False):
        audio = QMCv2.new(method)
        audio.write(self.audio)
        master = audio.master_key
        if stag:
            audio.extra_info = QMCv2STag(song_id=1, unknown=2, song_mid='test')
        audio.save(core_key=QMC_CORE, filething=path, garble_keys=garble, with_extra_info=stag)
        audio.close()
        return master

    def test_ncm_metadata_and_source_unchanged(self):
        source = self.work / '歌曲 中文.ncm'
        self.ncm(source)
        before = hashlib.sha256(source.read_bytes()).digest()
        target = convert_one(source, self.out)
        self.assert_mp3(target)
        from mutagen.easyid3 import EasyID3
        self.assertEqual(EasyID3(target)['title'], ['测试标题'])
        self.assertEqual(EasyID3(target)['artist'], ['测试歌手'])
        self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), before)

    def test_mflac_map(self):
        source = self.work / '无损.mflac'
        self.qmc(source)
        self.assert_mp3(convert_one(source, self.out))

    def test_mflac_rc4(self):
        source = self.work / 'rc4.mflac0'
        self.qmc(source, 'rc4')
        self.assert_mp3(convert_one(source, self.out))

    def test_qtag_mflac(self):
        source = self.work / '带尾部标签.mflac0'
        audio = QMCv2.new('rc4')
        audio.write(self.audio)
        audio.extra_info = QMCv2QTag(song_id=1, unknown=2)
        audio.save(core_key=QMC_CORE, filething=source, with_extra_info=True)
        audio.close()
        self.assert_mp3(convert_one(source, self.out))

    def test_qmcv1_config(self):
        source = self.work / '旧格式.qmcflac'
        mask = bytes(range(128))
        audio = QMCv1.new(mask=mask)
        audio.write(self.audio)
        audio.save(source)
        audio.close()
        keys = {'qmc_v1_mask': 'hex:' + mask.hex()}
        self.assert_mp3(convert_one(source, self.out, keys=keys))

    def test_v2_keys_required_and_supported(self):
        source = self.work / '新版.mflac'
        garble = [b'0123456789abcdef', b'fedcba9876543210']
        self.qmc(source, garble=garble)
        with self.assertRaisesRegex(ConversionError, 'V2'):
            convert_one(source, self.out, keys={'qmc_garble_keys': []})
        keys = {'qmc_garble_keys': ['hex:' + k.hex() for k in garble]}
        self.assert_mp3(convert_one(source, self.out, keys=keys))

    def test_stag_master_key(self):
        source = self.work / '无内嵌密钥.mflac0'
        master = self.qmc(source, 'rc4', stag=True)
        with self.assertRaisesRegex(ConversionError, 'master_key'):
            convert_one(source, self.out)
        keys = {'files': {source.name: {'master_key': 'hex:' + master.hex()}}}
        self.assert_mp3(convert_one(source, self.out, keys=keys))

    def test_collision_keeps_existing(self):
        self.out.mkdir()
        existing = self.out / (self.flac.stem + '.mp3')
        existing.write_bytes(b'keep existing')
        result = convert_one(self.flac, self.out, bitrate=192)
        self.assertEqual(existing.read_bytes(), b'keep existing')
        self.assertEqual(result.name, self.flac.stem + ' (1).mp3')
        self.assert_mp3(result)

    def test_corrupt_and_unknown_no_output(self):
        for name in ['bad.ncm', 'bad.mflac', 'bad.flac', 'bad.unknown']:
            source = self.work / name
            source.write_bytes(b'not audio')
            with self.assertRaises(ConversionError):
                convert_one(source, self.out)
        self.assertFalse(list(self.out.glob('*.mp3')))
        self.assertFalse(list(self.out.glob('.audio-to-mp3-*')))

    def test_cancel(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(Cancelled):
            convert_one(self.flac, self.out, cancel=cancel)
        self.assertFalse(self.out.exists())

    def test_discovery_and_batch_partial_failure(self):
        self.ncm(self.work / 'good.ncm')
        (self.work / 'bad.ncm').write_bytes(b'bad')
        (self.work / 'notes.txt').write_text('ignore')
        self.assertEqual(len(discover([self.work, self.work / 'good.ncm'])), 2)
        result = subprocess.run([sys.executable, str(Path(__file__).with_name('converter.py')), '--lang', 'zh-CN',
            str(self.work), '-o', str(self.out)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('成功 1，失败 1', result.stdout)
        self.assert_mp3(self.out / 'good.mp3')

    def test_invalid_configuration(self):
        path = self.work / 'keys.json'
        path.write_text('[]')
        with self.assertRaises(ConversionError):
            load_keys(path)

    def test_musicex_ekey_and_wrong_key(self):
        source = self.work / 'musicex.mflac'
        audio = QMCv2.new('rc4')
        audio.write(self.audio)
        master = audio.master_key
        footer = bytearray(192)
        footer[:12] = struct.pack('<III', 1234, 2, 5)
        footer[12:40] = '001t1RpP3ZmrwI'.encode('utf-16le')
        resource = 'F0M000150F7f2ztQel.mflac'.encode('utf-16le')
        footer[72:72+len(resource)] = resource
        footer[-16:] = struct.pack('<II', 192, 1) + b'musicex\0'
        source.write_bytes(audio.getvalue(nocryptlayer=True) + footer)
        info = musicex_info(source)
        self.assertEqual(info['song_id'], 1234)
        self.assertEqual(info['audio_size'], len(self.audio))
        self.assertEqual(info['resource_name'], 'F0M000150F7f2ztQel.mflac')
        with self.assertRaisesRegex(ConversionError, 'musicex'):
            convert_one(source, self.out)
        ekey = base64.b64encode(QMCv2KeyEncryptV1(QMC_CORE).encrypt(master)).decode('ascii')
        keys = {'files': {source.name: {'ekey': ekey}}}
        self.assert_mp3(convert_one(source, self.out, keys=keys))
        wrong = {'files': {source.name: {'master_key': 'hex:' + bytes(range(128)).hex()}}}
        with self.assertRaisesRegex(ConversionError, '不匹配'):
            convert_one(source, self.out, keys=wrong)

    def test_musicex_invalid_footer(self):
        source = self.work / 'corrupt.mflac'
        source.write_bytes(b'abc' + struct.pack('<II', 99999, 1) + b'musicex\0')
        with self.assertRaises(ConversionError):
            musicex_info(source)

    def test_wrapped_key_length_overlaps_raw_length(self):
        master = bytes(33 + i % 90 for i in range(496))
        wrapped = QMCv2KeyEncryptV1(QMC_CORE).encrypt(master)
        self.assertEqual(len(wrapped), 512)
        self.assertEqual(decode_ekey(base64.b64encode(wrapped).decode()), master)
        raw = bytes(33 + i % 90 for i in range(512))
        self.assertEqual(decode_ekey(base64.b64encode(raw).decode()), raw)

    def test_rc4_independent_vectors_and_chunk_offsets(self):
        # Fixed vectors cross-checked with qmunlock/qmc2.py (MIT), not
        # generated using the implementation under test.
        cipher = HardenedRC4(bytes(33 + i % 90 for i in range(512)))
        vectors = {
            0: 'c3e1626453586119bfd947e35f5c957ad9d3d2dd5a7d9b6b8fa1eaaa2781768d',
            120: '07622190a5064215d0eda784a60eb41e7e8c3e10ebb3e4e2e843e172a5432683',
            5112: '314cbb5992f9846d3a05c63a4237f5b63a66df81c8e8ca68e61eec1db452b578',
            1048573: 'bb11d1c3fb3311e03c79b9695991b53ff00e01d86ef1b0e84a0190b75dd1c665',
            2621432: 'de39e953162d5690923f17af1fe5baec8920b1a5bc167c1e9ba3397c52529e7f',
        }
        for offset, digest in vectors.items():
            self.assertEqual(hashlib.sha256(cipher.decrypt(bytes(16384), offset)).hexdigest(), digest)
            chunks = b''.join(cipher.decrypt(bytes(min(997, 16384-i)), offset+i)
                              for i in range(0, 16384, 997))
            self.assertEqual(hashlib.sha256(chunks).hexdigest(), digest)

    def test_safe_flac_structure_diagnostic(self):
        self.assertIn('首帧同步=正常', flac_diagnostic(self.flac))
        broken = self.work / 'broken.flac'
        broken.write_bytes(b'fLaC' + b'\x00\xff\xff\xff')
        self.assertIn('结构无效', flac_diagnostic(broken))

    def test_flac_md5_mismatch_rejected(self):
        source = self.work / 'wrong-md5.flac'
        data = bytearray(self.audio)
        data[26] ^= 1  # STREAMINFO MD5 only; the frames remain decodable.
        source.write_bytes(data)
        with self.assertRaisesRegex(ConversionError, 'FLAC 校验与解码'):
            convert_one(source, self.out)
        self.assertFalse(list(self.out.glob('*.mp3')))
        self.assertFalse(list(self.out.glob('.audio-to-mp3-*')))

    def test_flac_tags_preserved_through_pcm(self):
        source = self.work / 'tagged.flac'
        source.write_bytes(self.audio)
        from mutagen.flac import FLAC
        tag = FLAC(source)
        tag['title'] = ['原始标题']
        tag['artist'] = ['歌手甲', '歌手乙']
        tag['album'] = ['原始专辑']
        tag.save()
        result = convert_one(source, self.out)
        from mutagen.easyid3 import EasyID3
        output = EasyID3(result)
        self.assertEqual(output['title'], ['原始标题'])
        self.assertEqual(output['artist'], ['歌手甲; 歌手乙'])
        self.assertEqual(output['album'], ['原始专辑'])
        self.assert_mp3(result)

    def test_flac_trailer_accepted_only_after_complete_verified_audio(self):
        source = self.work / 'trailer.flac'
        source.write_bytes(self.audio + bytes.fromhex('f000ff0f44444048463c360e55fff0'))
        before = source.read_bytes()
        self.assert_mp3(convert_one(source, self.out))
        self.assertEqual(source.read_bytes(), before)
        self.assertFalse(list(self.out.glob('.audio-to-mp3-*')))

    def test_flac_trailer_without_md5_rejected(self):
        source = self.work / 'unchecked-trailer.flac'
        data = bytearray(self.audio)
        data[26:42] = bytes(16)
        source.write_bytes(data + b'not audio')
        with self.assertRaisesRegex(ConversionError, 'FLAC 校验与解码'):
            convert_one(source, self.out)
        self.assertFalse(list(self.out.glob('*.mp3')))

    def test_corrupt_flac_frames_not_treated_as_trailer(self):
        source = self.work / 'corrupt-frames.flac'
        data = bytearray(self.audio)
        data[-100] ^= 128
        source.write_bytes(data + b'trailer')
        with self.assertRaisesRegex(ConversionError, 'FLAC 校验与解码'):
            convert_one(source, self.out)
        self.assertFalse(list(self.out.glob('*.mp3')))


if __name__ == '__main__':
    unittest.main()
