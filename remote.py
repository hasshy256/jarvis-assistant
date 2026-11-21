"""iPhone からのリモコン。FastAPI のサーバーで、文字の依頼を受けてジャービス（Claude Code）に渡し、返事を返す。

  POST /command   {"text": "..."} → {"reply": "...", "action": null | "alarm" | ..., ...}
  GET  /api/dashboard, /api/history   iPhone の画面（PWA）用
  /               PWA（ホーム画面に追加できる Web アプリ）

声のジャービス（jarvis.py）とは別に動く。会話のつながりも別（PC で話した内容と混ざらない）だが、
人格と記憶（workspace/CLAUDE.md）、タスク、取り返しのつかない操作のガードは同じものを使う。
起動: `リモコン起動.bat`（合言葉は環境変数 JARVIS_TOKEN）
"""
import io
import json
import os
import re
import base64
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import datetime

import numpy as np
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import approvals
import gcal
import info
import local_llm
import memory
import minutes
import timers
import vault
from brain import Brain
from config import (CONFIRM_NO, CONFIRM_YES, END_PHRASES, HERE, LOCAL_LLM, LOG_DIR, REMOTE_HISTORY_FILE,
                    REMOTE_HISTORY_MAX, REMOTE_HOST, REMOTE_MEMORY_IDLE_SEC, REMOTE_PORT, REMOTE_RESTART_FLAG,
                    REMOTE_STATE_FILE, REMOTE_TOKEN_ENV, SCHEDULE_REFRESH_MIN, TASKS_FILE, UPLOAD_DIR, UPLOAD_MAX_MB,
                    WEATHER_REFRESH_MIN)
from config import MINUTES_AUDIO_DIR, MINUTES_DIR, REMOTE_ALIVE_SEC, SAMPLE_RATE
import push
from config import MANGA_DIR, SHUKATSU_DIR
from tts import NOT_SPOKEN, speakable, synthesize

PWA_DIR = os.path.join(HERE, "pwa")
SHUKATSU_SITE = os.path.join(SHUKATSU_DIR, "site")
SHUKATSU_COMPANIES = os.path.join(SHUKATSU_DIR, "data", "companies.json")
SHUKATSU_MINE = os.path.join(SHUKATSU_DIR, "data", "mine.json")
SHUKATSU_STATUSES = ("気になる", "説明会・イベント", "インターン応募", "インターン参加", "本選考エントリー", "選考中", "内定", "見送り")
ACTION_LINE = re.compile(r"^\s*ACTION\s*[:：]\s*(\{.*\})\s*$", re.M)
FILE_LINE = re.compile(r"^\s*FILE\s*[:：]\s*(.+?)\s*$", re.M)
SEND_MAX_MB = 100  # iPhone へ送れるファイルの大きさの上限
CALENDAR_WRITE = re.compile(r"(gcal__create_event|Google_Calendar__(create|update|delete)_event)")

# iPhone から来た依頼に毎回添える説明。iPhone でしかできない操作は、Claude には実行させず ACTION 行で返させる
REMOTE_NOTE = """（iPhone から文字で届いた依頼です。はっしぃは PC の前にいないので、返事は iPhone で読み上げられます。
- PC の画面操作は、はっしぃが結果を見られないので、頼まれない限りしない
{calendar}- iPhone 本体でしかできない操作（アラーム、タイマー、アプリを開く、Web ページを開く）は自分では実行せず、
  返事の最後の行に次の形で 1 つだけ書く。この行は読み上げられず、iPhone のショートカットが実行する
  ACTION: {{"action": "alarm", "time": "07:00"}}
  ACTION: {{"action": "timer", "seconds": 180}}
  ACTION: {{"action": "open_app", "app": "LINE"}}
  ACTION: {{"action": "open_url", "url": "https://..."}}
  返事の文は「7時に起こします」のように、これから iPhone ですることを伝える。操作が要らなければ ACTION 行は書かない
- PC のファイルを iPhone に送ってほしいと頼まれたら、そのファイルの場所をフルパスで調べ、
  返事の最後に次の形で 1 行ずつ書く（ACTION 行とは別。この行は読み上げられず、iPhone の画面にダウンロードの印が出る）
  FILE: C:\\Users\\...\\資料.pdf
  見つからなければ FILE 行は書かず、見つからなかったと伝える。頼まれていないファイルは送らない
- iPhone で鳴らすアラームやタイマーに reminders.json は使わない（そちらは PC で鳴る））

"""
CALENDAR_NOTE = "- Google カレンダーの予定の確認・登録は mcp__gcal__list_events と mcp__gcal__create_event を使う\n"


