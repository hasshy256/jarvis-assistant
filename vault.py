"""Obsidian の保管庫を長期記憶として使う。読み書きするのは保管庫の中の Jarvis フォルダだけ。

Jarvis/
  README.md   フォルダ構成・書き方・保存ルール（ほかの AI も同じルールで読み書きできるように）
  記憶/        はっしぃについて覚えておくこと（ノートごとに一行ずつ追記）
  会話ログ/    日付ごとの会話の要約（2026-10-18.md）
  メモ/        「メモして」と言われた内容（1 件 1 ファイル）

ここから書くのは「新しく作る」「末尾に足す」だけ。書き換えや削除は、Claude がガードの確認を通してから行う。
"""
import glob
import os
import re
import threading
from datetime import datetime

from config import (OBSIDIAN_VAULT, VAULT_AI_NAME, VAULT_CONTEXT_CHARS, VAULT_FOLDER,
                    VAULT_RECENT_LOG_CHARS)

ROOT = os.path.join(OBSIDIAN_VAULT, VAULT_FOLDER)
MEMORY_DIR = os.path.join(ROOT, "記憶")
LOG_DIR = os.path.join(ROOT, "会話ログ")
MEMO_DIR = os.path.join(ROOT, "メモ")
README = os.path.join(ROOT, "README.md")

_lock = threading.Lock()  # 本体・記憶係・iPhone のリモコンが同時に同じファイルへ書かないように

README_TEXT = """# Jarvis フォルダ

音声アシスタント「ジャービス」の長期記憶です。ジャービス（中身は AI）がこのフォルダを読み書きします。
別の AI がこのフォルダを使うときも、下のルールに従って読み書きしてください。

## フォルダ構成

| フォルダ | 中身 | ファイル名 |
|---|---|---|
| `記憶/` | ユーザー（はっしぃ）について覚えておくこと。プロフィール、好み、人物、決めたことなど | 話題ごとのノート（例: `好み.md`、人物は `佐々木さん.md`） |
| `会話ログ/` | 日付ごとの会話の要約 | `YYYY-MM-DD.md`（例: `2026-10-18.md`） |
| `メモ/` | 「メモして」と頼まれた内容 | `YYYY-MM-DD HHMM 見出し.md`（1 件 1 ファイル） |

## 書き方

- 文字コードは UTF-8。普通の Markdown だけで書く（特定の AI やプラグイン専用の書き方はしない）
- 関連するノートには `[[ノート名]]` の形でリンクを付ける（Obsidian のグラフでつながりが見える）
- 書き込むときは、日時と書き込んだ AI の名前を必ず残す

### 記憶/（例: `好み.md`）

```
# 好み

- 話す速さは速めが好み（2026-10-18 15:30 Claude）
- 映画はマーベル作品をよく観る [[関心ごと]]（2026-10-18 15:30 Claude）
```

- 1 つの事柄を 1 行（`- ` で始める）にして末尾に追記する。行の最後に `（YYYY-MM-DD HH:MM AI名）`
- 同じ内容がすでにあれば書かない
- ノートが合わなければ新しいノートを作ってよい（見出しは `# ノート名`）

### 会話ログ/（例: `2026-10-18.md`）

```
# 2026-10-18 の会話ログ

## 15:30（Claude）
予定の登録を頼まれ、10/30 に歯医者を登録した。お知らせは 30 分前。
関連: [[決めたこと]]
```

- 会話が 1 つ終わるごとに `## HH:MM（AI名）` の見出しで要約（1〜3 文）を末尾に追記する
- 時刻・天気・タイマー・アプリ起動など、定型の操作だけの会話は残さない

### メモ/（例: `2026-10-18 1530 牛乳を買う.md`）

```
# 牛乳を買う

牛乳を買う

- 作成: 2026-10-18 15:30（Claude）
- 日付: [[2026-10-18]]
```

## 保存ルール

- このフォルダ（Jarvis）の外は読み書きしない
- 追記と新しいノートの作成は自由に行ってよい
- ファイルの削除、既存の内容の書き換え・まとめ直しなどの大きな変更は、必ずユーザーに確認してから行う
- パスワード、カード番号、住所の番地などの機密は書かない
"""


def ensure():
    """フォルダと README.md がなければ作る（あるものには触らない）。"""
    for d in (MEMORY_DIR, LOG_DIR, MEMO_DIR):
        os.makedirs(d, exist_ok=True)
    if not os.path.exists(README):
        with open(README, "w", encoding="utf-8") as f:
            f.write(README_TEXT)


def is_inside(path):
    """Jarvis フォルダの中のパスか。"""
    root = os.path.normcase(os.path.abspath(ROOT))
    return os.path.normcase(os.path.abspath(path)).startswith(root + os.sep)


