@echo off
setlocal EnableExtensions

if /I "%~1"=="__RUN__" goto :MAIN
start "Atualizar Indicadores Banco Aura" cmd /k ""%~f0" __RUN__"
exit /b 0

:MAIN
set "STREAMLIT_DIR=%~dp0..\streamlit"
set "PAGE_ESTOQUE=https://luan9753.github.io/banco-aura-dashboard/ESTOQUE_DATALOGGERS.html"
set "PAGE_REVERSA=https://luan9753.github.io/banco-aura-dashboard/REVERSA_DATALOGGERS.html"

cd /d "%~dp0"
if errorlevel 1 set "ERRMSG=Nao foi possivel acessar a pasta do script." & goto :FAIL

set "CYCLE=1"

:LOOP
set "ERRMSG="
set "COMMITTED=0"
set "EXIT_CODE=0"

echo ============================================================
echo  ATUALIZAR INDICADORES - ESTOQUE + REVERSA - PUBLICAR NO GITHUB
echo ============================================================
echo Pasta atual: %CD%
echo Inicio do ciclo %CYCLE%: %date% %time%
echo.

echo [1/5] Atualizando indicador de estoque...
py ".\gerar_html_estoque.py"
if errorlevel 1 set "ERRMSG=Falha ao gerar o HTML do estoque (passo 1)." & goto :CYCLE_END
echo [OK] Estoque atualizado.
echo.

echo [2/5] Atualizando indicador de reversa...
py "%STREAMLIT_DIR%\gerar_snapshot_reversa.py"
if errorlevel 1 set "ERRMSG=Falha ao atualizar snapshots da reversa (passo 2)." & goto :CYCLE_END

py "%STREAMLIT_DIR%\gerar_modelo_final_reversa.py"
if errorlevel 1 set "ERRMSG=Falha ao gerar o modelo final da reversa (passo 2)." & goto :CYCLE_END

py ".\gerar_html_reversa.py"
if errorlevel 1 set "ERRMSG=Falha ao gerar o HTML da reversa (passo 2)." & goto :CYCLE_END
echo [OK] Reversa atualizada.
echo.

echo [3/5] Preparando commit no Git...
git add ESTOQUE_DATALOGGERS.html REVERSA_DATALOGGERS.html gerar_html_estoque.py gerar_html_reversa.py ATUALIZAR_ESTOQUE.bat ATUALIZAR_REVERSA.bat ATUALIZAR_INDICADORES.bat
if errorlevel 1 set "ERRMSG=Falha no git add (passo 3)." & goto :CYCLE_END

git diff --cached --quiet --exit-code
if errorlevel 1 goto :DO_COMMIT
echo [INFO] Nenhuma alteracao nova para commit.
goto :AFTER_COMMIT

:DO_COMMIT
set "COMMITTED=1"
for /f "tokens=1-3 delims=/ " %%a in ("%date%") do set "TODAY=%%c-%%b-%%a"
git commit -m "Atualiza indicadores HTML - %TODAY% %time:~0,8%"
if errorlevel 1 set "ERRMSG=Falha no git commit (passo 3)." & goto :CYCLE_END
echo [OK] Commit criado com sucesso.

:AFTER_COMMIT
echo.

echo [4/5] Sincronizando com o Git remoto...
set "ALL_PROXY="
set "HTTP_PROXY="
set "HTTPS_PROXY="
set "GIT_HTTP_PROXY="
set "GIT_HTTPS_PROXY="
set "GIT_TERMINAL_PROMPT=0"

git fetch origin
if errorlevel 1 set "ERRMSG=Falha no git fetch antes do push (passo 4)." & goto :CYCLE_END

git rebase --autostash origin/main
if errorlevel 1 set "ERRMSG=Falha no git rebase contra origin/main (passo 4)." & goto :CYCLE_END

echo.
echo [5/5] Enviando para GitHub...
git push origin HEAD:main
if errorlevel 1 set "ERRMSG=Falha no git push (passo 5). Faca login no GitHub pelo Git Credential Manager ou execute gh auth login e rode novamente." & goto :CYCLE_END
echo [OK] Push concluido.

:CYCLE_OK
echo.
echo ============================================================
echo  CICLO %CYCLE% CONCLUIDO COM SUCESSO
echo  URLs:
echo   - %PAGE_ESTOQUE%
echo   - %PAGE_REVERSA%
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
echo Proxima atualizacao em 40 minutos. Pressione Ctrl+C para encerrar.
timeout /t 2400 /nobreak >nul
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
