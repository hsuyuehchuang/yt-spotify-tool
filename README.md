# yt-music-tools

## 快速查閱

``` bash
python3 shazam_pipeline.py        # 長影片/混音 → Shazam 聲紋辨識 → Spotify（互動模式，直接貼網址）
python3 bandcamp-to-mp3.py        # 貼網址下載 MP3（Bandcamp / YouTube 單曲，互動模式）
python3 yt-to-spotify.py          # YouTube 自帶音樂卡片 → Spotify 播放清單
python3 spotify-to-mp3.py         # Spotify 播放清單 → MP3
python3 yt-to-mp3.py scrape       # YouTube 爬歌名 → 下載 MP3
python3 yt-to-mp3.py download     # YouTube 影片/Playlist → 下載 MP3
```

`shazam_pipeline` / `bandcamp` / `yt-to-mp3` / `spotify-to-mp3` 直接跑（不帶參數）就會進**互動模式**，
跳提示貼網址，**不用引號、不用跳脫**。把網址當參數傳時，含 `&` 的網址記得用引號包住（見各節範例）。
（`yt-to-spotify` 仍只吃參數。）

> `shazam_pipeline.py` 與 `yt-to-spotify.py` 差異：後者只能抓 YouTube **自帶的音樂卡片**（DJ mix /
> 合輯這類未標記長影片沒有卡片，會抓不到）；前者直接對音軌做 Shazam 聲紋辨識，專治未標記長影片。

---

## 環境需求

```bash
pip install -r requirements.txt    # 或手動：pip install yt-dlp spotipy mutagen requests shazamio ytmusicapi
sudo apt-get install ffmpeg
```

Spotify API：前往 [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) 建立 App，Redirect URI 填 `http://127.0.0.1:8888/callback`，API 選 Web API。Client ID / Secret 集中在 `config.py`。

---

## shazam_pipeline.py

把 **未標記的長影片**（DJ mix、音樂合輯）丟進來，對音軌做 Shazam 聲紋辨識，命中的歌全部匯進**單一 Spotify 播放清單**。

### 快速開始（互動模式，推薦）

直接跑，照提示貼網址即可，**不用引號、不用跳脫**：

```bash
python3 shazam_pipeline.py
```
```
貼上 YouTube 播放清單或影片網址: https://www.youtube.com/watch?v=5NV6Rdv1a3I
Spotify 播放清單名稱（直接 Enter = 用影片標題）: 我的混音清單
```

只想先看辨識結果、不要寫 Spotify（不需登入）：

```bash
python3 shazam_pipeline.py --no-spotify
```

### 第一次寫入 Spotify

第一次要寫清單時會開瀏覽器要求 Spotify 登入授權，授權後 token 會存在 `.cache`，之後不再詢問。
帳號設定在 `config.py`（Client ID / Secret / Redirect URI）。

### 進階：直接帶參數（自動化用）

網址當參數傳時，因為網址含 `&`，**要用引號包住**（不然 shell 會把 `&` 當成丟到背景）：

```bash
python3 shazam_pipeline.py "https://www.youtube.com/playlist?list=PLxxxx" "Weekender 2026"
python3 shazam_pipeline.py "https://www.youtube.com/watch?v=5NV6Rdv1a3I" "我的清單" --refresh
```

| 旗標 | 作用 |
|---|---|
| `--no-spotify` | 只辨識並印結果，完全不碰 Spotify（dry-run，不需登入） |
| `--refresh` | 忽略 `.shazam-cache` 既有 checkpoint，重新辨識 |

### 運作方式

1. 解析 playlist/影片 → 逐支下載音軌（`yt-dlp`，暫存檔處理完即刪）。
2. **自適應掃描（不快轉，整段掃完）**：12 秒窗；沒命中前進 6 秒（重疊密掃，避免漏），命中前進 12 秒（不重疊，提速）。逐段送 `shazamio`，`track_id` 去重，並顯示即時進度條（百分比 / 已掃秒數 / 命中數 / 剩餘視窗 / ETA）。
3. **瀑布流**：Spotify 直接搜 → 沒中改用 YT Music / SoundCloud 拿乾淨歌名回頭再搜 Spotify。最終都落地到同一個 Spotify 清單；各平台有但 Spotify 沒有的只記進報告。

### 注意

