@echo off
setlocal EnableExtensions EnableDelayedExpansion
set "SCRIPT_DIR=%~dp0"

rem Modos publicos:
rem   sem argumento  -> abre uma janela e roda em loop a cada 10 min
rem   __CHECK__      -> valida ambiente, sintaxe, variaveis e conexoes
rem   __ONCE__       -> roda um ciclo completo e sai
rem   __RUN__        -> modo interno usado pela janela do loop
if /I "%~1"=="__RUN__" goto :MAIN_LOOP
if /I "%~1"=="__CHECK__" goto :CHECK_MODE
if /I "%~1"=="__ONCE__" goto :ONCE_MODE
if not "%~1"=="" goto :USAGE

start "Atualizar Dashboards Aura - 10 min" cmd /k ""%~f0" __RUN__"
exit /b 0

:USAGE
echo Uso:
echo   %~nx0
echo   %~nx0 __CHECK__
echo   %~nx0 __ONCE__
exit /b 1

:CHECK_MODE
call :INIT
if errorlevel 1 exit /b 1
call :LOG "============================================================"
call :LOG " CHECK - ATUALIZAR DASHBOARDS AURA"
call :LOG "============================================================"
call :SETUP_TOOLS
if errorlevel 1 exit /b 1
call :RUN_CHECKS
set "CHECK_RC=%ERRORLEVEL%"
if "%CHECK_RC%"=="0" (
    call :LOG "[OK] CHECK concluido sem gerar commit nem push."
) else (
    call :LOG "[ERRO] CHECK falhou. Veja o erro real acima e no log."
)
exit /b %CHECK_RC%

:ONCE_MODE
call :INIT
if errorlevel 1 exit /b 1
call :RUN_WITH_LOCK ONCE 9>"%LOCKFILE%"
if errorlevel 1 (
    echo Ja existe uma instancia do atualizador unificado em execucao.
    echo Feche a outra janela antes de iniciar novamente.
    exit /b 1
)
exit /b %ERRORLEVEL%

:MAIN_LOOP
call :INIT
if errorlevel 1 exit /b 1
call :RUN_WITH_LOCK LOOP 9>"%LOCKFILE%"
if errorlevel 1 (
    echo Ja existe uma instancia do atualizador unificado em execucao.
    echo Feche a outra janela antes de iniciar novamente.
    exit /b 1
)
exit /b %ERRORLEVEL%

:INIT
cd /d "%SCRIPT_DIR%"
if errorlevel 1 exit /b 1

set "INTERVAL_MIN=10"
set "INTERVAL_SEC=600"
set "LOCKFILE=%TEMP%\aura_atualizar_tudo_10_min.lock"
set "LOG_DIR=%CD%\logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>nul

for /f "delims=" %%A in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "LOG_DATE=%%A"
set "LOG_FILE=%LOG_DIR%\atualizacao_%LOG_DATE%.log"
set "PY_EXE="
set "COMMIT_DONE=nao"
set "PUSH_DONE=nao"
set "STAGE_FILE="
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
call :SETUP_TOOLS
if errorlevel 1 (
    call :LOG "[ERRO] Corrija os itens acima e execute novamente."
    if /I "%RUN_MODE%"=="LOOP" pause
    exit /b 1
)

if /I "%RUN_MODE%"=="ONCE" (
    call :RUN_CYCLE
    set "ONCE_RC=!ERRORLEVEL!"
    exit /b !ONCE_RC!
)

:LOOP
call :RUN_CYCLE
set "CYCLE_RC=%ERRORLEVEL%"
call :WAIT_NEXT
goto :LOOP

:SETUP_TOOLS
call :SELECT_PYTHON
if errorlevel 1 exit /b 1

call :RUN_PY_VERSION
if errorlevel 1 exit /b 1

git --version >nul 2>nul
if errorlevel 1 (
    if exist "%LOCALAPPDATA%\Programs\Git\cmd\git.exe" set "PATH=%LOCALAPPDATA%\Programs\Git\cmd;%PATH%"
    if exist "%ProgramFiles%\Git\cmd\git.exe" set "PATH=%ProgramFiles%\Git\cmd;%PATH%"
    if exist "%ProgramFiles(x86)%\Git\cmd\git.exe" set "PATH=%ProgramFiles(x86)%\Git\cmd;%PATH%"
)

git --version >nul 2>nul
if errorlevel 1 (
    call :LOG "[ERRO] Git nao encontrado. Instale o Git for Windows e deixe git no PATH."
    exit /b 1
)

