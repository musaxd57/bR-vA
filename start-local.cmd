@echo off
cd /d "%~dp0"
".venv-local\Scripts\python.exe" manage.py upgrade-local
if errorlevel 1 goto :failed
".venv-local\Scripts\python.exe" app.py
pause
goto :eof
:failed
echo Yerel hazirlik tamamlanamadi. Yukaridaki hatayi kontrol edin.
pause
