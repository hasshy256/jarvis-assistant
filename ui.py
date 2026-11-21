"""ダッシュボード。pywebview で dashboard/index.html を開き、window.jarvis.* を呼んで更新する。"""
import base64
import http.server
import json
import mimetypes
import os
import queue
import re
import threading
import urllib.parse
from datetime import datetime

import requests
import webview

import mute
from config import DASHBOARD_SCREEN, DEV_TASKS_FILE, HERE, MANGA_DIR, POPUP_FILE, POPUP_PORT, TASKS_FILE, UPLOAD_DIR


class _ProxyHandler(http.server.BaseHTTPRequestHandler):
    """ポップアップ用の中継。サイトの「枠の中に表示させない」指定を外して、ダッシュボードの窓に出せるようにする。
    /p?u=<URL> がサイト、/f?p=<パス> が PC 内の画像や動画。127.0.0.1 からだけ受け付ける。"""

    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---- 蔵書の窓（/manga/）。リモコンの合言葉は窓の中では使えないので、127.0.0.1 の中継が直接 books.py を呼ぶ ----
    def _manga(self, method):
        """蔵書の画面と API を返す。扱ったら True。"""
        path = urllib.parse.urlparse(self.path).path
        if not (path == "/manga" or path.startswith("/manga/") or path.startswith("/api/manga")):
            return False
        try:
            import sys
            if MANGA_DIR not in sys.path:
                sys.path.insert(0, MANGA_DIR)
            import books
            if path.startswith("/api/manga"):
                body = {}
                if method == "POST":
                    n = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(n) or b"{}")
                if path == "/api/manga" and method == "GET":
                    out = books.summary()
                elif path == "/api/manga/add":
                    out = books.add_any(body.get("isbn", ""))
                elif path == "/api/manga/remove":
                    out = {"ok": books.remove_book(body.get("isbn", ""))}
                elif path == "/api/manga/watch":
                    out = {"ok": books.set_watch(body.get("series", ""), bool(body.get("watch")))}
                elif path == "/api/manga/check":
                    told = books.check_new()
                    out = {"told": told, "summary": books.summary()}
                else:
                    self._send(404, b"")
                    return True
                self._send(200, json.dumps(out, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
                return True
            site = os.path.join(MANGA_DIR, "site")
            rel = path[len("/manga/"):] if path.startswith("/manga/") else ""
            full = os.path.abspath(os.path.join(site, rel or "index.html"))
            if not full.startswith(os.path.abspath(site) + os.sep) or not os.path.isfile(full):
                self._send(404, b"")
                return True
            with open(full, "rb") as f:
                self._send(200, f.read(), mimetypes.guess_type(full)[0] or "application/octet-stream")
        except Exception as e:
            self._send(502, f"<body style='background:#02070d;color:#8fd;font:16px sans-serif;padding:2em'>蔵書を表示できませんでした: {e}</body>".encode("utf-8"))
        return True

    def do_POST(self):
        if not self._manga("POST"):
            self._send(404, b"")

    def do_GET(self):
        if self._manga("GET"):
            return
        q = urllib.parse.urlparse(self.path)
        args = urllib.parse.parse_qs(q.query)
        try:
            if q.path == "/y":  # YouTube: 参照元（このアドレス）を付けて埋め込み枠に入れる（ファイル直開きだとエラー 153 になる）
                m = re.search(r"(?:v=|youtu\.be/|embed/|shorts/)([\w-]{11})", args["u"][0])
                if not m:
                    raise ValueError("YouTube の動画 ID が読み取れません")
                self._send(200, ("<body style='margin:0;background:#000'><iframe style='width:100vw;height:100vh;border:0' "
                                 f"src='https://www.youtube.com/embed/{m.group(1)}?autoplay=1&rel=0' "
                                 "allow='autoplay; fullscreen; encrypted-media' allowfullscreen></iframe></body>").encode("utf-8"))
            elif q.path == "/i":  # 画像: 暗い背景の中央に収める
                self._send(200, ("<body style='margin:0;background:#02070d;display:flex;align-items:center;justify-content:center;height:100vh'>"
                                 f"<img style='max-width:100%;max-height:100%' src='/f?p={urllib.parse.quote(args['p'][0], safe='')}'></body>").encode("utf-8"))
            elif q.path == "/f":
                path = os.path.abspath(args["p"][0])
                ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
                with open(path, "rb") as f:
                    self._send(200, f.read(), ctype)
            elif q.path == "/p":
                url = args["u"][0]
                if not url.lower().startswith(("http://", "https://")):
                    raise ValueError("http(s) のみ")
                r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36",
                                                          "Accept-Language": "ja,en;q=0.8"})
                body = r.content
                ctype = r.headers.get("Content-Type", "text/html")
                if "text/html" in ctype:
                    html = body.decode(r.encoding or r.apparent_encoding or "utf-8", errors="replace")
                    base = f'<base href="{r.url}">'
                    html = re.sub(r"(<head[^>]*>)", r"\1" + base, html, count=1, flags=re.I) if re.search(r"<head", html, re.I) else base + html
                    body, ctype = html.encode("utf-8"), "text/html; charset=utf-8"
                self._send(200, body, ctype)
            else:
                self._send(404, b"")
        except Exception as e:
            self._send(502, f"<body style='background:#02070d;color:#8fd;font:16px sans-serif;padding:2em'>表示できませんでした: {e}</body>".encode("utf-8"))


