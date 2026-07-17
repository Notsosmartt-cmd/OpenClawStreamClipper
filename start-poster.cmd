@echo off
REM Buffer Clip Poster launcher (bare-metal Windows).
REM A separate sibling app to the dashboard: batch-posts finished clips to
REM TikTok + Instagram Reels through the Buffer.io API.
REM Default port 5100 (pin with POSTER_PORT). Start it like the dashboard:
REM   start-poster.cmd
"%~dp0.venv\Scripts\python.exe" "%~dp0poster\app.py" %*
