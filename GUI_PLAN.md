# 網頁 GUI 計畫（給之後實作）

目標：把現有 CLI 工具包成一個**本機網頁介面** —— 瀏覽器貼網址、按一下、看進度條，
不用開終端機;手機/其他裝置也能連。CLI 仍是「真正的邏輯」,網頁只是包一層。

## 範圍（先做 MVP，不要一開始就做大）

MVP 要能：
1. 一個輸入框貼網址 + 選動作(下載 MP3 / Shazam 辨識 → Spotify / 爬卡片 → Spotify)。
2. Shazam 那條有**即時進度條**(百分比 / 命中數 / ETA),沿用現有 `Progress` 的數據。
3. 結果頁:列出辨識到的歌 + 每首狀態(已加入 / Spotify 無),可下載 tracklist。
4. Spotify 登入走網頁 OAuth(網頁反而比 CLI 順)。

先不做：多使用者、帳號系統、雲端部署、下載檔案的瀏覽器直傳。

## 建議技術棧（刻意精簡，無重前端框架）

- 後端：**FastAPI**(async,內建背景任務,跟 shazamio 的 async 合拍)。
- 前端：**單一 HTML + 原生 JS**(不用 React/Vue)。一頁:輸入框、動作按鈕、進度條、結果表。
- 即時進度：**Server-Sent Events (SSE)**(`text/event-stream`,比 WebSocket 簡單,單向推進度剛好夠)。
- 不需要 Redis/Celery —— 本機單人,用 FastAPI 行程內的背景任務 + 一個 job 狀態 dict 就好。

## 架構重點與既有程式的接法

### 關鍵挑戰:Shazam 掃一支要 10 分鐘
不能卡在一個 HTTP request 裡。做法:
- `POST /jobs` 建一個工作 → 回 `job_id`，掃描在**背景任務**跑。
- `GET /jobs/{id}/events`(SSE)→ 持續推進度;前端拿來畫進度條。
- `GET /jobs/{id}` → 拿最終結果(歌單 + 狀態)。

### 進度怎麼接
現有 `recognizer.scan_track` 已經有 `on_window` / `on_found` callback,
`shazam_pipeline.Progress` 已算好 百分比/命中/ETA。**改一點點**:把那些 callback
改成「把進度事件丟進該 job 的 queue」,SSE 端從 queue 讀出來推給瀏覽器。
(等於把現在印到終端機的 `\r` 進度,改成推到網頁。)

### 不要直接 import `shazam_pipeline.py`
它是 CLI 用的(有 `input()`、`print`、`os._exit`)。**新增一個 `service.py`**,
把「下載 → 掃描 → 瀑布流 → 寫 Spotify」重組成一個可被 await 的函式,
**直接複用已經是模組的**:`audio_source` / `slicer` / `recognizer` / `waterfall` /
`spotify_client` / `config`。`shazam_pipeline.py` 與 `music.py` 保持不動當 CLI。

> 注意:`spotify-to-mp3.py` / `yt-to-mp3.py` / `bandcamp-to-mp3.py` 檔名含 `-` 不能 import。
> 下載類動作網頁版最省事的做法是**用 subprocess 呼叫**(像 `music.py` 那樣),把 stdout 串到 SSE。
> Shazam 那條才值得走 `service.py` 重組(因為要細緻的進度與結果)。

### Spotify OAuth(網頁版更順)
- 現有 redirect_uri 是 `http://127.0.0.1:8888/callback` —— 讓 FastAPI 跑在 8888,
  自己做 `/login`(導去 Spotify 授權)與 `/callback`(收 code、存 token 到 `.cache`)。
- spotipy 的 `SpotifyOAuth` 可重用;token 一樣存 `.cache`,跟 CLI 共用。

## 端點草圖

```
GET  /                      首頁(HTML)
POST /api/resolve           {url} -> {platform, actions[]}   判斷網址 + 可做哪些動作
POST /api/jobs              {url, action, playlist_name?} -> {job_id}
GET  /api/jobs/{id}/events  SSE:推 {pct, hits, eta, found:[...]}
GET  /api/jobs/{id}         {status, songs:[{song,artist,status,uri,pos}]}
GET  /login   /callback     Spotify OAuth
```

## 前端草圖（一頁）

- 上方:網址輸入框 + 「解析」按鈕 → 顯示平台 + 動作按鈕。
- 動作按下 → `POST /api/jobs` → 開 SSE → 畫**進度條**(沿用 pct/命中/ETA)。
- 完成 → 顯示**結果表**(歌名 / 歌手 / 狀態 / Spotify 連結) + 「下載 tracklist」。

## 分階段步驟（每步可獨立驗證）

1. FastAPI 起一個首頁 + `/api/resolve`(重用 `music.detect_platform`)→ 驗證:貼網址會回平台。
2. 下載類動作:`POST /api/jobs` 用 subprocess 跑既有 script,stdout 串到 SSE → 驗證:能下載、看得到輸出。
3. 寫 `service.py`:把 Shazam 管線重組成可 await + 回呼進度的函式(複用現有模組)。
4. Shazam job + SSE 進度條 → 驗證:瀏覽器看到即時進度,結束有歌單。
5. Spotify OAuth(/login /callback)+ 寫清單 + 結果表 → 驗證:授權後能寫入、結果頁正確。
6. tracklist 下載、ACRCloud 開關、錯誤處理收尾。

## 工時粗估

- MVP(階段 1–4):約 1 天。
- 含 Spotify 寫入 + 結果表(階段 5）:再半天到 1 天。

## 要先決定的事

- 只本機自己用,還是要能從手機連(同網段)?→ 影響綁定 `127.0.0.1` 還是 `0.0.0.0` + 風險。
- 金鑰:延續環境變數(ACRCloud)+ `.cache`(Spotify);網頁版不要把金鑰放進前端。
- CLI 當「真相來源」、網頁只包一層(建議)→ 之後改邏輯只改一處。

## 可直接重用的現成資產

- `music.detect_platform` / `build_command`(分流)。
- `recognizer.scan_track`(已有 `on_window`/`on_found` 進度回呼)、`ShazamRecognizer`/`ACRCloudEngine`/`EngineChain`。
- `shazam_pipeline.Progress`(pct/命中/ETA 計算邏輯,可抽出來共用)。
- `audio_source` / `slicer` / `waterfall` / `spotify_client` / `config`。
