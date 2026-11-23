@echo off
setlocal
rem Sobe o sistema para a inspecao heuristica manual (roteiro_avaliacao_heuristica.md).
rem Rode antes o B_sistema_ao_vivo.bat, que cria o banco e o indice usados aqui.
set "PYB=%~dp0..\..\..\dfd-assistido\dfd-assistido\backend\.venv\Scripts\python.exe"
set "PYF=%~dp0..\..\..\dfd-assistido\dfd-assistido\frontend\.venv\Scripts\python.exe"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
"%PYB%" "%~dp0subir_sistema.py" --py-frontend "%PYF%"
pause
