"""記憶の整理（月に一度、タスクスケジューラが動かす）。

「記憶/」の大きいノートから、終わった予定・取り消された方針・完了した作業の行を探し、
削除せずに「記憶アーカイブ/」へ移す（毎回の会話の始めに読み込む量を減らして、Claude の利用枠を節約する）。
判定は小さい Claude（MEMORY_MODEL）が行い、行番号だけを答える。書き換えは必ずバックアップを取ってから。

使い方: python memory_tidy.py            実行（大きいノートだけ）
        python memory_tidy.py --dry-run  何を移すかだけ表示（書き換えない）
        python memory_tidy.py --note 決めたこと --force   指定したノートを大きさに関係なく整理
"""
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime

import vault
from brain import find_claude
from config import DATA_DIR, MEMORY_MODEL

MIN_BYTES = 6000          # これより小さいノートは触らない
MAX_RATIO = 0.6           # 一度に移すのは行数の 6 割まで（判定の暴走への歯止め）
MIN_LINES = 15
ARCHIVE_DIR = os.path.join(vault.ROOT, "記憶アーカイブ")
BACKUP_DIR = os.path.join(DATA_DIR, "memory_backup")

PROMPT = """あなたはジャービス（ユーザーに仕える AI 執事）の記憶係です。今日は {today} です。
下は長期記憶のノート「{note}」の各行（行番号つき）です。毎回の会話の始めに全部読み込まれるので、読み込む量を減らしたいです。
「もう役に立たない行」の行番号だけを JSON で出力してください。説明やコードブロックの記号は書かず、次の形だけを出力します。

{{"archive": [3, 7, 12]}}

移してよい行:
- 過去の日付で終わった予定や出来事（もう過ぎた外出・授業・提出など）
- 後の行で取り消されたり、置き換えられたりした古い方針や情報
- 完了した作業の経過メモ（「承認待ち」だったが後で済んだもの、など）

移してはいけない行（迷ったら残す）:
- 今後も守る決まり・好み・習慣・プロフィール・人物の情報
- まだ終わっていない予定、未決定・承認待ちのこと
- 今も使う仕組みの説明や設定の事実

{lines}"""


def ask(note, lines):
    numbered = "\n".join(f"{i}: {l}" for i, l in enumerate(lines, 1))
    prompt = PROMPT.format(today=f"{datetime.now():%Y-%m-%d}", note=note, lines=numbered)
    p = subprocess.run([find_claude(), "-p", prompt, "--output-format", "json", "--model", MEMORY_MODEL, "--tools", ""],
                       cwd=DATA_DIR, capture_output=True, text=True, encoding="utf-8",
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    result = json.loads(p.stdout).get("result", "")
    m = re.search(r"\{.*\}", result, re.S)
    nums = json.loads(m.group(0)).get("archive", []) if m else []
    return sorted({n for n in nums if isinstance(n, int) and 1 <= n <= len(lines)})


def tidy(name, dry=False):
    path = os.path.join(vault.MEMORY_DIR, name + ".md")
    text = open(path, encoding="utf-8").read()
    lines = text.split("\n")
    # 先頭の見出しと空行は動かさない。移す対象は「- 」で始まる行だけ
    movable = [i for i, l in enumerate(lines, 1) if l.startswith("- ")]
    if len(movable) < MIN_LINES:
        return f"{name}: 行が少ないので何もしない"
    picked = [n for n in ask(name, lines) if n in set(movable)]
    if len(picked) > len(movable) * MAX_RATIO:
        return f"{name}: 移す行が多すぎる（{len(picked)}/{len(movable)}）ので中止"
    if not picked:
        return f"{name}: 移す行なし"
    if dry:
        return f"{name}: {len(picked)} 行を移す予定\n" + "\n".join("  " + lines[n - 1][:70] for n in picked)
    os.makedirs(BACKUP_DIR, exist_ok=True)
    shutil.copy2(path, os.path.join(BACKUP_DIR, f"{name}_{datetime.now():%Y%m%d_%H%M%S}.md"))
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    apath = os.path.join(ARCHIVE_DIR, f"{name}（過去分）.md")
    if not os.path.exists(apath):
        with open(apath, "w", encoding="utf-8", newline="\n") as f:
            f.write(f"# {name}（過去分）\n\n終わった予定・取り消された方針・完了した作業の記録。自動整理で「記憶/{name}」から移した。必要なときは Grep で探す。\n\n")
    with open(apath, "a", encoding="utf-8", newline="\n") as f:
        f.write("".join(lines[n - 1] + "\n" for n in picked))
    keep = [l for i, l in enumerate(lines, 1) if i not in set(picked)]
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(keep))
    return f"{name}: {len(picked)} 行をアーカイブへ（{len(text.encode('utf-8')) // 1024}KB → {os.path.getsize(path) // 1024}KB）"


def main(argv):
    dry = "--dry-run" in argv
    force = "--force" in argv
    only = argv[argv.index("--note") + 1] if "--note" in argv else None
    out = []
    for f in sorted(os.listdir(vault.MEMORY_DIR)):
        if not f.endswith(".md"):
            continue
        name = f[:-3]
        if only and name != only:
            continue
        if not force and os.path.getsize(os.path.join(vault.MEMORY_DIR, f)) < MIN_BYTES:
            continue
        try:
            out.append(tidy(name, dry))
        except Exception as e:
            out.append(f"{name}: 失敗 {e}")
    msg = "\n".join(out) or "整理するノートなし"
    print(msg)
    if not dry:
        try:
            with open(os.path.join(os.path.dirname(__file__), "logs", f"{datetime.now():%Y-%m}.log"), "a", encoding="utf-8") as f:
                f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} [記憶整理] " + msg.replace("\n", " / ") + "\n")
        except OSError:
            pass


if __name__ == "__main__":
    main(sys.argv[1:])
