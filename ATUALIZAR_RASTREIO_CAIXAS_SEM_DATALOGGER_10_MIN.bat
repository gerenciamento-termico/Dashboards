@echo off
setlocal EnableExtensions EnableDelayedExpansion
set "SCRIPT_DIR=%~dp0"

rem Modos:
rem   sem argumento -> abre uma janela e roda em loop a cada 10 min
rem   __RUN__       -> modo interno usado pela janela do loop
rem   __ONCE__      -> roda um ciclo e sai
if /I "%~1"=="__RUN__" goto :MAIN_LOOP
if /I "%~1"=="__ONCE__" goto :ONCE_MODE
if not "%~1"=="" goto :USAGE

start "Atualizar Rastreio Caixas sem Datalogger - 10 min" cmd /k ""%~f0" __RUN__"
exit /b 0

:USAGE
echo Uso:
echo   %~nx0
echo   %~nx0 __ONCE__
exit /b 1

:ONCE_MODE
call :INIT
if errorlevel 1 exit /b 1
call :RUN_WITH_LOCK ONCE 9>"%LOCKFILE%"
if errorlevel 1 (
    echo Ja existe uma instancia do atualizador de rastreio em execucao.
    exit /b 1
)
exit /b %ERRORLEVEL%

:MAIN_LOOP
call :INIT
if errorlevel 1 exit /b 1
call :RUN_WITH_LOCK LOOP 9>"%LOCKFILE%"
if errorlevel 1 (
    echo Ja existe uma instancia do atualizador de rastreio em execucao.
    echo Feche a outra janela antes de iniciar novamente.
    exit /b 1
)
exit /b %ERRORLEVEL%

:INIT
cd /d "%SCRIPT_DIR%"
if errorlevel 1 (
    echo ERRO - Nao foi possivel acessar a pasta do projeto.
    exit /b 1
)

set "INTERVAL_MIN=10"
set "INTERVAL_SEC=600"
set "LOCKFILE=%TEMP%\aura_rastreio_caixas_sem_datalogger_10_min.lock"
set "LOG_DIR=%CD%\logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>nul
for /f "delims=" %%A in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "LOG_DATE=%%A"
set "LOG_FILE=%LOG_DIR%\rastreio_caixas_sem_datalogger_%LOG_DATE%.log"
set "PY_EXE="
exit /b 0

