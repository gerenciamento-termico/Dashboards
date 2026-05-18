@echo off
setlocal EnableExtensions EnableDelayedExpansion

if /I "%~1"=="__RUN__" goto :MAIN
start "Atualizar Estoque Dataloggers - Hoje" cmd /k ""%~f0" __RUN__"
exit /b 0

:MAIN
cd /d "%~dp0"
if errorlevel 1 exit /b 1

set "PAGE_URL=https://luan9753.github.io/banco-aura-dashboard/ESTOQUE_DATALOGGERS.html"
set "INTERVAL_MIN=5"
set "INTERVAL_SEC=300"
set "LOCKFILE=%TEMP%\aura_estoque_hoje.lock"
set "LOCK_STALE_MIN=10"

powershell -NoProfile -Command ^
  "$p = '%LOCKFILE%';" ^
  "if (Test-Path $p) {" ^
  "  $age = (Get-Date) - ((Get-Item $p).LastWriteTime);" ^
  "  if ($age.TotalMinutes -lt %LOCK_STALE_MIN%) { exit 2 }" ^
  "  Remove-Item $p -Force" ^
  "}" ^
  "exit 0"
if errorlevel 2 (
    echo Ja existe uma instancia recente do launcher em execucao.
    echo Aguarde alguns minutos ou feche a outra janela antes de iniciar novamente.
    exit /b 1
)
> "%LOCKFILE%" echo %date% %time%

call :SETUP_TOOLS
if errorlevel 1 (
    echo.
    echo Corrija os itens acima e execute este arquivo novamente.
    echo.
    pause
    exit /b 1
)
goto :LOOP

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
set "HAS_CHANGES=0"

echo ============================================================
echo  ATUALIZAR ESTOQUE DATALOGGERS - SOMENTE HOJE
echo ============================================================
echo Pasta atual: %CD%
echo Inicio do ciclo: %date% %time%
echo.

echo [1/4] Sincronizando com origin/main...
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

echo [2/4] Gerando HTML atualizado...
%PY_CMD% ".\gerar_html_estoque.py"
if errorlevel 1 (
    set "ERRMSG=Falha ao gerar o HTML (passo 2)."
    goto :FAIL
)
echo [OK] HTML gerado com sucesso.
echo.

echo [3/4] Preparando commit no Git...
git add ESTOQUE_DATALOGGERS.html gerar_html_estoque.py
if errorlevel 1 (
    set "ERRMSG=Falha no git add (passo 3)."
    goto :FAIL
)

git diff --cached --quiet --exit-code
if errorlevel 1 goto :DO_COMMIT
echo [INFO] Nenhuma alteracao nova para commit.
goto :AFTER_COMMIT

:DO_COMMIT
set "HAS_CHANGES=1"
git commit -m "Atualiza ESTOQUE_DATALOGGERS.html - hoje"
if errorlevel 1 (
    set "ERRMSG=Falha no git commit (passo 3)."
    goto :FAIL
)
echo [OK] Commit criado com sucesso.

:AFTER_COMMIT
echo.

echo [4/4] Enviando para GitHub...
set "ALL_PROXY="
set "HTTP_PROXY="
set "HTTPS_PROXY="
set "GIT_HTTP_PROXY="
set "GIT_HTTPS_PROXY="

set "AHEAD_COUNT=0"
for /f %%A in ('git rev-list --count origin/main..HEAD 2^>nul') do set "AHEAD_COUNT=%%A"

if not "%AHEAD_COUNT%"=="0" goto :DO_PUSH
echo [INFO] Push nao necessario (sem commits locais pendentes).
goto :AFTER_PUSH

:DO_PUSH
echo [INFO] Enviando %AHEAD_COUNT% commit(s) pendente(s)...
git push origin HEAD:main
if errorlevel 1 (
    set "ERRMSG=Falha no git push (passo 4)."
    goto :FAIL
)
echo [OK] Push concluido com sucesso.

:AFTER_PUSH
echo.
echo [OK] Ciclo concluido com sucesso.
echo URL publicada: %PAGE_URL%
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

:CLEANUP
if exist "%LOCKFILE%" del "%LOCKFILE%" >nul 2>&1
exit /b %EXIT_CODE%
