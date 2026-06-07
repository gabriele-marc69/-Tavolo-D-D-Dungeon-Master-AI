@echo off
REM ============================================================
REM  Tavolo Gioco - Avvio del programma
REM ============================================================
setlocal EnableExtensions
cd /d "%~dp0"
title Tavolo GIOCO D AI D

python -c "import flask" 2>nul
if errorlevel 1 (
    echo [..] Installazione dipendenze...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [!] Installazione fallita.
        pause
        exit /b 1
    )
)

echo [..] Avvio Tavolo ^(http://localhost:5000^) ...
echo     Chiudi questa finestra per fermare il server.
echo.
python app.py

echo.
echo [i] Server terminato.
pause
endlocal
