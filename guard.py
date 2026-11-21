"""Claude Code の PreToolUse フック。取り返しのつかない操作を止め、声での承認があったときだけ通す。

brain.py が workspace/.claude/settings.json にこのスクリプトを登録する。
"""
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import approvals  # noqa: E402
import vault  # noqa: E402
from config import DEV_PROJECTS  # noqa: E402

WORKSPACE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workspace")

# 削除・終了・アンインストールなど。前後が英数字やハイフンでないものだけ拾う（Format-Table などは対象外）
DANGEROUS_COMMAND = re.compile(
    r"(?i)(?<![\w-])(remove-item|rm|rmdir|rd|del|erase|clear-content|clear-recyclebin|format-volume|format"
    r"|stop-computer|restart-computer|shutdown|logoff|stop-process|taskkill|uninstall-package"
    r"|remove-itemproperty|remove-appxpackage|reg\s+delete|winget\s+uninstall|msiexec\s+/x)(?![\w-])")
# メール送信、ゴミ箱・削除、招待への返事、共有など
# （予定の追加・変更・削除は確認なしで通す: はっしぃの希望。予定の削除だけは下の CALENDAR_FREE で delete_ から外す）
DANGEROUS_MCP = re.compile(r"(send_message|forward|__reply|trash_|delete_"
                           r"|respond_to_event|share_file|update_file|mark_\w*spam)")
# 確認なしで通す予定の操作（追加・変更・削除）。招待への返事は相手に届くので止めたまま
CALENDAR_FREE = re.compile(r"Calendar__(create|update|delete)_event|gcal__")


def describe(tool, inp):
    if tool in ("Bash", "PowerShell"):
        return inp.get("command", "")[:200]
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        return f"{inp.get('file_path', '')} への書き込み"
    keys = ("to", "subject", "summary", "title", "start", "fileId", "eventId", "messageId", "threadId")
    detail = ", ".join(f"{k}={inp[k]}" for k in keys if k in inp)
    return f"{tool.split('__')[-1]} {detail}"[:200]


# GitHub などへの公開（外に出るので確認する）
PUBLISH_COMMAND = re.compile(r"(?i)(git\s+push|--deploy\b|gh\s+(release|pr|repo)\b)")


def in_dev_project(path):
    """自作アプリのフォルダの中で、確認なしで書いてよい場所か（.env と .git の中は除く）"""
    p = os.path.normcase(os.path.abspath(path))
    for root in DEV_PROJECTS:
        r = os.path.normcase(os.path.abspath(root))
        if p.startswith(r + os.sep):
            parts = os.path.relpath(p, r).split(os.sep)
            return ".git" not in parts and not os.path.basename(p).startswith(".env")
    return False


# 画面操作の道具で送るキーのうち、取り返しのつかないもの（Shift+Delete = ごみ箱を通さない削除）
DANGEROUS_KEYS = re.compile(r"key\.ps1.*\+\{DEL(ETE)?\}", re.I)
# 保管庫（長期記憶）のファイルを PowerShell で書き換える・動かすコマンド
FILE_WRITE_COMMAND = re.compile(r"(?i)(set-content|add-content|out-file|move-item|rename-item|copy-item|new-item|>|"
                                r"\bmv\b|\bcp\b|\bren\b|\bmove\b|\bcopy\b)")


def vault_edit_needs_confirmation(tool, inp, path):
    """保管庫の Jarvis フォルダの中: 新しいノートの作成と追記は通し、既存の内容の書き換え・上書きは確認する。"""
    if tool == "Write":
        return os.path.exists(path)
    if tool == "Edit":
        return inp.get("old_string", "") not in inp.get("new_string", "")
    if tool == "MultiEdit":
        return any(e.get("old_string", "") not in e.get("new_string", "") for e in inp.get("edits", []))
    return True