def safe_name(name, limit=40):
    """ファイル名に使えない文字や、Obsidian のリンクを壊す文字を除く。"""
    name = re.sub(r'[\\/:*?"<>|#^\[\]\r\n\t]', "", name).strip(" .")
    return name[:limit] or "無題"


def _path(folder, name):
    path = os.path.join(folder, safe_name(name) + ".md")
    if not is_inside(path):  # 念のため（safe_name で区切り文字は消している）
        raise ValueError(f"Jarvis フォルダの外には書けません: {path}")
    return path


def _append(path, text, header):
    with _lock:
        ensure()
        new = not os.path.exists(path)
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            if new:
                f.write(header)
            f.write(text)


def _links(names):
    return " ".join(f"[[{safe_name(n)}]]" for n in names if n and n.strip())


# ---- メモ ----
MEMO_AFTER = re.compile(r"^(?P<body>.+?)(を|って|と|は)?(メモ|めも)(して|しといて|しておいて|しとい?て|を?とって|を?取って|お願い|おねがい"
                        r"|よろしく|しといてくれる|してくれる|してください)?(ください|くれ|ね|よ)?$")
MEMO_BEFORE = re.compile(r"^(メモ|めも)(して|を?取って|をお願い)[、,:：]?(?P<body>.+)$")


def memo_command(text):
    """「○○をメモして」「メモして、○○」なら ○○ を返す。メモの頼みでなければ None。"""
    plain = re.sub(r"[。．！!?？\s]+$", "", text.strip())
    m = MEMO_BEFORE.match(plain) or MEMO_AFTER.match(plain)
    if not m:
        return None
    body = m.group("body").strip(" 、,。")
    if not body or re.fullmatch(r"(これ|それ|今の|いまの|ちょっと|今から|いまから)", body):
        return None  # 中身が分からない頼みは Claude に聞き返してもらう
    return body


def save_memo(body, when=None, ai=VAULT_AI_NAME):
    """メモ/ に 1 件 1 ファイルで保存し、見出しを返す。"""
    when = when or datetime.now()
    title = safe_name(re.split(r"[。\n]", body)[0], 25)
    base = f"{when:%Y-%m-%d %H%M} {title}"
    path, n = _path(MEMO_DIR, base), 2
    while os.path.exists(path):  # 同じ分に同じ見出しのメモが来たら番号を付ける
        path, n = _path(MEMO_DIR, f"{base} {n}"), n + 1
    _append(path, f"{body}\n\n- 作成: {when:%Y-%m-%d %H:%M}（{ai}）\n- 日付: [[{when:%Y-%m-%d}]]\n", f"# {title}\n\n")
    return title


# ---- 会話ログ・記憶（追記だけ） ----
def append_log(summary, links=(), when=None, ai=VAULT_AI_NAME):
    when = when or datetime.now()
    path = _path(LOG_DIR, f"{when:%Y-%m-%d}")
    related = _links(links)
    text = f"\n## {when:%H:%M}（{ai}）\n{summary.strip()}\n" + (f"関連: {related}\n" if related else "")
    _append(path, text, f"# {when:%Y-%m-%d} の会話ログ\n")
    return path


def add_memory(note, text, links=(), when=None, ai=VAULT_AI_NAME):
    """記憶/<note>.md の末尾に一行足す。同じ内容がすでにあれば何もしない（False を返す）。"""
    when = when or datetime.now()
    path = _path(MEMORY_DIR, note)
    text = re.sub(r"\s+", " ", text).strip().lstrip("-・ ")
    if not text:
        return False
    try:
        with open(path, encoding="utf-8") as f:
            if text in f.read():
                return False
    except OSError:
        pass
    related = _links(n for n in links if safe_name(n) != safe_name(note))
    line = f"- {text}" + (f" {related}" if related else "") + f"（{when:%Y-%m-%d %H:%M} {ai}）\n"
    _append(path, line, f"# {safe_name(note)}\n\n")
    return True


def memory_notes():
    return sorted(os.path.splitext(os.path.basename(p))[0] for p in glob.glob(os.path.join(MEMORY_DIR, "*.md")))


def _read(path):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return f.read()
    except OSError:
        return ""


def memories_text(limit=VAULT_CONTEXT_CHARS):
    """記憶/ の全ノートをつなげた文。長すぎるときは新しく更新されたノートを優先する。"""
    paths = sorted(glob.glob(os.path.join(MEMORY_DIR, "*.md")), key=os.path.getmtime, reverse=True)
    parts, total = [], 0
    for p in paths:
        body = _read(p).strip()
        if total + len(body) > limit and parts:
            break
        parts.append(body[:limit])
        total += len(body)
    return "\n\n".join(parts)


