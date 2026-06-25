VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.PHONY: help install run shazam test clean

help:
	@echo "make install   建 .venv + 裝依賴 + 建 .env（clone 到新機器後先跑這個）"
	@echo "make run       統一入口 music.py（貼網址自動分流）"
	@echo "make shazam    Shazam/ACRCloud 辨識管線（互動）"
	@echo "make test      單元測試（不連網）"
	@echo "make clean     刪 .venv 與快取"

install:
	python3 -m venv $(VENV)
	$(PIP) install -U pip
	$(PIP) install -r requirements.txt
	@command -v ffmpeg >/dev/null 2>&1 || echo "[WARN] 找不到 ffmpeg，請執行: sudo apt-get install ffmpeg"
	@test -f .env || (cp .env.example .env && echo "[INFO] 已建立 .env；要用 ACRCloud 請填金鑰（留空=只用 Shazam）")
	@echo "[OK] 安裝完成。第一次寫 Spotify 會開瀏覽器登入。接著跑: make run"

run:
	$(PY) music.py

shazam:
	$(PY) shazam_pipeline.py

test:
	$(PY) test_recognizer.py

clean:
	rm -rf $(VENV) __pycache__ .shazam-cache
