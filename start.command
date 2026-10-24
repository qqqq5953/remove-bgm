#!/bin/bash
# remove-bgm 啟動程式：在 Finder 裡雙擊執行，會自動開啟瀏覽器。
# 關閉這個終端機視窗（或按 Ctrl+C）即可結束。

set -uo pipefail
cd "$(dirname "$0")" || exit 1

PORT="${REMOVE_BGM_PORT:-8765}"
URL="http://127.0.0.1:$PORT"
OPEN_CMD="${REMOVE_BGM_OPEN_CMD:-open}"

step() { printf '\n▶ %s\n' "$1"; }
fail() {
  printf '\n✖ %s\n\n' "$1"
  read -r -p "按 Enter 關閉視窗…" _ || true
  exit 1
}

# 雙擊執行時 PATH 很精簡，補上 Homebrew 與 uv 的常見安裝位置
for dir in /opt/homebrew/bin /usr/local/bin "$HOME/.local/bin" "$HOME/.cargo/bin"; do
  [ -d "$dir" ] && PATH="$dir:$PATH"
done
export PATH

# 已經在執行就直接開瀏覽器
if curl -fsS "$URL/api/health" >/dev/null 2>&1; then
  step "remove-bgm 已經在執行，開啟瀏覽器"
  "$OPEN_CMD" "$URL"
  exit 0
fi

# 檢查必要工具：ffmpeg、uv（前端已預先建置，不需要 Node.js）
missing=()
command -v ffmpeg >/dev/null 2>&1 || missing+=(ffmpeg)
command -v uv >/dev/null 2>&1 || missing+=(uv)
if [ ${#missing[@]} -gt 0 ]; then
  if command -v brew >/dev/null 2>&1; then
    step "安裝缺少的工具：${missing[*]}（使用 Homebrew）"
    brew install "${missing[@]}" || fail "用 Homebrew 安裝 ${missing[*]} 失敗，請看上面的錯誤訊息"
  else
    fail "找不到 ${missing[*]}，也沒有安裝 Homebrew。請依照 README.md 的「安裝」步驟先安裝 Homebrew。"
  fi
fi

step "準備 Python 環境（第一次會下載 PyTorch 等套件，約需幾分鐘）"
uv sync --project backend --quiet || fail "Python 環境安裝失敗，請看上面的錯誤訊息"

step "更新 yt-dlp（YouTube 常改版，保持最新才能下載）"
uv pip install --python backend/.venv --quiet --upgrade yt-dlp \
  || printf '  （更新失敗，先用目前的版本繼續）\n'

step "啟動 remove-bgm"
# --no-sync：避免 uv 把剛更新的 yt-dlp 換回鎖定版本
uv run --project backend --no-sync python -m remove_bgm.server --port "$PORT" &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null' EXIT INT TERM

for _ in $(seq 1 240); do
  if curl -fsS "$URL/api/health" >/dev/null 2>&1; then
    "$OPEN_CMD" "$URL"
    printf '\n✔ 已在瀏覽器開啟 %s\n  處理完成的檔案在：~/Movies/remove-bgm\n  關閉這個視窗即可結束。\n' "$URL"
    wait "$SERVER_PID"
    exit 0
  fi
  kill -0 "$SERVER_PID" 2>/dev/null || fail "伺服器啟動失敗（連接埠 $PORT 可能被其他程式占用），請看上面的錯誤訊息"
  sleep 0.5
done
fail "等待伺服器啟動逾時"