def start_proxy():
    try:
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", POPUP_PORT), _ProxyHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    except OSError:
        pass  # すでに使われている場合は、ポップアップだけ使えない


class Api:
    """ダッシュボードの JavaScript から呼べる関数（window.pywebview.api.*）。"""

    def __init__(self, stop_requested, mode_requests, text_requests, held_requests):
        self._stop_requested = stop_requested
        self._mode_requests = mode_requests
        self._text_requests = text_requests
        self._held_requests = held_requests

    def set_dev(self):
        self._mode_requests.put("dev")  # 開発ボタン（押すたびに入り切り）

    def held_action(self, action, fp):
        """開発モードの保留リストの「承認」「却下」ボタン。action: "approve" / "reject"。fp が空なら全部。"""
        if action in ("approve", "reject"):
            self._held_requests.put((action, fp or None))

    def toggle_dev_task(self, task_id):
        """開発タスクの完了・未完了を切り替える。"""
        try:
            with open(DEV_TASKS_FILE, encoding="utf-8-sig") as f:
                tasks = json.load(f)
            for t in tasks:
                if t.get("id") == task_id:
                    t["done"] = not t.get("done", False)
            with open(DEV_TASKS_FILE, "w", encoding="utf-8") as f:
                json.dump(tasks, f, ensure_ascii=False, indent=2)
        except (OSError, json.JSONDecodeError):
            pass

    def send_text(self, text, files):
        """入力欄から送られた文と添付ファイル。ファイルは uploads に保存して、そのパスをジャービス本体に渡す。
        files: [{"name": ファイル名, "data": base64}]。保存できなかったファイル名の一覧を返す。"""
        saved, failed = [], []
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        for i, f in enumerate(files or []):
            name = re.sub(r'[\\/:*?"<>|]', "_", f.get("name") or "file")
            path = os.path.join(UPLOAD_DIR, f"{stamp}_{i}_{name}")
            try:
                with open(path, "wb") as fh:
                    fh.write(base64.b64decode(f.get("data", "")))
                saved.append(path)
            except (OSError, ValueError):
                failed.append(name)
        if (text or "").strip() or saved:
            self._text_requests.put(((text or "").strip(), saved))
        return failed

    def stop(self):
        self._stop_requested.set()  # アラームの STOP ボタン

    def set_mode(self, mode):
        self._mode_requests.put(mode)  # 上部のモードボタン

    def toggle_mute(self, kind):
        """マイク（"mic"）・スピーカー（"speaker"）のミュートを切り替え、新しい状態を返す。"""
        if kind not in ("mic", "speaker", "voiceprint"):
            return mute.state()
        state = mute.toggle(kind)
        if kind == "speaker" and state["speaker"]:
            try:  # 話している途中なら、その場で黙らせる
                import pygame
                import sounddevice as sd
                if pygame.mixer.get_init():
                    pygame.mixer.music.stop()
                sd.stop()
            except Exception:
                pass
        return state

    def get_mutes(self):
        return mute.state()

    def toggle_task(self, task_id):
        """タスクの完了・未完了を切り替える（画面でクリックされたとき）。表示の更新は本体がファイルの変更を見て行う。"""
        try:
            with open(TASKS_FILE, encoding="utf-8-sig") as f:
                tasks = json.load(f)
            for t in tasks:
                if t.get("id") == task_id:
                    t["done"] = not t.get("done", False)
            with open(TASKS_FILE, "w", encoding="utf-8") as f:
                json.dump(tasks, f, ensure_ascii=False, indent=2)
        except (OSError, json.JSONDecodeError):
            pass