def log(msg):
    """logs/remote/ に残す（声のジャービスの会話ログとは分ける）。"""
    folder = os.path.join(LOG_DIR, "remote")
    os.makedirs(folder, exist_ok=True)
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    with open(os.path.join(folder, f"{datetime.now():%Y-%m}.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def plain(text):
    return re.sub(r"[、。,.!！?？\s]", "", text.lower())


def confirmation_answer(text):
    """「よろしいですか」への返事。jarvis.py と同じ判定。"""
    p = plain(text)
    if re.search(CONFIRM_NO, p):
        return "no"
    if re.search(CONFIRM_YES, p) and len(p) <= 15:
        return "yes"
    return None


def end_reply(text):
    p = re.sub(r"[、。,.!！?？\s〜ー]+$|[、。,.!！?？\s]", "", text)
    for phrase, reply in END_PHRASES.items():
        if phrase in p and len(p) <= len(phrase) + 6:
            return reply
    return None


def split_action(reply):
    """返事から ACTION 行を取り出す。(読み上げる返事, {"action": ..., ...} か None)"""
    action = None
    for m in ACTION_LINE.finditer(reply):
        try:
            data = json.loads(m.group(1))
            if isinstance(data, dict) and data.get("action"):
                action = data
                if isinstance(data.get("url"), str):  # 空白や日本語がそのままだと iPhone で開けないので変換する
                    data["url"] = urllib.parse.quote(data["url"].replace(" ", "+"), safe=":/?&=#%+@,;~!$'()*")
        except json.JSONDecodeError:
            pass
    return speakable(ACTION_LINE.sub("", reply)).strip(), action


def split_files(reply):
    """返事から FILE 行（iPhone へ送るファイルのパス）を取り出す。(FILE 行を除いた返事, パスの一覧)"""
    paths = [m.group(1) for m in FILE_LINE.finditer(reply)]
    return FILE_LINE.sub("", reply).strip(), paths


MINUTES_PROMPT = """（会議の文字起こしができました。議事録をまとめる依頼です）
Read ツールで {path} を読み、同じフォルダに「{stem}_議事録.md」という名前で議事録を Write ツールで保存してください。
内容は、日時、概要（2〜3 行）、議題ごとの要点、決まったこと、やること（担当と期限が分かれば付ける）、未決事項。
文字起こしは音声認識なので誤字や聞き間違いがある。意味が通る範囲で直し、推測で事実を足さない。話者は区別できない。
保存したら、決まったことと、やることを中心に、読み上げ用に 3 文以内で伝え、「詳しくお伝えしましょうか」と添える。"""


class Remote:
    def __init__(self):
        self.minutes_job = {}
        self.lock = threading.Lock()  # Claude の会話は 1 本なので、依頼は 1 つずつ順に処理する
        self.brain = None
        self.awaiting = False         # 取り返しのつかない操作の確認待ち
        self.local_turns = []         # PC 内の AI や本体だけで返したやりとり（次に Claude へ話すとき添える）
        self.history = self._load_history()
        self.cache = {}
        self.shared = {}              # iPhone へ送るファイル: 受け取り用の id → パス（再起動すると消える）
        self.shared_now = []          # 今回の返事で送るファイル [{"id", "name"}]
        self.mem_turns = []           # 記憶係にまだ渡していないやりとり
        self.mem_timer = None
        self.alive_at = 0.0           # 画面アプリが最後に「開いています」と知らせた時刻

    # ---- 画面アプリの開閉（閉じている間に返事ができたら通知する） ----
    def screen_open(self):
        return time.time() - self.alive_at < REMOTE_ALIVE_SEC

    def _notify_reply(self, reply):
        """画面アプリが開いていなければ、返事の内容を iPhone に通知する（開いていれば画面に出るので送らない）。"""
        if reply and not self.screen_open():
            threading.Thread(target=push.send, args=("ジャービス", NOT_SPOKEN.sub("", speakable(reply)).strip()),
                             daemon=True).start()

    # ---- 記憶（PC の会話と同じ保管庫に残す） ----
    def _queue_memory(self, user, reply):
        self.mem_turns.append((user, reply))
        if self.mem_timer:
            self.mem_timer.cancel()
        self.mem_timer = threading.Timer(REMOTE_MEMORY_IDLE_SEC, self._flush_memory)
        self.mem_timer.daemon = True
        self.mem_timer.start()

    def _flush_memory(self):
        with self.lock:
            turns, self.mem_turns = self.mem_turns, []
        memory.remember_async(turns, log)

    # ---- 頭脳 ----
    def _brain(self):
        if self.brain is None:
            extra = {}
            if gcal.ready():  # Google Calendar API の準備ができていれば、予定の道具を渡す
                extra = {"mcp_servers": {"gcal": gcal.sdk_server()},
                         "allowed_tools": ["mcp__gcal__list_events", "mcp__gcal__create_event"]}
            self.brain = Brain(state_file=REMOTE_STATE_FILE, **extra)
        return self.brain

    def warmup(self):
        try:
            log("Claude Code に接続中…")
            self._brain().warmup()
            log("接続しました")
        except Exception as e:
            log(f"接続できませんでした（{e}）。依頼が来たときに改めて接続します")

    # ---- 会話ログ（iPhone の画面用） ----
    def _load_history(self):
        try:
            with open(REMOTE_HISTORY_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return []

    def _remember(self, role, text, action=None, files=None):
        item = {"role": role, "text": text, "time": datetime.now().strftime("%m/%d %H:%M")}
        if action:
            item["action"] = action
        if files:
            item["files"] = files
        self.history = (self.history + [item])[-REMOTE_HISTORY_MAX:]
        with open(REMOTE_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(self.history, f, ensure_ascii=False)

    # ---- 声での依頼（iPhone の画面アプリの会話ボタン） ----
    def voice(self, data):
        """iPhone で録った声を PC の Whisper で文字にして、文字の依頼と同じように処理する。"""
        audio = decode_audio(data)
        if len(audio) < 16000 * 0.3:
            return {"heard": "", "reply": "", "action": None}
        text = get_stt().transcribe((audio * 32767).astype(np.int16), confirm=self.awaiting)
        if not text:
            log("iPhone（声）: 聞き取れませんでした")
            return {"heard": "", "reply": "", "action": None}
        closing = not self.awaiting and end_reply(text)  # 「ありがとう」などで会話モードを終える
        result = self.command(text)
        result["heard"] = text
        if closing:
            result["end"] = True
        return result

    # ---- 議事録（iPhone のマイクで録った会議） ----
    def minutes_start(self, data):
        """録音を受け取り、裏で文字起こし→議事録づくりを始める。"""
        if self.minutes_job.get("state") == "working":
            raise HTTPException(status_code=409, detail="前の議事録を作っています")
        started = datetime.now()
        os.makedirs(MINUTES_AUDIO_DIR, exist_ok=True)
        path = os.path.join(MINUTES_AUDIO_DIR, started.strftime("%Y%m%d_%H%M") + "_iphone.m4a")
        with open(path, "wb") as f:
            f.write(data)
        self.minutes_job = {"state": "working", "step": "文字にしています", "reply": ""}
        log(f"議事録(iPhone): 録音を受け取りました（{len(data) / 1e6:.1f} MB）")
        threading.Thread(target=self._minutes_work, args=(path, started), daemon=True).start()

    def _minutes_work(self, path, started):
        job = self.minutes_job
        try:
            with open(path, "rb") as f:
                audio = (decode_audio(f.read()) * 32767).astype(np.int16)
            segs = minutes.Minutes()._segments(audio)
            log(f"議事録(iPhone): 文字起こし開始（{len(audio) / SAMPLE_RATE / 60:.1f} 分・{len(segs)} 区切り）")
            lines = []
            for a, b, levels in segs:
                if not levels or max(levels) < 0.01:
                    continue
                text = get_stt().transcribe(audio[a:b])
                if text:
                    lines.append(f"[{a // SAMPLE_RATE // 60:02d}:{a // SAMPLE_RATE % 60:02d}] {text}")
            if not lines:
                job.update(state="done", reply="録音から話し声を聞き取れませんでした。")
                return
            os.makedirs(MINUTES_DIR, exist_ok=True)
            stem = started.strftime("%Y%m%d_%H%M")
            out = os.path.join(MINUTES_DIR, stem + "_文字起こし.txt")
            with open(out, "w", encoding="utf-8") as f:
                f.write(f"会議の文字起こし（{started:%Y年%m月%d日 %H:%M} 開始・約 {len(audio) / SAMPLE_RATE / 60:.0f} 分）\n"
                        "※音声認識なので誤字・聞き間違いがある。話者の区別はない\n\n" + "\n".join(lines) + "\n")
            job["step"] = "議事録にまとめています"
            prompt = MINUTES_PROMPT.format(path=out, stem=stem)
            with self.lock:
                reply, _ = self._brain().ask(prompt)
            reply, _ = split_files(reply)
            reply, _ = split_action(reply)
            self._remember("jarvis", reply)
            job.update(state="done", reply=reply)
            log(f"議事録(iPhone): 完了 {out}")
        except Exception as e:
            log(f"議事録(iPhone): 失敗: {e}")
            job.update(state="done", reply="申し訳ございません、議事録を作れませんでした。")

    # ---- 依頼の処理 ----
    def command(self, text, files=None):
        """files: 保存済みの添付ファイルのパスの一覧（iPhone から送られた写真・ファイル）。"""
        files = files or []
        with self.lock:
            t0 = time.time()
            asked = text
            if files:
                names = "、".join(os.path.basename(p).split("_", 2)[-1] for p in files)
                text = (text + "\n" if text else "") + "📎 " + names
            log(f"iPhone: {text}")
            self._remember("user", text)
            reply, action, source = self._handle(asked, files)
            log(f"返事（{source}・{time.time() - t0:.1f}s）: {reply}" + (f" / ACTION {action}" if action else ""))
            shared, self.shared_now = self.shared_now, []  # ジャービスが iPhone へ送るファイル
            self._remember("jarvis", reply, action, shared)
            self._queue_memory(text, reply)
            self._notify_reply(reply)
            result = {"reply": reply, "action": None}
            if shared:
                result["files"] = shared
            if action:
                result.update(action)  # {"reply": ..., "action": "alarm", "time": "07:00"} のように平らに並べる
            if self.awaiting:  # 確認待ちのときだけ付ける（ショートカットで「値がある」と判定しやすいように）
                result["awaiting"] = True
            return result

    def _handle(self, text, files=None):
        if files:
            # 添付つきは、道具を使う Claude に任せる（Read で中身を見てもらう）
            self.awaiting = False
            approvals.clear_pending()
            text = (text or "添付したファイルを確認して、内容を簡単に教えて") + (
                "\n\n（はっしぃが iPhone からファイルを添付しました。Read ツールで中身を確認してから答えてください: "
                + "、".join(files) + "）")
        elif self.awaiting:
            # 承認は、はっしぃの返事を受けたこのサーバーでだけ記録する（AI の自己申告では通らない）
            self.awaiting = False
            answer = confirmation_answer(text)
            if answer == "yes":
                log("承認: " + " / ".join(p["desc"] for p in approvals.pending()))
                approvals.approve_pending()
            else:
                approvals.clear_pending()
            if answer == "no":
                return "かしこまりました。取りやめます。", None, "本体"
        else:
            approvals.clear_pending()
            closing = end_reply(text)
            if closing:
                return closing, None, "本体"
            memo = vault.memo_command(text)  # 「○○をメモして」は保管庫のメモ/にすぐ保存
            if memo:
                try:
                    log(f"メモ: {vault.save_memo(memo)}")
                    return "メモしました。", None, "本体"
                except OSError as e:
                    log(f"メモを保存できません: {e}")
                    return "申し訳ございません、メモを保存できませんでした。", None, "本体"
            fast = self._timer(text)  # 「3分のタイマー」「7時に起こして」は Claude を通さずすぐ返す
            if fast:
                return (*fast, "本体")
            instant = local_llm.instant_reply(text)
            if instant:
                self.local_turns.append((text, instant))
                return instant, None, "本体"
            if LOCAL_LLM and local_llm.is_small_talk(text) and local_llm.available():
                try:
                    reply = local_llm.chat(text, self.local_turns, on_text=lambda s: None)
                except Exception as e:
                    log(f"PC 内の AI でエラー: {e}")
                    reply = None
                if reply:
                    self.local_turns.append((text, speakable(reply)))
                    return speakable(reply), None, "PC内AI"

        brain = self._brain()
        note = REMOTE_NOTE.format(calendar=CALENDAR_NOTE if gcal.ready() else "")
        query = vault.recall_query(text)  # 「○○って何だっけ」は先に保管庫を探して添える
        found = vault.search_context(query) if query is not None else ""
        reply, timing = brain.ask(note + found + self._with_local_context(text))
        if "error" in timing:
            log(f"エラー: {timing['error']}")
        if any(CALENDAR_WRITE.search(t) for t in brain.tools_used):
            self.cache.pop("events", None)  # 予定を登録したので、画面の予定をすぐ取り直す
            info.calendar_refresh.set()
        self.awaiting = bool(approvals.pending())  # ガードが操作を止めた → 次の依頼を「はい／いいえ」として扱う
        if self.awaiting:
            log("確認待ち: " + " / ".join(p["desc"] for p in approvals.pending()))
        reply, paths = split_files(reply)
        self.shared_now = [self._share(p) for p in paths]
        self.shared_now = [s for s in self.shared_now if s]
        reply, action = split_action(reply)
        return reply, action, "Claude"

    def _share(self, path):
        """送ってよいファイルなら受け取り用の id を発行する。存在しない・大きすぎるときは None。"""
        path = os.path.abspath(path.strip().strip('"'))
        if not os.path.isfile(path) or os.path.getsize(path) > SEND_MAX_MB * 1024 * 1024:
            log(f"送れないファイル: {path}")
            return None
        fid = secrets.token_urlsafe(12)
        self.shared[fid] = path
        log(f"iPhone へ送るファイル: {path}")
        return {"id": fid, "name": os.path.basename(path)}

    def _timer(self, text):
        found = timers.handle(text)
        if not found:
            return None
        kind, value = found
        now = datetime.now()
        if kind == "timer":
            seconds = round((value - now).total_seconds())
            return (f"{timers.describe_duration(seconds)}のタイマーをセットします。",
                    {"action": "timer", "seconds": seconds})
        if kind == "alarm":
            day = "明日の" if value.date() > now.date() else ""
            hour = value.hour
            spoken = f"{'午前' if hour < 12 else '午後'}{hour % 12 or 12}時" + (f"{value.minute}分" if value.minute else "")
            return (f"{day}{spoken}にアラームをセットします。",
                    {"action": "alarm", "time": value.strftime("%H:%M"), "date": value.strftime("%Y-%m-%d")})
        # 取り消し・残り時間は iPhone の時計アプリの中のことなので、ショートカットからは扱えない
        return "iPhone のタイマーとアラームは、時計アプリから止めてください。", None

    def _with_local_context(self, text):
        if not self.local_turns:
            return text
        lines = "\n".join(f"はっしぃ「{u}」→ ジャービス「{r}」" for u, r in self.local_turns[-6:])
        self.local_turns = []
        return f"（直前に、あなたを通さずに返したやりとり。参考にしてください）\n{lines}\n\n{text}"

    # ---- ダッシュボード ----
    def _cached(self, key, minutes, fetch):
        hit = self.cache.get(key)
        if hit and time.time() - hit[0] < minutes * 60:
            return hit[1]
        try:
            value = fetch()
        except Exception as e:
            log(f"{key} の取得に失敗: {e}")
            return hit[1] if hit else None  # 失敗したら前の値を出す
        if value is not None:
            self.cache[key] = (time.time(), value)
        return value

    def dashboard(self):
        try:
            with open(TASKS_FILE, encoding="utf-8-sig") as f:
                tasks = [t for t in json.load(f) if not t.get("done")]
        except (OSError, json.JSONDecodeError, TypeError):
            tasks = []
        return {"weather": self._cached("weather", WEATHER_REFRESH_MIN, info.fetch_weather),
                "events": self._cached("events", SCHEDULE_REFRESH_MIN, info.fetch_week),
                "calendar_api": gcal.ready(), "tasks": tasks[:8], "awaiting": self.awaiting,
                "app_version": app_version()}


def decode_audio(data):
    """iPhone の録音（m4a など）を 16kHz・モノラルの波形（-1〜1）に直す。"""
    import av
    chunks = []
    with av.open(io.BytesIO(data)) as box:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
        for frame in box.decode(audio=0):
            chunks += [f.to_ndarray().reshape(-1) for f in resampler.resample(frame)]
        chunks += [f.to_ndarray().reshape(-1) for f in resampler.resample(None)]
    return (np.concatenate(chunks) if chunks else np.zeros(0, np.int16)).astype(np.float32) / 32768


def app_version():
    """画面ファイル（pwa/）の更新時刻。変わると iPhone の画面が自分で読み込み直す。"""
    return int(max(os.path.getmtime(os.path.join(root, f)) for root, _, files in os.walk(PWA_DIR) for f in files))


# ---- 音声認識（声のジャービスと同じ Whisper。初めて使うときに読み込む） ----
_stt = None
_stt_lock = threading.Lock()


def get_stt():
    global _stt
    with _stt_lock:
        if _stt is None:
            from stt import STT
            _stt = STT()
            log(f"音声認識を準備しました（{_stt.engine}）")
        return _stt


# ---- サーバー ----
def load_token():
    """環境変数から合言葉を読む。登録より前に開いた画面から起動すると引き継がれないので、Windows の登録からも読む。"""
    token = os.environ.get(REMOTE_TOKEN_ENV, "").strip()
    if not token:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                token = str(winreg.QueryValueEx(key, REMOTE_TOKEN_ENV)[0]).strip()
        except OSError:
            pass
    return token


TOKEN = load_token()
remote = Remote()


class UTF8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"  # 文字コードを明記（書かないと日本語が化けるアプリがある）


app = FastAPI(title="Jarvis Remote", docs_url=None, redoc_url=None, openapi_url=None,
              default_response_class=UTF8JSONResponse)


def check_token(request: Request):
    """合言葉の確認。`Authorization: Bearer <トークン>` か `X-Jarvis-Token: <トークン>` で送る。"""
    sent = request.headers.get("x-jarvis-token") or ""
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        sent = auth[7:]
    if not TOKEN or not secrets.compare_digest(sent.strip().encode(), TOKEN.encode()):
        raise HTTPException(status_code=401, detail="トークンが違います")


def check_token_or_local(request: Request):
    """この PC の中から直接開いたとき（ジャービスのダッシュボードのポップアップなど）は、読むだけなら合言葉なしで通す。
    Tailscale 経由（x-forwarded-for が付く）は今までどおり合言葉が要る。"""
    host = request.client.host if request.client else ""
    if host in ("127.0.0.1", "::1") and not request.headers.get("x-forwarded-for"):
        return
    check_token(request)


class Command(BaseModel):
    text: str


@app.post("/command", dependencies=[Depends(check_token)])
def command(body: Command):
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text が空です")
    return remote.command(text)


class Attachment(BaseModel):
    name: str
    data: str  # base64


class CommandFiles(BaseModel):
    text: str = ""
    files: list[Attachment]


def save_uploads(files):
    """iPhone から届いた添付を uploads に保存し、パスの一覧を返す。"""
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    paths = []
    for i, f in enumerate(files):
        name = re.sub(r'[\/:*?"<>|]', "_", os.path.basename(f.name) or "file")
        try:
            data = base64.b64decode(f.data)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"{name} を読み取れません")
        if len(data) > UPLOAD_MAX_MB * 1024 * 1024:
            raise HTTPException(status_code=413, detail=f"{name} は {UPLOAD_MAX_MB}MB を超えています")
        path = os.path.join(UPLOAD_DIR, f"{stamp}_r{i}_{name}")
        with open(path, "wb") as fh:
            fh.write(data)
        paths.append(path)
    return paths


@app.post("/api/command-files", dependencies=[Depends(check_token)])
def command_files(body: CommandFiles):
    """文と添付ファイル（写真など）を一緒に受ける。返事は /command と同じ形。"""
    if not body.files:
        raise HTTPException(status_code=400, detail="files が空です")
    paths = save_uploads(body.files)
    return remote.command(body.text.strip(), paths)


@app.post("/api/voice", dependencies=[Depends(check_token)])
async def voice(request: Request):
    """本文は録音そのもの（audio/mp4 など）。返事は /command と同じ形に、聞き取った文 heard を足したもの。"""
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="録音が空です")
    return await run_in_threadpool(remote.voice, data)


@app.post("/api/minutes", dependencies=[Depends(check_token)])
async def minutes_upload(request: Request):
    """本文は会議の録音そのもの。受け取ったらすぐ返し、文字起こしと議事録づくりは裏で進む。"""
    data = await request.body()
    if len(data) < 2000:
        raise HTTPException(status_code=400, detail="録音が空です")
    remote.minutes_start(data)
    return {"state": "working"}


@app.get("/api/minutes/status", dependencies=[Depends(check_token)])
def minutes_status():
    return remote.minutes_job or {"state": "idle"}


class Alive(BaseModel):
    open: bool = True


@app.post("/api/alive", dependencies=[Depends(check_token)])
def alive(body: Alive):
    """画面アプリが開いている間、数秒おきに届く合図。閉じたときは open=false が届く（届かなくても途切れで判断する）。"""
    remote.alive_at = time.time() if body.open else 0.0
    return {"ok": True}


@app.post("/api/client-log", dependencies=[Depends(check_token)])
def client_log(body: Command):
    """iPhone の画面アプリから届く動作の記録（声の会話がうまく動かないときの調査用）。"""
    log(f"画面: {body.text[:500]}")
    return {"ok": True}


class Speech(BaseModel):
    text: str


@app.post("/api/tts", dependencies=[Depends(check_token)])
def tts_audio(body: Speech):
    """返事を PC のジャービスと同じ声の音声ファイルにして返す（iPhone の会話ボタンで読み上げる）。"""
    text = NOT_SPOKEN.sub("", speakable(body.text)).strip()
    if not text:
        raise HTTPException(status_code=400, detail="text が空です")
    path = synthesize(text)
    return FileResponse(path, media_type="audio/wav" if path.endswith(".wav") else "audio/mpeg")


@app.get("/api/dashboard", dependencies=[Depends(check_token)])
def dashboard():
    return remote.dashboard()


@app.get("/api/version", dependencies=[Depends(check_token)])
def version():
    return {"version": app_version()}


@app.get("/api/file/{fid}", dependencies=[Depends(check_token)])
def download(fid: str):
    """ジャービスが iPhone へ送ると言ったファイルだけを渡す（id は返事ごとに発行。任意のパスは受け付けない）。"""
    path = remote.shared.get(fid)
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="ファイルがありません（期限切れの可能性があります）")
    return FileResponse(path, filename=os.path.basename(path))


@app.get("/api/history", dependencies=[Depends(check_token)])
def history():
    return remote.history[-100:]


# ---- 就活ノート（../shukatsu）。調べた会社は companies.json（ジャービスが書く）、自分の進み具合とメモは mine.json（画面とジャービスの両方が書く） ----
shukatsu_lock = threading.Lock()


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


@app.get("/shukatsu", include_in_schema=False)
def shukatsu_page():  # 末尾の / なしで開かれたとき（そのままだと 404 になる）
    return RedirectResponse("/shukatsu/")


@app.get("/api/shukatsu", dependencies=[Depends(check_token)])
def shukatsu():
    return {"data": read_json(SHUKATSU_COMPANIES, {"companies": []}), "mine": read_json(SHUKATSU_MINE, {})}


class ShukatsuMine(BaseModel):
    id: str
    status: str | None = None
    priority: int | None = None
    memo: str | None = None


@app.post("/api/shukatsu/mine", dependencies=[Depends(check_token)])
def shukatsu_mine(body: ShukatsuMine):
    ids = {c.get("id") for c in read_json(SHUKATSU_COMPANIES, {}).get("companies", [])}
    if body.id not in ids:
        raise HTTPException(status_code=404, detail="その会社はありません")
    if body.status is not None and body.status not in SHUKATSU_STATUSES:
        raise HTTPException(status_code=400, detail="進み具合の値が違います")
    with shukatsu_lock:
        mine = read_json(SHUKATSU_MINE, {})
        entry = mine.get(body.id, {"status": "気になる", "priority": 0, "memo": ""})
        if body.status is not None:
            entry["status"] = body.status
        if body.priority is not None:
            entry["priority"] = max(0, min(3, body.priority))
        if body.memo is not None:
            entry["memo"] = body.memo[:10000]
        entry["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        mine[body.id] = entry
        tmp = SHUKATSU_MINE + ".tmp"  # 書きかけで壊れないよう、別名で書いてから置き換える
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(mine, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SHUKATSU_MINE)
    # OneDrive で見る閲覧用の 1 枚の HTML（Claude用\就活ノート.html）も書き出し直す。返事は待たせない
    subprocess.Popen([sys.executable, os.path.join(SHUKATSU_DIR, "build.py")], cwd=SHUKATSU_DIR,
                     creationflags=subprocess.CREATE_NO_WINDOW)
    log(f"就活ノートを更新: {body.id} {entry['status']} ★{entry['priority']}")
    return entry


# ---- 蔵書管理（../manga）。本の登録・一覧・新刊の確認。中身は manga/books.py ----
MANGA_SITE = os.path.join(MANGA_DIR, "site")
if os.path.isdir(MANGA_DIR):
    sys.path.insert(0, MANGA_DIR)
    try:
        import books as manga_books
    except Exception as _e:  # 蔵書の部品が壊れていても、リモコン全体は動かす
        manga_books = None
        print("蔵書管理を読み込めません:", _e)
else:
    manga_books = None


def manga_ready():
    if manga_books is None:
        raise HTTPException(status_code=503, detail="蔵書管理が使えません")


@app.get("/manga", include_in_schema=False)
def manga_page():
    return RedirectResponse("/manga/")


@app.get("/api/manga", dependencies=[Depends(check_token_or_local)])
def manga_list():
    manga_ready()
    return manga_books.summary()


class MangaAdd(BaseModel):
    isbn: str


@app.post("/api/manga/add", dependencies=[Depends(check_token)])
def manga_add(body: MangaAdd):
    manga_ready()
    r = manga_books.add_any(body.isbn)
    if r.get("ok"):
        log(f"蔵書に追加: {manga_books.label(r['book'])}")
    return r


@app.post("/api/manga/remove", dependencies=[Depends(check_token)])
def manga_remove(body: MangaAdd):
    manga_ready()
    return {"ok": manga_books.remove_book(body.isbn)}


class MangaWatch(BaseModel):
    series: str
    watch: bool


@app.post("/api/manga/watch", dependencies=[Depends(check_token)])
def manga_watch(body: MangaWatch):
    manga_ready()
    return {"ok": manga_books.set_watch(body.series, body.watch)}


@app.post("/api/manga/check", dependencies=[Depends(check_token)])
def manga_check():
    manga_ready()
    told = manga_books.check_new()
    return {"told": told, "summary": manga_books.summary()}


@app.get("/sw.js")
def service_worker():  # サイト全体を受け持てるよう、ルートから配る
    return FileResponse(os.path.join(PWA_DIR, "sw.js"), media_type="text/javascript",
                        headers={"Cache-Control": "no-cache"})


@app.middleware("http")
async def no_cache(request: Request, call_next):
    """画面のファイルを iPhone に古いまま使い続けさせない（毎回、新しい版がないか確かめさせる）。"""
    response = await call_next(request)
    path = request.url.path
    if not path.startswith("/api") and path != "/command":
        response.headers["Cache-Control"] = "no-cache"
    if path.endswith("app.js") and request.headers.get("x-forwarded-for"):  # Tailscale 経由（iPhone など）で開いたとき
        log(f"画面を読み込み: {path}（{response.status_code}）")
    return response


if os.path.isdir(SHUKATSU_SITE):  # "/" より先に登録する（後だと PWA 側に吸われる）
    app.mount("/shukatsu", StaticFiles(directory=SHUKATSU_SITE, html=True), name="shukatsu")
if os.path.isdir(MANGA_SITE):
    app.mount("/manga", StaticFiles(directory=MANGA_SITE, html=True), name="manga")
app.mount("/", StaticFiles(directory=PWA_DIR, html=True), name="pwa")


# ---- 再起動（リモコンの中から自分を止めると起動し直せないので、合図ファイルで自分に再起動させる） ----
def restart_self():
    """数秒後に同じ Python で remote.py を起動し直す手配をしてから、自分は終了する。"""
    log("リモコンを再起動します")
    if remote.mem_timer:
        remote.mem_timer.cancel()
    if remote.mem_turns:  # 記憶係にまだ渡していない会話は、終わる前に保管庫へ残す
        turns, remote.mem_turns = remote.mem_turns, []
        try:
            memory._remember(turns, log)
        except Exception as e:
            log(f"記憶の保存に失敗: {e}")
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    script = os.path.join(HERE, "remote.py")
    subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command",
                      f"Start-Sleep 3; Start-Process -FilePath '{pythonw}' -ArgumentList '\"{script}\"' "
                      f"-WorkingDirectory '{HERE}'"],
                     # 本体の終了に巻き込まれないよう、親から切り離して起動する
                     creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
                     | subprocess.CREATE_BREAKAWAY_FROM_JOB)
    os._exit(0)


