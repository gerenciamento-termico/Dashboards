@echo off
setlocal EnableExtensions EnableDelayedExpansion

if /I "%~1"=="__RUN__" goto :MAIN
if /I "%~1"=="__CHECK__" goto :CHECK
start "Atualizar Dashboards Aura - 10 min" cmd /k ""%~f0" __RUN__"
exit /b 0

:MAIN
cd /d "%~dp0"
if errorlevel 1 exit /b 1

set "INTERVAL_MIN=10"
set "INTERVAL_SEC=600"
set "LOCKFILE=%TEMP%\aura_atualizar_tudo_10_min.lock"
set "LOCK_STALE_MIN=30"
set "EXIT_CODE=0"

call :CHECK_LOCK
if errorlevel 1 exit /b 1

call :SETUP_TOOLS
if errorlevel 1 (
    echo.
    echo Corrija os itens acima e execute este arquivo novamente.
    echo.
    pause
    exit /b 1
)

goto :LOOP

:CHECK
cd /d "%~dp0"
if errorlevel 1 exit /b 1
call :SETUP_TOOLS
exit /b %ERRORLEVEL%

:CHECK_LOCK
powershell -NoProfile -Command ^
  "$p = '%LOCKFILE%';" ^
  "if (Test-Path $p) {" ^
  "  $age = (Get-Date) - ((Get-Item $p).LastWriteTime);" ^
  "  if ($age.TotalMinutes -lt %LOCK_STALE_MIN%) { exit 2 }" ^
  "  Remove-Item $p -Force" ^
  "}" ^
  "exit 0"
if errorlevel 2 (
    echo Ja existe uma instancia recente do atualizador unificado em execucao.
    echo Feche a outra janela ou aguarde alguns minutos antes de iniciar novamente.
    exit /b 1
)
> "%LOCKFILE%" echo %date% %time%
exit /b 0

:SETUP_TOOLS
set "PY_CMD="
where py >nul 2>nul
if not errorlevel 1 set "PY_CMD=py"

if not defined PY_CMD (
    where python >nul 2>nul
    if not errorlevel 1 set "PY_CMD=python"
)

if not defined PY_CMD (
    echo [ERRO] Python nao encontrado. Instale o Python e deixe o comando py ou python disponivel no PATH.
    exit /b 1
)

%PY_CMD% --version >nul 2>nul
if errorlevel 1 (
    echo [ERRO] Python encontrado, mas nao executou corretamente. Reinstale o Python ou ajuste o PATH.
    exit /b 1
)

where git >nul 2>nul
if errorlevel 1 (
    if exist "%LOCALAPPDATA%\Programs\Git\cmd\git.exe" set "PATH=%LOCALAPPDATA%\Programs\Git\cmd;%PATH%"
    if exist "%ProgramFiles%\Git\cmd\git.exe" set "PATH=%ProgramFiles%\Git\cmd;%PATH%"
    if exist "%ProgramFiles(x86)%\Git\cmd\git.exe" set "PATH=%ProgramFiles(x86)%\Git\cmd;%PATH%"
)

where git >nul 2>nul
if errorlevel 1 (
    echo [ERRO] Git nao encontrado. Instale o Git for Windows e execute este arquivo novamente.
    exit /b 1
)

git config --get user.name >nul 2>nul
if errorlevel 1 git config user.name "Aura Auto Update"

git config --get user.email >nul 2>nul
if errorlevel 1 git config user.email "aura-auto-update@example.local"

exit /b 0

:LOOP
> "%LOCKFILE%" echo %date% %time%
set "EXIT_CODE=0"
set "ERRMSG="

echo ============================================================
echo  ATUALIZAR DASHBOARDS AURA - ESTOQUE + ENTREGAS + HTML
echo ============================================================
echo Pasta atual: %CD%
echo Inicio do ciclo: %date% %time%
echo Intervalo padrao: %INTERVAL_MIN% minutos
echo.

echo [1/6] Sincronizando com origin/main...
if exist ".git\rebase-merge" (
    set "ERRMSG=Existe um rebase pendente no Git. Execute git rebase --abort ou me chame para limpar."
    goto :FAIL
)
if exist ".git\rebase-apply" (
    set "ERRMSG=Existe um rebase pendente no Git. Execute git rebase --abort ou me chame para limpar."
    goto :FAIL
)

git pull --rebase --autostash origin main
if errorlevel 1 (
    set "ERRMSG=Falha ao sincronizar com o remoto (passo 1)."
    goto :FAIL
)
echo [OK] Repositorio sincronizado.
echo.

