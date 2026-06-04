@echo off
REM Whisper / Piper model downloader (bare-metal Windows).
REM Downloads into models\whisper and models\piper (gitignored; recreate anytime).
REM
REM   get-models.cmd available             list downloadable Whisper models
REM   get-models.cmd whisper large-v3      download a Whisper model
REM   get-models.cmd status                show what's already cached
REM   get-models.cmd piper en_US-amy-low   download a Piper TTS voice
"%~dp0.venv\Scripts\python.exe" "%~dp0scripts\lib\fetch_assets.py" %*
