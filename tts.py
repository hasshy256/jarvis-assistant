"""音声合成。edge-tts（自然な日本語）で作った mp3 を pygame で再生。オフライン時は Windows 標準の声。

SpeechStream は Claude の返事を少しずつ受け取り、1 文できるたびに音声を作って順に読み上げる。
1 文目を話している間に 2 文目以降の音声を作っておくので、全文を待たずに話し始められる。
"""
import asyncio
import glob
import hashlib
import os
import queue
import re
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import edge_tts
import pygame

import mute
import voicevox
from config import TTS_ENGINE, VOICE, VOICE_RATE, VOICE_PITCH

SENTENCE_END = re.compile(r"(?<=[。！？!?\n])")
NOT_SPOKEN = re.compile(r"[*#`>|_~]+")  # 読み上げると変なマークダウン記号
SKIP_LINE = re.compile(r"https?://|^\s*(sources?|出典|参考)\s*[:：]", re.I)  # Web 検索の出典など


def speakable(text):
    """画面や読み上げに出す本文（URL や出典の行を除く）。"""
    return "\n".join(line for line in text.splitlines() if not SKIP_LINE.search(line)).strip()


def synthesize(text):
    """読み上げる音声ファイルを作ってパスを返す（PC の声のジャービスと、iPhone の会話ボタンで共通）。"""
    if TTS_ENGINE == "voicevox":
        try:
            return voicevox.synthesize(text)
        except Exception:
            pass  # エンジンが動いていなければ、いつもの声で話す
    name = hashlib.md5(f"{VOICE}{VOICE_RATE}{VOICE_PITCH}{text}".encode()).hexdigest()
    path = os.path.join(tempfile.gettempdir(), f"jarvis_{name}.mp3")
    if not os.path.exists(path):
        asyncio.run(edge_tts.Communicate(text, VOICE, rate=VOICE_RATE, pitch=VOICE_PITCH).save(path))
    return path


class Voice:
    def __init__(self):
        pygame.mixer.init()
        # 返事ごとに音声ファイルが溜まるので、1 週間より古いものは起動時に片付ける
        cutoff = time.time() - 7 * 86400
        for path in glob.glob(os.path.join(tempfile.gettempdir(), "jarvis_*.*")):
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
            except OSError:
                pass
        if TTS_ENGINE == "voicevox":  # エンジンを起動し、声のモデルを先に読み込んでおく（裏で）
            threading.Thread(target=voicevox.warmup, daemon=True).start()

    def prepare(self, text):
        return synthesize(text)

    def fallback_say(self, text):
        if mute.is_muted("speaker"):
            return
        ps = ("Add-Type -AssemblyName System.Speech; "
              "(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak([Console]::In.ReadToEnd())")
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], input=text, text=True,
                       encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW)

    def say(self, text, wait=True, stop_event=None):
        try:
            path = self.prepare(text)
        except Exception:
            self.fallback_say(text)
            return
        self.play(path)
        if wait:
            self.wait(stop_event)

    def play(self, path):
        if mute.is_muted("speaker"):  # ミュート中は鳴らさない（返事は会話ログに文字で出る）
            return
        pygame.mixer.music.load(path)
        pygame.mixer.music.play()

    def stop(self):
        pygame.mixer.music.stop()

    def busy(self):
        return pygame.mixer.music.get_busy()

    def wait(self, stop_event=None):
        while pygame.mixer.music.get_busy():
            if stop_event is not None and stop_event.is_set():
                stop_event.clear()
                self.stop()
                return
            time.sleep(0.05)

    def stream(self, on_start=None, on_sentence=None):
        return SpeechStream(self, on_start, on_sentence)


class SpeechStream:
    def __init__(self, voice, on_start=None, on_sentence=None):
        # on_sentence: 文が 1 つできるたびに（音声づくりを始めると同時に）呼ばれる。話す内容を、声より先に画面へ出すために使う
        self.voice, self.on_start, self.on_sentence = voice, on_start, on_sentence
        self.buf = ""
        self.audio = queue.Queue()  # (文, 音声ファイルの Future)。None で終わり
        # 1 本ずつ作る。同時に作ると 1 文目も遅くなる。2 文目以降は 1 文目を話している間にできあがる
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.stopped = threading.Event()
        self.started_at = None
        self.player = threading.Thread(target=self._play_all, daemon=True)
        self.player.start()

    def feed(self, text):
        """返事の断片を受け取り、文の区切りまで来たら音声づくりを始める。"""
        self.buf += text
        *done, self.buf = SENTENCE_END.split(self.buf)
        for sentence in done:
            self._enqueue(sentence)

    def _enqueue(self, sentence):
        sentence = NOT_SPOKEN.sub("", speakable(sentence)).strip()
        if sentence and not self.stopped.is_set():
            if self.on_sentence:  # 声を作る前に、まず画面へ（次の文が遅れても、読み上げる文は先に見える）
                try:
                    self.on_sentence(sentence)
                except Exception:
                    pass
            self.audio.put((sentence, self.pool.submit(self.voice.prepare, sentence)))

    def _play_all(self):
        while True:
            item = self.audio.get()
            if item is None or self.stopped.is_set():
                return
            sentence, future = item
            try:
                path = future.result()
            except Exception:
                path = None
            if self.started_at is None:
                self.started_at = time.time()
                if self.on_start:
                    self.on_start()
            if path is None:
                self.voice.fallback_say(sentence)
                continue
            self.voice.play(path)
            while pygame.mixer.music.get_busy() and not self.stopped.is_set():
                time.sleep(0.03)

    def finish(self, stop_event=None, clear=True):
        """残りを読み上げ終わるまで待つ。stop_event が立ったら途中で止める（clear=False なら立てたまま残す）。"""
        self._enqueue(self.buf)
        self.buf = ""
        self.audio.put(None)
        while self.player.is_alive():
            if stop_event is not None and stop_event.is_set():
                if clear:
                    stop_event.clear()
                self.stopped.set()
                self.voice.stop()
            time.sleep(0.05)
        self.pool.shutdown(wait=False)
