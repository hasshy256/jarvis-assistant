"""VOICEVOX（PC の中で動く音声合成）。config.py で TTS_ENGINE = "voicevox" にしたときだけ使う。

エンジン（vv-engine\\run.exe）が動いていなければ、裏で起動する。声は VOICEVOX_SPEAKER の番号で選ぶ
（番号は「声のサンプル」フォルダのファイル名の ID）。
"""
import glob
import hashlib
import json
import os
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request

from config import VOICEVOX_SPEAKER, VOICEVOX_SPEED, VOICEVOX_URL


def find_engine():
    # CPU 版を優先する（この PC の Arc GPU では GPU 版のほうが遅かった: CPU 0.3〜0.9 秒、GPU 約 8 秒）
    patterns = [r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\HiroshibaKazuyuki.VOICEVOX.CPU_*\VOICEVOX\vv-engine\run.exe",
                r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\HiroshibaKazuyuki.VOICEVOX*\VOICEVOX\vv-engine\run.exe",
                r"%LOCALAPPDATA%\Programs\VOICEVOX\vv-engine\run.exe"]
    for p in patterns:
        found = glob.glob(os.path.expandvars(p))
        if found:
            return found[0]
    return None


def running():
    try:
        with urllib.request.urlopen(f"{VOICEVOX_URL}/version", timeout=1):
            return True
    except Exception:
        return False


def ensure_running(wait_sec=60):
    if running():
        return True
    exe = find_engine()
    if not exe:
        return False
    subprocess.Popen([exe], creationflags=subprocess.CREATE_NO_WINDOW,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    end = time.time() + wait_sec
    while time.time() < end:
        if running():
            return True
        time.sleep(1)
    return False


def synthesize(text):
    """読み上げ用の wav ファイルのパス（同じ文は作り直さない）。"""
    name = hashlib.md5(f"vv{VOICEVOX_SPEAKER}{VOICEVOX_SPEED}{text}".encode()).hexdigest()
    path = os.path.join(tempfile.gettempdir(), f"jarvis_{name}.wav")
    if os.path.exists(path):
        return path
    query_url = f"{VOICEVOX_URL}/audio_query?" + urllib.parse.urlencode({"text": text, "speaker": VOICEVOX_SPEAKER})
    with urllib.request.urlopen(urllib.request.Request(query_url, method="POST"), timeout=30) as r:
        query = json.load(r)
    query["speedScale"] = VOICEVOX_SPEED
    req = urllib.request.Request(f"{VOICEVOX_URL}/synthesis?speaker={VOICEVOX_SPEAKER}",
                                 data=json.dumps(query).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    with open(path, "wb") as f:
        f.write(data)
    return path


def warmup():
    """声のモデルを先に読み込んでおく（最初の 1 文だけ時間がかかるため）。"""
    if ensure_running():
        synthesize("準備ができました。")
