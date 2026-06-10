@echo off
setlocal EnableExtensions

if /I "%~1"=="__RUN__" goto :MAIN
start "Atualizar Indicador Produtividade" cmd /k ""%~f0" __RUN__"
exit /b 0

:MAIN
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"
if errorlevel 1 set "ERRMSG=Nao foi possivel acessar a pasta do script." & goto :FAIL

set "PAGE_URL=https://luan9753.github.io/banco-aura-dashboard/indicador_produtividade.html"
set "CYCLE=1"

:LOOP
set "ERRMSG="
set "COMMITTED=0"
set "EXIT_CODE=0"

echo ============================================================
echo  ATUALIZAR INDICADOR PRODUTIVIDADE - PUBLICAR NO GITHUB
echo ============================================================
echo Pasta atual: %CD%
echo Inicio do ciclo %CYCLE%: %date% %time%
echo.

echo [1/4] Preparando atualizacao do indicador...
git add indicador_produtividade.html
if errorlevel 1 set "ERRMSG=Falha no git add do arquivo indicador_produtividade.html." & goto :CYCLE_END
echo [OK] Arquivo preparado para commit.
echo.

echo [2/4] Verificando se ha alteracoes para commit...
git diff --cached --quiet --exit-code
if errorlevel 1 goto :DO_COMMIT
echo [INFO] Nao ha alteracoes novas para commit.
goto :AFTER_COMMIT

:DO_COMMIT
set "COMMITTED=1"
for /f "tokens=1-3 delims=/ " %%a in ("%date%") do set "TODAY=%%c-%%b-%%a"
git commit -m "Atualiza indicador_produtividade.html - %TODAY% %time:~0,8%"
if errorlevel 1 set "ERRMSG=Falha ao criar commit do indicador_produtividade.html." & goto :CYCLE_END
echo [OK] Commit criado com sucesso.

:AFTER_COMMIT
echo.
echo [3/4] Sincronizando com o Git remoto...
set "ALL_PROXY="
set "HTTP_PROXY="
set "HTTPS_PROXY="
set "GIT_HTTP_PROXY="
set "GIT_HTTPS_PROXY="
set "GIT_TERMINAL_PROMPT=0"

git fetch origin
if errorlevel 1 set "ERRMSG=Falha no git fetch antes do push." & goto :CYCLE_END
git rebase --autostash origin/main
if errorlevel 1 set "ERRMSG=Falha no git rebase contra origin/main." & goto :CYCLE_END

echo.
echo [4/4] Enviando para GitHub...
git push origin HEAD:main
if errorlevel 1 set "ERRMSG=Falha no git push. Verifique autenticacao." & goto :CYCLE_END
echo [OK] Push concluido.

:CYCLE_OK
echo.
echo ============================================================
echo  CICLO %CYCLE% CONCLUIDO COM SUCESSO
echo  URL:
echo   - %PAGE_URL%
echo  Fim do ciclo: %date% %time%
echo ============================================================
goto :WAIT_NEXT

:CYCLE_END
set "EXIT_CODE=1"
echo.
echo [ERRO] %ERRMSG%
echo Fim do ciclo com erro: %date% %time%
echo.
goto :WAIT_NEXT

:WAIT_NEXT
echo Proxima atualizacao em 10 minutos. Pressione Ctrl+C para encerrar.
timeout /t 600 /nobreak >nul
set /a CYCLE+=1
goto :LOOP

:FAIL
set "EXIT_CODE=1"
echo.
echo [ERRO] %ERRMSG%
echo Fim com erro: %date% %time%
echo.
echo Pressione qualquer tecla para fechar...
pause >nul
exit /b %EXIT_CODE%
