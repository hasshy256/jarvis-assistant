"""話者認識。はっしぃの声だけに反応するため、声の特徴（声紋）を覚えて照合する。sherpa-onnx を使い、すべて PC の中で動く。

声紋は「ジャービス」と呼びかけたときの発言から自動で集める（呼びかけるのはほぼ本人のため）。
SPEAKER_MIN_SAMPLES 件たまるまでは照合せず、すべての声に反応する。
"""
import os
import threading

import numpy as np
import sherpa_onnx

from config import (DATA_DIR, SAMPLE_RATE, SPEAKER_MAX_SAMPLES, SPEAKER_MIN_SAMPLES, SPEAKER_MODEL,
                    SPEAKER_THRESHOLD)

PRINTS_FILE = os.path.join(DATA_DIR, "voiceprint.npy")
MIN_SEC = 1.0  # これより短い発言は声紋づくりにも照合にも使わない（判断がぶれる）


class SpeakerID:
    def __init__(self):
        config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=SPEAKER_MODEL, num_threads=4, provider="cpu")
        self.extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
        self.lock = threading.Lock()
        try:
            self.prints = list(np.load(PRINTS_FILE))
        except (OSError, ValueError):
            self.prints = []

    def _embed(self, pcm16):
        stream = self.extractor.create_stream()
        stream.accept_waveform(sample_rate=SAMPLE_RATE, waveform=pcm16.astype(np.float32) / 32768)
        stream.input_finished()
        e = np.array(self.extractor.compute(stream), dtype=np.float32)
        return e / (np.linalg.norm(e) + 1e-9)

    @property
    def ready(self):
        return len(self.prints) >= SPEAKER_MIN_SAMPLES

    def learn(self, pcm16):
        """本人の声として声紋に加える（古いものから入れ替える）。短すぎて使えなければ False。"""
        if len(pcm16) < SAMPLE_RATE * MIN_SEC:
            return False
        e = self._embed(pcm16)
        with self.lock:
            self.prints = (self.prints + [e])[-SPEAKER_MAX_SAMPLES:]
            os.makedirs(DATA_DIR, exist_ok=True)
            np.save(PRINTS_FILE, np.array(self.prints))
        return True

    def score(self, pcm16):
        """本人らしさ（-1〜1）。声紋が足りない、または発言が短すぎるときは None。"""
        if not self.ready or len(pcm16) < SAMPLE_RATE * MIN_SEC:
            return None
        e = self._embed(pcm16)
        with self.lock:
            sims = np.array(self.prints) @ e
        return float(np.mean(np.sort(sims)[-5:]))  # よく似た上位 5 件の平均

    def is_owner(self, pcm16):
        """本人の声なら True。判断できないとき（声紋が足りない・短すぎる）も True（反応しないより安全側）。"""
        s = self.score(pcm16)
        return True if s is None else s >= SPEAKER_THRESHOLD

    def reset(self):
        with self.lock:
            self.prints = []
            if os.path.exists(PRINTS_FILE):
                os.remove(PRINTS_FILE)
