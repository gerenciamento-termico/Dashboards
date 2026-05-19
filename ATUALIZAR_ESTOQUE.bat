@echo off
setlocal EnableExtensions

if /I "%~1"=="__RUN__" goto :MAIN
start "Atualizar Estoque Dataloggers" cmd /k ""%~f0" __RUN__"
exit /b 0

:MAIN

set "EXIT_CODE=0"
set "HAS_CHANGES=0"
set "PAGE_URL=https://luan9753.github.io/banco-aura-dashboard/ESTOQUE_DATALOGGERS.html"

cd /d "%~dp0"
if errorlevel 1 set "ERRMSG=Nao foi possivel acessar a pasta do script." & goto :FAIL

echo ============================================================
echo  ATUALIZAR ESTOQUE DATALOGGERS - PUBLICAR NO GITHUB
echo ============================================================
echo Pasta atual: %CD%
echo Inicio: %date% %time%
echo.

echo [1/3] Gerando HTML atualizado...
py ".\gerar_html_estoque.py"
if errorlevel 1 set "ERRMSG=Falha ao gerar o HTML (passo 1)." & goto :FAIL
echo [OK] HTML gerado com sucesso.
echo.

echo [2/3] Preparando commit no Git...
git add ESTOQUE_DATALOGGERS.html gerar_html_estoque.py ATUALIZAR_ESTOQUE.bat
if errorlevel 1 set "ERRMSG=Falha no git add (passo 2)." & goto :FAIL

git diff --cached --quiet --exit-code
if errorlevel 1 goto :DO_COMMIT
echo [INFO] Nenhuma alteracao nova para commit.
goto :AFTER_COMMIT

:DO_COMMIT
set "HAS_CHANGES=1"
git commit -m "Atualiza ESTOQUE_DATALOGGERS.html"
if errorlevel 1 set "ERRMSG=Falha no git commit (passo 2)." & goto :FAIL
echo [OK] Commit criado com sucesso.

:AFTER_COMMIT
echo.

echo [3/3] Enviando para GitHub...
set "ALL_PROXY="
set "HTTP_PROXY="
set "HTTPS_PROXY="
set "GIT_HTTP_PROXY="
set "GIT_HTTPS_PROXY="
set "GIT_TERMINAL_PROMPT=0"

if "%HAS_CHANGES%"=="1" goto :DO_PUSH
echo [INFO] Push nao necessario (sem alteracoes novas).
goto :AFTER_PUSH

:DO_PUSH
echo [INFO] Sincronizando com origin/main antes do push...
git pull --rebase --autostash origin main
if errorlevel 1 set "ERRMSG=Falha ao sincronizar com o remoto (passo 3)." & goto :FAIL

git push origin HEAD:main
if errorlevel 1 set "ERRMSG=Falha no git push (passo 3). Faca login no GitHub pelo Git Credential Manager ou execute gh auth login e rode novamente." & goto :FAIL
echo [OK] Push concluido com sucesso.

:AFTER_PUSH
echo.
echo [OK] Processo concluido com sucesso.
echo URL publicada: %PAGE_URL%
echo Fim: %date% %time%
goto :END

:FAIL
set "EXIT_CODE=1"
echo.
echo [ERRO] %ERRMSG%
echo Fim com erro: %date% %time%

:END
echo.
echo Pressione qualquer tecla para fechar...
pause >nul
exit /b %EXIT_CODE%
