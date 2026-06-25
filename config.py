"""集中金鑰與共用設定。

把各 script 原本散在檔案頂端的金鑰收斂到這裡，新管線（shazam_pipeline）與
既有 script（yt-to-spotify）共用同一份設定，避免複製多份。
"""

import os


def _load_dotenv(path=".env"):
    """把專案根目錄的 .env 載進環境變數（已存在的環境變數優先，不覆蓋）。

    讓 ACRCloud 金鑰可以放在一個 .env 檔（git 忽略），clone 到別台機器複製過去即可，
    不必每台 export。沒有 .env 就什麼都不做（退回純環境變數）。
    """
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip())


_load_dotenv()

# --- Spotify ---
# 沿用既有 script 的值。前往 https://developer.spotify.com/dashboard 建立 App，
# Redirect URI 填 http://127.0.0.1:8888/callback，API 選 Web API。
SPOTIFY_CLIENT_ID = "9e989e6f2e034ca695d116607ec0cca6"
SPOTIFY_CLIENT_SECRET = "35a77882d91343a7a45fb507e1bd82fc"
SPOTIFY_REDIRECT_URI = "http://127.0.0.1:8888/callback"

# 寫入播放清單需要的權限
SPOTIFY_SCOPE_WRITE = "playlist-read-private playlist-modify-public playlist-modify-private"

# --- SoundCloud ---
# SoundCloud 官方 API 註冊已關閉，沒有靜態金鑰；client_id 於執行期從 web player
# 動態抓取（見 waterfall.py）。這個開關可整個關掉 SoundCloud 那一層。
SOUNDCLOUD_ENABLED = True

# --- ACRCloud（可選的第二辨識引擎，對 DJ mix 較佳）---
# 去 https://console.acrcloud.com 建一個 "Audio & Video Recognition" 專案，
# 會給你 host / access_key / access_secret。三個都填了才會啟用 ACRCloud。
# 建議用環境變數，不要把金鑰 commit 進 git：
#   export ACRCLOUD_HOST=identify-xxx.acrcloud.com
#   export ACRCLOUD_ACCESS_KEY=xxxxxxxx
#   export ACRCLOUD_ACCESS_SECRET=xxxxxxxx
ACRCLOUD_HOST = os.environ.get("ACRCLOUD_HOST", "")
ACRCLOUD_ACCESS_KEY = os.environ.get("ACRCLOUD_ACCESS_KEY", "")
ACRCLOUD_ACCESS_SECRET = os.environ.get("ACRCLOUD_ACCESS_SECRET", "")

# --- 快取/暫存 ---
SHAZAM_CACHE_DIR = ".shazam-cache"   # 每影片一個 JSON 的辨識 checkpoint
