"""議事録。「議事録を取って」で録音を始め、「議事録を終わって」で止める。

録音中は音声を wav に書き溜めるだけ（軽い）。止めたら裏で無音のところで区切って文字起こしし、
できた文字起こしを Claude に渡して議事録（要点・決定事項・やること）にまとめてもらう。
"""
import os
import re
import threading
import wave
from datetime import datetime

import numpy as np

from config import MINUTES_AUDIO_DIR, MINUTES_DIR, MINUTES_SEG_MAX_SEC, MINUTES_SEG_MIN_SEC, SAMPLE_RATE

START_PHRASE = re.compile(r"議事録.*(取|と[っり]|撮|始|開始|録|記録)|会議(を)?(録音|記録)|(録音|記録).*会議")
STOP_PHRASE = re.compile(r"議事録.*(終|おわ|止|と[めま]|やめ|完了|まとめ|以上)|会議.*(終|おわ)|(終|おわ).*会議")
BLOCK = int(SAMPLE_RATE * 0.1)


def is_start(plain):
    return len(plain) <= 25 and bool(START_PHRASE.search(plain))


def is_stop(plain):
    return len(plain) <= 25 and bool(STOP_PHRASE.search(plain))


class Minutes:
    def __init__(self):
        self.active = False
        self.started = None
        self.wav = None
        self.path = None
        self.ready = None  # 文字起こしが済んだファイルのパス（本体が取りに来る）
        self.working = False
        self.seconds = 0.0

    def start(self):
        os.makedirs(MINUTES_AUDIO_DIR, exist_ok=True)
        self.started = datetime.now()
        self.path = os.path.join(MINUTES_AUDIO_DIR, self.started.strftime("%Y%m%d_%H%M") + ".wav")
        self.wav = wave.open(self.path, "wb")
        self.wav.setnchannels(1)
        self.wav.setsampwidth(2)
        self.wav.setframerate(SAMPLE_RATE)
        self.seconds = 0.0
        self.active = True

    def feed(self, block):
        if self.active:
            self.wav.writeframes(block.astype(np.int16).tobytes())
            self.seconds += len(block) / SAMPLE_RATE

    def stop(self, stt, log):
        """録音を止め、裏で文字起こしを始める。録音の長さ（秒）を返す。"""
        self.active = False
        self.wav.close()
        seconds = self.seconds
        self.working = True
        threading.Thread(target=self._transcribe, args=(self.path, self.started, stt, log), daemon=True).start()
        return seconds

    def _segments(self, audio):
        """無音のところで区切る。短すぎず長すぎない塊にする。"""
        n = len(audio) // BLOCK
        loud = [float(np.sqrt(np.mean((audio[i * BLOCK:(i + 1) * BLOCK].astype(np.float32) / 32768) ** 2))) for i in range(n)]
        floor = max(np.percentile(loud, 20) * 2.5, 0.006) if loud else 0.006
        segs, start, quiet = [], 0, 0
        for i, v in enumerate(loud):
            quiet = quiet + 1 if v < floor else 0
            length = (i + 1 - start) * 0.1
            if (quiet >= 6 and length >= MINUTES_SEG_MIN_SEC) or length >= MINUTES_SEG_MAX_SEC:
                segs.append((start * BLOCK, (i + 1) * BLOCK))
                start, quiet = i + 1, 0
        if start < n:
            segs.append((start * BLOCK, len(audio)))
        return [(a, b, loud[a // BLOCK:b // BLOCK]) for a, b in segs]

    def _transcribe(self, path, started, stt, log):
        try:
            with wave.open(path, "rb") as w:
                audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
            segs = self._segments(audio)
            log(f"議事録: 文字起こしを始めます（{len(audio) / SAMPLE_RATE / 60:.1f} 分・{len(segs)} 区切り）")
            lines = []
            for a, b, levels in segs:
                if not levels or max(levels) < 0.01:  # ほぼ無音
                    continue
                text = stt.transcribe(audio[a:b])
                if text:
                    lines.append(f"[{a // SAMPLE_RATE // 60:02d}:{a // SAMPLE_RATE % 60:02d}] {text}")
            os.makedirs(MINUTES_DIR, exist_ok=True)
            out = os.path.join(MINUTES_DIR, started.strftime("%Y%m%d_%H%M") + "_文字起こし.txt")
            with open(out, "w", encoding="utf-8") as f:
                f.write(f"会議の文字起こし（{started:%Y年%m月%d日 %H:%M} 開始・約 {len(audio) / SAMPLE_RATE / 60:.0f} 分）\n"
                        "※音声認識なので誤字・聞き間違いがある。話者の区別はない\n\n" + "\n".join(lines) + "\n")
            log(f"議事録: 文字起こし完了 {out}（{len(lines)} 行）")
            self.ready = out if lines else ""
        except Exception as e:
            log(f"議事録: 文字起こしに失敗: {e}")
            self.ready = ""
        finally:
            self.working = False
