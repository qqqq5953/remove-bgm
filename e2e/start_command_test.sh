#!/bin/bash
# 實際執行 start.command 的各種情境（brew、open 用假的指令代替）。
# 執行：bash e2e/start_command_test.sh

set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
PORT=8798
pass=0
failed=0

cleanup() {
  pkill -f "remove_bgm.server --port $PORT" 2>/dev/null
  rm -rf "$TMP"
}
trap cleanup EXIT

check() {
  if eval "$2"; then
    printf '  ✔ %s\n' "$1"
    pass=$((pass + 1))
  else
    printf '  ✖ %s\n' "$1"
    failed=$((failed + 1))
  fi
}

# 建立一個 bin 資料夾，放入系統指令的連結（可排除指定的指令），模擬「沒有安裝某工具」
make_sandbox_bin() {
  local dir="$1"
  shift
  mkdir -p "$dir"
  for f in /usr/bin/* /bin/*; do
    local name
    name="$(basename "$f")"
    for skip in "$@"; do [ "$name" = "$skip" ] && continue 2; done
    [ -e "$dir/$name" ] || ln -s "$f" "$dir/$name"
  done
}

# 假的 open：記錄網址
make_fake_open() {
  cat >"$1" <<EOF
#!/bin/bash
echo "\$1" >> "$TMP/opened.txt"
EOF
  chmod +x "$1"
}

run_start() {
  # $1 = PATH，$2 = HOME；背景執行並等待瀏覽器被開啟或程式結束
  local path="$1" home="$2"
  : >"$TMP/opened.txt"
  env -i PATH="$path" HOME="$home" REMOVE_BGM_PORT=$PORT REMOVE_BGM_OPEN_CMD="$TMP/fake-open" \
    REMOVE_BGM_OUTPUT_DIR="$TMP/out" UV_CACHE_DIR="${UV_CACHE_DIR:-$HOME/.cache/uv}" \
    SSL_CERT_FILE="${SSL_CERT_FILE:-}" HTTPS_PROXY="${HTTPS_PROXY:-}" HTTP_PROXY="${HTTP_PROXY:-}" \
    bash "$ROOT/start.command" </dev/null >"$TMP/out.log" 2>&1 &
  START_PID=$!
  for _ in $(seq 1 600); do
    [ -s "$TMP/opened.txt" ] && return 0
    kill -0 "$START_PID" 2>/dev/null || return 0
    sleep 0.5
  done
}

make_fake_open "$TMP/fake-open"
UV_DIR="$(dirname "$(command -v uv)")"

echo "情境 1：工具都已安裝 → 啟動伺服器並開啟瀏覽器"
make_sandbox_bin "$TMP/bin-full"
ln -sf "$UV_DIR/uv" "$TMP/bin-full/uv"
run_start "$TMP/bin-full" "$TMP/home1"
check "開啟了 http://127.0.0.1:$PORT" "grep -qx 'http://127.0.0.1:$PORT' '$TMP/opened.txt'"
check "health API 有回應" "curl -fsS http://127.0.0.1:$PORT/api/health | grep -q '\"ffmpeg\":true'"
check "首頁是建置好的前端" "curl -fsS http://127.0.0.1:$PORT/ | grep -q '去除配樂'"
check "有顯示成功訊息" "grep -q '已在瀏覽器開啟' '$TMP/out.log'"

echo "情境 2：已經在執行 → 不重複啟動，直接開瀏覽器"
FIRST_PID=$START_PID
run_start "$TMP/bin-full" "$TMP/home1"
wait "$START_PID"
check "結束代碼為 0" "[ $? -eq 0 ]"
check "有開啟瀏覽器" "grep -qx 'http://127.0.0.1:$PORT' '$TMP/opened.txt'"
check "顯示已在執行" "grep -q '已經在執行' '$TMP/out.log'"
kill "$FIRST_PID" 2>/dev/null
pkill -f "remove_bgm.server --port $PORT" 2>/dev/null
sleep 1

echo "情境 3：沒有 ffmpeg、有 Homebrew → 用 brew 安裝"
make_sandbox_bin "$TMP/bin-noffmpeg" ffmpeg ffprobe
ln -sf "$UV_DIR/uv" "$TMP/bin-noffmpeg/uv"
cat >"$TMP/bin-noffmpeg/brew" <<EOF
#!/bin/bash
echo "\$*" >> "$TMP/brew.txt"
ln -sf /usr/bin/ffmpeg "$TMP/bin-noffmpeg/ffmpeg"
ln -sf /usr/bin/ffprobe "$TMP/bin-noffmpeg/ffprobe"
EOF
chmod +x "$TMP/bin-noffmpeg/brew"
run_start "$TMP/bin-noffmpeg" "$TMP/home3"
check "呼叫了 brew install ffmpeg" "grep -qx 'install ffmpeg' '$TMP/brew.txt'"
check "安裝後成功啟動" "grep -qx 'http://127.0.0.1:$PORT' '$TMP/opened.txt'"
pkill -f "remove_bgm.server --port $PORT" 2>/dev/null
wait "$START_PID" 2>/dev/null
sleep 1

echo "情境 4：沒有 ffmpeg、uv，也沒有 Homebrew → 顯示安裝說明並結束"
make_sandbox_bin "$TMP/bin-empty" ffmpeg ffprobe uv brew
run_start "$TMP/bin-empty" "$TMP/home4"
wait "$START_PID"
code=$?
check "結束代碼為 1" "[ $code -eq 1 ]"
check "提示缺少 ffmpeg uv 與 Homebrew" "grep -q '找不到 ffmpeg uv，也沒有安裝 Homebrew' '$TMP/out.log'"
check "沒有開啟瀏覽器" "[ ! -s '$TMP/opened.txt' ]"

echo "情境 5：連接埠被占用 → 顯示錯誤"
python3 -m http.server $PORT --bind 127.0.0.1 >/dev/null 2>&1 &
BLOCKER=$!
sleep 1
run_start "$TMP/bin-full" "$TMP/home1"
wait "$START_PID"
code=$?
kill "$BLOCKER" 2>/dev/null
check "結束代碼為 1" "[ $code -eq 1 ]"
check "提示連接埠可能被占用" "grep -q '連接埠 $PORT 可能被其他程式占用' '$TMP/out.log'"

printf '\n通過 %d，失敗 %d\n' "$pass" "$failed"
[ "$failed" -eq 0 ]
