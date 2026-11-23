@echo off
setlocal
rem Retoma o E9: Gemini (amostra 6) e depois Groq. Continua de onde parou.
rem Script escrito pelo Claude; nao exibe nem grava chaves de API.
set "PY=%~dp0..\..\..\dfd-assistido\dfd-assistido\backend\.venv\Scripts\python.exe"
set "LOGS=%~dp0logs"
if not exist "%LOGS%" mkdir "%LOGS%"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if not exist "%PY%" (echo Python do ambiente nao encontrado & pause & exit /b 1)
cd /d "%~dp0..\backend"
>> "%LOGS%\E_status.txt" echo [%date% %time%] inicio
echo === E9 Gemini, amostra de 6 (continua de onde parou) ===
>> "%LOGS%\A4_E9_gemini.log" echo [%date% %time%] retomada
"%PY%" -u -m avaliacao.executar --somente-e9 --llm gemini --amostra 6 >> "%LOGS%\A4_E9_gemini.log" 2>&1
>> "%LOGS%\E_status.txt" echo gemini=%errorlevel%
echo === E9 Groq (continua de onde parou) ===
>> "%LOGS%\C_E9_groq.log" echo [%date% %time%] retomada
"%PY%" -u -m avaliacao.executar --somente-e9 --llm groq >> "%LOGS%\C_E9_groq.log" 2>&1
>> "%LOGS%\E_status.txt" echo groq=%errorlevel%
"%PY%" "%~dp0progresso_e9.py" > nul 2>&1
>> "%LOGS%\E_status.txt" echo [%date% %time%] fim
echo Concluido. Veja rodada_final\logs\E9_progresso.md
pause
