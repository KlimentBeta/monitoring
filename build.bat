@echo off
REM ==========================================================================
REM  WebsiteMonitor: сборка двух Windows-приложений (PyInstaller, onedir)
REM  Результат: build\release\WebsiteMonitorWeb\WebsiteMonitorWeb.exe
REM             build\release\WebsiteMonitorDesktop\WebsiteMonitorDesktop.exe
REM  Запускать из корня проекта на Windows (Python 3.12 должен быть в PATH):
REM      build.bat
REM ==========================================================================
setlocal
cd /d "%~dp0"

echo [1/4] Установка зависимостей...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
if errorlevel 1 goto :error

if not exist build\pyinstaller mkdir build\pyinstaller
if not exist build\release  mkdir build\release

echo [2/4] Сборка веб-версии (onedir)...
python -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --name WebsiteMonitorWeb ^
    --distpath build\release ^
    --workpath build\pyinstaller ^
    --specpath build\pyinstaller ^
    --icon assets\icon.ico ^
    --console ^
    --add-data "%CD%\app\web\templates;app\web\templates" ^
    --add-data "%CD%\app\web\static;app\web\static" ^
    --add-data "%CD%\assets;assets" ^
    web_main.py
if errorlevel 1 goto :error

echo [3/4] Сборка десктопной версии (onedir)...
python -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --name WebsiteMonitorDesktop ^
    --distpath build\release ^
    --workpath build\pyinstaller ^
    --specpath build\pyinstaller ^
    --icon assets\icon.ico ^
    --windowed ^
    --add-data "%CD%\assets;assets" ^
    desktop_main.py
if errorlevel 1 goto :error

echo [4/4] Проверка результатов...
if exist "build\release\WebsiteMonitorWeb\WebsiteMonitorWeb.exe" (
    echo   OK: build\release\WebsiteMonitorWeb\WebsiteMonitorWeb.exe
) else (
    echo   ОШИБКА: веб-версия не найдена
    goto :error
)
if exist "build\release\WebsiteMonitorDesktop\WebsiteMonitorDesktop.exe" (
    echo   OK: build\release\WebsiteMonitorDesktop\WebsiteMonitorDesktop.exe
) else (
    echo   ОШИБКА: десктопная версия не найдена
    goto :error
)

echo.
echo Готово! Запуск:
echo   build\release\WebsiteMonitorWeb\WebsiteMonitorWeb.exe
echo   build\release\WebsiteMonitorDesktop\WebsiteMonitorDesktop.exe
echo Общая база данных: %%LOCALAPPDATA%%\WebsiteMonitor\data\monitor.db
goto :eof

:error
echo Сборка прервана с ошибкой.
exit /b 1

endlocal
