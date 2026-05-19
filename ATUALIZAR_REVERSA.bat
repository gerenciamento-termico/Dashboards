@echo off
setlocal EnableExtensions

if /I "%~1"=="__RUN__" goto :MAIN
start "Atualizar Reversa Dataloggers" cmd /k ""%~f0" __RUN__"
exit /b 0

:MAIN

set "EXIT_CODE=0"
set "HAS_CHANGES=0"
set "WORKSPACE=%~dp0.."
set "STREAMLIT_DIR=%~dp0..\streamlit"
set "PAGE_URL=https://luan9753.github.io/banco-aura-dashboard/REVERSA_DATALOGGERS.html"

cd /d "%~dp0"
if errorlevel 1 set "ERRMSG=Nao foi possivel acessar a pasta do script." & goto :FAIL

echo ============================================================
echo  ATUALIZAR REVERSA DATALOGGERS - PUBLICAR NO GITHUB
echo ============================================================
echo Pasta atual: %CD%
echo Inicio: %date% %time%
echo.

echo [1/4] Atualizando snapshots do banco de dados...
py "%STREAMLIT_DIR%\gerar_snapshot_reversa.py"
if errorlevel 1 set "ERRMSG=Falha ao atualizar snapshots (passo 1)." & goto :FAIL
echo [OK] Snapshots atualizados.
echo.

echo [2/4] Gerando HTML atualizado...
py ".\gerar_html_reversa.py"
if errorlevel 1 set "ERRMSG=Falha ao gerar o HTML (passo 2)." & goto :FAIL
echo [OK] HTML gerado com sucesso.
echo.

echo [3/4] Preparando commit no Git...
git add REVERSA_DATALOGGERS.html gerar_html_reversa.py ATUALIZAR_REVERSA.bat

git diff --cached --quiet --exit-code
if errorlevel 1 goto :DO_COMMIT
echo [INFO] Nenhuma alteracao nova para commit.
goto :AFTER_COMMIT

:DO_COMMIT
set "HAS_CHANGES=1"
for /f "tokens=1-3 delims=/ " %%a in ("%date%") do set "TODAY=%%c-%%b-%%a"
git commit -m "Atualiza REVERSA_DATALOGGERS.html - %TODAY% %time:~0,8%"
if errorlevel 1 set "ERRMSG=Falha no git commit (passo 3)." & goto :FAIL
echo [OK] Commit criado com sucesso.

:AFTER_COMMIT
echo.

echo [4/4] Enviando para GitHub...
set "ALL_PROXY="
set "HTTP_PROXY="
set "HTTPS_PROXY="
set "GIT_TERMINAL_PROMPT=0"

if "%HAS_CHANGES%"=="0" (
    echo [INFO] Push nao necessario ^(sem alteracoes novas^).
    goto :AFTER_PUSH
)

git fetch origin
if errorlevel 1 set "ERRMSG=Falha no git fetch antes do push (passo 4)." & goto :FAIL

git rebase --autostash origin/main
if errorlevel 1 set "ERRMSG=Falha no git rebase contra origin/main (passo 4)." & goto :FAIL

git push origin HEAD:main
if errorlevel 1 set "ERRMSG=Falha no git push (passo 4). Faca login no GitHub pelo Git Credential Manager ou execute gh auth login e rode novamente." & goto :FAIL
echo [OK] Push concluido.

:AFTER_PUSH
echo.
echo ============================================================
echo  CONCLUIDO COM SUCESSO
echo  URL: %PAGE_URL%
echo  Fim: %date% %time%
echo ============================================================
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