git rev-parse --is-inside-work-tree >nul 2>nul
if errorlevel 1 (
    call :LOG "[ERRO] Esta pasta nao e um repositorio Git."
    exit /b 1
)

git config --get user.name >nul 2>nul
if errorlevel 1 git config user.name "Aura Auto Update"

git config --get user.email >nul 2>nul
if errorlevel 1 git config user.email "aura-auto-update@example.local"

exit /b 0

:SELECT_PYTHON
set "PY_EXE="
if exist "%SCRIPT_DIR%..\.venv\Scripts\python.exe" (
    "%SCRIPT_DIR%..\.venv\Scripts\python.exe" --version >nul 2>nul
    if not errorlevel 1 set "PY_EXE=%SCRIPT_DIR%..\.venv\Scripts\python.exe"
)
if not defined PY_EXE (
    "python" --version >nul 2>nul
    if not errorlevel 1 set "PY_EXE=python"
)
if not defined PY_EXE (
    "py" --version >nul 2>nul
    if not errorlevel 1 set "PY_EXE=py"
)
if not defined PY_EXE (
    call :LOG "[ERRO] Python nao encontrado. Use a .venv do pacote ou instale Python 3.11+."
    exit /b 1
)
call :LOG "[OK] Python selecionado: %PY_EXE%"
exit /b 0

:MAKE_STEP_LOG
set "STEP_LOG=%TEMP%\aura_step_%RANDOM%_%RANDOM%.log"
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

:RUN_PY_VERSION
set "STEP_NAME=Python --version"
call :MAKE_STEP_LOG
"%PY_EXE%" --version >"!STEP_LOG!" 2>&1
set "STEP_RC=%ERRORLEVEL%"
call :FLUSH_STEP
exit /b %ERRORLEVEL%

:RUN_CHECKS
call :LOG "[check] Validando Git..."
set "STEP_NAME=Git --version"
call :MAKE_STEP_LOG
git --version >"!STEP_LOG!" 2>&1
set "STEP_RC=%ERRORLEVEL%"
call :FLUSH_STEP
if errorlevel 1 exit /b 1

call :RUN_PY_COMPILE
if errorlevel 1 exit /b 1

call :LOG "[check] Validando .env, variaveis obrigatorias e conexoes..."
set "STEP_NAME=check-env"
call :MAKE_STEP_LOG
"%PY_EXE%" ".\aura_update_checks.py" check-env >"!STEP_LOG!" 2>&1
set "STEP_RC=%ERRORLEVEL%"
call :FLUSH_STEP
if errorlevel 1 exit /b 1
exit /b 0

:RUN_PY_COMPILE
call :LOG "[py_compile] Validando sintaxe dos scripts Python..."
set "STEP_NAME=py_compile"
call :MAKE_STEP_LOG
"%PY_EXE%" -m py_compile ^
    ".\gerar_html_estoque.py" ^
    ".\gerar_html_controle_entregas.py" ^
    ".\HTMLACOMPANHAMENTO.py" ^
    ".\gerar_dashboard_entregas.py" ^
    ".\env_utils.py" ^
    ".\aura_update_checks.py" >"!STEP_LOG!" 2>&1
set "STEP_RC=%ERRORLEVEL%"
call :FLUSH_STEP
exit /b %ERRORLEVEL%

:RUN_CYCLE
set "COMMIT_DONE=nao"
set "PUSH_DONE=nao"
set "ERRMSG="
set "STAGE_FILE="
set "CYCLE_START_TS="
for /f %%A in ('powershell -NoProfile -Command "[DateTimeOffset]::Now.ToUnixTimeSeconds()"') do set "CYCLE_START_TS=%%A"
for /f "delims=" %%A in ('powershell -NoProfile -Command "Get-Date -Format 'yyyy-MM-dd HH:mm:ss'"') do set "CYCLE_START_HUMAN=%%A"

call :LOG "============================================================"
call :LOG " ATUALIZAR DASHBOARDS AURA - ESTOQUE + ENTREGAS + HTML"
call :LOG "============================================================"
call :LOG "Pasta atual: %CD%"
call :LOG "Inicio do ciclo: !CYCLE_START_HUMAN!"
call :LOG "Intervalo configurado: %INTERVAL_SEC% segundos entre inicios de ciclo"
call :LOG ""

call :GIT_STATUS "Git status antes do pull"
if errorlevel 1 goto :CYCLE_FAIL

