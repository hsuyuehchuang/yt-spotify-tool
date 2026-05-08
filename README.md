# yt-music-tools

## 快速查閱

``` bash
python3 yt-to-spotify.py  "<yt_url>" ["<playlist_name>"]   # YouTube 爬歌名 → Spotify 播放清單
python3 spotify-to-mp3.py "<spotify_url>"                  # Spotify 播放清單 → MP3
python3 yt-to-mp3.py scrape   "<yt_url>"                   # YouTube 爬歌名 → 下載 MP3
python3 yt-to-mp3.py download "<yt_url>"                   # YouTube 影片/Playlist → 下載 MP3
python3 bandcamp-to-mp3.py                                  # 互動模式，貼網址下載
python3 bandcamp-to-mp3.py "<url>"                         # Bandcamp 或 YouTube 單曲
```

---

## 環境需求

```bash
pip install yt-dlp spotipy mutagen requests
sudo apt-get install ffmpeg
```

Spotify API：前往 [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) 建立 App，Redirect URI 填 `http://127.0.0.1:8888/callback`，API 選 Web API。Client ID / Secret 寫在各 script 頂部。

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
python spotify-to-mp3.py "<spotify_playlist_url>"
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

=== 完成 ===
成功: 18 首
失敗: 3 首
  - It'z a Rap - Phat Kat
```

**注意：**
- 下載資料夾自動命名：`YYYY-MM-DD_播放清單名稱`
- 檔名格式：`歌名 - Artist.mp3`
- 搜尋優先 YouTube Music Topic 頻道，找不到才做一般搜尋，兩輪都要過 title + artist + 時長比對
- 已存在的 MP3 自動跳過，重跑安全
- 每首歌之間隨機等待 2–5 秒
- 第一次執行會開瀏覽器要求 Spotify 登入授權（token 存在 `.cache`）

---

## yt-to-mp3.py

兩種模式。

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
成功: 18 首  已存在: 2 首
```

**注意（scrape / download 共用）：**
- 下載資料夾自動命名：`YYYY-MM-DD_Playlist名稱` 或 `YYYY-MM-DD_影片標題`
- 已存在的 MP3 自動跳過，重跑安全
- 封面（YouTube thumbnail）自動嵌入 MP3
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
