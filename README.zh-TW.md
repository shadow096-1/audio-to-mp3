# 音訊轉 MP3 · Windows 懶人包

[简体中文](README.md) · 繁體中文 · [English](README.en.md) · [日本語](README.ja.md)

Windows 10/11 x64 批次音訊轉換工具，支援 NCM、常見 MFLAC/MGG、FLAC 等格式。內建 Python、FFmpeg 和 Xiph FLAC，不需要安裝執行環境。

## 下載與使用

在本倉庫的 **Releases** 中下載 `AudioToMP3.zip`。

1. 將 ZIP 完整解壓到一個可寫資料夾。
2. 雙擊 `start_windows.bat`，瀏覽器會開啟本地介面。
3. 拖入音訊，加入後自動轉換。MP3 預設儲存在 `MP3輸出`，位元速率可選 128/192/256/320 kbps。

轉換新版 musicex MFLAC/MGG 時，需要讓電腦上的 QQ 音樂保持執行，並登入有該歌曲播放或下載許可權的賬號。程式預設嘗試自動獲取對應資源的 ekey，失敗時可以自動重試或手動填寫。

## 多語言支援

支援簡體中文、繁體中文、English、日本語。首次開啟時根據瀏覽器語言自動選擇，不匹配時使用英語。右上角「語言」可即時切換；轉換任務、輸入的 ekey 和位元速率選擇不會因切換而清空。

手動選擇會儲存到軟體目錄的 `preferences.json`，僅包含語言程式碼，重啟或更換本地埠後仍生效。目錄不可寫時會提示，當前會話仍可切換。檔名、歌曲標籤、輸出目錄、資源名和第三方解碼器的原始日誌保持原樣；介面、狀態、應用報錯和安全提示隨語言切換。

命令列支援 `--lang zh-CN|zh-TW|en|ja`，也可用 `AUDIO_TO_MP3_LANG` 環境變數；未指定時使用系統語言，不支援的語言回退到英語。例如：

```sh
python converter.py --lang en "song.ncm" -o output
```

從 Releases 下載 **v1.1.0** 的完整懶人包即可使用四種語言。升級時請先退出舊程式，再將新版完整解壓到新資料夾；原音訊和既有 MP3 輸出不會被改動。請勿只替換 HTML。

## 支援範圍

| 格式 | 支援情況 |
| --- | --- |
| 標準 NCM | 自動解密並轉 MP3，儘量保留文字標籤 |
| 常見 MFLAC/MGG 內嵌金鑰 | EncV1/EncV2、Map/RC4 |
| musicex V1 | 自動取鑰或手動提供歌曲 ekey |
| STag、QMCv1 | 命令列配置歌曲主金鑰或掩碼 |
| FLAC、MP3、WAV、OGG、M4A、AAC、OPUS、WMA、AIFF、APE | FFmpeg 支援的普通音訊 |
| 未知加密格式 | 暫不支援 |

## FLAC 解碼校驗

部分 FLAC 在完整音訊流後包含額外非音訊尾部，嚴格解碼時會出現 `invalid sync code`。程式使用 Xiph FLAC 解碼器；遇到尾部同步錯誤時，只有實際取樣數、PCM 音訊 MD5 和引數全部匹配、且錯誤發生在完整音訊之後，才接受結果。中途壞幀、取樣不足或校驗失敗會停止轉換。

已透過 40 項測試及真實 MFLAC 樣本的完整轉碼與 MP3 解碼檢查。共用程式碼在 Linux 驗證，Windows 包使用上游官方 x64 程式；不同 Windows 和 QQ 音樂客戶端版本仍可能存在相容性差異。

## 資料與執行方式

介面僅監聽本機 `127.0.0.1`。音訊轉換在本機完成。自動取鑰讀取當前使用者 QQ 音樂配置或已執行程序中的登入欄位，僅向 QQ 音樂 HTTPS 介面請求所選歌曲的 ekey，不上傳音訊。登入會話和歌曲金鑰僅短暫儲存在執行記憶體中，用完清理。可關閉自動取鑰。

原始音訊保留，輸出同名檔案自動編號。退出請點選介面“退出程式”。FLAC 轉換需要臨時無壓縮 WAV 的磁碟空間，完成後清理。

## 原始碼開發

倉庫包含原創原始碼與測試。完整 Windows 執行環境、第三方庫原始碼、許可證及對應 FLAC 原始碼包均隨 Release ZIP 提供。

開發時使用 Python 3.12，安裝 `requirements.txt` 中的依賴，並準備 FFmpeg、FFprobe 和 Xiph FLAC 命令列工具。可用 `FFMPEG_PATH` / `FLAC_PATH` 指定路徑。

```sh
python server.py
python -m unittest test_converter test_auto_ekey test_server test_i18n
node test_ui.cjs
```

### 新增翻譯

`locales.json` 是唯一翻譯源。`ui` 使用語義鍵和 `{name}` 佔位符；`messages` 以歷史中文訊息為相容鍵，覆蓋後端應用報錯。新增文字時同時補齊四種語言，不要翻譯檔案路徑、檔名、技術標識或金鑰。`test_i18n.py` 檢查鍵與佔位符一致性、後端訊息覆蓋、介面語言隔離和偏好儲存；`test_ui.cjs` 無需 npm 依賴，檢查實時切換及任務資料保留。

## 許可證

原創程式碼採用 GPL-2.0-or-later，見 `LICENSE`。第三方元件分別遵循 CPython PSF、FFmpeg GPL、FLAC GPL/Xiph BSD、LibTakiyasha MIT、pyaes MIT 和 Mutagen GPL；完整許可與來源見 Release ZIP。只處理你有權使用的音訊。
