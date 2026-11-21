"""ウェイクワード検出。Vosk を「ジャービス」と似た音の単語だけ聞き分けるモードで動かすので軽い。"""
import json

import numpy as np
from vosk import KaldiRecognizer, Model, SetLogLevel

from config import SAMPLE_RATE, VOSK_MODEL, WAKE_DISTRACTORS, WAKE_MIN_CONF, WAKE_WORD

MAX_SEGMENT_SEC = 20


class WakeDetector:
    def __init__(self):
        SetLogLevel(-1)
        self.model = Model(VOSK_MODEL)
        self.grammar = json.dumps([WAKE_WORD, *WAKE_DISTRACTORS, "[unk]"], ensure_ascii=False)
        self.reset()

    def reset(self):
        self.rec = KaldiRecognizer(self.model, SAMPLE_RATE, self.grammar)
        self.rec.SetWords(True)
        self.segment, self.fed, self.seg_start = [], 0, 0

    def feed(self, block):
        """呼びかけを検出したら、その後に続けて話した部分の音声を返す（なければ空配列）。それ以外は None。"""
        self.segment.append(block)
        self.fed += len(block)
        if len(self.segment) * len(block) > MAX_SEGMENT_SEC * SAMPLE_RATE:
            dropped = self.segment.pop(0)
            self.seg_start += len(dropped)
        if not self.rec.AcceptWaveform(block.tobytes()):
            return None
        words = json.loads(self.rec.Result()).get("result", [])
        segment, start = np.concatenate(self.segment), self.seg_start
        self.segment, self.seg_start = [], self.fed
        for w in words:
            if w["word"] == WAKE_WORD and w["conf"] >= WAKE_MIN_CONF:
                cut = max(0, int(w["end"] * SAMPLE_RATE) - start)
                return segment[cut:]
        return None
