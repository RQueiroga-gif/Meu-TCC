@echo off
setlocal
rem Conclui o E9 da Groq: roda, espera 20 minutos quando a cota de tokens esgota e
rem repete ate fechar as 140 geracoes (no maximo 45 tentativas). Escrito pelo Claude.
set "PY=%~dp0..\..\..\dfd-assistido\dfd-assistido\backend\.venv\Scripts\python.exe"
set "LOGS=%~dp0logs"
if not exist "%LOGS%" mkdir "%LOGS%"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if not exist "%PY%" (echo Python do ambiente nao encontrado & pause & exit /b 1)
cd /d "%~dp0..\backend"
set /a N=0
:volta
set /a N+=1
>> "%LOGS%\F_status.txt" echo [%date% %time%] tentativa %N%
>> "%LOGS%\C_E9_groq.log" echo [%date% %time%] tentativa automatica %N%
"%PY%" -u -m avaliacao.executar --somente-e9 --llm groq >> "%LOGS%\C_E9_groq.log" 2>&1
"%PY%" "%~dp0progresso_e9.py" > nul 2>&1
"%PY%" "%~dp0progresso_e9.py" --groq-completo
if not errorlevel 1 goto fim
if %N% GEQ 45 goto fim
echo Cota de tokens esgotada; nova tentativa em 20 minutos (tentativa %N% de 45)...
timeout /t 1200 /nobreak > nul
goto volta
:fim
"%PY%" "%~dp0progresso_e9.py" > nul 2>&1
>> "%LOGS%\F_status.txt" echo [%date% %time%] fim apos %N% tentativas
echo Concluido. Veja rodada_final\logs\E9_progresso.md
pause
