@echo off
cd /d "%~dp0"
echo === Drive Clone - Build ===
echo Pasta: %cd%
echo.

set PY=
python --version >nul 2>&1 && set PY=python
if not defined PY (py -3 --version >nul 2>&1 && set PY=py -3)
if not defined PY (
  echo ERRO: Python nao encontrado.
  echo Instale o Python 3.10 ou mais novo em python.org e marque "Add python.exe to PATH".
  pause
  exit /b 1
)
echo Usando: %PY%
%PY% --version
echo.

echo [1/3] Instalando dependencias...
%PY% -m pip install -r requirements.txt pyinstaller
if errorlevel 1 (
  echo.
  echo ERRO ao instalar as dependencias. Veja a mensagem acima.
  pause
  exit /b 1
)

echo.
echo [2/3] Gerando o .exe com PyInstaller (leva alguns minutos)...
%PY% -m PyInstaller --name DriveClone --onefile --windowed --noconfirm --collect-all customtkinter --collect-data googleapiclient --hidden-import google.auth.transport.requests --hidden-import google.oauth2.credentials --hidden-import google_auth_oauthlib.flow --hidden-import googleapiclient.discovery --hidden-import googleapiclient.http run.py
if errorlevel 1 (
  echo.
  echo ERRO ao gerar o .exe. Veja a mensagem acima.
  pause
  exit /b 1
)
if not exist dist\DriveClone.exe (
  echo.
  echo ERRO: o PyInstaller terminou mas o dist\DriveClone.exe nao foi criado.
  pause
  exit /b 1
)

echo.
echo [3/3] Concluido!
echo O executavel esta em:  %cd%\dist\DriveClone.exe
echo.
echo Na primeira vez, abra o programa e siga a aba "Comecar" para escolher o credentials.json.
pause
