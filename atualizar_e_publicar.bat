@echo off
setlocal EnableExtensions

cd /d "%~dp0"
if errorlevel 1 exit /b 1

set "PY_CMD=py"
where py >nul 2>nul
if errorlevel 1 set "PY_CMD=python"

%PY_CMD% HTMLACOMPANHAMENTO.py
if errorlevel 1 (
    echo ERRO: falha ao gerar o dashboard.
    exit /b 1
)

git add HTMLACOMPANHAMENTO.html
git diff --cached --quiet && exit /b 0

git commit -m "Dashboard atualizado automaticamente em %date% %time%"
set "GIT_TERMINAL_PROMPT=0"
git push origin main
if errorlevel 1 (
    echo ERRO: falha no git push. Faca login no GitHub pelo Git Credential Manager ou execute gh auth login e rode novamente.
    exit /b 1
)

echo Dashboard publicado com sucesso!