- `shazamio` 是逆向工程的非官方庫，會遇到限流/暫時封 IP。本管線內建退避重試；連續失敗會**優雅停止並保留進度**（每支影片辨識完即寫 `.shazam-cache/<video_id>.json`，重跑可接續）。
- 召回率 vs 速度由 `recognizer.py` 頂端的 `STEP_MISS` / `STEP_HIT` 控制：`STEP_MISS` 越小、沒命中的段落掃得越密（越不易漏，但越慢、越易被限流）；`STEP_HIT` 是命中後前進量。
- 找不到全部歌通常不是 bug：DJ mix 的混音/轉場，或該曲目不在 Shazam 資料庫，本來就辨識不出。
- YT Music 走 `ytmusicapi` 免授權搜尋；SoundCloud 官方 API 已關閉，client_id 於執行期從 web player 動態抓取（會隨改版失效，可在 `config.py` 關閉）。
- 僅供個人使用；逆向 Shazam 與下載音軌屬灰色地帶。

### 架構（模組分工）

| 模組 | 職責 |
|---|---|
| `shazam_pipeline.py` | 主進入點，串接整條管線、互動/參數解析、輸出統計 |
| `audio_source.py` | yt-dlp 解析 playlist、下載單支音軌 |
| `slicer.py` | ffmpeg 把指定時間窗切成 16kHz mono wav |
| `recognizer.py` | 密集掃描 + shazamio 容錯/pacing + 每影片 checkpoint |
| `waterfall.py` | 跨平台搜尋（Spotify → YT Music → SoundCloud） |
| `spotify_client.py` | Spotify 搜尋/建立清單/去重/寫入（與 `yt-to-spotify.py` 共用） |
| `config.py` | 集中金鑰與設定 |

狀態機單元測試（不連網）：`python3 test_recognizer.py`

---

## yt-to-spotify.py

從 YouTube 影片或 Playlist 爬取「音樂」區塊的歌曲，可選擇建立 Spotify 播放清單。

```bash
# 只印歌單，不建 Spotify 播放清單
python yt-to-spotify.py "<yt_url>"

# 爬完後自動建立（或更新）Spotify 播放清單
python yt-to-spotify.py "<yt_url>" "播放清單名稱"
```

```bash
# 範例
python yt-to-spotify.py "https://www.youtube.com/watch?v=eSsYFGnZYAI"
python yt-to-spotify.py "https://www.youtube.com/playlist?list=PLxxx" "Weekender 2026"
```

**輸出：**
```
[1/26] 影片標題 ...

==================================================
歌曲列表（共 21 首）
==================================================
1. Extra, Extra!! - Paula Perry
2. It'z a Rap - Phat Kat
...

==================================================
各影片找到的歌曲
==================================================
影片標題
  - Extra, Extra!! - Paula Perry
```

**注意：**
- 第二個參數不給就只印歌單，不動 Spotify
- 有給名稱：若播放清單已存在則沿用，已有的歌不重複新增
- Spotify 搜尋兩輪：結構化查詢 → 純文字查詢，兩輪都做 title + artist 驗證
- 每支影片爬完後隨機等待 1.5–3 秒

---

## spotify-to-mp3.py

從 Spotify 播放清單讀取歌曲，搜尋 YouTube 下載為 MP3，並寫入封面、歌手、專輯等 ID3 Tags。

```bash
python spotify-to-mp3.py                                   # 互動模式，貼一個下一個（免引號）
python spotify-to-mp3.py "<spotify_playlist_url>"          # 直接給網址
python spotify-to-mp3.py "<spotify_playlist_url>" ./music  # 指定輸出資料夾
```

```bash
# 範例
python spotify-to-mp3.py "https://open.spotify.com/playlist/2b1UMpzBx73UmYn4D8Xcmv"
```

**輸出：**
```
=== 讀取 Spotify 播放清單 ===
播放清單: Weekender 2026
下載資料夾: ./2026-05-08_Weekender 2026
共 21 首歌

[1/21] Extra, Extra!! - Paula Perry
    [完成] ./2026-05-08_Weekender 2026/Extra, Extra!! - Paula Perry.mp3
[2/21] It'z a Rap - Phat Kat
    [找不到 YouTube 對應歌曲]
...

==================================================
完成：成功 18 | 已存在 0 | 失敗 3
  - 失敗: It'z a Rap - Phat Kat
```

**注意：**
- 不帶參數 → 互動模式（貼一個下一個，免引號）
- 下載資料夾預設 `YYYY-MM-DD_播放清單名稱`；可用第二個參數指定資料夾
- 檔名格式：`歌名 - Artist.mp3`
- 搜尋優先 YouTube Music Topic 頻道，找不到才做一般搜尋，兩輪都要過 title + artist + 時長比對
- 已存在的 MP3 自動跳過，重跑安全
- 每首歌之間隨機等待 2–5 秒
- 第一次執行會開瀏覽器要求 Spotify 登入授權（token 存在 `.cache`）

---

## yt-to-mp3.py

