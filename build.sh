#!/usr/bin/env bash
# =============================================================================
#  WebsiteMonitor: сборка на Linux/macOS (PyInstaller, onedir).
#  Позволяет проверить процесс сборки на этой машине; для Windows используйте
#  build.bat (PyInstaller не умеет кросс-compile).
# =============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON="${PYTHON:-python3}"

echo "[1/4] Установка зависимостей (если нужно)..."
"$PYTHON" -m pip install -q -r requirements.txt
"$PYTHON" -m pip install -q -r requirements-dev.txt

rm -rf build/pyinstaller
mkdir -p build/pyinstaller build/release

echo "[2/4] Сборка веб-версии (onedir)..."
"$PYTHON" -m PyInstaller \
    --noconfirm --clean \
    --name WebsiteMonitorWeb \
    --distpath build/release \
    --workpath build/pyinstaller \
    --specpath build/pyinstaller \
    --add-data "$SCRIPT_DIR/app/web/templates:app/web/templates" \
    --add-data "$SCRIPT_DIR/app/web/static:app/web/static" \
    --add-data "$SCRIPT_DIR/assets:assets" \
    web_main.py

echo "[3/4] Сборка десктопной версии (onedir)..."
"$PYTHON" -m PyInstaller \
    --noconfirm --clean \
    --name WebsiteMonitorDesktop \
    --distpath build/release \
    --workpath build/pyinstaller \
    --specpath build/pyinstaller \
    --add-data "$SCRIPT_DIR/assets:assets" \
    desktop_main.py

echo "[4/4] Результаты:"
ls -la build/release/WebsiteMonitorWeb/ | head -5
ls -la build/release/WebsiteMonitorDesktop/ | head -5
echo "Готово."
