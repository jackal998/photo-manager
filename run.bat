@echo off
call "%~dp0.venv\Scripts\activate.bat"
REM launcher.py is the shipped entry (pyinstaller.spec) — it opens the desktop
REM web shell, so `run.bat` matches the packaged build.
python "%~dp0launcher.py" %*
