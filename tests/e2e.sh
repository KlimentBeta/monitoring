#!/usr/bin/env bash
# Сквозной тест обеих версий: общая БД, авто-мониторинг, ручная проверка,
# инциденты, сохранение после перезапуска.
set -u
cd /home/kali/Documents/vitalikCollege

export WEBSITE_MONITOR_ALLOW_PRIVATE=1
export MONITOR_DB="/tmp/wm_e2e/monitor.db"
rm -rf /tmp/wm_e2e && mkdir -p /tmp/wm_e2e
export WEBSITE_MONITOR_DB_PATH="$MONITOR_DB"

echo "== 1. Запуск локального HTTP-сервера сайта (порт 9031) =="
python3 -m http.server 9031 --bind 127.0.0.1 --directory /tmp/wm_e2e >/tmp/wm_e2e/site_server.log 2>&1 &
SITE_PID=$!

echo "== 2. Запуск веб-версии =="
.venv/bin/python web_main.py >/tmp/wm_e2e/web.log 2>&1 &
WEB_PID=$!
sleep 4
WEB_URL=$(grep -oP 'http://127\.0\.0\.1:\d+/' /tmp/wm_e2e/web.log | head -1)
echo "Веб-адрес: $WEB_URL"
curl -s -o /dev/null -w 'GET / -> %{http_code}\n' "$WEB_URL"

echo "== 3. Добавление сайта (интервал 5 c) и сайта-жертвы сбоя =="
curl -s -X POST "$WEB_URL/sites/add" -d 'name=Локальный сайт&url=http://127.0.0.1:9031/&interval_seconds=5' > /dev/null
curl -s -X POST "$WEB_URL/sites/add" -d 'name=Мёртвый сайт&url=http://127.0.0.1:9999/&interval_seconds=5' > /dev/null
sleep 8
echo "-- сайты после авто-проверок --"
curl -s "$WEB_URL/api/sites" | .venv/bin/python -m json.tool | head -60

echo "== 4. Итоговая сводка =="
curl -s "$WEB_URL/api/overview" | .venv/bin/python -m json.tool

echo "== 5. Статистика по живому сайту =="
curl -s "$WEB_URL/api/statistics?site_id=1&period=24h" | .venv/bin/python -m json.tool | head -30

echo "== 6. Одновременный запуск десктопной версии (offscreen) =="
QT_QPA_PLATFORM=offscreen timeout 6 .venv/bin/python desktop_main.py >/tmp/wm_e2e/desktop.log 2>&1 &
DESK_PID=$!
sleep 5
echo "десктоп-процесс жив: $(kill -0 $DESK_PID 2>/dev/null && echo да || echo нет)"
kill $DESK_PID 2>/dev/null; wait $DESK_PID 2>/dev/null

echo "== 7. Данные после десктопа (история должна продолжиться) =="
curl -s "$WEB_URL/api/overview" | .venv/bin/python -c 'import json,sys; d=json.load(sys.stdin); print("total:", d["total"], "incidents:", d["incidents"], "open:", d["open_incidents"])'

echo "== 8. Перезапуск веб-версии (сохранение данных) =="
kill $WEB_PID 2>/dev/null; wait $WEB_PID 2>/dev/null; sleep 1
.venv/bin/python web_main.py >/tmp/wm_e2e/web2.log 2>&1 &
WEB_PID2=$!
sleep 4
WEB_URL2=$(grep -oP 'http://127\.0\.0\.1:\d+/' /tmp/wm_e2e/web2.log | head -1)
echo "новый адрес: $WEB_URL2"
curl -s "$WEB_URL2/api/overview" | .venv/bin/python -c 'import json,sys; d=json.load(sys.stdin); print("total:", d["total"], "incidents:", d["incidents"], "available:", d["available"])'

echo "== 9. Очистка (удаление сайта) =="
SITE_ID=$(curl -s "$WEB_URL2/api/sites" | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)[0]["id"])')
curl -s -X POST "$WEB_URL2/sites/$SITE_ID/delete" >/dev/null
curl -s "$WEB_URL2/api/sites" | .venv/bin/python -c 'import json,sys; print("осталось сайтов:", len(json.load(sys.stdin)))'

kill $WEB_PID2 2>/dev/null; wait $WEB_PID2 2>/dev/null
kill $SITE_PID 2>/dev/null; wait $SITE_PID 2>/dev/null
echo "== e2e OK =="
