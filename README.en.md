# Audio to MP3 · Windows portable app

[简体中文](README.md) · [繁體中文](README.zh-TW.md) · English · [日本語](README.ja.md)

Batch-convert NCM, supported MFLAC/MGG and ordinary audio to MP3 on Windows 10/11 x64. The portable release bundles Python, FFmpeg and Xiph FLAC.

## Use

1. Open [v1.1.0 downloads](https://github.com/shadow096-1/audio-to-mp3/releases/tag/v1.1.0), choose **English**, and extract **the entire archive** to a writable folder.
2. Double-click `start_windows.bat`. Your browser opens the local interface.
3. Drop audio files or select a folder. Conversion starts automatically. Choose 128/192/256/320 kbps; results are saved to `MP3输出` by default.

NCM and ordinary FLAC work offline. Newer musicex MFLAC/MGG needs QQ Music running and signed in to an account with playback/download rights for the song. Automatic ekey retrieval can fail on unsupported versions; retry or supply the resource-specific ekey manually. Unknown encrypted formats are not supported. MP3 is lossy; transcoding cannot restore quality.

The separate packages are **Chinese-Simplified-zh-CN**, **Chinese-Traditional-zh-TW**, **English**, and **Japanese**. Download only one; each is the complete app with the matching language preselected. **Source code** archives do not include the runtime.

## Languages

Simplified Chinese, Traditional Chinese, English and Japanese are available in the top-right language selector. Choose the **English** archive for English on first launch. Only the optional **Auto-Language** archive follows the browser language with English fallback. Switching preserves jobs, bitrate and the ekey currently being entered. Your manual choice is saved in `preferences.json` next to the app, even when its local port changes. That file stores only the language code. A read-only folder prevents persistence but not switching.

App labels, statuses, prompts and app-owned errors are localized. Filenames, tags, resource identifiers, the output directory and raw decoder logs are preserved.

CLI: `python converter.py --lang en song.ncm -o output`. Languages: `zh-CN`, `zh-TW`, `en`, `ja`. You can also set `AUDIO_TO_MP3_LANG`; otherwise the system locale is used, with English fallback.

Download the complete **v1.1.0** portable package from Releases for all four languages. To upgrade, exit the old app and extract the new package to a new folder. Your original audio and existing MP3 outputs are preserved. Do not update only the HTML file.

## Privacy and limitations

The server listens only on `127.0.0.1`. Audio conversion is local. Automatic key retrieval reads the current user's QQ Music session and contacts only QQ Music over HTTPS; it does not upload audio. Session credentials and ekey are not saved to disk or logs. Originals are preserved and duplicate output names get numbered. Use “Exit app” to stop the server; closing the tab does not stop it. FLAC conversion requires disk space for temporary uncompressed WAV audio.

Windows-specific QQ Music session access cannot be verified by the Linux test suite. Compatibility varies by Windows/QQ Music version. See [the main README](README.md) for format details and the FLAC verification policy.

## Development

Python 3.12; install `requirements.txt` and provide FFmpeg, FFprobe and Xiph FLAC (`FFMPEG_PATH` / `FLAC_PATH` can override paths).

```sh
python server.py
python -m unittest test_converter test_auto_ekey test_server test_i18n
node test_ui.cjs
```

Translations live in `locales.json`. Keep keys and `{placeholders}` consistent across all four languages. No online translation service is used.

Original code: GPL-2.0-or-later. See [LICENSE](LICENSE) and the main README for third-party licensing. Process only audio you are entitled to use.
