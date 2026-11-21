"""ジャービス — ウェイクワード → 音声認識 → Claude Code → 読み上げ、とダッシュボード表示。

「ジャービス、○○」と一息で話しても、「ジャービス」→ ピッ → 「○○」と区切っても使える。
返事のあと数秒は、呼びかけなしで続けて話せる。ダッシュボードのウィンドウを閉じると終了する。
"""
import ctypes
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import keyboard  # noqa: E402
import numpy as np  # noqa: E402
import webview  # noqa: E402

import sounddevice as sd  # noqa: E402

import approvals  # noqa: E402
import devinfo  # noqa: E402
import devmode  # noqa: E402
import info  # noqa: E402
import memory  # noqa: E402
import minutes  # noqa: E402
import timers  # noqa: E402
import local_llm  # noqa: E402
import mute  # noqa: E402
import vault  # noqa: E402
from audio import Mic, beep, play_sound  # noqa: E402
from brain import Brain  # noqa: E402
from config import (COMMAND_WAIT_SEC, CONFIRM_NO, CONFIRM_WAIT_SEC, CONFIRM_YES, DATA_DIR, HERE,  # noqa: E402
                    DASHBOARD_HOTKEY, END_PHRASES, FOLLOWUP_SEC, HOTKEY, LOCAL_LLM, LOG_DIR, MIN_ALWAYS_UTTERANCE_SEC, MODE_NAMES,
                    MODE_PHRASES, MORNING_BRIEF, MORNING_START_HOUR, REMINDERS_FILE, RING_MAX_SEC, SAMPLE_RATE, TASKS_FILE,
                    DISK_WARN_GB, POPUP_FILE,
                    SILENCE_SEC, SPEAKER_CHECK_ALWAYS, SPEAKER_CHECK_NORMAL, SPEAKER_MIN_SAMPLES, SPEAKER_THRESHOLD,
                    WAKE_FROM_SLEEP, DEV_HOTKEY, DEV_PHRASE_ON, DEV_PHRASE_OFF, DEV_TASKS_FILE, DEV_PROJECT_REFRESH_SEC)

MORNING_PROMPT = """（今日はじめての起動、またはスリープ解除です。これは自動のあいさつの依頼です。現在 {now}）
今の時刻に合ったあいさつ（朝ならおはようございます、昼はこんにちは、夜はこんばんは）の一文だけを最初に書き、
それから調べて、次の内容を 30 秒ほどで読み上げられる長さにまとめて伝えてください。
今日の日付と曜日、設定した地域の今日の天気（Open-Meteo）、Google カレンダーの今日の予定、
Gmail の未読のうち大事そうなもの最大 3 件の差出人と用件（広告やメルマガは除く。なければ触れない）。"""
DAILY_FILE = os.path.join(DATA_DIR, "daily.json")
MINUTES_PROMPT = """（会議の文字起こしができました。議事録をまとめる依頼です）
Read ツールで {path} を読み、同じフォルダに「{stem}_議事録.md」という名前で議事録を Write ツールで保存してください。
内容は、日時、概要（2〜3 行）、議題ごとの要点、決まったこと、やること（担当と期限が分かれば付ける）、未決事項。
文字起こしは音声認識なので誤字や聞き間違いがある。意味が通る範囲で直し、推測で事実を足さない。話者は区別できない。
保存したら、決まったことと、やることを中心に、読み上げ用に 3 文以内で伝え、「詳しくお伝えしましょうか」と添える。"""


def make_timer_beep(freq=2000, beeps=4, on=0.08, off=0.06, pause=0.5):
    """キッチンタイマーの電子音「ピピピピッ」1 回ぶん（最後に休み）。"""
    t = np.linspace(0, on, int(SAMPLE_RATE * on), False)
    # 矩形波に近い音（奇数倍音を重ねる）で電子音らしくし、端をなめらかにしてプツッという音を防ぐ
    tone = sum(np.sin(2 * np.pi * freq * k * t) / k for k in (1, 3, 5))
    ramp = np.minimum(1, np.minimum(t, on - t) / 0.004)
    beep = tone * ramp
    gap = np.zeros(int(SAMPLE_RATE * off))
    pattern = np.concatenate([np.concatenate([beep, gap]) for _ in range(beeps)] + [np.zeros(int(SAMPLE_RATE * pause))])
    return (0.25 * pattern / np.max(np.abs(pattern))).astype(np.float32)


CHIME = make_timer_beep()
CHIME_SEC = len(CHIME) / SAMPLE_RATE
BEEP_BEFORE_VOICE_SEC = 3  # 鳴り始めてから声で知らせるまで
FIRED_FILE = os.path.join(DATA_DIR, "fired_reminders.json")


def load_json(path, default):
    try:
        with open(path, encoding="utf-8-sig") as f:  # PowerShell で書かれると先頭に BOM が付くことがある
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def jp_time(hhmm):
    """"15:30" → "午後3時半"、"0:15" → "午前0時15分"。"""
    h, m = map(int, hhmm.split(":"))
    ampm = "午前" if h < 12 else "午後"
    return f"{ampm}{h % 12}時" + ("" if m == 0 else "半" if m == 30 else f"{m}分")


def morning_due():
    """1 日の始まり（朝 6 時）を過ぎていて、今日まだあいさつしていなければ True。起動時とスリープ解除時に確かめる。"""
    now = datetime.now()
    return (MORNING_BRIEF and now.hour >= MORNING_START_HOUR
            and load_json(DAILY_FILE, {}).get("briefed") != now.strftime("%Y-%m-%d"))
from concurrent.futures import ThreadPoolExecutor  # noqa: E402

from speaker import SpeakerID  # noqa: E402
from stt import STT
from tts import Voice, speakable
from ui import Dashboard, start_proxy
from wake import WakeDetector


