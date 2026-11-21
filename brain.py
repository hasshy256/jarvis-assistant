"""頭脳。Claude Agent SDK で Claude Code を起動したまま使い回し、返事を少しずつ受け取る。

毎回 `claude -p` を起動すると 1 回 2 秒ほど余計にかかるので、プロセスを常駐させている。
認証は Claude Code のサブスクリプションログインのまま（API キーは使わない）。
"""
import asyncio
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

# Claude Code を起動するときに黒い端末の画面（Windows Terminal）が開かないようにする
_orig_popen_init = subprocess.Popen.__init__


def _popen_init_no_window(self, args, *a, **kw):
    first = args[0] if isinstance(args, (list, tuple)) and args else args
    if "claude" in str(first).lower():
        kw["creationflags"] = kw.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
    _orig_popen_init(self, args, *a, **kw)


subprocess.Popen.__init__ = _popen_init_no_window

from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, HookMatcher, ResultMessage,
                              StreamEvent, ToolUseBlock)

import guard
import vault

from config import (CLAUDE_MODEL, DATA_DIR, DEV_PROJECTS, HERE, PERMISSION_MODE, SESSION_IDLE_HOURS, STATE_FILE,
                    UPLOAD_DIR, WORKSPACE)

GUARDED_TOOLS = "Bash|PowerShell|Write|Edit|MultiEdit|NotebookEdit|mcp__.*"
ERROR_REPLY = "申し訳ございません、うまく考えがまとまりませんでした。"


def error_reply(message):
    """エラーの内容に合わせた返事。利用上限なら、いつ戻るかも伝える。"""
    if re.search(r"limit|上限", message, re.I):
        m = re.search(r"resets\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)", message, re.I)
        if m:
            hour, minute = int(m.group(1)) % 12, int(m.group(2) or 0)
            ampm = "午前" if m.group(3).lower() == "am" else "午後"
            when = f"{ampm}{hour}時" + (f"{minute}分" if minute else "")
            return f"申し訳ございません、Claude の利用上限に達しました。{when}ごろに回復します。"
        return "申し訳ございません、Claude の利用上限に達しました。しばらくしてからお試しください。"
    # 原因が分かるものは、そのまま伝える（直し方まで一言添える）
    causes = [
        (r"authenticat|oauth|\blogin\b|401|unauthorized|invalid api key|credential",
         "Claude のログインが切れています。このパソコンで claude を起動して、スラッシュ login でログインし直してください。"),
        (r"timed? ?out|ssl|handshake|connection|network|getaddrinfo|dns|econn|unreachable",
         "インターネットにうまくつながっていません。通信の状態をご確認ください。"),
        (r"not found|no such file|cannot find|winerror 2|claude.*(exe|cli)",
         "Claude の本体プログラムが見つかりません。このパソコンに Claude Code が入っているかご確認ください。"),
        (r"overloaded|529|503|500|rate",
         "Claude 側が混み合っています。少し待ってからお試しください。"),
    ]
    for pattern, cause in causes:
        if re.search(pattern, message, re.I):
            return f"申し訳ございません、考えられませんでした。原因は、{cause}"
    detail = re.sub(r"\s+", " ", message).strip()[:60]
    if detail:
        return f"{ERROR_REPLY}原因は特定できませんでした。ログに記録しています。"
    return ERROR_REPLY


def find_claude():
    exe = shutil.which("claude")
    if exe:
        return exe
    # VS Code 拡張に同梱の claude.exe（拡張の更新でフォルダ名が変わるので毎回探す）
    pattern = os.path.expanduser(r"~\.vscode\extensions\anthropic.claude-code-*\resources\native-binary\claude.exe")
    found = sorted(glob.glob(pattern), key=os.path.getmtime)
    if not found:
        raise SystemExit("Claude Code が見つかりません。VS Code の Claude Code 拡張を入れてください。")
    return found[-1]


def install_guard():
    """以前は workspace/.claude/settings.json にガードを登録していたが、そのファイルは OneDrive で別の PC とも共有され、
    PC ごとに違う Python の場所で上書きし合ってガードが動かなくなるおそれがあった。
    いまは SDK のフックとして本体の中から直接かけるので、古い登録が残っていれば消しておく。"""
    path = os.path.join(WORKSPACE, ".claude", "settings.json")
    try:
        with open(path, encoding="utf-8") as f:
            settings = json.load(f)
    except (OSError, json.JSONDecodeError):
        return
    if settings.pop("hooks", None) is not None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)


