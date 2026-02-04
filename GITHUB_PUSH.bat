@echo off
REM Quick-Push Script für Windows
REM Doppelklick zum Ausführen oder per CMD

echo ========================================
echo  GitHub Push - GBG Discord Bot
echo ========================================
echo.

cd /d "%~dp0"

echo [1/4] Git Status prüfen...
git status
echo.

echo [2/4] Dateien hinzufügen...
git add .
echo.

set /p commit_msg="Commit-Nachricht (Enter für default): "
if "%commit_msg%"=="" set commit_msg=Update Bot Code

echo.
echo [3/4] Commit erstellen: %commit_msg%
git commit -m "%commit_msg%"
echo.

echo [4/4] Auf GitHub pushen...
git push
echo.

if %errorlevel% equ 0 (
    echo ========================================
    echo  ERFOLG! Code ist auf GitHub.
    echo ========================================
) else (
    echo ========================================
    echo  FEHLER beim Pushen!
    echo ========================================
    echo.
    echo Mögliche Lösungen:
    echo 1. git remote add origin https://github.com/kilian558/GBG_KI.git
    echo 2. git branch -M main
    echo 3. git push -u origin main --force
)

echo.
pause
