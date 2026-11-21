"""開発モードの画面に出す情報。作業中のプロジェクトの git の状態・最近触ったファイル・ログの直近のエラー。
PC の中だけで読む（無料・ネット不要）。開発モード中だけ、数秒おきに呼ばれる。"""
import glob
import os
import re
import subprocess
import time

from config import DEV_PROJECTS, HERE, LOG_DIR

NO_WINDOW = 0x08000000  # 黒い窓を出さない
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "build", ".dart_tool", "logs", ".idea", "dist", "drafts", "minutes", "声のサンプル"}
ERROR_PAT = re.compile(r"失敗|Traceback|エラー|ERROR|Exception")


def _git(root, *args):
    try:
        r = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=5, creationflags=NO_WINDOW)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _recent_files(root, limit=5, budget=4000):
    """非 git のフォルダ用: 更新の新しいファイル。見るファイル数に上限を付けて重くならないようにする。"""
    found, seen = [], 0
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            seen += 1
            if seen > budget:
                break
            p = os.path.join(base, f)
            try:
                found.append((os.path.getmtime(p), os.path.relpath(p, root)))
            except OSError:
                pass
        if seen > budget:
            break
    found.sort(reverse=True)
    return [{"name": n, "ts": t} for t, n in found[:limit]]


def project_info(root):
    name = "jarvis" if os.path.normcase(root) == os.path.normcase(HERE) else os.path.basename(root.rstrip("\\/"))
    info = {"name": name, "git": False, "branch": "", "dirty": 0, "commits": [], "recent": [], "ts": 0}
    if os.path.isdir(os.path.join(root, ".git")):
        info["git"] = True
        info["branch"] = _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
        status = [ln for ln in _git(root, "status", "--porcelain").splitlines() if ln.strip()]
        info["dirty"] = len(status)
        for ln in _git(root, "log", "-3", "--format=%h%x09%ct%x09%s").splitlines():
            parts = ln.split("\t", 2)
            if len(parts) == 3:
                info["commits"].append({"hash": parts[0], "ts": int(parts[1]), "msg": parts[2]})
        touched = []
        for ln in status[:60]:
            path = ln[3:].split(" -> ")[-1].strip().strip('"')
            try:
                touched.append((os.path.getmtime(os.path.join(root, path)), path))
            except OSError:
                pass
        touched.sort(reverse=True)
        info["recent"] = [{"name": n, "ts": t} for t, n in touched[:5]]
        info["ts"] = max([c["ts"] for c in info["commits"][:1]] + [t for t, _ in touched[:1]] or [0])
    else:
        info["recent"] = _recent_files(root)
        info["ts"] = info["recent"][0]["ts"] if info["recent"] else 0
    return info


def recent_errors(count=3):
    """ログの終わりの方から、失敗やエラーの行を新しい順に拾う。"""
    files = sorted(glob.glob(os.path.join(LOG_DIR, "????-??.log")))
    if not files:
        return []
    try:
        with open(files[-1], "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 200_000))
            lines = f.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for ln in reversed(lines):
        # 会話の記録（ジャービス: …・あなた: …）に「エラー」の語が出ただけの行は、本物の不具合ではないので除く
        if (ERROR_PAT.search(ln) and re.match(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d ", ln)
                and not ln[20:].lstrip().startswith(("ジャービス", "あなた", "メモ", "承認", "確認待ち"))):
            out.append({"t": ln[11:16], "msg": ln[20:].strip()[:90]})
            if len(out) >= count:
                break
    return out


def collect():
    """画面に出す情報。作業中のプロジェクトは、いちばん最近触ったもの。"""
    roots = [r for r in dict.fromkeys([*DEV_PROJECTS, HERE]) if os.path.isdir(r)]
    projects = sorted((project_info(r) for r in roots), key=lambda p: -p["ts"])
    return {"main": projects[0] if projects else None, "others": [p["name"] for p in projects[1:]],
            "errors": recent_errors()}
