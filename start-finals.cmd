@echo off
REM THE FINALS Clipper launcher (bare-metal Windows).
REM A separate sibling app to the dashboard/poster: OCR-scans a VOD for
REM revive moments + end-of-match screens and cuts buffered montage clips.
REM Default port 5200 (pin with FINALS_PORT). Start it like the dashboard:
REM   start-finals.cmd
"%~dp0.venv\Scripts\python.exe" "%~dp0finals\app.py" %*
