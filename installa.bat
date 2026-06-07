@echo off
REM ============================================================
REM  Tavolo AI - Installazione completa su PC nuovo (Windows)
REM ============================================================
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title Installazione Tavolo AI MAster  

echo.
echo ============================================================
echo   TAVOLO - INSTALLAZIONE
echo ============================================================
echo.

REM ---- 1. Trova Python (python o py) -----------------------------------
set "PY="
for %%P in (python py) do (
    if not defined PY (
        %%P --version >nul 2>&1 && set "PY=%%P"
    )
)

if not defined PY (
    echo [!] Python non trovato su questo PC.
    echo.
    where winget >nul 2>&1
    if errorlevel 1 (
        echo     winget non disponibile. Installa Python a mano:
        echo     https://www.python.org/downloads/
        echo     IMPORTANTE: spunta "Add python.exe to PATH" durante l'installazione.
        echo.
        pause
        exit /b 1
    )
    echo     Installo Python 3.12 con winget...
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    if errorlevel 1 (
        echo [!] Installazione Python fallita. Installa a mano e riavvia questo script.
        pause
        exit /b 1
    )
    echo.
    echo [i] Python installato. CHIUDI questa finestra e riavvia installa.bat
    echo     ^(serve per aggiornare il PATH^).
    pause
    exit /b 0
)

echo [OK] Python trovato:
%PY% --version
echo.

REM ---- 2. Crea ambiente virtuale .venv ---------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo [..] Creo ambiente virtuale .venv ...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo [!] Creazione .venv fallita.
        pause
        exit /b 1
    )
) else (
    echo [OK] Ambiente virtuale .venv gia' presente.
)
set "VPY=.venv\Scripts\python.exe"
echo.

REM ---- 3. Aggiorna pip --------------------------------------------------
echo [..] Aggiorno pip ...
"%VPY%" -m pip install --upgrade pip
echo.

REM ---- 4. Installa dipendenze Python -----------------------------------
echo [..] Installo dipendenze ^(flask, requests, playwright, edge-tts^) ...
if exist "requirements.txt" (
    "%VPY%" -m pip install -r requirements.txt
) else (
    "%VPY%" -m pip install "flask>=3.0" "requests>=2.31" "playwright>=1.45" "edge-tts>=7.0"
)
if errorlevel 1 (
    echo [!] Installazione dipendenze fallita.
    pause
    exit /b 1
)
echo.

REM ---- 5. Scarica browser Chromium per Playwright ----------------------
echo [..] Scarico il browser Chromium per Playwright ^(puo' richiedere qualche minuto^) ...
"%VPY%" -m playwright install chromium
if errorlevel 1 (
    echo [!] Download Chromium fallito. Riprova con connessione attiva.
    pause
    exit /b 1
)
echo.

echo ============================================================
echo   INSTALLAZIONE COMPLETATA
echo ============================================================
echo.
echo   Avvia il programma con:  avvia.bat
echo   ^(apre automaticamente http://localhost:5000^)
echo.

REM ---- 6. Avvio opzionale subito ---------------------------------------
choice /C SN /N /M "Avviare il programma adesso? [S/N] "
if errorlevel 2 goto :fine
echo.
echo [..] Avvio Tavolo D^&D ...
"%VPY%" app.py

:fine
echo.
pause
endlocal
