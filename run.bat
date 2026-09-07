@echo off
call "%~dp0.venv\Scripts\activate.bat"
REM launcher.py is the shipped entry (pyinstaller.spec) — it dispatches to the
REM Qt desktop app by default and to the web shell when PHOTO_MANAGER_WEB is
REM set, so `run.bat` matches the packaged build (main.py ignored the env var).
python "%~dp0launcher.py" %*
