"""マイク入力。常に 0.1 秒ずつキューに積み、呼び出し側が必要な分だけ取り出す。"""
import queue

import numpy as np
import sounddevice as sd

import mute
from config import BLOCK_SEC, MAX_UTTERANCE_SEC, MIC_NAME, SAMPLE_RATE, SILENCE_SEC


def find_mic():
    """MIC_NAME を含む入力デバイスの番号。同じマイクがドライバ方式ごとに並ぶので MME のものを選ぶ。"""
    if not MIC_NAME:
        return None
    for i, d in enumerate(sd.query_devices()):
        if (d["max_input_channels"] > 0 and MIC_NAME.lower() in d["name"].lower()
                and sd.query_hostapis(d["hostapi"])["name"] == "MME"):
            return i
    print(f"マイク「{MIC_NAME}」が見つからないので既定のマイクを使います", flush=True)
    return None


def rms(block):
    return float(np.sqrt(np.mean((block.astype(np.float32) / 32768) ** 2)))


class Mic:
    def __init__(self):
        self.q = queue.Queue()
        self.noise = 0.005
        self.on_level = None  # 音量をダッシュボードに渡すためのフック
        self.stream = sd.InputStream(device=find_mic(), samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                                     blocksize=int(SAMPLE_RATE * BLOCK_SEC), callback=self._cb)

    def _cb(self, indata, frames, t, status):
        self.q.put(indata[:, 0].copy())

    def start(self):
        self.stream.start()

    def read(self, timeout=None, raw=False):
        """raw=True はミュート中でも本当の音を返す（「マイクオン」の聞き分けだけに使う）。"""
        block = self.q.get(timeout=timeout)
        if mute.is_muted("mic") and not raw:  # ミュート中は無音として扱う（呼びかけも発言も届かない）
            block = np.zeros_like(block)
        if self.on_level:
            self.on_level(rms(block))
        return block

    def drain(self):
        """自分の声を拾わないよう、話している間に溜まった音を捨てる。"""
        while True:
            try:
                self.q.get_nowait()
            except queue.Empty:
                return

    def track_noise(self, block):
        level = rms(block)
        if level < self.threshold:
            self.noise = self.noise * 0.95 + level * 0.05

    @property
    def threshold(self):
        return max(self.noise * 3, 0.008)

    def record_utterance(self, wait_sec, raw=False, abort=None):
        """話し始めるのを wait_sec 秒まで待ち、黙ったら止める。何も話さなければ None。
        abort() が True を返したら、話し始める前ならすぐ None を返す（入力欄から文が送られたときなど）。"""
        preroll, chunks, silent, waited = [], [], 0.0, 0.0
        while True:
            if abort is not None and not chunks and abort():
                return None
            try:
                block = self.read(timeout=1, raw=raw)
            except queue.Empty:
                continue
            loud = rms(block) > self.threshold
            if not chunks:
                if loud:
                    chunks = preroll + [block]
                    continue
                preroll = (preroll + [block])[-3:]
                waited += BLOCK_SEC
                if waited >= wait_sec:
                    return None
                continue
            chunks.append(block)
            silent = 0.0 if loud else silent + BLOCK_SEC
            if silent >= SILENCE_SEC or len(chunks) * BLOCK_SEC >= MAX_UTTERANCE_SEC:
                return np.concatenate(chunks)


def play_sound(samples, blocking=False):
    """効果音を鳴らす（スピーカーのミュート中は鳴らさない）。"""
    if not mute.is_muted("speaker"):
        sd.play(samples, SAMPLE_RATE, blocking=blocking)


def beep(freq=880, ms=260):
    """合図の「ピッ」。ワイヤレスのヘッドセットは鳴り始めの音を取りこぼすので、前に短い無音を入れ、長めにする。"""
    t = np.linspace(0, ms / 1000, int(SAMPLE_RATE * ms / 1000), False)
    tone = np.sin(2 * np.pi * freq * t) + 0.3 * np.sin(2 * np.pi * freq * 2 * t)  # 倍音を足して聞き取りやすく
    fade = np.minimum(1, np.minimum(t, t[-1] - t) / 0.02)  # 端をなめらかにして「プツッ」という音を防ぐ
    lead = np.zeros(int(SAMPLE_RATE * 0.18))
    play_sound(np.concatenate([lead, 0.3 * tone * fade]).astype(np.float32), blocking=True)
