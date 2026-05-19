@echo off
setlocal EnableExtensions

if /I "%~1"=="__RUN__" goto :MAIN
start "Atualizar Controle de Entregas" cmd /k ""%~f0" __RUN__"
exit /b 0

:MAIN
set "PAGE_URL=https://luan9753.github.io/banco-aura-dashboard/CONTROLE_ENTREGAS_20D.html"
set "LOCK_FILE=%~dp0.git\index.lock"
set "CYCLE=1"

cd /d "%~dp0"
if errorlevel 1 set "ERRMSG=Nao foi possivel acessar a pasta do script." & goto :FAIL

:LOOP
set "ERRMSG="

echo ============================================================
echo  ATUALIZAR CONTROLE DE ENTREGAS - PUBLICAR NO GITHUB
echo ============================================================
echo Pasta atual: %CD%
echo Inicio do ciclo %CYCLE%: %date% %time%
echo.

echo [1/4] Gerando HTML e CSV atualizados...
py ".\gerar_html_controle_entregas.py"
if errorlevel 1 set "ERRMSG=Falha ao gerar o HTML do controle de entregas (passo 1)." & goto :CYCLE_FAIL
echo [OK] HTML gerado com sucesso.
echo.

echo [2/4] Aguardando Git ficar livre...
call :WAIT_GIT_FREE
if errorlevel 1 set "ERRMSG=Falha ao aguardar o Git ficar livre (passo 2)." & goto :CYCLE_FAIL
echo [OK] Git liberado.
echo.

echo [3/4] Preparando commit no Git...
git add CONTROLE_ENTREGAS_20D.html CONTROLE_ENTREGAS_20D.csv CONTROLE_ENTREGAS_20D_SLA_PENDENTES.csv gerar_html_controle_entregas.py ATUALIZAR_CONTROLE_ENTREGAS.bat
if errorlevel 1 set "ERRMSG=Falha no git add (passo 3)." & goto :CYCLE_FAIL

git diff --cached --quiet --exit-code
if errorlevel 1 goto :DO_COMMIT
echo [INFO] Nenhuma alteracao nova para commit.
goto :AFTER_COMMIT

:DO_COMMIT
for /f "tokens=1-3 delims=/ " %%a in ("%date%") do set "TODAY=%%c-%%b-%%a"
git commit -m "Atualiza CONTROLE_ENTREGAS_20D.html - %TODAY% %time:~0,8%"
if errorlevel 1 set "ERRMSG=Falha no git commit (passo 3)." & goto :CYCLE_FAIL
echo [OK] Commit criado com sucesso.

:AFTER_COMMIT
echo.

echo [4/4] Enviando para GitHub...
set "ALL_PROXY="
set "HTTP_PROXY="
set "HTTPS_PROXY="
set "GIT_HTTP_PROXY="
set "GIT_HTTPS_PROXY="
set "GIT_TERMINAL_PROMPT=0"

git fetch origin
if errorlevel 1 set "ERRMSG=Falha no git fetch antes do push (passo 4)." & goto :CYCLE_FAIL

git rebase --autostash origin/main
if errorlevel 1 set "ERRMSG=Falha no git rebase contra origin/main (passo 4)." & goto :CYCLE_FAIL

git push origin HEAD:main
if errorlevel 1 set "ERRMSG=Falha no git push (passo 4). Faca login no GitHub pelo Git Credential Manager ou execute gh auth login e rode novamente." & goto :CYCLE_FAIL
echo [OK] Push concluido.

:CYCLE_OK
echo.
echo ============================================================
echo  CICLO %CYCLE% CONCLUIDO COM SUCESSO
echo  URL: %PAGE_URL%
echo  Fim do ciclo: %date% %time%
echo ============================================================
goto :WAIT_NEXT

:CYCLE_FAIL
echo.
echo [ERRO] %ERRMSG%
echo Fim do ciclo com erro: %date% %time%
echo.
goto :WAIT_NEXT

:WAIT_NEXT
echo Proxima atualizacao em 30 minutos. Pressione Ctrl+C para encerrar.
timeout /t 1800 /nobreak >nul
set /a CYCLE+=1
goto :LOOP

:WAIT_GIT_FREE
if exist "%LOCK_FILE%" (
    echo   Git ocupado no momento. Aguardando...
    timeout /t 5 /nobreak >nul
    goto :WAIT_GIT_FREE
)
tasklist /FI "IMAGENAME eq git.exe" | find /I "git.exe" >nul
if not errorlevel 1 (
    echo   Processos git ativos. Aguardando...
    timeout /t 5 /nobreak >nul
    goto :WAIT_GIT_FREE
)
exit /b 0

:FAIL
echo.
echo [ERRO] %ERRMSG%
echo Fim com erro: %date% %time%
echo.
echo Pressione qualquer tecla para fechar...
pause >nul
exit /b 1
