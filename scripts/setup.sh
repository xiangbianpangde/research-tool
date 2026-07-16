#!/usr/bin/env bash
# research-tool 交互式一键部署入口（macOS / Linux）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# 优先 conda 环境 research-tool（里面有 research CLI 与依赖）
if [ -f /opt/anaconda3/etc/profile.d/conda.sh ]; then
  # shellcheck source=/dev/null
  source /opt/anaconda3/etc/profile.d/conda.sh
  if conda env list 2>/dev/null | grep -qE '^research-tool\s'; then
    conda activate research-tool 2>/dev/null || true
  fi
fi

if [ -x "$ROOT/.venv/bin/python" ]; then
  PY="$ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "[ERROR] 需要 Python 3.11+"
  exit 1
fi

echo "[setup] 使用解释器: $PY"
exec "$PY" "$ROOT/scripts/setup_interactive.py" "$@"