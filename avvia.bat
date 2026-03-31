@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title AI Analisi Bandi SaaS v2.1 - Avvio

cls
echo.
echo  ╔══════════════════════════════════════════════════════════╗
echo  ║       AI ANALISI BANDI  SaaS v2.1  —  Avvio Guidato    ║
echo  ║       Modello: llama-3.3-70b-versatile  ^|  Groq         ║
echo  ╚══════════════════════════════════════════════════════════╝
echo.

:: ── Spostati nella cartella dello script ────────────────────────────────
cd /d "%~dp0"

:: ════════════════════════════════════════════════════════════════════════
::  STEP 1 — Controlla Python
:: ════════════════════════════════════════════════════════════════════════
echo  [1/4] Controllo Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo  ┌─────────────────────────────────────────────────────────┐
    echo  │  ERRORE: Python non trovato                             │
    echo  │                                                         │
    echo  │  Soluzione:                                             │
    echo  │  1. Scarica Python 3.12 da:                            │
    echo  │     https://python.org/downloads/release/python-3128/  │
    echo  │  2. Durante l'installazione spunta:                    │
    echo  │     "Add Python to PATH"                               │
    echo  │  3. Riavvia questo file                                │
    echo  └─────────────────────────────────────────────────────────┘
    echo.
    pause
    exit /b 1
)
for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo  [OK] Python !PYVER! trovato
echo.

:: ════════════════════════════════════════════════════════════════════════
::  STEP 2 — Configurazione GROQ_API_KEY
:: ════════════════════════════════════════════════════════════════════════
echo  [2/4] Configurazione chiave API...

:: Crea .env dall'esempio se non esiste
if not exist ".env" (
    if exist ".env.example" (
        copy .env.example .env >nul
    ) else (
        echo GROQ_API_KEY=> .env
        echo GROQ_MODEL=llama-3.3-70b-versatile>> .env
    )
)

:: Leggi la chiave attuale da .env
set CURRENT_KEY=
for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
    if /i "%%a"=="GROQ_API_KEY" set CURRENT_KEY=%%b
)
:: Rimuovi spazi iniziali dalla chiave
for /f "tokens=* delims= " %%x in ("!CURRENT_KEY!") do set CURRENT_KEY=%%x

:: Controlla se la chiave è assente o è quella di esempio
set KEY_OK=0
if not "!CURRENT_KEY!"=="" (
    if not "!CURRENT_KEY!"=="gsk_LA_TUA_CHIAVE_GROQ" (
        if "!CURRENT_KEY:~0,4!"=="gsk_" set KEY_OK=1
    )
)

if "!KEY_OK!"=="0" (
    echo.
    echo  ┌─────────────────────────────────────────────────────────┐
    echo  │  GROQ API KEY non configurata                           │
    echo  │                                                         │
    echo  │  La chiave serve per l'analisi AI dei bandi.           │
    echo  │  E' GRATUITA e richiede solo registrazione:            │
    echo  │                                                         │
    echo  │  → https://console.groq.com/keys                       │
    echo  │    (account gratuito, nessuna carta di credito^)        │
    echo  └─────────────────────────────────────────────────────────┘
    echo.
    echo  Inserisci la tua GROQ_API_KEY (inizia con gsk_...)
    echo  Premi INVIO senza scrivere nulla per avviare in modalita offline
    echo.
    set /p INPUT_KEY="  Chiave API: "

    :: Rimuovi spazi
    for /f "tokens=* delims= " %%x in ("!INPUT_KEY!") do set INPUT_KEY=%%x

    if "!INPUT_KEY!"=="" (
        echo.
        echo  [AVVISO] Nessuna chiave inserita — avvio in LOCAL_MODE
        echo           L'analisi AI non sara disponibile.
    ) else (
        :: Scrivi la chiave nel file .env
        :: Strategia: riscrivi riga per riga sostituendo GROQ_API_KEY
        set TMPFILE=.env.tmp
        if exist "!TMPFILE!" del "!TMPFILE!"
        set KEY_WRITTEN=0
        for /f "usebackq delims=" %%L in (".env") do (
            set LINE=%%L
            if "!LINE:~0,12!"=="GROQ_API_KEY" (
                echo GROQ_API_KEY=!INPUT_KEY!>>"!TMPFILE!"
                set KEY_WRITTEN=1
            ) else (
                echo %%L>>"!TMPFILE!"
            )
        )
        if "!KEY_WRITTEN!"=="0" (
            echo GROQ_API_KEY=!INPUT_KEY!>>"!TMPFILE!"
        )
        move /y "!TMPFILE!" ".env" >nul
        echo.
        echo  [OK] Chiave salvata in .env
    )
    echo.
)

