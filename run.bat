@echo off
REM Inicia la plataforma en Windows.  Doble clic o: run.bat
cd /d "%~dp0"
if not exist .env ( echo Falta el archivo .env ^(copia .env.example y completalo^) & pause & exit /b 1 )
pip install -q -r requirements.txt
cd server
python -m waitress --listen=0.0.0.0:5000 app:app