def log(msg):
    os.makedirs(LOG_DIR, exist_ok=True)
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:   # 📎 などの絵文字がコンソール（cp932）で出せず、処理ごと落ちるのを防ぐ
        print(line.encode("cp932", "replace").decode("cp932"), flush=True)
    with open(os.path.join(LOG_DIR, f"{datetime.now():%Y-%m}.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


LOG_LINE = re.compile(r"^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):\d{2} (.*)$")
LOG_ROLES = (("あなた: ", "user"), ("ジャービス: ", "jarvis"), ("お知らせ: ", "jarvis"), ("[鳴動] ", "jarvis"))


def load_history(limit=300):
    """logs/*.log から過去の会話を読み出す（ダッシュボードのログ欄用）。"""
    # 月ごとの会話ログ（2026-11.log など）だけを読む。manaba.log のような別の記録が名前順で後ろに来ても、会話が押し出されないように
    files = (sorted(f for f in os.listdir(LOG_DIR) if re.match(r"\d{4}-\d{2}.*\.log$", f))[-2:]
             if os.path.isdir(LOG_DIR) else [])
    items, today = [], datetime.now().strftime("%m/%d")
    for name in files:
        with open(os.path.join(LOG_DIR, name), encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.rstrip("\n")
                m = LOG_LINE.match(line)
                if not m:  # 複数行の返事の続き
                    if items and items[-1].get("open") and line.strip():
                        items[-1]["text"] += "\n" + line
                    continue
                if items:
                    items[-1]["open"] = False
                rest = m.group(6)
                for prefix, role in LOG_ROLES:
                    if rest.startswith(prefix):
                        day = f"{m.group(2)}/{m.group(3)}"
                        time_label = f"{m.group(4)}:{m.group(5)}" if day == today else f"{day} {m.group(4)}:{m.group(5)}"
                        items.append({"role": role, "text": rest[len(prefix):], "time": time_label, "open": True})
                        break
    for i in items:
        i.pop("open", None)
        i["text"] = speakable(i["text"])
    return [i for i in items if i["text"]][-limit:]


RESTART_FLAG = os.path.join(DATA_DIR, "restart.flag")  # このファイルが置かれたら再起動する（AI や Claude Code が使う）
SHOW_FLAG = os.path.join(DATA_DIR, "show.flag")  # このファイルが置かれたら画面を出す（起動中にもう一度起動されたとき）
QUIT_PHRASE = re.compile(r"^(ジャービス)(を)?(終了|シャットダウン)(して|しよう|お願い|してください)?$")
DASHBOARD_PHRASE = re.compile(r"^(ダッシュボード|画面)(を)?(出して|表示して|見せて|開いて)$")
# 「蔵書管理」「蔵書教えて」「漫画一覧見たい」など → ダッシュボードの窓に蔵書の画面を出す
MANGA_PHRASE = re.compile(r"(蔵書|漫画一覧|マンガ一覧|まんが一覧|漫画の一覧|マンガの一覧|漫画リスト|マンガリスト)")
MANGA_ASK = re.compile(r"(新刊|何巻|なんかん|持って|続き|いつ|追加|登録|買)")
RESTART_PHRASE = re.compile(r"^(ジャービス)?(を)?(再起動|リスタート)(して|しよう|お願い|してください)?$")


def restart_self():
    """数秒後に ジャービス.lnk で起動し直す手配をしてから、自分は終了する（二重起動防止のロックも外れる）。"""
    log("再起動します")
    devmode.save_for_restart()  # 開発モード中なら、起動し直したあとも続ける
    # どの PC でも動くよう、ショートカットではなく、いま動いている Python（の pythonw）で起動し直す
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    script = os.path.join(HERE, "jarvis.py")
    subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command",
                      f"Start-Sleep 3; Start-Process -FilePath '{pythonw}' -ArgumentList '\"{script}\"' "
                      f"-WorkingDirectory '{HERE}'"],
                     # 本体の終了に巻き込まれないよう、親から切り離して起動する（DETACHED_PROCESS だと一緒に消える）
                     creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
                     | subprocess.CREATE_BREAKAWAY_FROM_JOB)
    os._exit(0)


def start_manaba():
    """manaba の巡回（manaba.py）を別プロセスで起動する。ID・パスワードが未登録なら何もしない（重い処理を本体に混ぜない）。"""
    try:
        from config import MANABA_ENABLED
        import manaba
        if not MANABA_ENABLED or not manaba.has_credentials():
            log("manaba の巡回は未設定のため起動しません（python manaba.py setup で登録）")
            return
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        subprocess.Popen([pythonw, os.path.join(HERE, "manaba.py"), "run"], cwd=HERE,
                         creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
        log("manaba の巡回を起動しました")
    except Exception:
        log("manaba の巡回を起動できません:\n" + traceback.format_exc())


def restart_if_requested():
    if os.path.exists(RESTART_FLAG):
        os.remove(RESTART_FLAG)
        restart_self()


def show_if_requested(ui):
    if os.path.exists(SHOW_FLAG):
        os.remove(SHOW_FLAG)
        ui.show()


# 「私の声を覚えて」「声の登録をやり直して」
ENROLL_PHRASE = re.compile(r"^(ジャービス)?(私|僕|ぼく|俺|おれ|自分)?の?声(を|の登録を?)?"
                           r"(覚えて|登録して|学習して|(?P<again>覚え直して|やり直して|リセットして))(ください)?$")
CALENDAR_WRITE = re.compile(r"Google_Calendar__(create|update|delete|respond_to)_event")


def push_timers(ui):
    ui.timers([{"kind": i["kind"], "title": i.get("title", ""), "at": at.timestamp() * 1000}
               for at, i in timers.active()])


def load_tasks():
    """画面に出すタスク。完了にした日（done_at）を記録し、日付が変わったら完了済みを隠す（ファイルには残す）。"""
    tasks = load_json(TASKS_FILE, [])
    if not isinstance(tasks, list):
        return []
    today = datetime.now().strftime("%Y-%m-%d")
    changed = False
    for t in tasks:
        if t.get("done") and not t.get("done_at"):
            t["done_at"] = today
            changed = True
        elif not t.get("done") and t.get("done_at"):
            del t["done_at"]
            changed = True
    if changed:
        save_json(TASKS_FILE, tasks)
    return [t for t in tasks if not (t.get("done") and t.get("done_at", today) < today)]


MODE_FILE = os.path.join(DATA_DIR, "mode.json")
MODE_ANNOUNCE = {
    "normal": "通常モードにしました。ご用の際は、ジャービスとお呼びください。",
    "always": "常時会話モードにしました。お呼びにならなくても、そのまま話しかけてください。",
    "sleep": "おやすみなさいませ。起きて、とお声がけいただくまで休みます。",
}


def mode_command(text):
    """「スリープ」「常時会話モード」などモードを切り替える一言なら、切り替え先を返す。"""
    plain = re.sub(r"[、。,.!！?？\s]", "", text)
    if len(plain) > 20:
        return None
    for mode, pattern in MODE_PHRASES.items():
        if re.search(pattern, plain):
            return mode
    return None


DEV_ON_TEXT = "開発モードに入ります。確認は省略しますが、メールの送信などは保留にして、あとでお伺いします。"
DEV_OFF_TEXT = "開発モードを終えました。"
DEV_AUTO_OFF_TEXT = "操作がしばらくなかったので、開発モードを終えました。"
DEV_UNTIL_OFF_TEXT = "お決めの時刻になりましたので、開発モードを終えました。"
HELD_APPROVE = re.compile(r"保留(を|は)?(全部|すべて|全て)?(承認|許可|オッケー|オーケー|ok|通して|実行|進めて)", re.I)
HELD_REJECT = re.compile(r"保留(を|は)?(全部|すべて|全て)?(却下|キャンセル|破棄|やめ|取り消|なし)")


def dev_command(text):
    """開発モードに入る一言なら True、出る一言なら False、どちらでもなければ None。"""
    plain = re.sub(r"[、。,.!！?？\s]", "", text)
    if len(plain) > 20:
        return None
    if re.search(DEV_PHRASE_OFF, plain):
        return False
    if re.search(DEV_PHRASE_ON, plain):
        return True
    return None


def held_command(text):
    """開発モード中の「保留を承認」「保留を却下」。"approve" / "reject" / None。"""
    plain = re.sub(r"[、。,.!！?？\s]", "", text)
    if len(plain) > 20:
        return None
    if HELD_REJECT.search(plain):
        return "reject"
    if HELD_APPROVE.search(plain):
        return "approve"
    return None


class ReplyView:
    """返事を、文ができるそばから画面に出す（全文のできあがりを待たない）。
    1 文目を話し始めたあと、次の文までに時間が空いても、1 文目はもう画面に出ている。話す内容は必ず画面にも出る。"""

    def __init__(self, ui):
        self.ui, self.began = ui, False

    def sentence(self, text):
        if self.began:
            self.ui.reply_append(text)
        else:
            self.began = True
            self.ui.reply_begin(text)

    def finish(self, full):
        """返事が終わったとき。出ていた分を、整えた全文に置き換える（出ていなければ、全文を今出す）。"""
        if self.began:
            self.ui.reply_end(full)
        elif full:
            self.ui.message("jarvis", full)

    def discard(self):
        """出した分を取り消す（PC 内の AI の返事を捨てて、Claude に回すとき）。"""
        if self.began:
            self.began = False
            self.ui.reply_discard()


def load_dev_tasks():
    tasks = load_json(DEV_TASKS_FILE, [])
    return tasks if isinstance(tasks, list) else []


def dev_loop(ui):
    """開発モード中だけ、git の状態と、確認を省略した操作・保留の一覧を画面に送り続ける。"""
    last, next_info = None, 0
    while True:
        try:
            snap = devmode.snapshot()
            if snap["active"]:
                if time.time() >= next_info:
                    next_info = time.time() + DEV_PROJECT_REFRESH_SEC
                    ui.dev_info(devinfo.collect())
                activity = {"events": snap["events"][-8:], "held": approvals.held(), "since": snap["since"],
                            "until": snap["until"]}
                if activity != last:
                    last = activity
                    ui.dev_activity(activity)
            else:
                last, next_info = None, 0
        except Exception as e:
            log(f"開発モードの情報の送信に失敗: {e}")
        time.sleep(1)


def confirmation_answer(text):
    """「よろしいですか」への返事を "yes" / "no" / None（どちらでもない）で返す。"""
    plain = re.sub(r"[、。,.!！?？\s]", "", text.lower())
    if re.search(CONFIRM_NO, plain):
        return "no"
    if re.search(CONFIRM_YES, plain) and len(plain) <= 15:
        return "yes"
    return None


def end_reply(text):
    """「ありがとう」など会話を終える一言なら、その返事を返す。用件が続いていれば None。"""
    plain = re.sub(r"[、。,.!！?？\s〜ー]+$|[、。,.!！?？\s]", "", text)
    for phrase, reply in END_PHRASES.items():
        # 「ありがとう、ついでに明日の予定も」のように用件が続くときは終わらせない
        if phrase in plain and len(plain) <= len(phrase) + 6:
            return reply
    return None


class Jarvis:
    def __init__(self, ui):
        self.ui = ui
        log("起動中…（初回はモデルの準備で数分かかります）")
        self.stt = STT()
        self.minutes = minutes.Minutes()
        log(f"音声認識: {getattr(self.stt, 'device', 'CPU')}（{self.stt.engine}）")
        self.pool = ThreadPoolExecutor(max_workers=2)
        try:
            self.speaker = SpeakerID()
            log(f"話者認識: 声を {len(self.speaker.prints)} 件覚えています"
                + ("" if self.speaker.ready else f"（{SPEAKER_MIN_SAMPLES} 件たまるまでは、どの声にも反応します）"))
        except Exception as e:
            self.speaker = None
            log(f"話者認識を使えません: {e}")
        self.wake = WakeDetector()
        self.voice = Voice()
        self.brain = Brain()
        self.mic = Mic()
        self.mic.on_level = ui.level
        for reply in set(END_PHRASES.values()):
            self.voice.prepare(reply)  # 締めの返事はすぐ言えるよう先に作っておく
        self.hotkey = threading.Event()
        keyboard.add_hotkey(HOTKEY, self.hotkey.set)
        keyboard.add_hotkey(DASHBOARD_HOTKEY, ui.toggle)  # ダッシュボードを出す・隠す
        self.dev_toggle = threading.Event()
        keyboard.add_hotkey(DEV_HOTKEY, self.dev_toggle.set)  # 開発モードの入り切り
        for text in (DEV_ON_TEXT, DEV_OFF_TEXT, DEV_AUTO_OFF_TEXT, DEV_UNTIL_OFF_TEXT):
            self.voice.prepare(text)
        self.awaiting = False  # 取り返しのつかない操作の確認待ち
        self.cancel = threading.Event()  # 考え中・話し中に割り込まれた
        self.current_speech = None
        self.barged = None               # 割り込みの「ジャービス、○○」の音声（STOP ボタンなら None）
        self.voice.prepare("かしこまりました。取りやめます。")
        self.turns = []        # 今の会話のやりとり（終わったら記憶係に渡す）
        self.local_turns = []  # Claude を通さずに返したやりとり（次に Claude へ話すとき添える）
        if LOCAL_LLM:
            self.pool.submit(self._warm_local)
        self.next_reminder_check = 0
        saved = load_json(MODE_FILE, {})
        self.mode = saved.get("mode") if saved.get("mode") in MODE_NAMES else "normal"
        self.mode_before_sleep = saved.get("before_sleep", "normal")
        self.missed = []  # スリープ中に鳴らさなかったお知らせ（解除したら伝える）
        for text in MODE_ANNOUNCE.values():
            self.voice.prepare(text)
        self._warmup()

    def _warm_local(self):
        try:
            if local_llm.available():
                local_llm.warmup()
                log("PC 内の AI の準備ができました")
            else:
                log("PC 内の AI（Ollama）が見つからないので、雑談も Claude が答えます")
        except Exception as e:
            log(f"PC 内の AI の準備に失敗: {e}")

    def _warmup(self):
        """先に Claude Code に接続しておき、最初の返事から速くする。PC 起動直後でネットが未接続なら少し待つ。"""
        for attempt in range(6):
            try:
                log("Claude Code に接続中…")
                self.brain.warmup()
                return
            except Exception as e:
                log(f"接続できませんでした（{e}）。10 秒後にやり直します")
                time.sleep(10)
        log("接続できないまま起動します。話しかけたときに改めて接続します")

    def run(self):
        self.mic.start()
        log(f"起動しました（{MODE_NAMES[self.mode]}）。「ジャービス」と呼ぶか {HOTKEY.upper()} を押してください")
        self.ui.mode(self.mode)
        resumed = devmode.resume()  # 開発中の再起動なら、開発モードを続ける（演出なしで画面を切り替える）
        if resumed:
            log("開発モードを続けています" + ("（時間指定）" if resumed == "until" else "（再起動から）"))
            self.ui.dev(True, quiet=True)
        if self.mode != "sleep":
            self.ui.state("speaking")
            if morning_due():  # その日最初の起動なら、あいさつ（天気・予定・メール）
                self._morning_brief()
            else:
                greeting = "再起動しました。開発モードを続けます。" if resumed else "起動しました。"
                self.ui.message("jarvis", greeting)  # 話す内容は必ず画面にも出す
                self.voice.say(greeting)
        self._back_to_idle()
        while True:
            typed = self.ui.take_text_request()  # ダッシュボードの入力欄から送られた文・ファイル
            if typed:
                self.handle_typed(*typed)
                continue
            requested = self.ui.take_mode_request()  # ダッシュボードのモードボタン
            if requested == "dev":  # 開発ボタン（押すたびに入り切り）
                self.set_dev(not devmode.active)
            elif requested and requested != self.mode:
                self.set_mode(requested)
            if self.dev_toggle.is_set():
                self.dev_toggle.clear()
                self.set_dev(not devmode.active)
            expired = devmode.expired()  # "until"（時間指定の期限）か "idle"（操作のない時間）
            if expired:
                self.set_dev(False, auto=expired)
            held_request = self.ui.take_held_request()  # 画面の保留リストの「承認」「却下」ボタン
            if held_request:
                self.handle_held(*held_request)
                continue
            if self.mode == "sleep":
                if time.time() >= self.next_reminder_check:
                    self.next_reminder_check = time.time() + 1
                    self._remind_if_due(silent=True)  # 鳴らさずに記録だけしておく
            else:
                self._proactive()
            if self.hotkey.is_set():
                self.hotkey.clear()
                if self.mode == "sleep":
                    self.set_mode(self.mode_before_sleep)
                elif mute.is_muted("mic"):
                    self.set_mute("mic", False)  # マイクのミュート中にホットキー → マイクを戻す
                else:
                    self.converse(np.zeros(0, np.int16))
                continue
            if mute.is_muted("mic") and self.mode != "sleep":
                # マイクのミュート中は「ジャービス、マイクオン」だけを聞き分ける。音は PC の中で処理し、Claude には送らない
                try:
                    block = self.mic.read(timeout=0.3, raw=True)
                except queue.Empty:
                    continue
                command = self.wake.feed(block)
                if command is not None:
                    self._maybe_unmute_mic(command)
                continue
            if self.minutes.active:
                # 議事録の録音中: 音を書き溜めるだけ。「ジャービス、議事録を終わって」で止める
                try:
                    block = self.mic.read(timeout=0.3)
                except queue.Empty:
                    continue
                self.minutes.feed(block)
                command = self.wake.feed(block)
                if command is not None:
                    log("呼びかけを検出（議事録の録音中）")
                    self.converse(command)
                continue
            if self.mode == "always":
                # 呼びかけなしで、話した言葉をそのまま受け取る（1 秒ずつ区切って、ほかの用事も見る）
                audio = self.mic.record_utterance(1.0)
                if audio is not None and len(audio) >= SAMPLE_RATE * MIN_ALWAYS_UTTERANCE_SEC:
                    self.converse(audio, direct=True)
                continue
            try:
                block = self.mic.read(timeout=0.3)
            except queue.Empty:
                continue
            self.mic.track_noise(block)
            command = self.wake.feed(block)
            if command is None:
                continue
            if self.mode == "sleep":
                self._maybe_wake_up(command)
            else:
                log("呼びかけを検出")
                self.converse(command)

    def set_mode(self, mode):
        if mode == "sleep" and self.mode != "sleep":
            self.mode_before_sleep = self.mode
        was_sleep = self.mode == "sleep"
        self.mode = mode
        if was_sleep and mode != "sleep":
            self.ui.show()  # ×で閉じて隠れていた画面も、スリープを解いたら出し直す
        save_json(MODE_FILE, {"mode": mode, "before_sleep": self.mode_before_sleep})
        log(f"モード: {MODE_NAMES[mode]}")
        self.ui.mode(mode)
        brief = was_sleep and mode != "sleep" and morning_due()  # その日最初のスリープ解除なら、あいさつ
        message = "スリープを解除しました。" if was_sleep else MODE_ANNOUNCE[mode]
        if was_sleep and mode != "normal":
            message += f"{MODE_NAMES[mode]}に戻ります。"
        if was_sleep and self.missed:
            message += "お休みの間に、" + "、".join(self.missed) + "がございました。"
            self.missed = []
        elif brief:
            message = ""  # このあとのあいさつに任せる
        if message:
            self.ui.message("jarvis", message)
            self.ui.state("speaking")
            self.voice.say(message, stop_event=self.hotkey)
        if brief:
            self._morning_brief()
        self._back_to_idle()

    def set_dev(self, on, auto=False, speak=True):
        """開発モードの入り切り。入っている間は、ガードの確認を省略する（メール送信などは保留リストへ）。
        auto は自動で切るときの理由（"until" か "idle"）。speak=False なら、あとで別の言葉を話すので黙っておく。"""
        if on == devmode.active:
            return
        if on:
            approvals.clear_held()
            devmode.enable()
        else:
            leftover = len(approvals.held())
            devmode.disable()
            approvals.clear_held()
            if leftover:
                log(f"開発モードを終えたので、保留 {leftover} 件を破棄しました")
        log(f"開発モード: {'オン' if on else 'オフ'}" + ("（自動）" if auto else ""))
        self.ui.dev(on)
        message = DEV_ON_TEXT if on else (DEV_UNTIL_OFF_TEXT if auto == "until" else DEV_AUTO_OFF_TEXT if auto else DEV_OFF_TEXT)
        if not speak:
            return
        self.ui.message("jarvis", message)
        if self.mode != "sleep":
            self.ui.state("speaking")
            self.voice.say(message, stop_event=self.hotkey)
        self._back_to_idle()

    def handle_held(self, action, fp):
        """保留リストの承認・却下。承認したら、AI に同じ操作をやり直してもらう。"""
        if action == "reject":
            approvals.reject_held(fp)
            log("保留を却下しました")
            return
        descs = approvals.approve_held(fp)
        if not descs:
            return
        log("保留を承認: " + " / ".join(descs))
        prompt = ("ユーザーが保留していた操作を承認しました。次の操作を、保留したときとまったく同じ内容でもう一度実行してください: "
                  + " ／ ".join(descs))
        self.handle_typed(prompt, [])

    def set_mute(self, kind, muted):
        """kind: "mic" / "speaker" / "both"。声での切り替えとホットキーから使う（画面のボタンは ui.py が直接切り替える）。"""
        for k in (("mic", "speaker") if kind == "both" else (kind,)):
            if k == "speaker" and muted:
                self.ui.message("jarvis", "スピーカーをミュートします。")
                self._say_now("スピーカーをミュートします。")  # 黙る前に一言
            mute.set_muted(k, muted)
        self.ui.mutes(mute.state())
        name = {"mic": "マイク", "speaker": "スピーカー", "both": "ミュート", "voiceprint": "声紋認証"}[kind]
        if kind == "mic" and muted:
            message = "マイクをミュートしました。戻すときは、ジャービス、マイクオン、とお声がけください。"
        elif kind == "speaker" and muted:
            message = "スピーカーをミュートしました。"
        elif kind == "voiceprint":
            if muted:
                message = "声紋認証をオフにしました。どなたのお声にも反応いたします。"
            elif self.speaker and self.speaker.ready:
                message = "声紋認証をオンにしました。ほかの方のお声には反応いたしません。"
            else:
                message = "声紋認証をオンにしました。ただ、お声の登録がまだですので、私の声を覚えて、とお申し付けください。"
        else:
            message = f"{name}を解除しました。" if kind == "both" else f"{name}をオンにしました。"
        log(message)
        self.ui.message("jarvis", message)
        if not (kind == "speaker" and muted):
            self._say_now(message)
        self._back_to_idle()

    def enroll_voice(self, reset=False):
        """「私の声を覚えて」: その場で何回か話してもらい、本人の声として覚える。
        照合を始めるのに足りない回数ぶん（最低 3 回）を 1 回の登録でまとめて取る。画面は登録専用の色と表示になる。"""
        if reset:
            self.speaker.reset()
        times = max(3, SPEAKER_MIN_SAMPLES - len(self.speaker.prints))
        self.ui.state("enroll")
        self.ui.enroll(0, times, "intro")
        intro = f"かしこまりました。ピッという合図のあとに、何か一言お話しください。これを{times}回繰り返します。"
        self.ui.message("jarvis", intro)
        self.voice.say(intro, stop_event=self.hotkey)
        got, tries = 0, 0
        while got < times and tries < times + 2:  # 聞き取れなかった回は、2 回までやり直す
            tries += 1
            self.mic.drain()
            self.ui.enroll(got + 1, times, "listen")
            beep()
            audio = self.mic.record_utterance(8)
            if audio is not None and self.speaker.learn(audio):
                got += 1
                self.ui.enroll(got, times, "ok")
                beep(1320, 140)  # 覚えた合図（少し高い音）
            else:
                self.ui.enroll(got + 1, times, "retry")
                self.ui.message("jarvis", "もう少し長めにお話しください。")
                self.voice.say("もう少し長めにお話しください。", stop_event=self.hotkey)
        n = len(self.speaker.prints)
        message = (f"ありがとうございます。お声を覚えました。今後は、ほかの方のお声には反応いたしません。" if self.speaker.ready
                   else f"ありがとうございます。あと{SPEAKER_MIN_SAMPLES - n}回ほど、お声を覚えれば準備が整います。")
        if got == 0:
            message = "お声が聞き取れませんでした。もう一度、私の声を覚えて、とお申し付けください。"
        log(f"話者認識: 声を {n} 件覚えています")
        self.ui.message("jarvis", message)
        self._say_now(message)
        self._back_to_idle()

    def _say_now(self, text):
        self.ui.state("speaking")
        self.voice.say(text, stop_event=self.hotkey)

    def _maybe_unmute_mic(self, audio):
        """マイクのミュート中に「ジャービス」と聞こえたとき。マイクを戻す言葉のときだけ解除し、それ以外は黙って無視する。"""
        if len(audio) < SAMPLE_RATE * 0.4:
            audio = self.mic.record_utterance(COMMAND_WAIT_SEC, raw=True)
        else:
            rest = self.mic.record_utterance(SILENCE_SEC, raw=True)
            if rest is not None:
                audio = np.concatenate([audio, rest])
        text = self.stt.transcribe(audio) if audio is not None else ""
        command = mute.voice_command(text) if text else None
        if command and command[0] in ("mic", "both") and not command[1]:
            log(f"あなた: {text}")
            self.ui.message("user", text)
            self.set_mute(command[0], False)
        else:
            self.mic.drain()
            self.wake.reset()

    def _maybe_wake_up(self, audio):
        """スリープ中に「ジャービス」と聞こえたとき。「起きて」などのときだけ解除し、それ以外は黙って無視する。"""
        if len(audio) < SAMPLE_RATE * 0.4:
            audio = self.mic.record_utterance(COMMAND_WAIT_SEC)
        else:
            rest = self.mic.record_utterance(SILENCE_SEC)
            if rest is not None:
                audio = np.concatenate([audio, rest])
        text = self.stt.transcribe(audio) if audio is not None else ""
        if text and re.search(WAKE_FROM_SLEEP, text):
            log(f"あなた: {text}")
            self.ui.message("user", text)
            self.set_mode(self.mode_before_sleep)
        else:
            self.mic.drain()
            self.wake.reset()

    def converse(self, audio, direct=False):
        """direct=True は常時会話モード（呼びかけなしで聞き取った発言をそのまま処理する）。"""
        self.ui.state("listening")
        if direct:
            pass
        elif len(audio) < SAMPLE_RATE * 0.4:  # 「ジャービス」だけで止まった → 用件を待つ
            beep()
            audio = self.mic.record_utterance(COMMAND_WAIT_SEC)
            if audio is None:
                beep(440)
                self._back_to_idle()
                return
        else:  # 読点などの短い間で区切られただけなら、続きも拾う
            rest = self.mic.record_utterance(SILENCE_SEC)
            if rest is not None:
                audio = np.concatenate([audio, rest])
        if not direct and self.speaker:
            # 「ジャービス」と呼びかけた声はほぼ本人なので、声紋として覚える（裏で動かして待たせない）
            self.pool.submit(self.speaker.learn, audio)
        while audio is not None:
            keep_going = self.turn(audio)
            if self.cancel.is_set():  # 考え中・話し中に割り込まれた → 新しい発言を聞いて続ける
                audio = self._next_after_interrupt()
                continue
            if not keep_going:  # 「ありがとう」などで会話終了
                break
            self.mic.drain()
            self.ui.state("confirm" if self.awaiting else "listening")
            # 続けて話せば会話が続く。確認待ちのときは少し長めに待つ
            # 入力欄から文が送られたら、待つのをやめてそちらを先に処理する
            audio = self.mic.record_utterance(CONFIRM_WAIT_SEC if self.awaiting else FOLLOWUP_SEC, abort=self.ui.has_text)
            if audio is None and self.awaiting and self.ui.has_text():
                self.handle_typed(*self.ui.take_text_request())  # 確認待ちへの返事を入力で受けた
                return
        self._back_to_idle()

    def turn(self, audio):
        """1往復ぶん。会話を続けるなら True、終わりの言葉なら False。"""
        t0 = time.time()
        check = (self.speaker and not mute.is_muted("voiceprint")  # 声紋認証をオフにしていれば照合しない
                 and (SPEAKER_CHECK_ALWAYS if self.mode == "always" else (SPEAKER_CHECK_NORMAL or devmode.active)))  # 開発モード中は通常モードでも声紋を照合
        # 声の照合（約 1 秒）は音声認識と同時に走らせて、待ち時間を増やさない
        owner = self.pool.submit(self.speaker.score, audio) if check else None
        text = self.stt.transcribe(audio, confirm=self.awaiting)
        t_stt = time.time() - t0
        if owner is not None:
            score = owner.result()
            if score is not None and score < SPEAKER_THRESHOLD:
                log(f"ほかの人の声として無視（似ている度合い {score:.2f}）: {text}")
                return False
        if not text:
            if self.mode == "always":
                return False  # 常時会話モードでは、物音などで聞き返さない
            self.ui.state("speaking")
            self.ui.message("jarvis", "申し訳ございません、聞き取れませんでした。")
            self.voice.say("申し訳ございません、聞き取れませんでした。")
            return True
        log(f"あなた: {text}")
        self.ui.message("user", text)
        return self.handle_text(text, timing_prefix=f"認識 {t_stt:.1f}s / ")

    def handle_typed(self, text, files):
        """ダッシュボードの入力欄から送られた文（と添付ファイル）。確認待ちになったら、声か入力での返事を待つ。"""
        keep = self._typed_once(text, files)
        while keep and self.awaiting:
            self.ui.state("confirm")
            audio = self.mic.record_utterance(CONFIRM_WAIT_SEC, abort=self.ui.has_text)
            if audio is not None:
                keep = self.turn(audio)
                continue
            request = self.ui.take_text_request()
            if request is None:
                break
            keep = self._typed_once(*request)
        self._back_to_idle()

    def _typed_once(self, text, files):
        names = [os.path.basename(p).split("_", 2)[-1] for p in files]
        shown = text + (("\n" if text else "") + "📎 " + "、".join(names) if names else "")
        log(f"あなた（入力）: {shown}")
        self.ui.message("user", shown)
        if not files:
            return self.handle_text(text, timing_prefix="入力 / ")
        prompt = (text or "添付したファイルを確認して、内容を簡単に教えて") + (
            "\n\n（ユーザーがダッシュボードからファイルを添付しました。Read ツールで中身を確認してから答えてください: "
            + "、".join(files) + "）")
        return self.handle_text(text or prompt, claude_text=prompt, timing_prefix="入力 / ")

    def handle_text(self, text, claude_text=None, timing_prefix=""):
        """声でも文字入力でも共通の処理。claude_text は Claude に渡す文（添付ファイルの案内付きなど。なければ text）。
        会話を続けるなら True、終わりの言葉なら False。"""
        devmode.touch()  # 開発モードの自動オフまでの時間をリセット
        if self.awaiting:
            # 承認はここ（ユーザーの声・入力を受けたジャービス本体）でだけ記録する。AI が「承認された」と言っても通らない
            self.awaiting = False
            answer = confirmation_answer(text)
            if answer == "yes":
                log("承認: " + " / ".join(p["desc"] for p in approvals.pending()))
                approvals.approve_pending()
            else:
                approvals.clear_pending()
            if answer == "no":
                return self._close("かしこまりました。取りやめます。")
        else:
            approvals.clear_pending()
            if RESTART_PHRASE.match(re.sub(r"[、。,.!！?？\s]", "", text)):
                self._close("再起動します。少々お待ちください。")
                restart_self()
            if QUIT_PHRASE.match(re.sub(r"[、。,.!！?？\s]", "", text)):
                self._close("終了いたします。またいつでもお呼びください。")
                log("「終了して」と頼まれたので終了します")
                self.ui.quit()
                return False
            if DASHBOARD_PHRASE.search(re.sub(r"[、。,.!！?？\s]", "", text)):
                self.ui.show()
                return self._close("画面をお出ししました。")
            plain_m = re.sub(r"[、。,.!！?？\s]", "", text)
            if len(plain_m) <= 20 and MANGA_PHRASE.search(plain_m) and not MANGA_ASK.search(plain_m):
                self.ui.show()
                self.ui.show_manga()
                return self._close("蔵書の画面をお出ししました。")
            enroll = ENROLL_PHRASE.search(re.sub(r"[、。,.!！?？\s]", "", text))
            if enroll and self.speaker:
                self.enroll_voice(reset=bool(enroll.group("again")))
                return False
            muting = mute.voice_command(text)
            if muting:
                kind, muted = muting
                already = (all(not mute.is_muted(k) for k in ("mic", "speaker")) if kind == "both"
                           else mute.is_muted(kind) == muted)
                if already:
                    name = {"mic": "マイク", "speaker": "スピーカー", "both": "マイクもスピーカーも",
                            "voiceprint": "声紋認証"}[kind]
                    off = "オフ" if kind == "voiceprint" else "ミュート中"
                    return self._close(f"{name}はすでに{off if muted else 'オン'}です。")
                self.set_mute(kind, muted)
                return False
            plain_text = re.sub(r"[、。,.!！?？\s]", "", text)
            if self.minutes.active and minutes.is_stop(plain_text):
                seconds = self.minutes.stop(self.stt, log)
                return self._close(f"議事録の録音を終えました。{max(1, round(seconds / 60))}分ほどです。"
                                   "文字起こしを裏で進めますので、まとまりましたらお知らせします。")
            if minutes.is_start(plain_text):
                if self.minutes.active:
                    return self._close("すでに議事録を取っております。")
                if self.minutes.working:
                    return self._close("前の会議の文字起こしを進めております。終わってからお受けします。")
                self.minutes.start()
                log("議事録: 録音開始")
                return self._close("議事録を取り始めます。終わりましたら、ジャービス、議事録を終わって、とお伝えください。")
            timed = devmode.until_command(text)  # 「7時まで作業してOK」「2時間開発モードで」「時間指定をやめて」
            if timed:
                kind, ts, label = timed
                if kind == "clear":
                    devmode.set_until(0)
                    return self._close("時間の指定を外しました。操作がしばらくなければ、自動で終えます。")
                if not devmode.active:
                    self.set_dev(True, speak=False)
                devmode.set_until(ts)
                log(f"開発モード: {label}まで")
                return self._close(f"かしこまりました。{label}まで開発モードを続けます。")
            dev = dev_command(text)  # 「開発モード」「開発モード終わり」
            if dev is not None:
                if dev != devmode.active:
                    self.set_dev(dev)
                else:
                    self._close("すでに開発モードです。" if dev else "開発モードではありません。")
                return False
            if devmode.active and approvals.held():  # 「保留を承認して」「保留を却下して」
                action = held_command(text)
                if action:
                    self.handle_held(action, None)
                    return False
            mode = mode_command(text)
            if mode:
                if mode != self.mode:
                    self.set_mode(mode)
                else:
                    self._close(f"すでに{MODE_NAMES[mode]}です。")
                return False
            closing = end_reply(text)
            if closing:
                return self._close(closing)
            if morning_due() and not claude_text:  # その日最初の応答なら、先にあいさつ（天気・予定・メール）
                self._morning_brief()
                if local_llm.is_small_talk(text):  # 「おはよう」だけなら、あいさつで済ませる
                    return True
            memo = vault.memo_command(text)  # 「○○をメモして」は Claude を通さず保管庫のメモ/にすぐ保存
            if memo:
                try:
                    title = vault.save_memo(memo)
                    log(f"メモ: {title}")
                    return self._close("メモしました。")
                except OSError as e:
                    log(f"メモを保存できません: {e}")
                    return self._close("申し訳ございません、メモを保存できませんでした。")
            action = timers.handle(text)  # 「3分のタイマー」などは Claude を通さずすぐセット
            if action:
                return self._close(self._timer_action(*action))
            query = vault.recall_query(text)  # 「○○って何だっけ」「前に話した○○」は先に保管庫を探して添える
            if query is not None:
                claude_text = vault.search_context(query) + (claude_text or text)
            instant = None if claude_text else local_llm.instant_reply(text)  # 「今何時？」などは AI を使わずその場で答える
            if instant:
                self._reply_now(text, instant)
                return True
            if LOCAL_LLM and not claude_text and local_llm.is_small_talk(text):  # あいさつや雑談は PC 内の AI に任せる
                reply = self.respond_local(text, timing_prefix)
                if reply is not None:
                    return True

        reply = self.respond(self._with_local_context(claude_text or text), timing_prefix)
        self.turns.append((text, speakable(reply)))
        if self.cancel.is_set():  # 途中で止めたので、止める前に積まれた確認待ちも取り消す
            approvals.clear_pending()
            return True
        self.awaiting = bool(approvals.pending())  # ガードが操作を止めた → 返事を確認として扱う
        if self.awaiting:
            log("確認待ち: " + " / ".join(p["desc"] for p in approvals.pending()))
            self.ui.state("confirm")
        return True

    def _reply_now(self, text, reply):
        """AI を通さずに返した返事（時刻・日付）。次に Claude へ渡す文脈には残す（定型なので会話ログには残さない）。"""
        log(f"ジャービス: {reply}")
        self.ui.message("jarvis", reply)
        self.local_turns.append((text, reply))
        self._say_now(reply)

    def respond_local(self, text, timing_prefix=""):
        """PC 内の AI で返す。使えない・Claude に任せるべき話題なら None（何も話さない）。"""
        if not local_llm.available():
            return None
        self.ui.state("thinking")
        t1 = time.time()
        watching = self._start_interrupt_watch()
        view = ReplyView(self.ui)
        speech = self.current_speech = self.voice.stream(on_start=lambda: self.ui.state("speaking"), on_sentence=view.sentence)
        try:
            reply = local_llm.chat(text, self.local_turns, on_text=speech.feed)
        except Exception as e:
            log(f"PC 内の AI でエラー: {e}")
            reply = None
        if reply is None and not self.cancel.is_set():
            speech.finish()
            watching.set()
            view.discard()  # 捨てる返事が画面に出ていたら取り消す
            log("PC 内の AI → Claude に回します")
            return None
        reply = reply or "（中断しました）"
        log(f"ジャービス（PC内AI）: {reply}")
        view.finish(speakable(reply))
        speech.finish(stop_event=self.cancel, clear=False)
        watching.set()
        first_voice = f"{speech.started_at - t1:.1f}s" if speech.started_at else "-"
        log(f"[計測] {timing_prefix}PC内AI 話し始め {first_voice} / 完成 {time.time() - t1:.1f}s")
        self.turns.append((text, speakable(reply)))
        self.local_turns.append((text, speakable(reply)))
        return reply

    def _with_local_context(self, text):
        """PC 内の AI や本体だけで返したやりとりを、次に Claude へ話しかけるときに添える（話がつながるように）。"""
        if not self.local_turns:
            return text
        lines = "\n".join(f"ユーザー「{u}」→ ジャービス「{r}」" for u, r in self.local_turns[-6:])
        self.local_turns = []
        return f"（直前に、あなたを通さずに返したやりとり。参考にしてください）\n{lines}\n\n{text}"

    # ---- 割り込み（考え中・話し中に「ジャービス」、ホットキー、STOP ボタンで止める） ----
    def _start_interrupt_watch(self):
        """考え中・話し中の割り込みを見張り始める。戻り値の Event を set すると見張りを終える。"""
        self.cancel.clear()
        self.barged = None
        self.ui.stop_requested.clear()
        self.hotkey.clear()
        self.mic.drain()
        self.wake.reset()
        done = threading.Event()
        threading.Thread(target=self._watch_for_interrupt, args=(done,), daemon=True).start()
        return done

    def _on_tool(self, started):
        """ファイルを見る・調べるなどのツールを使っている間は、画面を「確認中」にする。"""
        if self.cancel.is_set():
            return
        self.ui.state("checking" if started else "speaking")

    def _watch_for_interrupt(self, done):
        quiet_until = 0.0
        while not done.is_set():
            if self.hotkey.is_set():  # ホットキー: 止めて、すぐ次の用件を聞く
                self.hotkey.clear()
                return self._interrupt(np.zeros(0, np.int16))
            if self.ui.stop_requested.is_set():  # STOP ボタン: 止めるだけ
                self.ui.stop_requested.clear()
                return self._interrupt(None)
            try:
                block = self.mic.read(timeout=0.1)
            except queue.Empty:
                continue
            if self.voice.busy():   # 話している間は、自分の声の誤検知を防ぐためマイクを聞かない（STOP・ホットキーは有効）
                quiet_until = time.time() + 0.8   # 話し終えた直後の残響も聞かない
            if time.time() < quiet_until:
                self.wake.reset()
                continue
            command = self.wake.feed(block)
            if command is not None:  # 「ジャービス、○○」: 止めて、その○○を新しい用件にする
                if self._is_own_echo(command):
                    self.wake.reset()  # 自分の声がマイクに入っただけ。割り込みにしない
                    continue
                return self._interrupt(command)

    def _is_own_echo(self, command):
        """話している最中の呼びかけが、スピーカーから出た自分の声（ユーザー以外の声）なら True。"""
        if not self.speaker or mute.is_muted("voiceprint"):
            return False
        score = self.speaker.score(command)  # 声紋が足りない・短すぎるときは None（割り込みを通す）
        if score is None:
            return False
        if score < SPEAKER_THRESHOLD:
            log(f"話し中の呼びかけを、ほかの声（自分の声）として無視（似ている度合い {score:.2f}）")
            return True
        log(f"話し中の呼びかけを割り込みとして受付（似ている度合い {score:.2f}）")
        return False

    def _interrupt(self, audio):
        log("割り込みで中断しました")
        self.barged = audio
        self.cancel.set()
        self.brain.cancel()
        if self.current_speech is not None:
            self.current_speech.stopped.set()  # まだ読み上げていない文も読まない
        self.voice.stop()

    def _next_after_interrupt(self):
        """割り込まれたあとに聞く次の発言（なければ None）。"""
        audio, self.barged = self.barged, None
        self.cancel.clear()
        self.ui.state("listening")
        if audio is None:  # STOP ボタン: 普段の「続けて話せる」時間だけ待つ
            self.mic.drain()
            return self.mic.record_utterance(FOLLOWUP_SEC)
        if len(audio) < SAMPLE_RATE * 0.4:  # 「ジャービス」だけ、またはホットキー → ピッと鳴らして用件を待つ
            beep()
            return self.mic.record_utterance(COMMAND_WAIT_SEC)
        rest = self.mic.record_utterance(SILENCE_SEC)  # 「ジャービス、○○」の続きも拾う
        return np.concatenate([audio, rest]) if rest is not None else audio

    def respond(self, text, timing_prefix=""):
        """Claude に聞き、返事を 1 文できるごとに読み上げる（全文を待たない）。返事の本文を返す。"""
        self.ui.state("thinking")
        t1 = time.time()
        watching = self._start_interrupt_watch()
        view = ReplyView(self.ui)  # 返事は文ができるたびに画面へ出す
        speech = self.current_speech = self.voice.stream(on_start=lambda: self.ui.state("speaking"), on_sentence=view.sentence)
        reply, timing = self.brain.ask(text, on_text=speech.feed, on_tool=self._on_tool)
        if self.cancel.is_set():
            reply = (reply + "（中断）") if reply else "（中断しました）"
        log(f"ジャービス: {reply}")
        view.finish(speakable(reply))
        if any(CALENDAR_WRITE.search(t) for t in self.brain.tools_used):
            info.calendar_refresh.set()  # 予定を登録・変更したので、ダッシュボードの予定をすぐ取り直す
        speech.finish(stop_event=self.cancel, clear=False)
        watching.set()
        first_voice = f"{speech.started_at - t1:.1f}s" if speech.started_at else "-"
        first_text = f"{timing['first']:.1f}s" if timing.get("first") else "-"
        log(f"[計測] {timing_prefix}返事の書き始め {first_text} / 話し始め {first_voice}"
            f" / 返事の完成 {timing['wall']:.1f}s" + (f" / エラー: {timing['error']}" if "error" in timing else ""))
        return reply

    def _timer_action(self, kind, value):
        """timers.handle の結果を実行し、返事の文を返す。"""
        now = datetime.now()
        if kind == "timer":
            timers.add("timer", value, "タイマー")
            reply = f"{timers.describe_duration((value - now).total_seconds())}のタイマーをセットしました。"
        elif kind == "alarm":
            timers.add("alarm", value, "アラーム")
            day = "明日の" if value.date() > now.date() else ""
            reply = f"{day}{jp_time(value.strftime('%H:%M'))}にアラームをセットしました。"
        elif kind == "cancel":
            name = "タイマー" if value == "timer" else "アラーム"
            reply = f"{name}を取り消しました。" if timers.cancel(value) else f"セットされている{name}はありません。"
        else:  # status
            running = timers.active("timer")
            reply = (f"あと{timers.describe_duration((running[0][0] - now).total_seconds())}です。"
                     if running else "動いているタイマーはありません。")
        self._push_timers()
        return reply

    def _push_timers(self):
        push_timers(self.ui)

    def _proactive(self):
        """待機中に、自分から話しかけることがないか確かめる（タイマーがあるので 1 秒おき）。"""
        now = time.time()
        if now < self.next_reminder_check:
            return
        self.next_reminder_check = now + 1
        self._remind_if_due()
        if self.minutes.ready is not None:
            path, self.minutes.ready = self.minutes.ready, None
            if not path:
                self.ui.message("jarvis", "議事録の文字起こしができませんでした。ログをご確認ください。")
                self._say_now("議事録の文字起こしができませんでした。ログをご確認ください。")
                self._back_to_idle()
                return
            self.respond(MINUTES_PROMPT.format(path=path, stem=os.path.basename(path).replace("_文字起こし.txt", "")))
            self._back_to_idle()

    def _morning_brief(self):
        """その日最初の起動・スリープ解除のあいさつ（天気・予定・大事なメール）。"""
        save_json(DAILY_FILE, {"briefed": datetime.now().strftime("%Y-%m-%d")})
        log("今日はじめてなので、あいさつ")
        self.respond(MORNING_PROMPT.format(now=datetime.now().strftime("%Y年%m月%d日 %H時%M分")))

    def ring(self, kind, title, at):
        """タイマー・アラームを鳴らす。「ジャービス」と呼ぶ、ホットキー、STOP ボタンで止まる。最長 RING_MAX_SEC 秒。"""
        if kind == "timer":
            message = "タイマーが終了しました。"
        else:
            message = f"{jp_time(at.strftime('%H:%M'))}です。アラームの時刻です。"
        if title and title not in ("タイマー", "アラーム"):
            message += f"{title}のお時間です。"
        log(f"[鳴動] {message}")
        self.ui.message("jarvis", message)
        self.ui.state("alarm")
        stop = self.ui.stop_requested
        stop.clear()
        self.hotkey.clear()
        self.mic.drain()
        self.wake.reset()
        end = time.time() + RING_MAX_SEC
        # まず電子音だけを鳴らし、BEEP_BEFORE_VOICE_SEC 秒たってから声で知らせる（以後 12 秒おき）
        last_chime, next_voice = 0.0, time.time() + BEEP_BEFORE_VOICE_SEC
        while time.time() < end and not stop.is_set() and not self.hotkey.is_set():
            t = time.time()
            speaking = self.voice.busy()
            if t >= next_voice and not speaking and t - last_chime >= CHIME_SEC:  # 「ピピピピッ」の切れ目で話す
                self.voice.say(message, wait=False)
                next_voice = t + 12
            elif not speaking and t - last_chime >= CHIME_SEC:
                play_sound(CHIME)
                last_chime = t
            try:
                block = self.mic.read(timeout=0.1)
            except queue.Empty:
                continue
            if self.wake.feed(block) is not None:  # 「ジャービス」で止める
                break
        sd.stop()
        self.voice.stop()
        stop.clear()
        self.hotkey.clear()
        self._push_timers()
        self._back_to_idle()

    def _check_disk(self, silent=False):
        """C ドライブの空きが DISK_WARN_GB を切ったら、1 日 1 回だけ声で知らせる（10 分おきに調べる）。"""
        now = time.time()
        if not DISK_WARN_GB or now - getattr(self, "_disk_checked", 0) < 600:
            return
        self._disk_checked = now
        try:
            import shutil
            free_gb = shutil.disk_usage("C:\\").free / 1024 ** 3
        except OSError:
            return
        today = datetime.now().strftime("%Y-%m-%d")
        warned_file = os.path.join(DATA_DIR, "disk_warned.json")
        if free_gb >= DISK_WARN_GB or load_json(warned_file, "") == today:
            return
        save_json(warned_file, today)
        notice = f"Cドライブの空きが残り{free_gb:.0f}ギガになりました。そろそろ掃除のお時間です。"
        log(f"お知らせ: {notice}")
        if silent:
            self.missed.append("Cドライブの空き容量の警告")
            return
        self.ui.message("jarvis", notice)
        self.ui.state("speaking")
        self.voice.say(notice, stop_event=self.hotkey)
        self._back_to_idle()

    def _remind_if_due(self, silent=False):
        """reminders.json（ジャービスが頼まれて書き込む）の時刻になったら声をかける。
        silent=True（スリープ中）は鳴らさず self.missed に記録だけする。"""
        self._check_disk(silent)
        now = time.time()
        reminders = load_json(REMINDERS_FILE, [])
        fired = set(load_json(FIRED_FILE, []))
        kept = []
        for r in reminders if isinstance(reminders, list) else []:
            if not isinstance(r, dict) or "start" not in r:
                continue  # 書式が壊れた行は捨てる
            start_dt = timers.parse_start(r["start"])
            if start_dt is None:
                continue  # 書式が壊れた行は捨てる
            start = start_dt.timestamp()
            if start < now - 86400:
                continue  # 1 日以上前のものは片付ける
            kept.append(r)
            before = int(r.get("before_min", 0))
            key = f"{r['start']}|{before}|{r.get('title')}"
            # PC が切れていて大幅に過ぎたものは言わない（予定の 30 分後まで）
            if key in fired or not (start - before * 60 <= now < start + 1800):
                continue
            fired.add(key)
            save_json(FIRED_FILE, sorted(fired))
            if silent:
                kind = r.get("kind")
                what = "タイマー" if kind == "timer" else f"{jp_time(r['start'][11:16])}のアラーム" if kind == "alarm" \
                    else f"{jp_time(r['start'][11:16])}の{r.get('title', '')}のお知らせ"
                self.missed.append(what)
                log(f"スリープ中のため鳴らさず: {what}")
                continue
            if r.get("kind") in ("timer", "alarm"):
                self.ring(r["kind"], r.get("title", ""), start_dt)
                continue
            hhmm = r["start"][11:16]
            remaining = round((start - now) / 60)  # 頼まれた「何分前」ではなく、実際の残り時間で言う
            if r.get("message"):
                notice = r["message"]
            elif remaining > 0:
                notice = f"{remaining}分後、{jp_time(hhmm)}から{r.get('title', '')}のご予定です。"
            else:
                notice = f"{jp_time(hhmm)}になりました。{r.get('title', '')}のお時間です。"
            log(f"お知らせ: {notice}")
            self.ui.message("jarvis", notice)
            self.ui.state("speaking")
            self.voice.say(notice, stop_event=self.hotkey)
            self._back_to_idle()
        if isinstance(reminders, list) and len(kept) != len(reminders):
            save_json(REMINDERS_FILE, kept)
            save_json(FIRED_FILE, sorted(k for k in fired if any(k.startswith(r["start"]) for r in kept)))

    def _close(self, reply):
        log(f"ジャービス: {reply}")
        self.ui.message("jarvis", reply)
        self.ui.state("speaking")
        self.voice.say(reply, stop_event=self.hotkey)
        return False

    def _back_to_idle(self):
        if self.awaiting:  # 返事がないまま終わった確認は取り消す
            self.awaiting = False
            approvals.clear_pending()
        memory.remember_async(self.turns, log)  # 会話を振り返って保管庫の会話ログと記憶に追記（裏で動く）
        self.turns = []
        self.mic.drain()
        self.wake.reset()
        self.ui.state("sleep" if self.mode == "sleep" else "idle")
        log("スリープ中" if self.mode == "sleep" else "待機中")


def main():
    # 二重起動の防止（自動起動中に bat からも起動した、など）
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\JarvisAssistant")  # noqa: F841 終了まで保持
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        log("ジャービスはすでに起動しています（画面を出します）")
        open(SHOW_FLAG, "w").close()  # 動いている方が見つけて、隠れていた画面を出す
        return
    ui = Dashboard()
    start_proxy()  # ポップアップ用の中継（127.0.0.1 のみ）

    def worker():
        try:
            ui.history(load_history())  # 前回までの会話をログ欄に出しておく（スクロールでさかのぼれる）
            # タスクとタイマーのファイルは専用の見張りが 0.5 秒おきに見て、会話中やスリープ中でもすぐ画面に出す
            info.start_updaters(ui, log, file_watches=[
                (TASKS_FILE, lambda: ui.tasks(load_tasks())),
                (DEV_TASKS_FILE, lambda: ui.dev_tasks(load_dev_tasks())),
                (REMINDERS_FILE, lambda: push_timers(ui)),
                (POPUP_FILE, ui.popup_from_file),
                (RESTART_FLAG, restart_if_requested),
                (SHOW_FLAG, lambda: show_if_requested(ui)),
            ])
            start_manaba()
            threading.Thread(target=dev_loop, args=(ui,), daemon=True).start()
            Jarvis(ui).run()
        except Exception:
            log(traceback.format_exc())
            ui.system("SYSTEM ERROR")

    webview.start(worker)
    log("終了しました")
    os._exit(0)  # 本当に終了するとき（「ジャービスを終了して」）は、マイクやホットキーのスレッドごと終わらせる


if __name__ == "__main__":
    try:
        main()
    except Exception:  # 黒い画面なし（pythonw）で動くので、落ちた理由はログに残す
        log(traceback.format_exc())
        raise
