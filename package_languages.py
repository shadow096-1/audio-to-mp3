"""Build complete Windows portable archives with a preselected UI language.

Usage: python package_languages.py AudioToMP3-v1.1.0.zip --output dist
No third-party build dependencies. Runtime binaries are copied unchanged.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

PACKAGES = {
    'zh-CN': ('Chinese-Simplified-zh-CN', '简体中文', 'README.md',
              '完整解压整个文件夹，双击 start_windows.bat。默认使用简体中文。'),
    'zh-TW': ('Chinese-Traditional-zh-TW', '繁體中文', 'README.zh-TW.md',
              '完整解壓縮整個資料夾，按兩下 start_windows.bat。預設使用繁體中文。'),
    'en': ('English', 'English', 'README.en.md',
           'Extract the entire folder and run start_windows.bat. The interface starts in English.'),
    'ja': ('Japanese', '日本語', 'README.ja.md',
           'フォルダーをすべて展開して start_windows.bat を起動してください。既定の言語は日本語です。'),
}


def build(source, output):
    source, output = Path(source), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source) as archive:
        if archive.testzip() is not None:
            raise ValueError('Corrupt input archive')
        entry = next(n for n in archive.namelist() if n.endswith('/version.py'))
        prefix = entry.rsplit('/', 1)[0] + '/'
        version = re.search(r'__version__\s*=\s*[\'"]([^\'"]+)', archive.read(entry).decode())[1]
        if not re.fullmatch(r'\d+\.\d+\.\d+', version):
            raise ValueError('Unsupported version')
        original = {n[len(prefix):]: archive.read(n) for n in archive.namelist()
                    if n.startswith(prefix) and not n.endswith('/')}
    # Update app source/docs when run from this repository; never bundle dev files or keys.
    allowed = ['server.py', 'converter.py', 'webui.html', 'version.py', 'i18n.py',
               'locales.json', 'README.md', 'README.zh-TW.md', 'README.en.md',
               'README.ja.md', 'CHANGELOG.md', 'package_languages.py']
    for name in allowed:
        path = Path(__file__).with_name(name)
        if path.is_file():
            original[name] = path.read_bytes()
    if version not in original['version.py'].decode():
        raise ValueError('Source and portable base versions differ')
    original.pop('checksums.sha256', None)
    original.pop('preferences.json', None)
    results = []
    for language, (slug, label, readme, instruction) in PACKAGES.items():
        folder = f'AudioToMP3-v{version}-{slug}'
        filename = f'{folder}-Windows-x64.zip'
        target = output / filename
        if target.resolve() == source.resolve():
            raise ValueError('Output must not replace input')
        payload = dict(original)
        payload['preferences.json'] = (json.dumps({'language': language}) + '\n').encode()
        payload['START-HERE.txt'] = (f'AudioToMP3 v{version} · {label}\n\n{instruction}\n\n'
                                     f'{readme}\n').encode('utf-8')
        payload['checksums.sha256'] = ''.join(
            hashlib.sha256(data).hexdigest()+'  '+name+'\n'
            for name, data in sorted(payload.items())).encode()
        with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for name, data in sorted(payload.items()):
                archive.writestr(folder+'/'+name, data)
        with zipfile.ZipFile(target) as archive:
            if archive.testzip() is not None:
                raise ValueError('Corrupt output archive')
            config = json.loads(archive.read(folder+'/preferences.json'))
            assert config == {'language': language}
            # The interpreter, codecs, DLLs and bundled licenses are unchanged.
            for name in ('runtime/python.exe', 'bin/ffmpeg.exe', 'bin/flac.exe',
                         'bin/libFLAC.dll', 'LICENSE'):
                assert archive.read(folder+'/'+name) == original[name]
            for line in archive.read(folder+'/checksums.sha256').decode().splitlines():
                digest, name = line.split('  ', 1)
                assert hashlib.sha256(archive.read(folder+'/'+name)).hexdigest() == digest
        results.append({'language': language, 'label': label, 'file': str(target.resolve()),
                        'filename': filename, 'size': target.stat().st_size,
                        'sha256': hashlib.sha256(target.read_bytes()).hexdigest()})
        print(f'{label}: {filename}', flush=True)
    (output/'packages.json').write_text(json.dumps(results, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, default=Path('dist'))
    args = parser.parse_args()
    build(args.source, args.output)
