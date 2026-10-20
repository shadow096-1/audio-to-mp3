# 音频转 MP3 · Windows 懒人包

简体中文 · [繁體中文](README.zh-TW.md) · [English](README.en.md) · [日本語](README.ja.md)

Windows 10/11 x64 批量音频转换工具，支持 NCM、常见 MFLAC/MGG、FLAC 等格式。内置 Python、FFmpeg 和 Xiph FLAC，不需要安装运行环境。

## 下载与使用

在本仓库的 **Releases** 中下载 `AudioToMP3.zip`。

1. 将 ZIP 完整解压到一个可写文件夹。
2. 双击 `start_windows.bat`，浏览器会打开本地界面。
3. 拖入音频，加入后自动转换。MP3 默认保存在 `MP3输出`，码率可选 128/192/256/320 kbps。

转换新版 musicex MFLAC/MGG 时，需要让电脑上的 QQ 音乐保持运行，并登录有该歌曲播放或下载权限的账号。程序默认尝试自动获取对应资源的 ekey，失败时可以自动重试或手动填写。

## 多语言支持

支持简体中文、繁體中文、English、日本語。首次打开时根据浏览器语言自动选择，不匹配时使用英语。右上角「语言」可即时切换；转换任务、输入的 ekey 和码率选择不会因切换而清空。

手动选择会保存到软件目录的 `preferences.json`，仅包含语言代码，重启或更换本地端口后仍生效。目录不可写时会提示，当前会话仍可切换。文件名、歌曲标签、输出目录、资源名和第三方解码器的原始日志保持原样；界面、状态、应用报错和安全提示随语言切换。

命令行支持 `--lang zh-CN|zh-TW|en|ja`，也可用 `AUDIO_TO_MP3_LANG` 环境变量；未指定时使用系统语言，不支持的语言回退到英语。例如：

```sh
python converter.py --lang en "song.ncm" -o output
```

旧版 Release ZIP 尚不包含这些新增源码。已下载懒人包的用户，需同时更新 `server.py`、`converter.py`、`webui.html`，并添加 `i18n.py` 和 `locales.json`。不要只替换 HTML。

## 支持范围

| 格式 | 支持情况 |
| --- | --- |
| 标准 NCM | 自动解密并转 MP3，尽量保留文字标签 |
| 常见 MFLAC/MGG 内嵌密钥 | EncV1/EncV2、Map/RC4 |
| musicex V1 | 自动取钥或手动提供歌曲 ekey |
| STag、QMCv1 | 命令行配置歌曲主密钥或掩码 |
| FLAC、MP3、WAV、OGG、M4A、AAC、OPUS、WMA、AIFF、APE | FFmpeg 支持的普通音频 |
| 未知加密格式 | 暂不支持 |

## v4 改进

部分 FLAC 在完整音频流后包含额外非音频尾部，严格解码时会出现 `invalid sync code`。v4 使用 Xiph FLAC 解码器；遇到尾部同步错误时，只有实际采样数、PCM 音频 MD5 和参数全部匹配、且错误发生在完整音频之后，才接受结果。中途坏帧、采样不足或校验失败会停止转换。

已通过 31 项测试及真实 MFLAC 样本的完整转码与 MP3 解码检查。共用代码在 Linux 验证，Windows 包使用上游官方 x64 程序；不同 Windows 和 QQ 音乐客户端版本仍可能存在兼容性差异。

## 数据与运行方式

界面仅监听本机 `127.0.0.1`。音频转换在本机完成。自动取钥读取当前用户 QQ 音乐配置或已运行进程中的登录字段，仅向 QQ 音乐 HTTPS 接口请求所选歌曲的 ekey，不上传音频。登录会话和歌曲密钥仅短暂保存在运行内存中，用完清理。可关闭自动取钥。

原始音频保留，输出同名文件自动编号。退出请点击界面“退出程序”。FLAC 转换需要临时无压缩 WAV 的磁盘空间，完成后清理。

## 源码开发

仓库包含原创源码与测试。完整 Windows 运行环境、第三方库源码、许可证及对应 FLAC 源码包均随 Release ZIP 提供。

开发时使用 Python 3.12，安装 `requirements.txt` 中的依赖，并准备 FFmpeg、FFprobe 和 Xiph FLAC 命令行工具。可用 `FFMPEG_PATH` / `FLAC_PATH` 指定路径。

```sh
python server.py
python -m unittest test_converter test_auto_ekey test_server test_i18n
node test_ui.cjs
```

### 添加翻译

`locales.json` 是唯一翻译源。`ui` 使用语义键和 `{name}` 占位符；`messages` 以历史中文消息为兼容键，覆盖后端应用报错。新增文本时同时补齐四种语言，不要翻译文件路径、文件名、技术标识或密钥。`test_i18n.py` 检查键与占位符一致性、后端消息覆盖、接口语言隔离和偏好保存；`test_ui.cjs` 无需 npm 依赖，检查实时切换及任务数据保留。

## 许可证

原创代码采用 GPL-2.0-or-later，见 `LICENSE`。第三方组件分别遵循 CPython PSF、FFmpeg GPL、FLAC GPL/Xiph BSD、LibTakiyasha MIT、pyaes MIT 和 Mutagen GPL；完整许可与来源见 Release ZIP。只处理你有权使用的音频。
