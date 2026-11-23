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
echo [%date% %time%] inicio> "%LOGS%\A_status.txt"
echo === 1/5 pytest com cobertura ===
"%PY%" -m pytest -p no:cacheprovider --cov=app --cov-report=term --cov-report=json:"%LOGS%\cobertura.json" > "%LOGS%\A1_pytest.log" 2>&1
echo pytest=%errorlevel%>> "%LOGS%\A_status.txt"
echo === 2/5 E1 a E8 com BGE-M3 (cerca de 40 min) ===
"%PY%" -u -m avaliacao.executar > "%LOGS%\A2_E1_E8.log" 2>&1
set ERRO_E18=%errorlevel%
echo E1_E8=%ERRO_E18%>> "%LOGS%\A_status.txt"
if not "%ERRO_E18%"=="0" goto fim
echo === 3/5 teste minimo dos provedores ===
"%PY%" -u "%~dp0smoke_provedores.py" > "%LOGS%\A3_smoke.log" 2>&1
echo smoke=%errorlevel%>> "%LOGS%\A_status.txt"
echo === 4/5 E9 Gemini, amostra de 6 ===
"%PY%" -u -m avaliacao.executar --somente-e9 --llm gemini --amostra 6 > "%LOGS%\A4_E9_gemini.log" 2>&1
echo E9_gemini=%errorlevel%>> "%LOGS%\A_status.txt"
echo === 5/5 E9 Groq (para quando a cota do dia acabar; retome com C_E9_groq.bat) ===
echo [%date% %time%] inicio>> "%LOGS%\C_E9_groq.log"
"%PY%" -u -m avaliacao.executar --somente-e9 --llm groq >> "%LOGS%\C_E9_groq.log" 2>&1
echo E9_groq=%errorlevel%>> "%LOGS%\A_status.txt"
:fim
echo [%date% %time%] fim>> "%LOGS%\A_status.txt"
echo Concluido. Resultados em backend\avaliacao\resultados e logs em rodada_final\logs.
pause
