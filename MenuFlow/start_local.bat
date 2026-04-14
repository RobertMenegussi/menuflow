@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto :venv_ready

echo [1/3] Criando ambiente virtual...
python -m venv .venv 2>nul
if errorlevel 1 py -m venv .venv
if not exist ".venv\Scripts\python.exe" (
  echo.
  echo Nao consegui criar o ambiente virtual.
  echo Instale o Python 3 e tente novamente.
  pause
  exit /b 1
)

:venv_ready
echo [2/3] Instalando dependencias...
call .venv\Scripts\python.exe -m pip install --upgrade pip >nul
call .venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo Falha ao instalar dependencias.
  pause
  exit /b 1
)

echo [3/3] Iniciando servidor...
echo.
echo Admin:   http://127.0.0.1:5000/admin
echo Cliente: http://127.0.0.1:5000/client
echo Reserva: http://127.0.0.1:5000/reserve
echo.
call .venv\Scripts\python.exe app.py
pause
