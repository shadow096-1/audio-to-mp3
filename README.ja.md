# 音声を MP3 に変換 · Windows ポータブル版

[简体中文](README.md) · [繁體中文](README.zh-TW.md) · [English](README.en.md) · 日本語

Windows 10/11 x64 向けの一括音声変換ツールです。NCM、対応する MFLAC/MGG、FLAC などを MP3 に変換します。ポータブル配布版には Python、FFmpeg、Xiph FLAC を同梱しています。

## 使い方

1. [Releases](https://github.com/shadow096-1/audio-to-mp3/releases) から `AudioToMP3.zip` をダウンロードし、書き込み可能なフォルダーに**すべて展開**します。
2. `start_windows.bat` を起動すると、ブラウザーにローカル画面が開きます。
3. 音声をドロップするかフォルダーを選択すると、自動で変換します。128/192/256/320 kbps を選択でき、既定の出力先は `MP3输出` です。

通常の NCM / FLAC はオフラインで処理できます。新しい musicex MFLAC/MGG の自動キー取得には、QQ Music の起動とログイン、および対象曲の再生・ダウンロード権限が必要です。失敗した場合は再試行するか、対象リソースの ekey を入力します。未知の暗号化形式やすべてのバージョンへの対応は保証しません。MP3 は非可逆圧縮であり、変換しても元の品質を超えることはありません。

## 言語

右上の「言語」で簡体字中国語、繁体字中国語、英語、日本語を切り替えられます。初回はブラウザーの言語に従い、未対応の場合は英語を使用します。切り替えても変換一覧、ビットレート、入力中の ekey は消えません。

選択した言語はアプリのフォルダー内の `preferences.json` に保存され、再起動やローカルポートの変更後も維持されます。このファイルには言語コードのみ保存します。フォルダーに書き込めない場合は通知しますが、その場での切り替えは可能です。

画面、状態、アプリのエラーを翻訳します。ファイル名、タグ、出力先、リソース名、外部デコーダーの生ログは変更しません。

CLI の例：`python converter.py --lang ja song.ncm -o output`。言語コード：`zh-CN`、`zh-TW`、`en`、`ja`。環境変数 `AUDIO_TO_MP3_LANG` でも指定でき、未指定時はシステム言語、未対応時は英語を使用します。

**既存の古い Release ZIP には、この多言語化のソースはまだ含まれていません。** 既存のポータブル版を更新する場合は `server.py`、`converter.py`、`webui.html` を置き換え、`i18n.py`、`locales.json` を一緒に追加してください。ランタイム、実行ファイル、vendor は保持します。

## データと注意点

サーバーは `127.0.0.1` のみで待ち受け、音声は本機で処理します。自動キー取得時のみ、現在のユーザーの QQ Music のログイン情報を読み取り、QQ Music の HTTPS 接口に問い合わせます。音声をアップロードせず、ログイン情報と ekey をディスクやログに保存しません。

元ファイルは保持し、同名の出力には連番を付けます。終了には「アプリを終了」を使用してください。タブを閉じるだけでは終了しません。FLAC 変換には一時 WAV 用の空き容量が必要です。

Linux でのテストでは、実際の Windows / QQ Music のログイン情報の読み取りを検証できません。詳細と対応範囲は[メイン README](README.md)を参照してください。

## 開発

Python 3.12、`requirements.txt` の依存関係、FFmpeg、FFprobe、Xiph FLAC が必要です。`FFMPEG_PATH` / `FLAC_PATH` で実行ファイルを指定できます。

```sh
python server.py
python -m unittest test_converter test_auto_ekey test_server test_i18n
node test_ui.cjs
```

翻訳は `locales.json` で管理し、オンライン翻訳サービスは使用しません。原著コードは GPL-2.0-or-later。第三者ライセンスはメイン README と配布物を参照してください。使用権限のある音声のみを処理してください。
