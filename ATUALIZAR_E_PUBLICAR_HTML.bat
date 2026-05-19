@echo off
setlocal EnableExtensions

if /I "%~1"=="__RUN__" goto :MAIN
start "Atualizar e Publicar HTML Aura" cmd /k ""%~f0" __RUN__"
exit /b 0

:MAIN

set "EXIT_CODE=0"
set "HAS_CHANGES=0"
set "HAS_STASH=0"
set "PAGE_URL=https://luan9753.github.io/banco-aura-dashboard/HTMLACOMPANHAMENTO.html"

cd /d "%~dp0"
if errorlevel 1 set "ERRMSG=Nao foi possivel acessar a pasta do script." & goto :FAIL

echo ============================================================
echo  ATUALIZAR E PUBLICAR - HTML ACOMPANHAMENTO
echo ============================================================
echo Pasta atual: %CD%
echo Inicio: %date% %time%
echo.

echo [1/4] Gerando HTML atualizado a partir do banco...
py ".\HTMLACOMPANHAMENTO.py"
if errorlevel 1 set "ERRMSG=Falha ao gerar o HTML (passo 1)." & goto :FAIL
echo [OK] HTML gerado com sucesso.
echo.

echo [2/4] Preparando commit no Git...
git add HTMLACOMPANHAMENTO.html HTMLACOMPANHAMENTO.py gerar_dashboard_entregas.py ATUALIZAR_E_PUBLICAR_HTML.bat
if errorlevel 1 set "ERRMSG=Falha no git add (passo 2)." & goto :FAIL

git diff --cached --quiet --exit-code
if errorlevel 1 goto :DO_COMMIT
echo [INFO] Nenhuma alteracao nova para commit.
goto :AFTER_COMMIT

:DO_COMMIT
set "HAS_CHANGES=1"
echo [INFO] Alteracoes detectadas. Criando commit...
git commit -m "Atualiza HTMLACOMPANHAMENTO e script de publicacao"
if errorlevel 1 set "ERRMSG=Falha no git commit (passo 2)." & goto :FAIL
echo [OK] Commit criado com sucesso.

:AFTER_COMMIT
echo.

echo [3/4] Enviando para GitHub...
set "ALL_PROXY="
set "HTTP_PROXY="
set "HTTPS_PROXY="
set "GIT_HTTP_PROXY="
set "GIT_HTTPS_PROXY="

if "%HAS_CHANGES%"=="1" goto :DO_PUSH
echo [INFO] Push nao necessario (sem alteracoes novas).
goto :AFTER_PUSH

:DO_PUSH
git status --porcelain | findstr /r "." >nul
if not errorlevel 1 (
    echo [INFO] Salvando alteracoes locais temporariamente...
    git stash push --include-untracked -m "auto-publish HTMLACOMPANHAMENTO" >nul
    if errorlevel 1 set "ERRMSG=Falha ao criar stash local." & goto :FAIL
    set "HAS_STASH=1"
)

echo [INFO] Sincronizando com origin/main antes do push...
set "GIT_TERMINAL_PROMPT=0"
git pull --rebase origin main
if errorlevel 1 set "ERRMSG=Falha ao sincronizar com o remoto (passo 3)." & goto :FAIL

git push origin HEAD:main
if errorlevel 1 set "ERRMSG=Falha no git push (passo 3). Faca login no GitHub pelo Git Credential Manager ou execute gh auth login e rode novamente." & goto :FAIL
echo [OK] Push concluido com sucesso.

if "%HAS_STASH%"=="1" (
    echo [INFO] Restaurando alteracoes locais...
    git stash pop --index
    if errorlevel 1 set "ERRMSG=Falha ao restaurar o stash local." & goto :FAIL
)

:AFTER_PUSH
echo.

echo [4/4] Processo concluido com sucesso.
echo URL publicada: %PAGE_URL%
echo Fim: %date% %time%
goto :END

:FAIL
set "EXIT_CODE=1"
echo.
if "%HAS_STASH%"=="1" (
    echo [INFO] Restaurando alteracoes locais...
    git stash pop --index >nul
    if errorlevel 1 echo [WARN] Nao foi possivel restaurar o stash local automaticamente.
)
echo [ERRO] %ERRMSG%
echo Fim com erro: %date% %time%

:END
echo.
echo Pressione qualquer tecla para fechar...
pause >nul
exit /b %EXIT_CODE%