echo [2/6] Atualizando ESTOQUE_DATALOGGERS.html...
%PY_CMD% ".\gerar_html_estoque.py"
if errorlevel 1 (
    set "ERRMSG=Falha ao atualizar ESTOQUE_DATALOGGERS.html (passo 2)."
    goto :FAIL
)
echo [OK] Estoque atualizado.
echo.

echo [3/6] Atualizando CONTROLE_ENTREGAS_20D.html e CSVs...
%PY_CMD% ".\gerar_html_controle_entregas.py"
if errorlevel 1 (
    set "ERRMSG=Falha ao atualizar CONTROLE_ENTREGAS_20D (passo 3)."
    goto :FAIL
)
echo [OK] Controle de entregas atualizado.
echo.

echo [4/6] Atualizando HTMLACOMPANHAMENTO.html...
%PY_CMD% ".\HTMLACOMPANHAMENTO.py"
if errorlevel 1 (
    set "ERRMSG=Falha ao atualizar HTMLACOMPANHAMENTO.html (passo 4)."
    goto :FAIL
)
echo [OK] HTML acompanhamento atualizado.
echo.

echo [5/6] Preparando commit unico no Git...
git add ESTOQUE_DATALOGGERS.html gerar_html_estoque.py
if errorlevel 1 (
    set "ERRMSG=Falha no git add do estoque (passo 5)."
    goto :FAIL
)

git add CONTROLE_ENTREGAS_20D.html CONTROLE_ENTREGAS_20D.csv CONTROLE_ENTREGAS_20D_SLA_PENDENTES.csv gerar_html_controle_entregas.py
if errorlevel 1 (
    set "ERRMSG=Falha no git add do controle de entregas (passo 5)."
    goto :FAIL
)

git add HTMLACOMPANHAMENTO.html HTMLACOMPANHAMENTO.py gerar_dashboard_entregas.py
if errorlevel 1 (
    set "ERRMSG=Falha no git add do HTML acompanhamento (passo 5)."
    goto :FAIL
)

git add ATUALIZAR_TUDO_10_MIN.bat
if errorlevel 1 (
    set "ERRMSG=Falha no git add do atualizador unificado (passo 5)."
    goto :FAIL
)

git diff --cached --quiet --exit-code
if errorlevel 1 goto :DO_COMMIT
echo [INFO] Nenhuma alteracao nova para commit.
goto :AFTER_COMMIT

:DO_COMMIT
git commit -m "Atualiza dashboards Aura"
if errorlevel 1 (
    set "ERRMSG=Falha no git commit (passo 5)."
    goto :FAIL
)
echo [OK] Commit criado com sucesso.

:AFTER_COMMIT
echo.

echo [6/6] Enviando para GitHub...
set "ALL_PROXY="
set "HTTP_PROXY="
set "HTTPS_PROXY="
set "GIT_HTTP_PROXY="
set "GIT_HTTPS_PROXY="

set "AHEAD_COUNT=0"
for /f %%A in ('git rev-list --count origin/main..HEAD 2^>nul') do set "AHEAD_COUNT=%%A"

if "%AHEAD_COUNT%"=="0" (
    echo [INFO] Push nao necessario (sem commits locais pendentes).
    goto :AFTER_PUSH
)

echo [INFO] Enviando %AHEAD_COUNT% commit(s) pendente(s)...
git push origin HEAD:main
if errorlevel 1 (
    set "ERRMSG=Falha no git push (passo 6)."
    goto :FAIL
)
echo [OK] Push concluido com sucesso.

:AFTER_PUSH
echo.
echo [OK] Ciclo concluido com sucesso.
echo URLs publicadas:
echo   https://luan9753.github.io/banco-aura-dashboard/ESTOQUE_DATALOGGERS.html
echo   https://luan9753.github.io/banco-aura-dashboard/CONTROLE_ENTREGAS_20D.html
echo   https://luan9753.github.io/banco-aura-dashboard/HTMLACOMPANHAMENTO.html
echo Fim do ciclo: %date% %time%
echo.
echo Proxima atualizacao em %INTERVAL_MIN% minutos. Pressione Ctrl+C para encerrar.
timeout /t %INTERVAL_SEC% /nobreak >nul
goto :LOOP

:FAIL
set "EXIT_CODE=1"
echo.
echo [ERRO] %ERRMSG%
echo Fim com erro: %date% %time%
echo.
echo Proxima tentativa em %INTERVAL_MIN% minutos. Pressione Ctrl+C para encerrar.
timeout /t %INTERVAL_SEC% /nobreak >nul
goto :LOOP