call :LOG "[1/8] Sincronizando com origin/main..."
if exist ".git\rebase-merge" (
    set "ERRMSG=Existe um rebase pendente no Git. Execute git rebase --abort ou resolva o rebase antes do ciclo."
    goto :CYCLE_FAIL
)
if exist ".git\rebase-apply" (
    set "ERRMSG=Existe um rebase pendente no Git. Execute git rebase --abort ou resolva o rebase antes do ciclo."
    goto :CYCLE_FAIL
)
set "ALL_PROXY="
set "HTTP_PROXY="
set "HTTPS_PROXY="
set "GIT_HTTP_PROXY="
set "GIT_HTTPS_PROXY="
set "GIT_TERMINAL_PROMPT=0"
set "STEP_NAME=git pull --rebase --autostash origin main"
call :MAKE_STEP_LOG
git pull --rebase --autostash origin main >"!STEP_LOG!" 2>&1
set "STEP_RC=%ERRORLEVEL%"
call :FLUSH_STEP
if errorlevel 1 (
    set "ERRMSG=Falha ao sincronizar com origin/main."
    goto :CYCLE_FAIL
)

call :LOG "[2/8] Validando sintaxe Python antes de gerar..."
call :RUN_PY_COMPILE
if errorlevel 1 (
    set "ERRMSG=Falha de sintaxe em script Python."
    goto :CYCLE_FAIL
)

call :RUN_SCRIPT "gerar_html_estoque.py" "[3/8] Gerando ESTOQUE_DATALOGGERS.html..."
if errorlevel 1 (
    set "ERRMSG=Falha ao gerar ESTOQUE_DATALOGGERS.html."
    goto :CYCLE_FAIL
)

call :RUN_SCRIPT "gerar_html_controle_entregas.py" "[4/8] Gerando CONTROLE_ENTREGAS_20D.html e CSVs..."
if errorlevel 1 (
    set "ERRMSG=Falha ao gerar CONTROLE_ENTREGAS_20D."
    goto :CYCLE_FAIL
)

call :RUN_SCRIPT "HTMLACOMPANHAMENTO.py" "[5/8] Gerando HTMLACOMPANHAMENTO.html..."
if errorlevel 1 (
    set "ERRMSG=Falha ao gerar HTMLACOMPANHAMENTO.html."
    goto :CYCLE_FAIL
)

call :LOG "[6/8] Validando HTMLs gerados e payloads reais..."
set "STEP_NAME=validate-html"
call :MAKE_STEP_LOG
"%PY_EXE%" ".\aura_update_checks.py" validate-html --cycle-start "!CYCLE_START_TS!" >"!STEP_LOG!" 2>&1
set "STEP_RC=%ERRORLEVEL%"
call :FLUSH_STEP
if errorlevel 1 (
    set "ERRMSG=Validacao dos HTMLs falhou; nada sera enviado."
    goto :CYCLE_FAIL
)

call :GIT_STATUS "Git status antes do stage"
if errorlevel 1 goto :CYCLE_FAIL

call :LOG "[7/8] Preparando git add somente para alteracoes reais..."
set "STAGE_FILE=%TEMP%\aura_stage_%RANDOM%_%RANDOM%.txt"
set "STEP_NAME=changed-files"
call :MAKE_STEP_LOG
"%PY_EXE%" ".\aura_update_checks.py" changed-files --restore-timestamp-only --out "!STAGE_FILE!" >"!STEP_LOG!" 2>&1
set "STEP_RC=%ERRORLEVEL%"
call :FLUSH_STEP
if errorlevel 1 (
    set "ERRMSG=Falha ao calcular arquivos para commit."
    goto :CYCLE_FAIL
)

if not exist "!STAGE_FILE!" (
    set "ERRMSG=Arquivo temporario de stage nao foi criado."
    goto :CYCLE_FAIL
)

set "FILES_TO_ADD=0"
for /f "usebackq delims=" %%F in ("!STAGE_FILE!") do (
    if not "%%F"=="" (
        set /a FILES_TO_ADD+=1
        call :LOG "git add %%F"
        git add -- "%%F" >>"%LOG_FILE%" 2>&1
        if errorlevel 1 (
            set "ERRMSG=Falha no git add de %%F."
            goto :CYCLE_FAIL
        )
    )
)

if "!FILES_TO_ADD!"=="0" (
    call :LOG "[INFO] Nenhuma alteracao real para commit."
    goto :AFTER_COMMIT
)

git diff --cached --quiet --exit-code
set "DIFF_RC=%ERRORLEVEL%"
if "%DIFF_RC%"=="0" (
    call :LOG "[INFO] Nada ficou staged para commit."
    goto :AFTER_COMMIT
)
if not "%DIFF_RC%"=="1" (
    set "ERRMSG=Falha ao avaliar git diff --cached."
    goto :CYCLE_FAIL
)

