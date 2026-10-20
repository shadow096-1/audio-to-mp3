# Changelog

## v1.1.0 — 2026-10-20

- 新增简体中文、繁体中文、英语、日语，包含界面、转换状态、提示及应用报错。
- 首次按浏览器语言选择，提供即时切换；重启后保留语言偏好。
- 切换语言时保留转换任务、码率和输入中的 ekey。
- 命令行新增 `--lang`、`AUDIO_TO_MP3_LANG` 与 `--version`。
- 补充四种语言的使用说明和多语言自动检查。
- 完整 Windows x64 懒人包继续内置 Python、FFmpeg、Xiph FLAC 及第三方许可。

Validation: 40 Python tests plus offline JavaScript UI behavior checks on Linux. Native Windows / QQ Music session access and real-browser layout have not been verified. MP3 is lossy; format and platform compatibility limitations still apply.