兩種模式（scrape / download）。**不帶參數直接跑會進互動模式**：先問要哪個模式，再貼網址（免引號，貼一個跑一個）。

```bash
python yt-to-mp3.py            # 互動模式（先選模式再貼網址）
```

### scrape 模式

爬每支影片的音樂區塊，用 YouTube 直接提供的連結下載（最準確），找不到連結才做名稱搜尋。

```bash
python yt-to-mp3.py scrape "<yt_url>"
```

```bash
# 範例
python yt-to-mp3.py scrape "https://www.youtube.com/playlist?list=PLxxx"
python yt-to-mp3.py scrape "https://www.youtube.com/watch?v=xxx"
```

**輸出：**
```
[1/26] 影片標題
  [直接] Extra, Extra!! - Paula Perry
  [相似] Losing Out - Black Milk
[2/26] 影片標題
  [找不到] Some Track - Artist

==================================================
直接下載: 15 首
相似下載: 4 首
找不到:   2 首
  - Some Track - Artist
```

| 標記 | 意思 |
|---|---|
| `[直接]` | YouTube 音樂區塊提供的直接連結，100% 正確 |
| `[相似]` | 名稱搜尋找到，有比對 title + artist，但不保證 100% |
| `[找不到]` | 兩種方式都失敗 |

### download 模式

直接把 YouTube Playlist 或單一影片下載成 MP3，不做任何爬蟲。

```bash
python yt-to-mp3.py download "<yt_url>"
```

```bash
# 範例（Playlist）
python yt-to-mp3.py download "https://www.youtube.com/playlist?list=PLxxx"

# 範例（單一影片）
python yt-to-mp3.py download "https://www.youtube.com/watch?v=xxx"

# 範例（watch?v=...&list=... 格式會自動轉成 playlist）
python yt-to-mp3.py download "https://www.youtube.com/watch?v=xxx&list=PLxxx"
```

**輸出：**
```
下載資料夾: ./2026-05-08_Playlist Title

[1/20] 影片標題
  影片標題  87%
  [完成] 影片標題
[2/20] 影片標題
  [已存在，跳過]
...

==================================================
完成：成功 18 | 已存在 2 | 失敗 0
```

**注意（scrape / download 共用）：**
- 不帶參數 → 互動模式（先選模式，再貼一個跑一個）
- 下載資料夾自動命名：`YYYY-MM-DD_Playlist名稱` 或 `YYYY-MM-DD_影片標題`
- 已存在的 MP3 自動跳過，重跑安全
- 封面（YouTube thumbnail）自動嵌入 MP3，並寫入 artist / album / title 等 metadata
- 音質：`bestaudio/best` + MP3 VBR 最高品質
- 每支影片之間隨機等待 1.5–4 秒

---

## bandcamp-to-mp3.py

快速下載 MP3，支援 **Bandcamp** 與 **YouTube 單曲**，自動嵌入封面與 ID3 metadata。
- Bandcamp 部分只下載平台允許播放的內容（免費下載 / 可串流預覽），不繞過付費機制
- YouTube 不支援 playlist（要下整個 playlist 請用 `yt-to-mp3.py`）

### 三種用法

```bash
# 1. 互動模式（最方便）：貼一個下載一個，空白 Enter 或 Ctrl+C 結束
python3 bandcamp-to-mp3.py
>>> https://artist.bandcamp.com/album/xxx
>>> https://www.youtube.com/watch?v=yyy
>>> [Enter]

# 2. 一次給多個 URL
python3 bandcamp-to-mp3.py "url1" "url2" "url3"

# 3. 單個 URL
python3 bandcamp-to-mp3.py "https://www.youtube.com/watch?v=xxx"
```

支援的 URL 類型：
- Bandcamp 單曲：`https://artist.bandcamp.com/track/song-name`
- Bandcamp 專輯：`https://artist.bandcamp.com/album/album-name`
- Bandcamp 藝術家頁面：`https://artist.bandcamp.com/music`
- YouTube 單一影片：`https://www.youtube.com/watch?v=xxx`

### 推薦：設 alias 更方便

在 `~/.bashrc` 加一行：

```bash
alias bc='cd ~/Desktop/vscode/github/yt-spotify-tool && python3 bandcamp-to-mp3.py'
```

之後輸入 `bc` 就直接進互動模式。

**注意：**
- 下載資料夾固定為 `YYYY-MM-DD_QuickDownload`，Bandcamp 和 YouTube 都進同個資料夾
- 檔名格式：`歌名 - Artist.mp3`
- 已存在的 MP3 自動跳過
- YouTube 帶 `list=` 或 `/playlist` 的 URL 會被擋下，要求改用 `yt-to-mp3.py`
- 每首歌之間隨機等待 1.5–4 秒