def recent_logs_text(limit=VAULT_RECENT_LOG_CHARS):
    """直近の会話ログ（新しいほうから limit 文字ぶん）。"""
    text = ""
    for p in sorted(glob.glob(os.path.join(LOG_DIR, "*.md")), reverse=True)[:3]:
        text = _read(p).strip() + "\n\n" + text
        if len(text) >= limit:
            break
    return text[-limit:].strip()


def context_for_new_session():
    """新しい会話の最初に Claude へ渡す、記憶と最近の会話ログ。"""
    try:
        ensure()
        memories, logs = memories_text(), recent_logs_text()
    except OSError as e:
        return f"（長期記憶の保管庫 {ROOT} を開けませんでした: {e}）"
    return ("（ここからは自動で添えている長期記憶です。はっしぃの発言ではありません。"
            f"保管庫の Jarvis フォルダ: {ROOT}　ルールはその中の README.md）\n"
            f"## 記憶\n{memories or '（まだありません）'}\n\n## 最近の会話ログ\n{logs or '（まだありません）'}\n"
            "（長期記憶はここまで。以前の話の続きとして自然に応じてください）\n\n")


# ---- 検索（「○○って何だっけ」「前に話した○○」） ----
RECALL = [
    re.compile(r"前に(話した|はなした|話してた|言った|いった|言ってた|メモした|決めた|教えた|覚えてもらった)(?P<q>.*)"),
    re.compile(r"(?P<q>.+?)(って|て|は|が)?(何|なん|どこ|いつ|誰|だれ|どう)(だ|だった|でした)っけ"),
    re.compile(r"(?P<q>.+?)(について|のこと|の件)(覚えてる|おぼえてる|覚えてます|知ってる)"),
]
FILLER = re.compile(r"^(の|あの|その|例の|って|は|が|を)+|(の(こと|件|話)|について|って|は|が|を|やつ|こと|件|話|"
                    r"覚えてる|おぼえてる|何だっけ|なんだっけ|だっけ|教えて)+$")


def recall_query(text):
    """思い出す頼みなら検索する言葉を返す（言葉がなければ ""）。思い出す頼みでなければ None。"""
    plain = re.sub(r"[、。,.!！?？\s]", "", text)
    for pattern in RECALL:
        m = pattern.search(plain)
        if m:
            q = m.group("q")
            for _ in range(3):
                q = FILLER.sub("", q)
            return q
    return None


def keywords(query):
    words = [w for w in re.split(r"[\s、。・]+|について|って|こと|の|は|が|を|に|で|と|や", query) if len(w) >= 2]
    return list(dict.fromkeys(([query] if query else []) + words))


def search(query, limit=5):
    """Jarvis フォルダの中を探し、[(Jarvis からの相対パス, 当たった行), ...] を返す（よく当たったものから）。
    query が空なら新しく書いたものから返す。"""
    words = keywords(query)
    hits = []
    for path in glob.glob(os.path.join(ROOT, "**", "*.md"), recursive=True):
        if os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(README)):
            continue
        rel = os.path.relpath(path, ROOT)
        body = _read(path)
        haystack = (rel + "\n" + body).lower()
        score = sum(haystack.count(w.lower()) for w in words) + (5 * haystack.count(query.lower()) if query else 0)
        if words and not score:
            continue
        lines = [ln.strip() for ln in body.splitlines() if ln.strip() and not ln.startswith("# ")]
        matched = [ln for ln in lines if any(w.lower() in ln.lower() for w in words)]
        hits.append((score, os.path.getmtime(path), rel, (matched or lines)[:4]))  # ファイル名で当たったときは先頭の行
    hits.sort(key=lambda h: (h[0], h[1]), reverse=True)
    return [(rel, lines) for _, _, rel, lines in hits[:limit]]


def search_context(query):
    """検索結果を、Claude への依頼に添える文にする。"""
    found = search(query)
    label = f"「{query}」" if query else "最近のもの"
    if not found:
        return (f"（長期記憶の保管庫 {ROOT} を{label}で探しましたが見つかりませんでした。"
                "言い方を変えて Grep で探しても見つからなければ、記録にないと正直に伝えてください）\n")
    lines = "\n".join(f"- {rel}: " + " / ".join(ln[:150] for ln in body) for rel, body in found)
    return (f"（長期記憶の保管庫 {ROOT} を{label}で探した結果です。これをもとに答えてください。"
            f"足りなければこのフォルダの中を Grep・Read で探してください）\n{lines}\n\n")
