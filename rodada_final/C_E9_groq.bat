@echo off
setlocal
rem Rodada final pre-banca. Scripts escritos pelo Claude; leia antes de executar.
rem Nao exibem nem gravam chaves de API. Saidas em rodada_final\logs.
set "RAIZ=%~dp0.."
set "PY=%~dp0..\..\..\dfd-assistido\dfd-assistido\backend\.venv\Scripts\python.exe"
set "LOGS=%~dp0logs"
if not exist "%LOGS%" mkdir "%LOGS%"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if not exist "%PY%" (
  echo Python do ambiente nao encontrado: "%PY%"
  pause
  exit /b 1
)
cd /d "%RAIZ%\backend"
echo === E9 Groq: continua de onde parou ===
>> "%LOGS%\C_E9_groq.log" echo [%date% %time%] inicio
"%PY%" -u -m avaliacao.executar --somente-e9 --llm groq >> "%LOGS%\C_E9_groq.log" 2>&1
>> "%LOGS%\C_status.txt" echo [%date% %time%] E9_groq=%errorlevel%
echo Concluido. Veja rodada_final\logs\C_E9_groq.log.
pause
