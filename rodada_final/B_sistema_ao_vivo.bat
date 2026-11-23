@echo off
setlocal
rem Situacao 3: sistema ao vivo com Groq (geracao) e Gemini (revisao), roteiro de testes
rem e capturas. Script escrito pelo Claude; nao exibe nem grava chaves de API.
set "PYB=%~dp0..\..\..\dfd-assistido\dfd-assistido\backend\.venv\Scripts\python.exe"
set "PYF=%~dp0..\..\..\dfd-assistido\dfd-assistido\frontend\.venv\Scripts\python.exe"
set "LOGS=%~dp0logs"
if not exist "%LOGS%" mkdir "%LOGS%"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if not exist "%PYB%" (echo Python do backend nao encontrado & pause & exit /b 1)
if not exist "%PYF%" (echo Python do frontend nao encontrado & pause & exit /b 1)
>> "%LOGS%\B_status.txt" echo [%date% %time%] inicio
echo === Instalando o Playwright no ambiente do backend (usa o Chrome ja instalado) ===
"%PYB%" -m pip install -q playwright==1.65.2 > "%LOGS%\B_pip.log" 2>&1
>> "%LOGS%\B_status.txt" echo pip=%errorlevel%
echo === Teste minimo dos provedores (uma chamada a cada modelo) ===
cd /d "%~dp0..\backend"
"%PYB%" -u "%~dp0smoke_provedores.py" > "%LOGS%\B0_smoke.log" 2>&1
>> "%LOGS%\B_status.txt" echo smoke=%errorlevel%
echo === Roteiro ao vivo: leva alguns minutos (indexacao com BGE-M3 + chamadas ao modelo) ===
cd /d "%~dp0"
"%PYB%" -u "%~dp0ao_vivo.py" --py-frontend "%PYF%" --navegador chrome > "%LOGS%\B_ao_vivo.log" 2>&1
>> "%LOGS%\B_status.txt" echo [%date% %time%] ao_vivo=%errorlevel%
echo Concluido. Relatorio em rodada_final\ao_vivo\relatorio_ao_vivo.md
pause
