"""開発モード。入っている間だけ、ガードが止める操作を確認なしで通す（guard.py が active を見る）。

入れるのはボタン・ホットキー・ユーザーの声だけ（AI が自分でオンにすることはできない）。
- 再起動をまたぐ: 実装や確認のための再起動（restart_self）の直前に状態を保存し、起動時に読み戻す。
  再起動でない起動（PC の再起動後や、落ちたあとの起動）は、時間指定（until）が残っているときだけ戻る。
- 時間指定: 「7時まで作業してOK」のように期限を決めると、その間は自動オフ（操作がない時間）を止め、期限になったら終える。
- 保存ファイル（dev_state.json）は guard.py が書き換えを止める（AI が書いて自分で入る抜け道をふさぐ）。
"""
import json
import os
import re
import threading
import time
import unicodedata
from datetime import datetime, timedelta

from config import DEV_AUTO_OFF_HOURS, DEV_GUARD_LOG, DEV_RESUME_SEC, DEV_STATE_FILE, LOG_DIR

_lock = threading.Lock()
active = False
since = 0.0         # 入った時刻（epoch 秒）
last_touch = 0.0    # 最後に操作があった時刻（自動で切るための目安）
until = 0.0         # 時間指定（epoch 秒）。0 なら指定なし
events = []         # 確認なしで通した操作 [{"t": "HH:MM:SS", "desc": 説明}]（新しいものが後ろ）
MAX_EVENTS = 30
MAX_HOLD_HOURS = 24


# ---- 入り切り ----
def enable():
    global active, since, last_touch, until
    with _lock:
        active, since, last_touch, until = True, time.time(), time.time(), 0.0
        events.clear()
    _write("開発モード: 開始")


def disable():
    global active, until
    with _lock:
        was, active, until = active, False, 0.0
    _clear_state()
    if was:
        _write("開発モード: 終了")


def touch():
    """操作があったことを知らせる（自動で切るまでの時間をリセット）。"""
    global last_touch
    last_touch = time.time()


def expired():
    """終わらせる時刻になっていれば、理由を返す（"until" = 時間指定の期限 / "idle" = 操作のない時間）。まだなら ""。"""
    if not active:
        return ""
    if until:  # 時間指定中は、操作がなくても続け、期限が来たら終える
        return "until" if time.time() >= until else ""
    if DEV_AUTO_OFF_HOURS and time.time() - last_touch > DEV_AUTO_OFF_HOURS * 3600:
        return "idle"
    return ""


def set_until(ts):
    """時間指定をつける（epoch 秒）。0 で外す（ふつうの自動オフに戻る）。PC を再起動しても続くよう、保存する。"""
    global until
    until = float(ts or 0)
    touch()
    _write(f"時間指定: {datetime.fromtimestamp(until):%m/%d %H:%M} まで" if until else "時間指定: 解除")
    _save_state(restart=False)


def record_skip(desc):
    """ガードが止めるはずだった操作を、確認なしで通したと記録する。"""
    touch()
    with _lock:
        events.append({"t": datetime.now().strftime("%H:%M:%S"), "desc": desc[:140]})
        del events[:-MAX_EVENTS]
    _write("確認を省略: " + desc[:300])


def snapshot():
    with _lock:
        return {"active": active, "since": since, "until": until, "events": list(events)}


# ---- 再起動をまたぐ ----
def _save_state(restart):
    if not active:
        _clear_state()
        return
    try:
        os.makedirs(os.path.dirname(DEV_STATE_FILE), exist_ok=True)
        with open(DEV_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"active": True, "since": since, "until": until, "saved": time.time(), "restart": restart}, f)
    except OSError:
        pass


def _clear_state():
    try:
        os.remove(DEV_STATE_FILE)
    except OSError:
        pass


def save_for_restart():
    """再起動の直前に呼ぶ。開発モード中なら状態を保存（次の起動が DEV_RESUME_SEC 以内なら戻る）、そうでなければ保存ファイルを消す。
    消すことで、開発モードでないときに誰かが書いた保存ファイルで入れてしまうのを防ぐ。"""
    _save_state(restart=True)


