@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%.."
set "STAGING_DIR=%SCRIPT_DIR%A333_Dnakeiot_Staging"
set "RELEASE_DIR=%PROJECT_ROOT%\Release\OHScrcpy-A333-Dnakeiot"

echo [INFO] Building the Dnakeiot A333 Windows release.

where python >nul 2>nul || (
    echo [ERROR] Python 3 is required.
    exit /b 1
)

if not exist "%PROJECT_ROOT%\Server\bin\Dnakeiot\ohscrcpy_server" (
    echo [ERROR] Missing Dnakeiot AArch64 server resource.
    exit /b 1
)

if not exist "%PROJECT_ROOT%\Server\bin\Dnakeiot\server_manifest.json" (
    echo [ERROR] Missing Dnakeiot server manifest.
    exit /b 1
)

python "%PROJECT_ROOT%\tools\verify_dnakeiot_a333_resource.py" "%PROJECT_ROOT%\Server\bin\Dnakeiot" || exit /b 1

if exist "%STAGING_DIR%" rmdir /s /q "%STAGING_DIR%"
mkdir "%STAGING_DIR%" || exit /b 1

xcopy /E /I /Q /Y "%PROJECT_ROOT%\Client\core" "%STAGING_DIR%\core" >nul || goto :error
xcopy /E /I /Q /Y "%PROJECT_ROOT%\Client\video" "%STAGING_DIR%\video" >nul || goto :error
xcopy /E /I /Q /Y "%PROJECT_ROOT%\Client\gui" "%STAGING_DIR%\gui" >nul || goto :error
xcopy /E /I /Q /Y "%PROJECT_ROOT%\Client\utils" "%STAGING_DIR%\utils" >nul || goto :error
xcopy /E /I /Q /Y "%PROJECT_ROOT%\Client\config" "%STAGING_DIR%\config" >nul || goto :error
xcopy /E /I /Q /Y "%PROJECT_ROOT%\Client\hdc" "%STAGING_DIR%\hdc" >nul || goto :error
xcopy /E /I /Q /Y "%PROJECT_ROOT%\Server\bin\Dnakeiot" "%STAGING_DIR%\Dnakeiot" >nul || goto :error
copy /Y "%PROJECT_ROOT%\Client\main.py" "%STAGING_DIR%\main.py" >nul || goto :error
copy /Y "%PROJECT_ROOT%\Client\requirements.txt" "%STAGING_DIR%\requirements.txt" >nul || goto :error
copy /Y "%SCRIPT_DIR%\Executer\app.ico" "%STAGING_DIR%\app.ico" >nul || goto :error
for /d /r "%STAGING_DIR%" %%d in (__pycache__) do @if exist "%%d" rmdir /s /q "%%d"

pushd "%STAGING_DIR%" || goto :error
python -m pip install -r requirements.txt pyinstaller || goto :error_popd
python -m PyInstaller main.py --name "OHScrcpy" --noconfirm --clean --windowed --onedir ^
    --collect-submodules core --collect-submodules video --collect-submodules gui --collect-submodules utils ^
    --add-data "Dnakeiot;Dnakeiot" ^
    --add-data "hdc\Windows\x64\hdc.exe;." ^
    --add-data "hdc\Windows\x64\libusb_shared.dll;." ^
    --add-data "config\log_config.json;config" ^
    --icon app.ico || goto :error_popd

if not exist "dist\OHScrcpy\OHScrcpy.exe" goto :error_popd
if not exist "dist\OHScrcpy\_internal\Dnakeiot\ohscrcpy_server" goto :error_popd
if not exist "dist\OHScrcpy\_internal\Dnakeiot\server_manifest.json" goto :error_popd

if exist "%RELEASE_DIR%" rmdir /s /q "%RELEASE_DIR%"
mkdir "%RELEASE_DIR%" || goto :error_popd
xcopy /E /I /Q /Y "dist\OHScrcpy" "%RELEASE_DIR%" >nul || goto :error_popd
certutil -hashfile "%RELEASE_DIR%\OHScrcpy.exe" SHA256 > "%RELEASE_DIR%\OHScrcpy.exe.sha256"
popd

echo [OK] Release created: %RELEASE_DIR%\OHScrcpy.exe
echo [OK] This release contains only the Dnakeiot ELF64/AArch64 server resource.
exit /b 0

:error_popd
popd
:error
echo [ERROR] Windows release build failed.
exit /b 1
