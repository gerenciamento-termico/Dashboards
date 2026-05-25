@echo off
setlocal EnableExtensions
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"
if errorlevel 1 exit /b 1

set "PY_EXE="
if exist "%SCRIPT_DIR%..\.venv\Scripts\python.exe" (
    "%SCRIPT_DIR%..\.venv\Scripts\python.exe" --version >nul 2>nul
    if not errorlevel 1 set "PY_EXE=%SCRIPT_DIR%..\.venv\Scripts\python.exe"
)
if not defined PY_EXE (
    python --version >nul 2>nul
    if not errorlevel 1 set "PY_EXE=python"
)
if not defined PY_EXE (
    py --version >nul 2>nul
    if not errorlevel 1 set "PY_EXE=py"
)
if not defined PY_EXE (
    echo ERRO - Python nao encontrado. Use a .venv do pacote ou instale Python 3.11+.
    exit /b 1
)

if not exist "logs" mkdir "logs" >nul 2>nul

echo ============================================================
echo VERIFICACAO DIARIA DOS DASHBOARDS AURA
echo ============================================================
echo Pasta atual: %CD%
echo Log: %CD%\logs\verificacao_diaria.txt
echo.

"%PY_EXE%" ".\aura_update_checks.py" daily-check
set "CHECK_RC=%ERRORLEVEL%"
echo.
if "%CHECK_RC%"=="0" (
    echo OK - Verificacao diaria concluida. Veja logs\verificacao_diaria.txt
) else (
    echo ERRO - Verificacao diaria encontrou falhas. Veja logs\verificacao_diaria.txt
)
exit /b %CHECK_RC%
