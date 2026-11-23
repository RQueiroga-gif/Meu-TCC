@echo off
setlocal
rem Copia dos bancos temporarios as minutas do E9 (somente leitura). Escrito pelo Claude.
set "PY=%~dp0..\..\..\dfd-assistido\dfd-assistido\backend\.venv\Scripts\python.exe"
set "LOGS=%~dp0logs"
if not exist "%LOGS%" mkdir "%LOGS%"
set PYTHONUTF8=1
if not exist "%PY%" (echo Python do ambiente nao encontrado & pause & exit /b 1)
"%PY%" "%~dp0extrair_minutas_e9.py" > "%LOGS%\G_extrair.log" 2>&1
>> "%LOGS%\G_extrair.log" echo codigo=%errorlevel%
type "%LOGS%\G_extrair.log"
timeout /t 15 > nul
