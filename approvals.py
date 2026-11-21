"""取り返しのつかない操作の「確認待ち」と「承認済み」を記録する。guard.py（フック）とジャービス本体の両方が使う。

流れ: guard.py が操作を止めて pending に積む → ジャービスが読み上げて確認 →
ユーザーが声で承認したらジャービス本体が approved に移す → AI が同じ操作をやり直すと guard.py が通す。
承認は AI の自己申告ではなく、ジャービス本体が声を聞いて判断したときだけ記録される。
"""
import json
import os
import time

DATA_DIR = os.path.expandvars(r"%LOCALAPPDATA%\jarvis")
PENDING = os.path.join(DATA_DIR, "pending.json")
APPROVED = os.path.join(DATA_DIR, "approved.json")
APPROVAL_TTL_SEC = 300


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []


def _save(path, items):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False)


def add_pending(fingerprint, description):
    items = [p for p in _load(PENDING) if p["fp"] != fingerprint]
    _save(PENDING, items + [{"fp": fingerprint, "desc": description, "time": time.time()}])


def pending():
    return _load(PENDING)


def clear_pending():
    _save(PENDING, [])


def approve_pending():
    """確認待ちの操作をすべて承認する（ジャービス本体が「はい」を聞いたときだけ呼ぶ）。"""
    now = time.time()
    approved = [a for a in _load(APPROVED) if a["expires"] > now]
    approved += [{"fp": p["fp"], "expires": now + APPROVAL_TTL_SEC} for p in pending()]
    _save(APPROVED, approved)
    clear_pending()


def consume_approval(fingerprint):
    """承認済みならその承認を使い切って True。1回の承認で1回だけ通す。"""
    now = time.time()
    approved = [a for a in _load(APPROVED) if a["expires"] > now]
    hit = any(a["fp"] == fingerprint for a in approved)
    _save(APPROVED, [a for a in approved if a["fp"] != fingerprint])
    return hit


# ---- 保留リスト（開発モード用） ----
# 開発モード中、最低限の歯止めに当たった操作は、その場で確認せずにここへ積む。
# 作業は止めず、あとでユーザーが画面のボタンか声で承認する。承認されると approved に入り、AI が同じ操作をやり直せば通る。
HELD = os.path.join(DATA_DIR, "held.json")
HELD_APPROVAL_TTL_SEC = 3600


def add_held(fingerprint, description):
    items = [h for h in _load(HELD) if h["fp"] != fingerprint]
    _save(HELD, items + [{"fp": fingerprint, "desc": description, "time": time.time()}])


def held():
    return _load(HELD)


def clear_held():
    _save(HELD, [])


def approve_held(fingerprint=None):
    """保留を承認する（fingerprint を指定すればその 1 件、なければ全部）。承認した操作の説明の一覧を返す。
    画面のボタンか、ユーザーの声を聞いたジャービス本体だけが呼ぶ。"""
    now = time.time()
    items = held()
    chosen = [h for h in items if fingerprint in (None, h["fp"])]
    approved = [a for a in _load(APPROVED) if a["expires"] > now]
    approved += [{"fp": h["fp"], "expires": now + HELD_APPROVAL_TTL_SEC} for h in chosen]
    _save(APPROVED, approved)
    _save(HELD, [h for h in items if h not in chosen])
    return [h["desc"] for h in chosen]


def reject_held(fingerprint=None):
    """保留を却下する（指定がなければ全部）。"""
    _save(HELD, [h for h in held() if fingerprint not in (None, h["fp"])])