class Brain:
    def __init__(self, state_file=STATE_FILE, **extra_options):
        """state_file: 会話のつながりの保存先（iPhone 用は別のファイルにして、PC の会話と混ざらないようにする）。
        extra_options: ClaudeAgentOptions に足すもの（iPhone 用の追加の道具など）。"""
        self.exe = find_claude()
        self.state_file = state_file
        self.extra_options = extra_options
        install_guard()
        state = self._load_state()
        self.session_id = state.get("session_id")
        self.last_used = state.get("last_used", 0)
        self.client = None
        self.tools_used = []
        self.on_tool = lambda started: None
        self.cancelled = False
        self.fresh = False  # 新しい会話を始めたばかり（最初の依頼に長期記憶を添える）
        self.loop = asyncio.new_event_loop()  # SDK は async なので専用スレッドでイベントループを回す
        threading.Thread(target=self.loop.run_forever, daemon=True).start()

    def _load_state(self):
        try:
            with open(self.state_file, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_state(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump({"session_id": self.session_id, "last_used": self.last_used}, f)

    def _expired(self):
        return time.time() - self.last_used > SESSION_IDLE_HOURS * 3600

    async def _connect(self, resume):
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:
                pass
        self.fresh = resume is None
        vault.ensure()
        options = ClaudeAgentOptions(
            cwd=WORKSPACE, model=CLAUDE_MODEL, permission_mode=PERMISSION_MODE, cli_path=self.exe,
            setting_sources=["user", "project", "local"],  # CLAUDE.md（人格と記憶）と guard フックを読む
            include_partial_messages=True, resume=resume,
            # 添付されたファイル、長期記憶（Obsidian の保管庫の Jarvis フォルダ）、自作アプリのフォルダ
            add_dirs=[UPLOAD_DIR, vault.ROOT, *DEV_PROJECTS],
            # 取り返しのつかない操作を止めるガード（guard.py）を、本体の中から直接かける
            hooks={"PreToolUse": [HookMatcher(matcher=GUARDED_TOOLS, hooks=[guard.sdk_hook])]},
            extra_args={"chrome": None},  # Chrome 連携（Claude in Chrome 拡張）をオンにする。繋がらなければこの行を消す
            **self.extra_options)
        self.client = ClaudeSDKClient(options)
        await self.client.connect()

    async def _query(self, text, on_text):
        """読み上げるのは「最初の一言（ツールを使う前）」と「最後の返事」だけ。
        ツールを使っている途中に書かれた文（画面操作のメモなど）は読み上げず、返事にも含めない。"""
        if self.fresh:  # 新しい会話では、前回までの記憶と最近の会話ログを読み込んでから話す
            text = vault.context_for_new_session() + text
            self.fresh = False
        await self.client.query(text)
        reply, first = "", None
        tool_started = False  # この依頼の中で、もうツールを使い始めたか
        held = ""             # ツールを使い始めた後の文。最後の返事だと分かるまで保留する
        t0 = time.time()
        async for m in self.client.receive_response():
            if isinstance(m, StreamEvent):
                ev = m.event
                kind = ev.get("type")
                if kind == "content_block_start" and ev.get("content_block", {}).get("type") == "tool_use":
                    tool_started = True
                    held = ""  # ツールの前に書かれた途中のメモは捨てる
                    self.on_tool(True)   # 画面に「確認中」を出す
                elif kind == "content_block_delta" and ev["delta"].get("type") == "text_delta":
                    first = first or time.time() - t0
                    if tool_started:
                        held += ev["delta"]["text"]
                    else:  # 最初の一言（「お調べします」など）はすぐ読み上げる
                        reply += ev["delta"]["text"]
                        on_text(ev["delta"]["text"])
                elif kind == "content_block_stop" and not tool_started:
                    on_text("\n")
                elif kind == "message_delta" and tool_started:
                    if ev.get("delta", {}).get("stop_reason") not in (None, "tool_use"):  # ツールを使わずに終わった = 最後の返事
                        reply += ("\n" if reply else "") + held
                        self.on_tool(False)   # 最後の返事に入る（「確認中」を終える）
                        on_text(held + "\n")
                    held = ""
            elif isinstance(m, AssistantMessage):
                self.tools_used += [b.name for b in m.content if isinstance(b, ToolUseBlock)]
            elif isinstance(m, ResultMessage):
                self.session_id = m.session_id
                if m.is_error and not reply:
                    raise RuntimeError(m.result or "エラー")
                if not reply and not self.tools_used:  # 空振りのときも、原因をログに残す
                    raise RuntimeError(f"返事が空でした（{getattr(m, 'subtype', '')}）")
        return reply, first

    def cancel(self):
        """考えている途中の返事を止める（割り込み）。会話のつながりはそのまま残る。"""
        self.cancelled = True
        if self.client is not None:
            try:
                asyncio.run_coroutine_threadsafe(self.client.interrupt(), self.loop)
            except Exception:
                pass

    async def _ask(self, text, on_text):
        if self.client is None or self._expired():
            # しばらく空いたら新しい会話にする（長期の記憶は CLAUDE.md が引き継ぐ）
            await self._connect(None if self._expired() else self.session_id)
        try:
            return await self._query(text, on_text)
        except Exception:
            if self.cancelled:  # 割り込みで止めただけなので、やり直さない
                raise
            await self._connect(None)  # 古いセッションが開けない、プロセスが落ちた等 → 新しく始めて 1 回だけやり直す
            return await self._query(text, on_text)

    def warmup(self):
        """起動時に先に接続しておき、最初の返事を速くする。"""
        resume = None if self._expired() else self.session_id
        asyncio.run_coroutine_threadsafe(self._connect(resume), self.loop).result(timeout=120)

    def ask(self, text, on_text=lambda s: None, on_tool=lambda started: None):
        """返事の本文と所要時間を返す。on_text には返事が少しずつ渡される。使ったツール名は self.tools_used に残る。
        on_tool(True) はツールを使い始めたとき、on_tool(False) は最後の返事に入ったときに呼ばれる。"""
        t0 = time.time()
        self.on_tool = on_tool
        self.tools_used = []
        self.cancelled = False
        try:
            future = asyncio.run_coroutine_threadsafe(self._ask(text, on_text), self.loop)
            reply, first = future.result(timeout=600)
        except Exception as e:
            if self.cancelled:  # 割り込み: エラー扱いにせず、接続もそのまま使い続ける
                return "", {"wall": time.time() - t0, "first": None}
            self.client = None
            reply = error_reply(str(e))
            on_text(reply)
            return reply, {"wall": time.time() - t0, "first": None, "error": str(e)[:300]}
        self.last_used = time.time()
        self._save_state()
        if self.cancelled:  # 割り込みで止めたときは、そこまでの返事だけ（空でもエラーにしない）
            return reply.strip(), {"wall": time.time() - t0, "first": first}
        return reply.strip() or ERROR_REPLY, {"wall": time.time() - t0, "first": first}
