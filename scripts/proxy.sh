#!/usr/bin/env bash
# research-tool 项目独立代理（VLESS Reality -> 本地 HTTP 127.0.0.1:10809）
# 仅监听本机 10809，不与 v2rayN（10808）/系统代理冲突；
# 凭证在 .cache/xray/config.json（已被 .gitignore 忽略，绝不可提交）。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
XRAY_DIR="$ROOT/.cache/xray"
XRAY="$XRAY_DIR/xray"
CONF="$XRAY_DIR/config.json"
PID_FILE="$XRAY_DIR/xray.pid"
LOG="$XRAY_DIR/xray.log"
PORT=10809

cmd="${1:-status}"
case "$cmd" in
  start)
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "代理已在运行 (PID $(cat "$PID_FILE")) -> http://127.0.0.1:$PORT"; exit 0
    fi
    nohup "$XRAY" run -c "$CONF" > "$LOG" 2>&1 &
    echo $! > "$PID_FILE"
    sleep 1
    if ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "启动失败，见日志: $LOG"; tail -5 "$LOG" 2>/dev/null; exit 1
    fi
    echo "代理已启动 -> http://127.0.0.1:$PORT (PID $(cat "$PID_FILE"))"
    echo "日志: $LOG"
    ;;
  stop)
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      kill "$(cat "$PID_FILE")" && rm -f "$PID_FILE" && echo "已停止"
    else
      rm -f "$PID_FILE"; echo "未运行"
    fi
    ;;
  status)
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "运行中 (PID $(cat "$PID_FILE")) -> http://127.0.0.1:$PORT"
    else
      echo "未运行"
    fi
    ;;
  restart) "$0" stop || true; "$0" start;;
  log) tail -n "${2:-30}" "$LOG" 2>/dev/null || echo "无日志";;
  *) echo "用法: $0 {start|stop|status|restart|log}"; exit 1;;
esac