def watch_restart_flag():
    """合図ファイルを 1 秒おきに見る。依頼の処理中（返事を返す前）は待ち、返し終えてから再起動する。"""
    while True:
        time.sleep(1)
        if not os.path.exists(REMOTE_RESTART_FLAG):
            continue
        if not remote.lock.acquire(blocking=False):
            continue  # 返事を作っている最中
        remote.lock.release()
        time.sleep(2)  # 返事が iPhone に届くのを待つ
        if remote.lock.locked():
            continue
        try:
            os.remove(REMOTE_RESTART_FLAG)
        except OSError:
            pass
        restart_self()


def main():
    import uvicorn

    for name in ("stdout", "stderr"):  # 黒い画面なし（pythonw）で動くと出力先がなく、uvicorn が落ちるので捨て先を用意する
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
    if not TOKEN:
        raise SystemExit(f"合言葉が設定されていません。環境変数 {REMOTE_TOKEN_ENV} を設定してから起動してください（README の手順）")
    log(f"リモコンを起動します: http://{REMOTE_HOST}:{REMOTE_PORT}"
        + ("（Google Calendar API: 使用）" if gcal.ready() else "（Google Calendar API: 未設定。予定は Claude 経由）"))
    threading.Thread(target=remote.warmup, daemon=True).start()
    threading.Thread(target=watch_restart_flag, daemon=True).start()
    threading.Thread(target=get_stt, daemon=True).start()  # 会話ボタンの 1 回目から速く聞き取れるよう、先に読み込む
    uvicorn.run(app, host=REMOTE_HOST, port=REMOTE_PORT, log_level="warning")


if __name__ == "__main__":
    sys.exit(main())

