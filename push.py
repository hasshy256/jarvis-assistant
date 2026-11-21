"""iPhone への通知（無料アプリ ntfy）。トピック名は DATA_DIR の push_topic.txt から読む（無ければ何もしない）。

使い方: import push; push.send("タイトル", "本文")   失敗しても例外は投げない（本体の動きを止めないため）
"""
import os

import requests

from config import NTFY_SERVER, NTFY_TOPIC_FILE


def topic():
    try:
        with open(NTFY_TOPIC_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def send(title, message, click=None):
    """送れたら True。トピック未設定・通信失敗は False。本文は長いと切る。"""
    name = topic()
    if not name:
        return False
    body = {"topic": name, "title": title[:60], "message": message[:400], "priority": 3}
    if click:
        body["click"] = click
    try:
        r = requests.post(NTFY_SERVER, json=body, timeout=10)
        return r.ok
    except requests.RequestException:
        return False
