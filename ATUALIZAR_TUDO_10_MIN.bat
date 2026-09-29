@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if errorlevel 1 (
  echo Nao foi possivel abrir a pasta do script.
  pause
  exit /b 1
)
set "SCRIPT_DIR=%~dp0"
set "MODE=%~1"

if "%MODE%"=="" set "MODE=LOOP"
if /I "%MODE%"=="RUN" set "MODE=LOOP"
if /I "%MODE%"=="__RUN__" set "MODE=LOOP"
if /I "%MODE%"=="ONCE" set "MODE=ONCE"
if /I "%MODE%"=="__ONCE__" set "MODE=ONCE"
if /I "%MODE%"=="CHECK" set "MODE=CHECK"
if /I "%MODE%"=="__CHECK__" set "MODE=CHECK"
if /I "%MODE%"=="DRY_RUN" set "MODE=DRY_RUN"
if /I "%MODE%"=="__DRY_RUN__" set "MODE=DRY_RUN"

echo.
echo Iniciando atualizador Aura...
echo Pasta: %CD%
echo Modo: %MODE%

if exist "%SCRIPT_DIR%hub_share.env" (
  for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%SCRIPT_DIR%hub_share.env") do (
    if not "%%A"=="" set "%%A=%%B"
  )
)

if "%AURA_HUB_GIT_URL%"=="" set "AURA_HUB_GIT_URL=https://github.com/gerenciamento-termico/Dashboards.git"
echo Destino Git: %AURA_HUB_GIT_URL%
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%ATUALIZAR_TUDO_10_MIN.ps1" -Mode "%MODE%"
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
  echo.
  echo O atualizador terminou com erro. Verifique o log em logs\atualizar_tudo_YYYYMMDD.log
  pause
)
exit /b %RC%