:LOG
if "%~1"=="" (
    echo.
    >>"%LOG_FILE%" echo.
    exit /b 0
)
echo(%~1
>>"%LOG_FILE%" echo([%date% %time%] %~1
exit /b 0

:RUN_WITH_LOCK
set "RUN_MODE=%~1"
call :SELECT_PYTHON
if errorlevel 1 exit /b 1

if /I "%RUN_MODE%"=="ONCE" goto :RUN_ONCE

:LOOP
call :RUN_CYCLE
call :WAIT_NEXT
goto :LOOP

:RUN_ONCE
call :RUN_CYCLE
exit /b %ERRORLEVEL%

:SELECT_PYTHON
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
    call :LOG "[ERRO] Python nao encontrado. Use a .venv do pacote ou instale Python 3.11+."
    exit /b 1
)
call :LOG "[OK] Python selecionado: %PY_EXE%"
exit /b 0

:MAKE_STEP_LOG
set "STEP_LOG=%TEMP%\aura_rastreio_step_%RANDOM%_%RANDOM%.log"
exit /b 0

:FLUSH_STEP
if exist "!STEP_LOG!" (
    type "!STEP_LOG!"
    type "!STEP_LOG!" >> "!LOG_FILE!"
    del /q "!STEP_LOG!" >nul 2>nul
)
if not "!STEP_RC!"=="0" (
    call :LOG "[ERRO] !STEP_NAME! falhou com codigo !STEP_RC!."
    exit /b !STEP_RC!
)
exit /b 0

:RUN_CYCLE
set "ERRMSG="
set "CYCLE_START_TS="
for /f %%A in ('powershell -NoProfile -Command "[DateTimeOffset]::Now.ToUnixTimeSeconds()"') do set "CYCLE_START_TS=%%A"
for /f "delims=" %%A in ('powershell -NoProfile -Command "Get-Date -Format 'yyyy-MM-dd HH:mm:ss'"') do set "CYCLE_START_HUMAN=%%A"

call :LOG "============================================================"
call :LOG " ATUALIZAR RASTREIO DE CAIXAS SEM DATALOGGER"
call :LOG "============================================================"
call :LOG "Pasta atual: %CD%"
call :LOG "Inicio do ciclo: !CYCLE_START_HUMAN!"
call :LOG "Intervalo configurado: %INTERVAL_MIN% minutos"
call :LOG ""

call :LOG "[1/3] Validando sintaxe do gerador..."
set "STEP_NAME=py_compile rastreio"
call :MAKE_STEP_LOG
"%PY_EXE%" -m py_compile ".\gerar_html_rastreio_caixas_sem_datalogger.py" ".\validar_dashboards_publicados.py" >"!STEP_LOG!" 2>&1
set "STEP_RC=%ERRORLEVEL%"
call :FLUSH_STEP
if errorlevel 1 (
    set "ERRMSG=Falha de sintaxe nos scripts do rastreio."
    goto :CYCLE_FAIL
)

call :LOG "[2/3] Gerando RASTREIO_CAIXAS_SEM_DATALOGGER.html..."
set "STEP_NAME=gerar_html_rastreio_caixas_sem_datalogger.py"
call :MAKE_STEP_LOG
"%PY_EXE%" ".\gerar_html_rastreio_caixas_sem_datalogger.py" >"!STEP_LOG!" 2>&1
set "STEP_RC=%ERRORLEVEL%"
call :FLUSH_STEP
if errorlevel 1 (
    set "ERRMSG=Falha ao gerar RASTREIO_CAIXAS_SEM_DATALOGGER.html."
    goto :CYCLE_FAIL
)

call :LOG "[3/3] Validando HTML gerado..."
set "STEP_NAME=validate-html rastreio"
call :MAKE_STEP_LOG
"%PY_EXE%" ".\validar_dashboards_publicados.py" validate-html --only rastreio --cycle-start "!CYCLE_START_TS!" >"!STEP_LOG!" 2>&1
set "STEP_RC=%ERRORLEVEL%"
call :FLUSH_STEP
if errorlevel 1 (
    set "ERRMSG=Validacao do HTML de rastreio falhou."
    goto :CYCLE_FAIL
)

call :LOG ""
call :LOG "[OK] RASTREIO_CAIXAS_SEM_DATALOGGER.html atualizado e validado."
call :FINISH_CYCLE 0
exit /b 0

:CYCLE_FAIL
if not defined ERRMSG set "ERRMSG=Erro inesperado no ciclo."
call :LOG ""
call :LOG "[ERRO] !ERRMSG!"
call :FINISH_CYCLE 1
exit /b 1

:FINISH_CYCLE
set "FINAL_RC=%~1"
set "CYCLE_END_TS="
for /f %%A in ('powershell -NoProfile -Command "[DateTimeOffset]::Now.ToUnixTimeSeconds()"') do set "CYCLE_END_TS=%%A"
for /f "delims=" %%A in ('powershell -NoProfile -Command "Get-Date -Format 'yyyy-MM-dd HH:mm:ss'"') do set "CYCLE_END_HUMAN=%%A"
set /a ELAPSED_SEC=!CYCLE_END_TS!-!CYCLE_START_TS!
call :LOG "Fim do ciclo: !CYCLE_END_HUMAN!"
call :LOG "Tempo gasto: !ELAPSED_SEC! segundo(s)"
exit /b %FINAL_RC%

:WAIT_NEXT
set "WAIT_SEC=%INTERVAL_SEC%"
if defined CYCLE_START_TS (
    for /f %%A in ('powershell -NoProfile -Command "$elapsed = [DateTimeOffset]::Now.ToUnixTimeSeconds() - [int64]!CYCLE_START_TS!; $wait = %INTERVAL_SEC% - $elapsed; if ($wait -lt 1) { 1 } else { [int]$wait }"') do set "WAIT_SEC=%%A"
)
for /f "delims=" %%A in ('powershell -NoProfile -Command "(Get-Date).AddSeconds(!WAIT_SEC!).ToString('yyyy-MM-dd HH:mm:ss')"') do set "NEXT_AT=%%A"
call :LOG "Proxima atualizacao: !NEXT_AT! (!WAIT_SEC! segundo(s) de espera)"
call :LOG "Atualizacao automatica: a cada %INTERVAL_MIN% minutos"
echo Pressione Ctrl+C para encerrar.
timeout /t !WAIT_SEC! /nobreak >nul
exit /b 0