def resume():
    """起動時に呼ぶ。保存された状態から開発モードに戻れたら "restart"（再起動で続けた）か "until"（時間指定が残っていた）、戻らなければ ""。"""
    global active, since, last_touch, until
    try:
        with open(DEV_STATE_FILE, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        return ""
    now = time.time()
    saved_until = float(st.get("until") or 0)
    if not st.get("active"):
        kind = ""
    elif saved_until > now:
        kind = "until"
    elif st.get("restart") and now - float(st.get("saved") or 0) <= DEV_RESUME_SEC:
        kind = "restart"
    else:
        kind = ""
    if not kind:
        _clear_state()
        return ""
    with _lock:
        active, since, last_touch = True, float(st.get("since") or now), now
        until = saved_until if saved_until > now else 0.0
    _write(f"開発モード: 再開（{'再起動から' if kind == 'restart' else '時間指定が残っていたため'}）")
    _clear_state() if kind == "restart" and not until else _save_state(restart=False)
    return kind


# ---- 声の時間指定 ----
_ABS = re.compile(r"(午前|午後|朝|夜|晩)?(\d{1,2})時(半|(\d{1,2})分)?(まで|ごろまで|頃まで)(?!に)")
_DUR = re.compile(r"(\d{1,2})時間(半|(\d{1,2})分)?")
_MIN = re.compile(r"(?<!\d)(\d{1,3})分(間|だけ|くらい|ほど)?(?!前|後)")
_EXPLICIT = re.compile(r"開発|作業")
_SECONDARY = re.compile(r"続け|維持|任せ|やって|いい|OK|ok|大丈夫|よろしく")
_CLEAR = re.compile(r"時間(指定)?(を)?(解除|やめ|なし|取り消|キャンセル)|自動(オフ|で切れる)?に戻")


def _label(dt, now):
    h12 = dt.hour % 12 or 12
    day = "" if dt.date() == now.date() else ("明日の" if dt.date() == (now + timedelta(days=1)).date() else f"{dt.month}月{dt.day}日の")
    return f"{day}{'午前' if dt.hour < 12 else '午後'}{h12}時" + (f"{dt.minute}分" if dt.minute else "")


def parse_until(text, now=None):
    """「7時まで」「午後3時半まで」「2時間」「30分」などから、期限 (datetime, 読み上げ用の言い方) を返す。読み取れなければ None。"""
    now = now or datetime.now()
    t = re.sub(r"[、。,.\s]", "", unicodedata.normalize("NFKC", text))
    m = _ABS.search(t)
    if m:
        period, hour = m.group(1), int(m.group(2))
        minute = 30 if m.group(3) == "半" else int(m.group(4) or 0)
        if hour > 24 or minute > 59:
            return None
        if period in ("午後", "夜", "晩"):
            hours = [hour + 12 if hour < 12 else hour]
        elif period in ("午前", "朝"):
            hours = [hour % 12 if hour == 12 else hour]
        elif hour >= 13:
            hours = [hour]
        else:
            hours = [hour % 12, hour % 12 + 12]  # 午前か午後か言われなければ、いちばん近い未来のほう
        cands = []
        for h in hours:
            dt = now.replace(hour=h % 24, minute=minute, second=0, microsecond=0)
            if h >= 24 or dt <= now + timedelta(minutes=1):
                dt += timedelta(days=1)
            cands.append(dt)
        dt = min(cands)
        return dt, _label(dt, now)
    mins = None
    m = _DUR.search(t)
    if m:
        mins = int(m.group(1)) * 60 + (30 if m.group(2) == "半" else int(m.group(3) or 0))
        label = f"{m.group(1)}時間" + ("半" if m.group(2) == "半" else f"{m.group(3)}分" if m.group(3) else "")
    else:
        m = _MIN.search(t)
        if m:
            mins, label = int(m.group(1)), f"{m.group(1)}分"
    if not mins or mins > MAX_HOLD_HOURS * 60:
        return None
    dt = now + timedelta(minutes=mins)
    return dt, f"{_label(dt, now)}（今から{label}）"


def until_command(text, now=None):
    """開発モードの時間指定の頼みなら ("set", 期限の epoch 秒, 言い方)、解除なら ("clear", 0, "")、どちらでもなければ None。
    「開発」「作業」の語があれば、開発モードでなくても入る。「続けて」「いいよ」だけの言い方は、開発モード中のときだけ受ける。"""
    plain = unicodedata.normalize("NFKC", text)
    if len(plain) > 60:
        return None
    if active and _CLEAR.search(plain):
        return "clear", 0.0, ""
    if not (_EXPLICIT.search(plain) or (active and _SECONDARY.search(plain))):
        return None
    parsed = parse_until(plain, now)
    if not parsed:
        return None
    dt, label = parsed
    return "set", dt.timestamp(), label


def _write(msg):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(DEV_GUARD_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
    except OSError:
        pass
