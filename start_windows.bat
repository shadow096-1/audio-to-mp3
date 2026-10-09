@echo off
setlocal
cd /d "%~dp0"
if not exist "runtime\python.exe" (
  echo Please extract the entire ZIP into a folder before starting.
  pause
  exit /b 1
)
if not exist "bin\ffmpeg.exe" (
  echo The FFmpeg file is missing. Please extract the entire ZIP again.
  pause
  exit /b 1
)
"%~dp0runtime\python.exe" "%~dp0server.py"
if errorlevel 1 (
  echo Startup failed. Please copy the error above.
  pause
)
