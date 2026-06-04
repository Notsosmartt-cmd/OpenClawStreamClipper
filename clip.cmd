@echo off
REM Native pipeline launcher (bare-metal Windows).
REM Runs the Python orchestrator inside the project venv. Used by the OpenClaw
REM stream-clipper skill's `exec` tool and available for manual runs.
REM Forwards all arguments to run_pipeline.py, e.g.:
REM   clip.cmd --style auto --vod lacy
REM   clip.cmd --list
"%~dp0.venv\Scripts\python.exe" "%~dp0scripts\run_pipeline.py" %*