for /f "delims=" %%A in ('powershell -NoProfile -Command "Get-Date -Format 'yyyy-MM-dd HH:mm'"') do set "COMMIT_STAMP=%%A"
set "STEP_NAME=git commit"
call :MAKE_STEP_LOG
git commit -m "Atualiza dashboards Aura - !COMMIT_STAMP!" >"!STEP_LOG!" 2>&1
set "STEP_RC=%ERRORLEVEL%"
call :FLUSH_STEP
if errorlevel 1 (
    set "ERRMSG=Falha no git commit."
    goto :CYCLE_FAIL
)
set "COMMIT_DONE=sim"
call :LOG "[OK] Commit criado: Atualiza dashboards Aura - !COMMIT_STAMP!"

:AFTER_COMMIT
call :LOG "[8/8] Verificando push para origin/main..."
set "AHEAD_COUNT=0"
for /f %%A in ('git rev-list --count origin/main..HEAD 2^>nul') do set "AHEAD_COUNT=%%A"
if "!AHEAD_COUNT!"=="0" (
    call :LOG "[INFO] Push nao necessario - sem commits locais pendentes."
    goto :AFTER_PUSH
)

call :LOG "[INFO] Enviando !AHEAD_COUNT! commit(s) pendente(s)..."
set "STEP_NAME=git push origin HEAD:main"
call :MAKE_STEP_LOG
git push origin HEAD:main >"!STEP_LOG!" 2>&1
set "STEP_RC=%ERRORLEVEL%"
call :FLUSH_STEP
if errorlevel 1 (
    set "ERRMSG=Falha no git push. Faca login no GitHub pelo Git Credential Manager ou gh auth login e rode novamente."
    goto :CYCLE_FAIL
)
set "PUSH_DONE=sim"
call :LOG "[OK] Push concluido com sucesso."

:AFTER_PUSH
call :GIT_STATUS "Git status apos ciclo"
call :LOG ""
call :LOG "[OK] Ciclo concluido com sucesso."
call :LOG "Commit neste ciclo: !COMMIT_DONE!"
call :LOG "Push neste ciclo: !PUSH_DONE!"
if "!PUSH_DONE!"=="sim" (
    call :LOG "Arquivos enviados neste ciclo:"
    for /f "usebackq delims=" %%F in ("!STAGE_FILE!") do (
        if not "%%F"=="" call :LOG "  - %%F"
    )
)
call :LOG "URLs publicadas:"
call :LOG "  https://luan9753.github.io/banco-aura-dashboard/ESTOQUE_DATALOGGERS.html"
call :LOG "  https://luan9753.github.io/banco-aura-dashboard/CONTROLE_ENTREGAS_20D.html"
call :LOG "  https://luan9753.github.io/banco-aura-dashboard/HTMLACOMPANHAMENTO.html"
call :FINISH_CYCLE 0
if exist "!STAGE_FILE!" del /q "!STAGE_FILE!" >nul 2>nul
exit /b 0

:CYCLE_FAIL
if not defined ERRMSG set "ERRMSG=Erro inesperado no ciclo."
call :LOG ""
call :LOG "[ERRO] !ERRMSG!"
call :LOG "[ERRO] GeraÃƒÂ§ÃƒÂ£o falhou ou validacao falhou; commit e push foram bloqueados."
call :GIT_STATUS "Git status apos erro"
call :FINISH_CYCLE 1
if exist "!STAGE_FILE!" del /q "!STAGE_FILE!" >nul 2>nul
exit /b 1

:RUN_SCRIPT
set "SCRIPT_NAME=%~1"
set "STEP_LABEL=%~2"
call :LOG "%STEP_LABEL%"
set "STEP_NAME=%SCRIPT_NAME%"
call :MAKE_STEP_LOG
"%PY_EXE%" ".\%SCRIPT_NAME%" >"!STEP_LOG!" 2>&1
set "STEP_RC=%ERRORLEVEL%"
call :FLUSH_STEP
exit /b %ERRORLEVEL%

:GIT_STATUS
call :LOG "%~1:"
set "STEP_NAME=%~1"
call :MAKE_STEP_LOG
git status --short --branch >"!STEP_LOG!" 2>&1
set "STEP_RC=%ERRORLEVEL%"
call :FLUSH_STEP
exit /b %ERRORLEVEL%

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
call :LOG "Proxima execucao: !NEXT_AT! (!WAIT_SEC! segundo(s) de espera)"
echo Pressione Ctrl+C para encerrar.
timeout /t !WAIT_SEC! /nobreak >nul
exit /b 0
