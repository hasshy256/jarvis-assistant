"""タイマーとアラーム。「3分のタイマー」「7時に起こして」などの決まった言い方は Claude を通さずにすぐセットする。

セットしたものは reminders.json（お知らせと同じファイル）に kind="timer" / "alarm" として入る。
複雑な頼み方は Claude が同じ形式で書き込む。鳴らすのは jarvis.py。
"""
import json
import re
from datetime import datetime, timedelta

from config import REMINDERS_FILE

KANJI = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
NUM = r"(\d+|[一二三四五六七八九十]+)"


def to_int(s):
    if s.isdigit():
        return int(s)
    if "十" in s:  # 十, 十五, 二十, 二十五
        tens, _, ones = s.partition("十")
        return KANJI.get(tens, 1) * 10 + KANJI.get(ones, 0)
    return KANJI.get(s, 0)


def parse_duration(text):
    """「1時間半」「3分」「90秒」「5分30秒」→ 秒数。見つからなければ None。"""
    total, found = 0, False
    if m := re.search(NUM + r"\s*時間(半)?", text):
        total += to_int(m.group(1)) * 3600 + (1800 if m.group(2) else 0)
        found = True
    if m := re.search(NUM + r"\s*分(半)?(?!後に起)", text):
        total += to_int(m.group(1)) * 60 + (30 if m.group(2) else 0)
        found = True
    if m := re.search(NUM + r"\s*秒", text):
        total += to_int(m.group(1))
        found = True
    return total if found and total > 0 else None


def parse_clock(text, now, wake_up=False):
    """「午後3時半」「明日の朝7時」「7時15分」→ 次に来るその時刻。見つからなければ None。"""
    m = re.search(r"(午前|午後|朝|夜|夕方|昼)?\s*" + NUM + r"\s*時\s*(半|" + NUM + r"\s*分)?", text)
    if not m or "時間" in text[m.start():m.end() + 1]:
        return None
    ampm, hour = m.group(1), to_int(m.group(2))
    minute = 30 if m.group(3) == "半" else to_int(m.group(4)) if m.group(4) else 0
    if hour > 23 or minute > 59:
        return None
    if ampm in ("午後", "夜", "夕方") and hour < 12:
        hour += 12
    base = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if "明日" in text:
        return base + timedelta(days=1)
    if base > now:
        return base
    # 時刻を過ぎていたら、午前/午後の指定がなく「起こして」でもなければ 12 時間後（13 時に「3時」→ 15 時）を先に試す
    if not ampm and not wake_up and hour < 12 and base + timedelta(hours=12) > now:
        return base + timedelta(hours=12)
    return base + timedelta(days=1)


def handle(text, now=None):
    """決まった言い方なら (kind, 値) を返す。Claude に任せるべきなら None。
    ("timer", 終了時刻) / ("alarm", 鳴らす時刻) / ("cancel", "timer" | "alarm") / ("status", None)"""
    now = now or datetime.now()
    plain = re.sub(r"[、。,.!！?？\s]", "", text)
    kind = "timer" if "タイマー" in plain else "alarm" if re.search("アラーム|目覚まし", plain) else None
    if kind and re.search("止め|とめ|消し|けし|キャンセル|解除|やめ|取り消", plain):
        return ("cancel", kind)
    if kind == "timer" and re.search("あと|残り|何分", plain):
        return ("status", None)
    wake_up = "起こして" in plain
    if kind == "alarm" or wake_up:
        at = parse_clock(plain, now, wake_up)
        return ("alarm", at) if at else None
    if kind == "timer" or re.search(r"(分|秒|時間)(後|経ったら|たったら)(に)?(教えて|知らせて|呼んで|鳴らして)", plain):
        seconds = parse_duration(plain)
        return ("timer", now + timedelta(seconds=seconds)) if seconds else None
    return None


def load():
    try:
        with open(REMINDERS_FILE, encoding="utf-8-sig") as f:
            items = json.load(f)
        return items if isinstance(items, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save(items):
    with open(REMINDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def add(kind, at, title):
    items = load()
    items.append({"kind": kind, "title": title, "start": at.strftime("%Y-%m-%dT%H:%M:%S"), "before_min": 0})
    save(items)


def cancel(kind):
    items = load()
    kept = [i for i in items if i.get("kind") != kind]
    save(kept)
    return len(items) - len(kept)


def active(kind=None):
    """これから鳴るタイマー・アラーム（早い順）。"""
    now = datetime.now()
    out = []
    for i in load():
        if i.get("kind") in ("timer", "alarm") and (kind is None or i["kind"] == kind):
            at = parse_start(i.get("start", ""))
            if at and at > now:
                out.append((at, i))
    return sorted(out, key=lambda x: x[0])


def parse_start(s):
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except (TypeError, ValueError):
            pass
    return None


def describe_duration(seconds):
    seconds = int(round(seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    parts = [f"{h}時間" if h else "", f"{m}分" if m else "", f"{s}秒" if s and not h else ""]
    return "".join(parts) or "0秒"
