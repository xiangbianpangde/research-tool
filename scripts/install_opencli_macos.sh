#!/usr/bin/env bash
# Install OpenCLI CLI + download Browser Bridge extension (macOS).
# Cookie/session: opencli reads Chrome login state after extension is connected.
# Does NOT write TWITTER_* secrets into .env.
set -euo pipefail

CACHE="${HOME}/.cache/research-tool/opencli"
EXT_VER="1.0.22"
RELEASE_TAG="v1.8.6"
EXT_ZIP="opencli-extension-v${EXT_VER}.zip"
EXT_URL="https://github.com/jackwener/OpenCLI/releases/download/${RELEASE_TAG}/${EXT_ZIP}"
EXT_DIR="${CACHE}/opencli-extension-v${EXT_VER}"

mkdir -p "${CACHE}"

echo "==> Install @jackwener/opencli (Node >= 20)"
node --version
npm install -g --allow-scripts=@jackwener/opencli @jackwener/opencli

echo "==> Download Browser Bridge extension → ${EXT_DIR}"
curl -fsSL -o "${CACHE}/${EXT_ZIP}" "${EXT_URL}"
rm -rf "${EXT_DIR}"
unzip -q -o "${CACHE}/${EXT_ZIP}" -d "${EXT_DIR}"
test -f "${EXT_DIR}/manifest.json"

echo
echo "CLI: $(command -v opencli)  ($(opencli -V 2>/dev/null || true))"
echo
echo "【必须你本地点一下】加载扩展（cookie 由此自动获取，勿手抄到 .env）："
echo "  1) Chrome 打开 chrome://extensions"
echo "  2) 开启「开发者模式」"
echo "  3) 「加载已解压的扩展程序」→ 选择："
echo "       ${EXT_DIR}"
echo "  4) 在同一 Chrome 登录 https://x.com"
echo "  5) 运行: opencli doctor"
echo "  6) 验证: opencli twitter search \"VGGT\" -n 3 -f json"
echo "  7) research collect \"主题\" -s x -n 3 --dry-run"
echo
opencli daemon restart >/dev/null 2>&1 || true
opencli doctor || true
