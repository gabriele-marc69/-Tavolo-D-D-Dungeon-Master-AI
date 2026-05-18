@echo off
REM ============================================================
REM  Tavolo D&D - Avvio del programma
REM ============================================================
setlocal EnableExtensions
cd /d "%~dp0"
title Tavolo D^&D

if not exist ".venv\Scripts\python.exe" (
    echo [!] Ambiente non trovato. Esegui prima:  installa.bat
    echo.
    pause
    exit /b 1
)

echo [..] Avvio Tavolo D^&D  ^(http://localhost:5000^) ...
echo     Chiudi questa finestra per fermare il server.
echo.
".venv\Scripts\python.exe" app.py

echo.
echo [i] Server terminato.
pause
endlocal
