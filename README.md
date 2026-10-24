# remove-bgm 去除配樂

把電影、影集片段的**背景音樂去掉，只留下對白與音效**，輸出可以直接匯入 **Logic Pro** 的素材包，用來練習幫畫面寫配樂。

- 輸入：貼 YouTube 網址，或上傳影片檔
- 可以只擷取其中一段（例如 1:23 到 3:45）
- 輸出：去掉配樂的影片（H.264 .mov）＋ 對白、音效、混音 WAV（48kHz / 24-bit），可選擇是否輸出原配樂
- 全部在自己的電腦上執行，不會上傳到任何伺服器

分離模型使用 [Bandit v2](https://github.com/kwatcharasupat/bandit-v2)，這是專為電影音軌訓練的模型，直接把聲音分成「對白／音樂／音效」三軌。

## 安裝（第一次）

需要 macOS（Apple Silicon 會用 GPU 加速）。

1. 安裝 [Homebrew](https://brew.sh/zh-tw/)：打開「終端機」，貼上官網首頁的安裝指令並執行（過程中會要求輸入電腦密碼）。
2. 下載這個專案：
   ```bash
   git clone https://github.com/qqqq5953/remove-bgm.git ~/remove-bgm
   ```
   如果是從 GitHub 下載 zip 解壓縮的，請先在終端機執行一次：
   ```bash
   chmod +x ~/remove-bgm/start.command
   ```
3. 在 Finder 打開資料夾，**對 `start.command` 按右鍵 →「打開」**。第一次需要這樣做才能通過 macOS 的安全檢查，之後直接雙擊即可。

`start.command` 會自動完成以下事情：

- 用 Homebrew 安裝 `ffmpeg` 和 `uv`（如果還沒裝）
- 建立 Python 環境（第一次會下載 PyTorch，約需幾分鐘）
- 把 yt-dlp 更新到最新版
- 開啟瀏覽器

第一次處理影片時，會自動下載模型權重（約數百 MB），存在 `~/Library/Caches/remove-bgm/models`。

> 不需要安裝 Node.js：網頁介面已經預先建置好。
>
> 如果你其他用 uv 的專案也裝過同版本的套件（例如 PyTorch、yt-dlp），uv 會共用下載快取，不會重複下載，也不會多佔一份硬碟空間（類似 pnpm）。

## 使用方式

1. 雙擊 `start.command`，瀏覽器會打開操作頁面。
2. 選擇「YouTube 網址」貼上網址，或切換到「上傳檔案」拖入影片。
3. （選填）填寫開始、結束時間，只處理需要的片段。格式為 `1:23` 或 `1:02:03`。
4. （選填）調整對白、音效的音量。這只影響影片音軌與「對白＋音效」混音檔。
5. （選填）勾選「輸出原配樂 stem」，寫完配樂後可以跟原作比較。
6. 按「開始處理」，完成後可以直接試聽、下載 zip，或「在 Finder 中顯示」。

處理完的檔案存在 `~/Movies/remove-bgm/<片名>_<開始>-<結束>/`：

| 檔案 | 內容 |
|---|---|
| `video_no_music.mov` | 去掉配樂的影片（H.264，音軌是對白＋音效） |
| `dialogue.wav` | 對白 |
| `effects.wav` | 音效 |
| `dialogue+effects.wav` | 對白＋音效混音（套用你設定的音量） |
| `original_music.wav` | 原配樂（有勾選才會輸出） |

結束使用時，關閉 `start.command` 開啟的終端機視窗即可。

## 匯入 Logic Pro

1. 新增專案，在「檔案 > 專案設定 > 音訊」把取樣率設為 **48 kHz**。
2. 「檔案 > 影片 > 打開影片…」，選擇 `video_no_music.mov`。
3. 把 `dialogue.wav`、`effects.wav`（以及要比較時的 `original_music.wav`）拖進專案，放在**第一小節開頭**。所有檔案都從 0 秒開始、長度跟影片一致，對齊開頭就會跟畫面同步。
4. 開新的軌道寫你的配樂。

## 常見問題

**YouTube 下載失敗**
YouTube 常改版，重新執行 `start.command` 會自動更新 yt-dlp。如果是有年齡限制、需要登入的影片，請先用其他方式下載，再用「上傳檔案」處理。

**分離得不乾淨**
音樂和音效最難分開：弦樂長音和環境聲、大鼓和爆炸聲的頻譜很像，effects 軌裡可能殘留一點音樂。對白通常分得最好。

**Apple GPU（MPS）出錯**
程式遇到 MPS 不支援的運算時，會自動改用 CPU 重跑。如果想強制使用 CPU，可以在終端機執行：
```bash
REMOVE_BGM_DEVICE=cpu ~/remove-bgm/start.command
```

**連接埠被占用**
預設使用 8765。要換其他連接埠：
```bash
REMOVE_BGM_PORT=9000 ~/remove-bgm/start.command
```

## 開發

```
backend/    Python + FastAPI（下載、ffmpeg、分離模型、API）
frontend/   純 TypeScript + HTML + CSS（Vite 只負責編譯），建置結果 dist/ 有 commit
e2e/        端到端測試（Playwright）與 start.command 測試
```

### 測試

```bash
# 後端單元與 API 測試
cd backend && uv run pytest

# 真實模型測試（會下載模型權重，需要能連到 zenodo.org）
cd backend && uv run pytest -m model

# 前端測試（含「dist/ 與原始碼一致」的檢查）
cd frontend && npm install && npm test

# 修改前端後要重新建置並 commit dist/
cd frontend && npm run build

# 端到端測試（真實後端與前端；分離器與 yt-dlp 用測試替身）
cd e2e && npm install && npx playwright install chromium && npm test

# start.command 各情境測試
bash e2e/start_command_test.sh
```

開發前端時，可以先啟動後端（`cd backend && uv run python -m remove_bgm.server`），再執行 `cd frontend && npm run dev`，`/api` 會自動轉給後端。

## 授權與致謝

- 分離模型程式碼移植自 [kwatcharasupat/bandit-v2](https://github.com/kwatcharasupat/bandit-v2)（Apache License 2.0），論文：Watcharasupat, Wu & Orife, *Remastering Divide and Remaster: A Cinematic Audio Source Separation Dataset with Multilingual Support*（2024）。
- 模型權重來自 [Zenodo 12701995](https://zenodo.org/records/12701995)，使用條件請參考該頁面。
- 下載 YouTube 影片請遵守 YouTube 服務條款，處理後的素材僅供個人練習使用。