# 開発モードの保存ファイル。AI が書き換えて「再起動すれば開発モードに入れる」状態を作れないよう、いつでも触れさせない
DEV_STATE_FILE_PAT = re.compile(r"dev_state\.json", re.I)


def touches_dev_state(inp):
    return bool(DEV_STATE_FILE_PAT.search(json.dumps(inp or {}, ensure_ascii=False)))


def needs_confirmation(tool, inp):
    if touches_dev_state(inp):
        return True
    if tool in ("Bash", "PowerShell"):
        command = inp.get("command", "")
        touches_vault = vault.ROOT.lower() in command.lower() or os.path.basename(vault.OBSIDIAN_VAULT).lower() in command.lower()
        return bool(DANGEROUS_COMMAND.search(command) or DANGEROUS_KEYS.search(command)
                    or PUBLISH_COMMAND.search(command)
                    or (touches_vault and FILE_WRITE_COMMAND.search(command)))
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = os.path.abspath(inp.get("file_path") or inp.get("notebook_path") or "")
        if vault.is_inside(path):
            return vault_edit_needs_confirmation(tool, inp, path)
        if in_dev_project(path):
            return False
        return not path.lower().startswith(WORKSPACE.lower() + os.sep)  # workspace 内以外への書き込み
    if tool.startswith("mcp__"):
        if CALENDAR_FREE.search(tool):
            return False
        return bool(DANGEROUS_MCP.search(tool))
    return False


def output(decision, reason):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                   "permissionDecision": decision,
                                   "permissionDecisionReason": reason}}


import devmode  # noqa: E402
from config import DATA_DIR, HERE  # noqa: E402

# ---- 開発モード中の「歯止め」 ----
# 開発モード中は、ふつうに止まる操作を確認なしで通す。ただし次のものだけは通さず、その場の承認も求めずに
# 「保留リスト」に積む（作業は続ける。あとで画面のボタンか声で承認すると、AI が同じ操作をやり直して通る）。
# メール・共有・招待への返事など、外の人に届くもの
HARD_MCP = re.compile(r"(send_message|forward|__reply|share_file|respond_to_event)")
# シャットダウン・再起動、ディスクの初期化、アプリや設定の消去、強制 push
HARD_COMMAND = re.compile(
    r"(?i)(?<![\w-])(stop-computer|restart-computer|shutdown|logoff|format-volume|format|clear-recyclebin"
    r"|uninstall-package|remove-appxpackage|reg\s+delete|winget\s+uninstall|msiexec\s+/x)(?![\w-])"
    r"|git\s+push\b[^|;&\n]*(\s--force\b|\s--force-with-lease\b|\s-f\b)")
DELETE_VERB = re.compile(r"(?i)(?<![\w-])(remove-item|rm|rmdir|rd|del|erase|clear-content)(?![\w-])")
ABS_PATH = re.compile(r"""(?:[A-Za-z]:[\\/]|\$env:\w+|%\w+%|~)[^\s"'`;|)]*""")
SYSTEM_DIRS = [os.environ.get("SystemRoot", r"C:\Windows"), os.environ.get("ProgramFiles", r"C:\Program Files"),
               os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), os.environ.get("ProgramData", r"C:\ProgramData")]
# 開発モードで消してよい場所: 開発フォルダ・ジャービス本体・ジャービスのデータ置き場（.git と .env は除く）
DELETE_ROOTS = [*DEV_PROJECTS, HERE, DATA_DIR]


def _under(path, root, strict=True):
    p, r = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(root))
    return p.startswith(r + os.sep) or (not strict and p == r)


def _delete_target_ok(raw):
    if ".." in raw.replace("\\", "/").split("/") or raw.startswith("~"):
        return False
    path = os.path.expandvars(re.sub(r"\$env:(\w+)", r"%\1%", raw))
    if not any(_under(path, root) for root in DELETE_ROOTS):
        return False
    parts = path.replace("/", "\\").split("\\")
    return ".git" not in parts and not parts[-1].lower().startswith(".env")


