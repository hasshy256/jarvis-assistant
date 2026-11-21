"""会話が終わるたびに、別の Claude（記憶係）が会話を振り返り、Obsidian の保管庫に残す内容を決める。

記憶係はツールを使わず JSON で答えるだけにして、書き込みは vault.py が行う（追記だけ。日時と AI 名の形がぶれない）。
会話中のジャービスとは別のプロセスで動かすので、次の呼びかけを待たせない。
"""
import json
import re
import subprocess
import threading
from datetime import datetime

import vault
from brain import find_claude
from config import DATA_DIR, MEMORY_MODEL

PROMPT = """あなたはジャービス（ユーザーに仕える AI 執事）の記憶係です。下の会話を読み、長期記憶に残す内容を JSON だけで出力してください。
ツールは使いません。説明やコードブロックの記号も書かず、次の形の JSON だけを出力します。

{{"log": "会話の要約", "log_links": ["関連するノート名"], "memories": [{{"note": "ノート名", "text": "覚える内容を一文で", "links": ["関連するノート名"]}}]}}

- log: 会話の要点を 1〜3 文で（誰が何を頼み、どうなったか）。
  ただし、時刻・天気・タイマー・アラーム・アプリの起動・音量・モードの切り替えといった定型のやりとりしかない会話なら、
  要約せず必ず null にする。定型のやりとりとそれ以外が混ざっているときは、定型の部分を除いて要約する
- 「来週の金曜」「明日」のような言い方は、会話の日時から計算して「10月23日（金）」のような日付にして書く
- memories: 今後も役立つことだけ。ユーザー本人のこと（プロフィール、好み、習慣、学校や仕事）、人物、予定や約束、決めたこと。
  なければ []。下の「今の記憶」にすでにある内容は書かない。その場限りの話、雑談、パスワードやカード番号、住所の番地などの機密は書かない
- note: 今あるノート（{notes}）から合うものを選ぶ。合うものがなければ短い名前で新しく作る（人物ならその人の名前、例: 佐々木さん）
- log_links・links: 内容に関係する今あるノート名や人物名。なければ []

今の記憶:
{memories}

会話（{date}）:
{conversation}"""

_lock = threading.Lock()  # 記憶係が同時に 2 つ動かないように


def remember_async(turns, log):
    """turns: [(ユーザーの発言, ジャービスの返事), ...]"""
    if turns:
        threading.Thread(target=_remember, args=(list(turns), log), daemon=True).start()


def parse(result):
    """記憶係の出力から JSON を取り出す（前後に余計な文やコードブロックがあっても読む）。"""
    m = re.search(r"\{.*\}", result, re.S)
    return json.loads(m.group(0)) if m else None


def save(data, when=None):
    """記憶係の答えを保管庫に書き込み、何をしたかの一言を返す。"""
    when = when or datetime.now()
    done = []
    if data.get("log"):
        vault.append_log(data["log"], data.get("log_links") or [], when)
        done.append("会話ログ1件")
    added = sum(vault.add_memory(m["note"], m["text"], m.get("links") or [], when)
                for m in data.get("memories") or [] if m.get("note") and m.get("text"))
    if added:
        done.append(f"記憶{added}件")
    return "、".join(done) + "を追加" if done else "残すものなし"


def _remember(turns, log):
    when = datetime.now()
    conversation = "\n".join(f"ユーザー: {u}\nジャービス: {j}" for u, j in turns)
    with _lock:
        try:
            vault.ensure()
            prompt = PROMPT.format(notes="、".join(vault.memory_notes()) or "まだない",
                                   memories=vault.memories_text() or "（まだない）",
                                   date=f"{when:%Y-%m-%d %H:%M}", conversation=conversation)
            p = subprocess.run([find_claude(), "-p", prompt, "--output-format", "json", "--model", MEMORY_MODEL,
                                "--tools", ""],
                               cwd=DATA_DIR, capture_output=True, text=True, encoding="utf-8",
                               creationflags=subprocess.CREATE_NO_WINDOW)
            data = parse(json.loads(p.stdout).get("result", ""))
            log(f"[記憶] {save(data, when) if data else '答えを読めませんでした'}")
        except Exception as e:
            log(f"[記憶] 更新に失敗: {e} {getattr(locals().get('p'), 'stderr', '')[:200]}")
