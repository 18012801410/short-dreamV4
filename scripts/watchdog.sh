#!/usr/bin/env bash
# 本机守护：API / worker 进程消失即自动拉起（外部杀进程/误杀兜底）。
# 日志：data/logs/worker_watchdog.log（含 worker 自身 stdout/stderr，可查死因）
cd "$(dirname "$0")/.."
LOG=data/logs/worker_watchdog.log
PY="D:/anaconda/python.exe"

while true; do
  RUNNING=$(powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -match 'server.worker' } | Select-Object -First 1 -ExpandProperty ProcessId" 2>/dev/null)
  if [ -z "$RUNNING" ]; then
    echo "$(date '+%F %T') [watchdog] worker 不在，拉起" >> "$LOG"
    "$PY" -m server.worker >> "$LOG" 2>&1 &
  fi
  API_RUNNING=$(powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -match 'server.api.main' } | Select-Object -First 1 -ExpandProperty ProcessId" 2>/dev/null)
  if [ -z "$API_RUNNING" ]; then
    echo "$(date '+%F %T') [watchdog] API 不在，拉起" >> "$LOG"
    "$PY" -m uvicorn server.api.main:app --host 127.0.0.1 --port 8000 >> "$LOG" 2>&1 &
  fi
  sleep 30
done