class Dashboard:
    def __init__(self):
        self.stop_requested = threading.Event()
        self.mode_requests = queue.Queue()
        self.text_requests = queue.Queue()  # 入力欄から送られた (文, [添付ファイルのパス])
        self.held_requests = queue.Queue()  # 開発モードの保留リストの (承認か却下, 指紋)
        screens = webview.screens
        if DASHBOARD_SCREEN == "secondary":  # メインでない（左上が 0,0 でない）モニターを選ぶ
            screen = next((s for s in screens if (s.x, s.y) != (0, 0)), screens[0])
        else:
            screen = screens[min(int(DASHBOARD_SCREEN), len(screens) - 1)]
        self.window = webview.create_window(
            "J.A.R.V.I.S.", url=os.path.join(HERE, "dashboard", "index.html"),
            screen=screen, maximized=True, background_color="#02070d", text_select=True,
            js_api=Api(self.stop_requested, self.mode_requests, self.text_requests, self.held_requests))
        self.ready = threading.Event()
        self.window.events.loaded += self.ready.set
        self.window.events.closing += self._on_closing
        self._last_state = None
        self.visible = True
        self.quitting = False

    def _on_closing(self):
        """×で閉じても終了せず、画面を隠してスリープモードにする。「ジャービス、起きて」で解除すると画面も戻る。"""
        if self.quitting:
            return True
        self.hide()
        self.mode_requests.put("sleep")
        return False  # False を返すと閉じるのを取りやめる

    def hide(self):
        self.visible = False
        self.window.hide()

    def show(self):
        self.visible = True
        self.window.show()
        self.window.maximize()  # restore() だと最大化が解けて小さいウィンドウになる

    def toggle(self):
        self.show() if not self.visible else self.hide()

    def quit(self):
        """本当に終了する（「ジャービスを終了して」）。"""
        self.quitting = True
        self.window.destroy()

    def _call(self, fn, *args):
        if not self.ready.wait(timeout=30):
            return
        js_args = ", ".join(json.dumps(a, ensure_ascii=False) for a in args)
        try:
            self.window.run_js(f"window.jarvis.{fn}({js_args})")
        except Exception:
            pass  # ウィンドウを閉じた直後など

    def state(self, name):
        if name != self._last_state:
            self._last_state = name
            self._call("setState", name)

    def enroll(self, step, total, phase):
        """声の登録の進み具合。phase: intro（説明中）/ listen（話してほしい）/ ok（覚えた）/ retry（聞き取れなかった）"""
        self._call("setEnroll", step, total, phase)

    def level(self, value):
        if self._last_state in ("listening", "confirm", "enroll"):
            self._call("setLevel", round(value, 4))

    def message(self, role, text):
        self._call("addMessage", role, text)

    def reply_begin(self, text):
        """返事の 1 文目を、会話ログの新しい吹き出しとして出す（以降は reply_append で足していく）。"""
        self._call("beginReply", text)

    def reply_append(self, text):
        self._call("appendReply", text)

    def reply_end(self, full):
        """返事の終わり。出していた吹き出しを、整えた全文に置き換える。"""
        self._call("endReply", full)

    def reply_discard(self):
        self._call("discardReply")

    def weather(self, data):
        self._call("setWeather", data)

    def schedule(self, items):
        self._call("setSchedule", items)

    def tasks(self, items):
        self._call("setTasks", items)

    def week(self, items):
        self._call("setWeek", items)

    def news(self, items):
        self._call("setNews", items)

    def stats(self, data):
        self._call("setStats", data)

    def mutes(self, state):
        self._call("setMutes", state)

    def mode(self, name):
        self._call("setMode", name)

    def dev(self, on, quiet=False):
        """開発モードの入り切り（色と画面の切り替えアニメーションが走る）。quiet=True は演出なしで即座に切り替える（再起動で続けるとき）。"""
        self._call("setDev", bool(on), bool(quiet))

    def dev_info(self, data):
        self._call("setDevInfo", data)

    def dev_activity(self, data):
        self._call("setDevActivity", data)

    def dev_tasks(self, items):
        self._call("setDevTasks", items)

    def take_held_request(self):
        """画面の保留リストで押されたボタン（なければ None）。"""
        try:
            return self.held_requests.get_nowait()
        except queue.Empty:
            return None

    def has_text(self):
        return not self.text_requests.empty()

    def take_text_request(self):
        try:
            return self.text_requests.get_nowait()
        except queue.Empty:
            return None

    def take_mode_request(self):
        """ボタンで選ばれたモード（なければ None）。"""
        try:
            return self.mode_requests.get_nowait()
        except queue.Empty:
            return None

    def history(self, items):
        self._call("setHistory", items)

    def timers(self, items):
        """items: [{"kind": "timer" | "alarm", "title": str, "at": エポックミリ秒}]"""
        self._call("setTimers", items)

    def system(self, text):
        self._call("setSystem", text)

    def show_manga(self):
        """蔵書の画面をダッシュボードの窓に出す。"""
        self._call("showPopup", "蔵書", f"http://127.0.0.1:{POPUP_PORT}/manga/", f"http://127.0.0.1:{POPUP_PORT}/manga/")

    def popup_from_file(self):
        """popup.json（popup.ps1 が書く）を読んで、ダッシュボードの中に窓を出す。
        {"url": URL か PC 内のファイルパス, "title": 見出し}"""
        # 見張りは起動直後にも 1 回呼ばれる。起動より前に書かれた古い頼みは無視する（前回の動画が勝手に再生されるのを防ぐ）
        started = getattr(self, "_popup_started", None)
        if started is None:
            started = self._popup_started = datetime.now().timestamp()
        try:
            if os.path.getmtime(POPUP_FILE) < started:
                return
        except OSError:
            return
        try:
            with open(POPUP_FILE, encoding="utf-8-sig") as f:
                d = json.load(f)
        except (OSError, json.JSONDecodeError):
            return
        if d.get("close"):  # 「消して」: 窓を閉じる（動画も止まる）
            self._call("closePopup")
            return
        target = d.get("url", "")
        if not target:
            return
        quoted = urllib.parse.quote(target, safe="")
        if re.match(r"https?://(www\.|m\.)?(youtube\.com|youtu\.be)/", target, re.I):
            route = "y?u="
        elif target.lower().startswith(("http://", "https://")):
            route = "p?u="
        elif target.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")):
            route = "i?p="
        else:
            route = "f?p="
        src = f"http://127.0.0.1:{POPUP_PORT}/{route}{quoted}"
        self._call("showPopup", d.get("title") or "", src, target)
