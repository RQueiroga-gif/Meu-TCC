@echo off
setlocal
rem Fecha o E9 depois que a Groq terminar: copia as minutas dos bancos temporarios,
rem reaplica a medida corrigida (0.4.5), refaz as tabelas do E9 sem novas geracoes
rem e roda os testes. Nenhum modelo de linguagem e chamado. Escrito pelo Claude.
set "PY=%~dp0..\..\..\dfd-assistido\dfd-assistido\backend\.venv\Scripts\python.exe"
set "LOGS=%~dp0logs"
if not exist "%LOGS%" mkdir "%LOGS%"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if not exist "%PY%" (echo Python do ambiente nao encontrado & pause & exit /b 1)
> "%LOGS%\H_status.txt" echo [%date% %time%] inicio
"%PY%" "%~dp0extrair_minutas_e9.py" > "%LOGS%\H1_extrair.log" 2>&1
>> "%LOGS%\H_status.txt" echo extrair=%errorlevel%
cd /d "%~dp0..\backend"
"%PY%" -m avaliacao.remedir_e9 --minutas "%~dp0e9_minutas\minutas_e9.jsonl" > "%LOGS%\H2_remedir.log" 2>&1
>> "%LOGS%\H_status.txt" echo remedir=%errorlevel%
"%PY%" -u -m avaliacao.executar --somente-e9 --llm groq > "%LOGS%\H3_e9_groq.log" 2>&1
>> "%LOGS%\H_status.txt" echo e9_groq=%errorlevel%
"%PY%" -u -m avaliacao.executar --somente-e9 --llm gemini --amostra 6 > "%LOGS%\H4_e9_gemini.log" 2>&1
>> "%LOGS%\H_status.txt" echo e9_gemini=%errorlevel%
"%PY%" -m pytest -q --cov=app --cov-report=term > "%LOGS%\H5_pytest.log" 2>&1
>> "%LOGS%\H_status.txt" echo pytest=%errorlevel%
"%PY%" "%~dp0progresso_e9.py" > nul 2>&1
>> "%LOGS%\H_status.txt" echo [%date% %time%] fim
type "%LOGS%\H_status.txt"
timeout /t 20 > nul
