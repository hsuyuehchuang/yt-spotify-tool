"""用 ffmpeg 把音軌的指定時間窗切到記憶體（wav bytes）。

選 ffmpeg 逐段 seek 而非 pydub 整檔載入：長影片（數十分鐘）整檔解碼成 PCM 會吃
數百 MB 記憶體；逐段 -ss seek 只解出當前 12 秒，記憶體有界。repo 本來就依賴 ffmpeg。

輸出固定 16kHz 單聲道 wav —— Shazam 指紋本來就在 16kHz mono 上運算，這是最小且
最通用的可解碼輸入。

切片寫到暫存檔再讀回 bytes（隨即刪除）：ffmpeg 輸出到 pipe 無法回填 RIFF 大小欄位
（變成佔位值），會讓 shazamio 的解碼器誤判而噴一堆 "invalid mpeg audio header"；
寫成檔案則 header 正確、輸出乾淨。仍不留永久切片檔。
"""

import os
import subprocess
import tempfile

SAMPLE_RATE = 16000
CHANNELS = 1


class FfmpegError(RuntimeError):
    pass


def probe_duration(path: str) -> float:
    """回傳音軌總長度（秒）。失敗丟 FfmpegError。"""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except FileNotFoundError as e:
        raise FfmpegError("找不到 ffprobe，請先安裝 ffmpeg") from e
    except subprocess.CalledProcessError as e:
        raise FfmpegError(f"ffprobe 失敗: {e.stderr.strip()}") from e
    try:
        return float(out.stdout.strip())
    except ValueError as e:
        raise FfmpegError(f"無法解析時長: {out.stdout!r}") from e


def cut(path: str, start: float, duration: float) -> bytes:
    """切出 [start, start+duration) 的音訊，回傳 16kHz mono wav bytes。"""
    fd, tmp = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start:.3f}",     # 放在 -i 之前 = 快速 input seek
        "-t", f"{duration:.3f}",
        "-i", path,
        "-ac", str(CHANNELS),
        "-ar", str(SAMPLE_RATE),
        "-f", "wav",
        "-loglevel", "error",
        tmp,
    ]
    try:
        subprocess.run(cmd, capture_output=True, check=True)
        with open(tmp, "rb") as f:
            return f.read()
    except FileNotFoundError as e:
        raise FfmpegError("找不到 ffmpeg，請先安裝 ffmpeg") from e
    except subprocess.CalledProcessError as e:
        raise FfmpegError(f"ffmpeg 切片失敗: {e.stderr.decode(errors='replace').strip()}") from e
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
