@echo off
chcp 65001 >nul
REM ===================================================================
REM  Recipe Finder -- One-Click Launcher  (Windows)
REM  Double-click this file OR open CMD here and type:  run.bat
REM ===================================================================

cd /d "%~dp0"
title Recipe Finder with Nutrition Analysis

echo.
echo ===================================================
echo    Recipe Finder with Nutrition Analysis
echo ===================================================
echo.

REM -- Step 1: Check Python --------------------------------------------
python --version >nul 2>&1
IF ERRORLEVEL 1 (
    echo [ERROR] Python not found.
    echo         Download from: https://www.python.org/downloads/
    echo         Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)
FOR /F "tokens=*" %%i IN ('python --version') DO SET PYVER=%%i
echo [OK] %PYVER%

REM -- Step 2: Create virtual environment ------------------------------
IF NOT EXIST "venv\" (
    echo.
    echo [INFO] Creating virtual environment...
    python -m venv venv
)
echo [OK] Virtual environment: ready

REM -- Step 3: Activate venv -------------------------------------------
call venv\Scripts\activate.bat

REM -- Step 4: Install packages (first run only) ----------------------
IF NOT EXIST "venv\.packages_ok" (
    echo.
    echo [INFO] Installing required packages ^(first run only^)...
    python -m pip install --quiet -r requirements.txt
    IF ERRORLEVEL 1 (
        echo [ERROR] Package install failed. Check your internet connection.
        pause
        exit /b 1
    )
    echo. > venv\.packages_ok
    echo [OK] All packages installed
) ELSE (
    echo [OK] Packages: already installed
)

REM -- Step 5: Check .env file -----------------------------------------
echo.
IF NOT EXIST ".env" (
    echo ---------------------------------------------------
    echo  WARNING: .env FILE NOT FOUND
    echo.
    echo  You need a .env file with your API key.
    echo.
    echo  QUICK SETUP:
    echo  1. Get a FREE key at: https://spoonacular.com/food-api
    echo  2. Create a file named  .env  in this folder
    echo  3. Add this line to it:
    echo       SPOONACULAR_API_KEY=your_actual_key_here
    echo  4. Save, then run run.bat again
    echo ---------------------------------------------------
    echo.
    IF EXIST ".env.example" notepad .env.example
    pause
    exit /b 0
)
echo [OK] .env file: found

REM -- Step 6: Launch app ----------------------------------------------
echo.
echo [INFO] Launching Recipe Finder...
echo        (Close the app window to stop)
echo.
python main.py
pause