def hard_stop(tool, inp):
    """開発モード中でも、すぐには通さず保留に積む操作か。"""
    if touches_dev_state(inp):
        return True
    if tool.startswith("mcp__"):
        return bool(HARD_MCP.search(tool))
    if tool in ("Bash", "PowerShell"):
        command = inp.get("command", "")
        if HARD_COMMAND.search(command) or DANGEROUS_KEYS.search(command):
            return True
        if vault.ROOT.lower() in command.lower() and FILE_WRITE_COMMAND.search(command):
            return True  # 記憶（Obsidian）の書き換え
        # 消す操作は、パスが開発の範囲に収まっているときだけ通す。確かめるのは、消す語を含む文だけ
        # （「Set-Location <ジャービスのフォルダ>; ...」のように、別の文に出てくるパスは見ない。パイプ（|）の前後は同じ文として見る）
        for statement in re.split(r"[;\n]|&&|\|\|", command):
            if DELETE_VERB.search(statement) and not all(_delete_target_ok(p) for p in ABS_PATH.findall(statement)):
                return True
        return False
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = os.path.abspath(inp.get("file_path") or inp.get("notebook_path") or "")
        if vault.is_inside(path):
            return vault_edit_needs_confirmation(tool, inp, path)
        return any(_under(path, d, strict=False) for d in SYSTEM_DIRS)
    return False


def decide(tool, inp):
    """止めるべき操作なら、フックの返事（許可か拒否）を返す。普通の操作なら {}（通常の権限判定に任せる）。"""
    inp = inp or {}
    if not needs_confirmation(tool, inp):
        return {}
    fp = hashlib.sha1(f"{tool}\n{json.dumps(inp, sort_keys=True, ensure_ascii=False)}".encode()).hexdigest()
    if approvals.consume_approval(fp):
        return output("allow", "はっしぃが承認済み")
    if devmode.active:
        if not hard_stop(tool, inp):  # 確認を省略して通し、記録だけ残す
            devmode.record_skip(describe(tool, inp))
            return output("allow", "開発モード中のため確認を省略")
        approvals.add_held(fp, describe(tool, inp))  # 保留に積む（その場の承認は求めない）
        devmode.touch()
        return output("deny", "開発モード中ですが、この操作は保留リストに積みました（はっしぃがあとで画面か声で承認します）。"
                              "今は実行できないので、この操作に頼らない残りの作業を続けてください。"
                              "別のコマンドや方法で回避しようとしないでください。承認されたら、まったく同じ内容でやり直すよう頼まれます。"
                              "返事の最後に「保留が何件あります」と一言だけ添えてください。")
    approvals.add_pending(fp, describe(tool, inp))
    return output("deny", "この操作は取り返しがつかないため、はっしぃの承認が必要です。"
                          "何をするのかを一文で読み上げて「よろしいですか」と確認し、返事を待ってください。"
                          "承認されたら、まったく同じ内容でもう一度実行すれば通ります。"
                          "別のコマンドや方法で回避しようとしないでください。")


async def sdk_hook(input_data, tool_use_id, context):
    """Claude Agent SDK のフックとして、ジャービス本体の中から直接かける（brain.py が登録する）。
    設定ファイルに書かないので、OneDrive で共有している別の PC の設定と食い違うことがない。"""
    try:
        return decide(input_data.get("tool_name", ""), input_data.get("tool_input"))
    except Exception as e:  # ガード自体が壊れたら素通りさせず止める
        return output("deny", f"ガードでエラーが起きたため操作を止めました: {e}")


if __name__ == "__main__":  # コマンドとして呼ぶとき（動作確認用）: 標準入力にフックの JSON を渡す
    try:
        event = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
        result = decide(event.get("tool_name", ""), event.get("tool_input"))
        if result:
            print(json.dumps(result))
    except Exception as e:
        print(f"ガードでエラーが起きたため操作を止めました: {e}", file=sys.stderr)
        sys.exit(2)
