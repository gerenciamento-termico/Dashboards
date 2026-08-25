@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if errorlevel 1 (
  echo Nao foi possivel abrir a pasta do script.
  pause
  exit /b 1
)

echo ============================================================
echo  PENDENCIAS DE SINCRONISMO
echo ============================================================
echo Pasta: %CD%
echo Inicio: %date% %time%
echo.
echo Gerando snapshot, HTML, CSV e XLSX a partir do CSV organizado...
if exist "%USERPROFILE%\Downloads\sem_sync_ares_589_pendente.csv" (
  copy /Y "%USERPROFILE%\Downloads\sem_sync_ares_589_pendente.csv" "%CD%\sem_sync_ares_589_pendente.csv" >nul
  echo Seed: Downloads\sem_sync_ares_589_pendente.csv
) else if exist "%CD%\sem_sync_ares_589_pendente.csv" (
  echo Seed: sem_sync_ares_589_pendente.csv local
) else (
  echo CSV organizado nao encontrado em Downloads.
  pause
  exit /b 1
)
py ".\gerar_html_pendencias_sincronismo.py"
if errorlevel 1 (
  echo Falha ao gerar Pendencias de Sincronismo.
  pause
  exit /b 1
)
echo.
echo [OK] Arquivos gerados:
echo   PENDENCIAS_SINCRONISMO.html
echo   PENDENCIAS_SINCRONISMO.csv
echo   PENDENCIAS_SINCRONISMO.xlsx
echo   snapshot_pendencias_sincronismo\pendencias_sincronismo.json
echo.
echo A publicacao no GitHub Pages ocorre no ciclo de ATUALIZAR_TUDO_10_MIN.bat.
echo URL: https://gerenciamento-termico.github.io/Dashboards/PENDENCIAS_SINCRONISMO.html
echo Fim: %date% %time%
exit /b 0