if "!KEY_OK!"=="1" (
    :: Mostra solo ultimi 6 caratteri per sicurezza
    set MASKED=!CURRENT_KEY!
    call :maskkey "!CURRENT_KEY!"
    echo  [OK] GROQ_API_KEY configurata: !MASKED!
    echo.
)

:: ════════════════════════════════════════════════════════════════════════
::  STEP 3 — Ambiente virtuale e dipendenze
:: ════════════════════════════════════════════════════════════════════════
echo  [3/4] Ambiente Python e dipendenze...

set PYTHON_CMD=python
if exist "venv\Scripts\python.exe" (
    echo  [OK] Ambiente virtuale trovato
    set PYTHON_CMD=venv\Scripts\python.exe
) else (
    echo  [INFO] Creo ambiente virtuale (operazione unica)...
    python -m venv venv >nul 2>&1
    if errorlevel 1 (
        echo  [AVVISO] venv non disponibile - uso Python di sistema
    ) else (
        echo  [OK] Ambiente virtuale creato
        set PYTHON_CMD=venv\Scripts\python.exe
    )
)

echo  [INFO] Aggiorno pip...
!PYTHON_CMD! -m pip install -q --upgrade pip
echo  [INFO] Installo dipendenze (solo al primo avvio)...
!PYTHON_CMD! -m pip install -q -r requirements.txt

if errorlevel 1 (
    echo.
    echo  ┌─────────────────────────────────────────────────────────┐
    echo  │  ERRORE: Installazione dipendenze fallita               │
    echo  │                                                         │
    echo  │  Soluzioni:                                             │
    echo  │  1. Controlla la connessione internet                  │
    echo  │  2. Usa Python 3.11 o 3.12 (non 3.13/3.14^)           │
    echo  │     → https://python.org/downloads/release/python-3128/│
    echo  │  3. Cancella la cartella "venv" e riprova              │
    echo  └─────────────────────────────────────────────────────────┘
    echo.
    pause
    exit /b 1
)
echo  [OK] Dipendenze pronte
echo.

:: ════════════════════════════════════════════════════════════════════════
::  STEP 4 — Avvio backend + apertura browser
:: ════════════════════════════════════════════════════════════════════════
echo  [4/4] Avvio backend...
echo.
echo  ┌─────────────────────────────────────────────────────────┐
echo  │  Backend: http://localhost:8000                         │
echo  │  Docs API: http://localhost:8000/docs                  │
echo  │                                                         │
echo  │  Il browser si apre automaticamente.                   │
echo  │  Lascia questa finestra aperta durante l'utilizzo.     │
echo  │  Premi Ctrl+C per fermare il server.                   │
echo  └─────────────────────────────────────────────────────────┘
echo.

:: Apri browser con piccolo ritardo per dare tempo al backend di partire
start "" cmd /c "timeout /t 2 >nul && start "" \"%~dp0AI_Analisi_Bandi_SaaS.html\""

!PYTHON_CMD! -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload

if errorlevel 1 (
    echo.
    echo  ┌─────────────────────────────────────────────────────────┐
    echo  │  ERRORE: Backend arrestato inaspettatamente            │
    echo  │                                                         │
    echo  │  Soluzioni:                                             │
    echo  │  1. La porta 8000 e gia in uso?                       │
    echo  │     Chiudi altri terminali con uvicorn/Python          │
    echo  │  2. Errore nel file main.py?                           │
    echo  │     Controlla i messaggi sopra                        │
    echo  └─────────────────────────────────────────────────────────┘
    echo.
    pause
)
goto :eof

:: ── Subroutine: maschera la chiave mostrando solo gsk_...XXXXXX ──────────
:maskkey
set FULLKEY=%~1
set MASKED=gsk_...!FULLKEY:~-6!
goto :eof
