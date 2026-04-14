@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto :venv_ready

echo [1/4] Criando ambiente virtual...
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
echo [2/4] Instalando dependencias...
call .venv\Scripts\python.exe -m pip install --upgrade pip >nul
call .venv\Scripts\python.exe -m pip install -r requirements.txt >nul
if errorlevel 1 (
  echo.
  echo Falha ao instalar dependencias.
  pause
  exit /b 1
)

start "MenuFlow Server" cmd /k ".venv\Scripts\python.exe app.py"

echo [3/4] Esperando /health responder...
set READY=
for /L %%i in (1,1,40) do (
  powershell -Command "try { (Invoke-WebRequest -UseBasicParsing http://127.0.0.1:5000/health).StatusCode } catch { 0 }" | findstr /C:"200" >nul && set READY=1 && goto :tunnel
  timeout /t 1 >nul
)
:tunnel
if not defined READY (
  echo Nao consegui subir o servidor. Veja a janela "MenuFlow Server".
  pause
  exit /b 1
)

where cloudflared >nul 2>nul
if errorlevel 1 (
  echo cloudflared nao encontrado.
  echo Instale com:
  echo   winget install --id Cloudflare.cloudflared
  pause
  exit /b 1
)

echo [4/4] Abrindo Cloudflare Quick Tunnel...
echo.
echo Quando aparecer um link trycloudflare.com, use:
echo   /client  (cliente)
echo   /admin   (admin)
echo   /reserve (reserva)
echo.
cloudflared tunnel --url http://127.0.0.1:5000
pause
