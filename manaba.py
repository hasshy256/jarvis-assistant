"""manaba の巡回。ログイン → 課題・資料・ニュースの新着を調べる → タスク欄・Obsidian・声のお知らせに反映する。

巡回・取得・差分の検知は AI を使わない定型処理。AI（Claude Code）を使うのは、新着の資料・課題を要約するときだけ。

使い方（PC で）:
  python manaba.py setup      ID・パスワードを資格情報（keyring）に登録する（初回だけ。コードには書かない）
  python manaba.py light      軽い巡回（課題一覧だけ）を 1 回
  python manaba.py full       しっかり巡回（全コースの課題・資料・ニュース）を 1 回
  python manaba.py dump       ページの HTML を保存する（セレクタの調整用。MANABA_DIR\\debug に出る）
  python manaba.py run        常駐して時間割に合わせて巡回する（ジャービス本体が起動時に呼ぶ）
"""
import argparse
import getpass
import json
import os
import random
import re
import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup

import config
from config import MANABA_BASE, MANABA_DIR, REMINDERS_FILE, TASKS_FILE

KEYRING_SERVICE = "jarvis-manaba"
STORAGE_STATE = os.path.join(MANABA_DIR, "storage_state.json")   # ログイン状態（セッション切れのときだけ入れ直す）
STATE_FILE = os.path.join(MANABA_DIR, "state.json")              # 前回の結果（差分を取るため）
FAIL_FLAG = os.path.join(MANABA_DIR, "login_failed.flag")         # ログインに失敗した印（setup をやり直すと消える）
DEBUG_DIR = os.path.join(MANABA_DIR, "debug")
LOG_FILE = os.path.join(config.LOG_DIR, "manaba.log")

DATETIME_RE = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})\s+(\d{1,2}):(\d{2})")
ITEM_RE = re.compile(r"course_(\d+)_(report|query|survey)_(\d+)")
COURSE_RE = re.compile(r"(?:^|/)course_(\d+)/?(?:\?.*)?$")
FILE_RE = re.compile(r"\.(pdf|pptx?|docx?|xlsx?|zip|txt|csv|png|jpe?g)(\?|$)", re.I)
KIND_NAMES = {"report": "レポート", "query": "小テスト", "survey": "アンケート"}
WEEKDAYS = "月火水木金土日"


class LoginFailed(Exception):
    pass


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    try:
        print(line)
    except Exception:
        pass   # pythonw では標準出力がない
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_json(path, default):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)   # 書きかけのファイルを見せない


def safe_name(text, limit=60):
    return re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", text).strip(" .")[:limit] or "無題"


# ---------------------------------------------------------------- 資格情報
def get_credentials(ask=True):
    import keyring
    user = keyring.get_password(KEYRING_SERVICE, "userid")
    password = keyring.get_password(KEYRING_SERVICE, "password")
    if user and password:
        return user, password
    if not ask or not sys.stdin or not sys.stdin.isatty():
        return None
    print("manaba の ID とパスワードを登録します（Windows の資格情報に保存され、ファイルには残りません）")
    user = input("ID: ").strip()
    password = getpass.getpass("パスワード: ")
    if not user or not password:
        return None
    keyring.set_password(KEYRING_SERVICE, "userid", user)
    keyring.set_password(KEYRING_SERVICE, "password", password)
    return user, password


def has_credentials():
    try:
        return get_credentials(ask=False) is not None
    except Exception:
        return False


# ---------------------------------------------------------------- 通知・タスク
def notify(message, title="manaba"):
    """ジャービス本体は reminders.json を見て読み上げる（スリープ中は鳴らさず、解除時にまとめて伝える）。"""
    items = load_json(REMINDERS_FILE, [])
    if not isinstance(items, list):
        items = []
    items.append({"title": title, "start": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "before_min": 0,
                  "message": message})
    save_json(REMINDERS_FILE, items)
    log(f"通知: {message}")


def spoken_date(dt):
    return f"{dt.month}月{dt.day}日"


def parse_deadline(text):
    """文章の中の日時のうち、いちばん後ろのもの（= 受付終了）を datetime で返す。"""
    found = DATETIME_RE.findall(text or "")
    if not found:
        return None
    y, mo, d, h, mi = (int(x) for x in found[-1])
    try:
        return datetime(y, mo, d, h, mi)
    except ValueError:
        return None


def sync_task(item):
    """課題 1 件をタスク欄に反映する。課題の URL をキーにして、重複して足さない。
    締切が変わったら更新、manaba で提出済みになったら完了にする（完了を未完了に戻すことはしない）。"""
    tasks = load_json(TASKS_FILE, [])
    if not isinstance(tasks, list):
        tasks = []
    title = f"{item['course']}：{item['title']}" if item.get("course") else item["title"]
    due = item.get("deadline")
    due_date = due[:10] if due else None
    found = next((t for t in tasks if t.get("manaba_url") == item["url"]), None)
    changed = False
    if found is None:
        if item.get("submitted"):
            return False   # 最初から提出済みのものは足さない
        task = {"id": max([t["id"] for t in tasks if isinstance(t.get("id"), int)] + [0]) + 1,
                "title": title, "done": False, "manaba_url": item["url"]}
        if due_date:
            task["due"] = due_date
            task["due_at"] = due
        tasks.append(task)
        changed = True
    else:
        if due_date and (found.get("due") != due_date or found.get("due_at") != due):
            found["due"], found["due_at"] = due_date, due
            changed = True
        if item.get("submitted") and not found.get("done"):
            found["done"] = True
            changed = True
    if changed:
        save_json(TASKS_FILE, tasks)
    return changed


# ---------------------------------------------------------------- ページの読み取り（純粋な関数。AI は使わない）
def parse_assignments(html, base_url):
    """課題・小テスト・アンケートの一覧ページから、項目を取り出す。"""
    soup = BeautifulSoup(html, "lxml")
    out = {}
    for row in soup.find_all("tr"):
        link = row.find("a", href=ITEM_RE)
        if not link:
            continue
        m = ITEM_RE.search(link["href"])
        course_id, kind = m.group(1), m.group(2)
        url = urljoin(base_url, link["href"].split("?")[0])
        course_link = row.find("a", href=re.compile(rf"course_{course_id}/?(\?.*)?$"))
        text = row.get_text(" ", strip=True)
        deadline = parse_deadline(text)
        out[url] = {"url": url, "title": link.get_text(strip=True), "kind": kind, "course_id": course_id,
                    "course": course_link.get_text(strip=True) if course_link else "",
                    "deadline": deadline.strftime("%Y-%m-%dT%H:%M") if deadline else None,
                    "submitted": bool(re.search(r"提出済|回答済", text))}
    return out


def parse_courses(html, base_url):
    """コース一覧（マイページ）から {コースID: 科目名} を取り出す。"""
    soup = BeautifulSoup(html, "lxml")
    courses = {}
    for a in soup.find_all("a", href=True):
        m = COURSE_RE.search(a["href"].split("#")[0])
        name = a.get_text(" ", strip=True)
        if m and name and m.group(1) not in courses:
            courses[m.group(1)] = name
    return courses


def parse_links(html, base_url, pattern):
    """pattern に合う href のリンクを {url: 表示名} で返す（ニュース・コンテンツの一覧用）。"""
    soup = BeautifulSoup(html, "lxml")
    out = {}
    for a in soup.find_all("a", href=re.compile(pattern)):
        url = urljoin(base_url, a["href"].split("#")[0])
        text = a.get_text(" ", strip=True)
        if text and url not in out:
            out[url] = text
    return out


def parse_file_links(html, base_url):
    soup = BeautifulSoup(html, "lxml")
    out = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if FILE_RE.search(href) or "download" in href.lower():
            out[urljoin(base_url, href)] = a.get_text(" ", strip=True)
    return out


def page_text(html, limit=6000):
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    body = soup.find(id="container") or soup.body or soup
    return re.sub(r"\n{3,}", "\n\n", body.get_text("\n", strip=True))[:limit]


# ---------------------------------------------------------------- 巡回の予定
def load_timetable():
    data = load_json(config.MANABA_TIMETABLE, {})   # 毎回読み直すので、書き換えれば再起動なしで反映される
    out = []
    for c in data.get("classes", []) if isinstance(data, dict) else []:
        try:
            out.append((WEEKDAYS.index(c["day"][0]), c["start"], c["end"]))
        except (KeyError, ValueError, IndexError, TypeError):
            continue
    return out


def in_class(now=None):
    now = now or datetime.now()
    hhmm = now.strftime("%H:%M")
    return any(d == now.weekday() and s <= hhmm < e for d, s, e in load_timetable())


def is_quiet(now=None):
    now = now or datetime.now()
    start, end = config.MANABA_QUIET
    return start <= now.hour < end


def next_interval_min(now=None):
    return config.MANABA_CLASS_INTERVAL_MIN if in_class(now) else config.MANABA_INTERVAL_MIN


def due_full_slot(state, now=None):
    """しっかり巡回の時刻を過ぎていて、まだやっていなければ、その枠の名前（"2026-10-19 12:00"）を返す。"""
    now = now or datetime.now()
    done = state.get("full_slot", "")
    slots = [f"{now:%Y-%m-%d} {t}" for t in config.MANABA_FULL_TIMES]
    due = [s for s in slots if s <= now.strftime("%Y-%m-%d %H:%M") and s > done]
    return due[-1] if due else None


# ---------------------------------------------------------------- ブラウザ
class Session:
    """Playwright のブラウザ。ログイン状態（storage_state）を再利用し、切れたときだけ入れ直す。"""

    def __init__(self):
        from playwright.sync_api import sync_playwright
        os.makedirs(MANABA_DIR, exist_ok=True)
        self._pw = sync_playwright().start()
        try:
            try:
                self.browser = self._pw.chromium.launch(channel="chrome", headless=True)
            except Exception:
                self.browser = self._pw.chromium.launch(headless=True)   # Chrome が無ければ同梱のものを使う
        except Exception:
            self._pw.stop()   # 止めないと次の巡回で「asyncio の中で同期 API」という別のエラーに化けて原因が隠れる
            raise
        kwargs = {"storage_state": STORAGE_STATE} if os.path.exists(STORAGE_STATE) else {}
        self.context = self.browser.new_context(locale="ja-JP", **kwargs)
        self.page = self.context.new_page()
        self.page.set_default_timeout(30000)
        self.relogged = False

    def close(self):
        try:
            self.context.storage_state(path=STORAGE_STATE)
        except Exception:
            pass
        self.browser.close()
        self._pw.stop()

    def _logged_out(self):
        return self.page.locator("input[name=password]").count() > 0

    def login(self):
        creds = get_credentials(ask=False)
        if not creds:
            raise LoginFailed("資格情報が未登録です")
        for attempt in range(2):   # 再試行は 1 回まで
            self.page.goto(urljoin(MANABA_BASE, "login"))
            self.page.fill("input[name=userid]", creds[0])
            self.page.fill("input[name=password]", creds[1])
            self.page.click("#login")
            self.page.wait_for_load_state("load")
            if not self._logged_out():
                self.context.storage_state(path=STORAGE_STATE)
                log("ログインしました")
                return
            log(f"ログインに失敗しました（{attempt + 1} 回目）")
            time.sleep(3)
        raise LoginFailed("ID かパスワードが違うようです")

    def get(self, path):
        """ページを開いて HTML を返す。ログイン画面が出たら 1 回だけ入れ直す。"""
        time.sleep(random.uniform(*config.MANABA_PAGE_WAIT_SEC))
        url = urljoin(MANABA_BASE, path)
        self.page.goto(url)
        self.page.wait_for_load_state("load")
        if self._logged_out():
            if self.relogged:
                raise LoginFailed("ログインが続きません")
            self.relogged = True
            self.login()
            self.page.goto(url)
            self.page.wait_for_load_state("load")
        return self.page.content()

    def download(self, url, folder):
        time.sleep(random.uniform(*config.MANABA_PAGE_WAIT_SEC))
        resp = self.context.request.get(url)
        if not resp.ok:
            return None
        disp = resp.headers.get("content-disposition", "")
        m = re.search(r"filename\*=UTF-8''([^;]+)|filename=\"?([^\";]+)", disp)
        name = unquote(m.group(1) or m.group(2)) if m else os.path.basename(urlparse(url).path) or "file"
        name = safe_name(name, 120)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name)
        if os.path.exists(path):   # 同名は上書きせず、日付を付けて残す
            stem, ext = os.path.splitext(name)
            path = os.path.join(folder, f"{stem}_{datetime.now():%Y%m%d-%H%M%S}{ext}")
        with open(path, "wb") as f:
            f.write(resp.body())
        return path


# ---------------------------------------------------------------- 要約と Obsidian
def pdf_text(path, limit=15000):
    try:
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(path).pages)[:limit]
    except Exception:
        return ""


def find_claude():
    import shutil
    exe = shutil.which("claude")
    if exe:
        return exe
    from brain import find_claude as brain_find
    return brain_find()


def summarize(kind, title, course, text):
    """新着があるときだけ Claude Code で要約する（失敗しても巡回は止めない）。"""
    if kind == "assignment":
        ask = ("次は大学の授業の課題ページの文章です。「求められていること / 締切 / 提出形式 / 考えるポイント」の見出しで、"
               "短く整理してください。提出物そのものは作らず、理解と計画の手助けだけにしてください。")
    else:
        ask = "次は大学の授業資料（またはお知らせ）の文章です。「要点」と「重要用語」（用語ごとに一言の説明）にまとめてください。"
    prompt = f"{ask}\n\n科目: {course}\nタイトル: {title}\n\n{text}"
    try:
        cwd = os.path.join(MANABA_DIR, "work")
        os.makedirs(cwd, exist_ok=True)   # ジャービスの人格（workspace）を読ませないため別のフォルダで動かす
        out = subprocess.run([find_claude(), "-p", "--model", config.MANABA_SUMMARY_MODEL], input=prompt, cwd=cwd,
                             capture_output=True, text=True, encoding="utf-8", timeout=300,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception as e:
        log(f"要約できませんでした: {e}")
        return ""


def write_note(course, title, url, body, summary, files=()):
    """Obsidian の Jarvis/manaba/科目名/日付_タイトル.md にノートを作る。"""
    folder = os.path.join(config.OBSIDIAN_VAULT, config.VAULT_FOLDER, config.MANABA_VAULT_FOLDER, safe_name(course))
    os.makedirs(folder, exist_ok=True)
    base = f"{datetime.now():%Y-%m-%d}_{safe_name(title)}"
    path, n = os.path.join(folder, base + ".md"), 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{base}_{n}.md")
        n += 1
    lines = [f"# {title}", "", f"- 科目: {course}", f"- 元のページ: {url}",
             f"- 取得: {datetime.now():%Y-%m-%d %H:%M} {config.VAULT_AI_NAME}"]
    lines += [f"- 保存した資料: {f}" for f in files]
    lines += ["", "## 要約", "", summary or "（要約できませんでした）"]
    if body and not summary:
        lines += ["", "## 本文", "", body[:3000]]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


# ---------------------------------------------------------------- 巡回の本体
def new_state():
    return {"assignments": {}, "news": {}, "contents": {}, "files": {}, "courses": {}, "baseline": False}


def process_assignment(sess, a, prev, first_run):
    """課題 1 件の新規・変更を処理して、通知文（あれば）を返す。"""
    note = None
    if prev is None:
        if not first_run:
            due = datetime.strptime(a["deadline"], "%Y-%m-%dT%H:%M") if a["deadline"] else None
            note = f"{a['course'] or '授業'}の{KIND_NAMES[a['kind']]}が出ました。" + \
                   (f"締切は{spoken_date(due)}です。" if due else "")
            try:
                text = page_text(sess.get(a["url"]))
                write_note(a["course"] or "不明", a["title"], a["url"], text,
                           summarize("assignment", a["title"], a["course"], text))
            except LoginFailed:
                raise
            except Exception as e:
                log(f"課題のノートを作れませんでした: {e}")
    elif a["deadline"] and prev.get("deadline") != a["deadline"]:
        due = datetime.strptime(a["deadline"], "%Y-%m-%dT%H:%M")
        note = f"{a['course'] or '授業'}の{a['title']}の締切が{spoken_date(due)}に変わりました。"
    sync_task(a)
    return note


def check_submitted(sess, state, current):
    """一覧から消えた課題は、ページを開いて提出済みか確かめ、そうならタスクを完了にする。"""
    for url, old in list(state["assignments"].items()):
        if url in current or old.get("submitted"):
            continue
        try:
            text = page_text(sess.get(url), 3000)
        except LoginFailed:
            raise
        except Exception:
            continue
        if re.search(r"提出済|回答済|提出日時", text):
            old["submitted"] = True
            sync_task({**old, "submitted": True})
            log(f"提出済みを確認: {old['title']}")


def light_patrol(sess, state):
    first_run = not state.get("baseline")
    current, notes, new_courses = {}, [], set()
    for page in ("home_summary_report", "home_summary_query", "home_summary_survey"):
        current.update(parse_assignments(sess.get(page), MANABA_BASE))
    for url, a in current.items():
        prev = state["assignments"].get(url)
        note = process_assignment(sess, a, prev, first_run)
        if prev is None:
            new_courses.add(a["course_id"])
        if note:
            notes.append(note)
        state["assignments"][url] = a
    check_submitted(sess, state, current)
    if first_run and current:
        notes.append(f"manabaの課題を{len(current)}件、タスクに取り込みました。")
    state["baseline"] = True
    return notes, new_courses


def course_pass(sess, state, course_id, first_run):
    """1 つのコースのニュース・コンテンツ・課題の新着を調べ、資料を保存して要約する。"""
    name = state["courses"].get(course_id, f"コース{course_id}")
    folder = os.path.join(config.MANABA_FILES_DIR, safe_name(name))
    notes = []
    for key, path, pattern in (("news", f"course_{course_id}_news", rf"course_{course_id}_news_\d+"),
                               ("contents", f"course_{course_id}_page", rf"course_{course_id}_page_\d+")):
        try:
            links = parse_links(sess.get(path), MANABA_BASE, pattern)
        except LoginFailed:
            raise
        except Exception as e:
            log(f"{name} の {key} を読めませんでした: {e}")
            continue
        for url, title in links.items():
            if url in state[key]:
                continue
            state[key][url] = title
            if first_run:
                continue   # 初回は今あるものを記録するだけ（過去の分を一度に落とさない）
            html = sess.get(url)
            saved = []
            for furl in parse_file_links(html, url):
                if furl in state["files"]:
                    continue
                p = sess.download(furl, folder)
                if p:
                    state["files"][furl] = p
                    saved.append(p)
            text = page_text(html) + "".join("\n\n" + pdf_text(p) for p in saved if p.lower().endswith(".pdf"))
            write_note(name, title, url, text, summarize("material", title, name, text), saved)
            notes.append(f"{name}に{'お知らせ' if key == 'news' else '資料'}「{title}」が追加されました。")
    for kind in ("report", "query", "survey"):
        try:
            found = parse_assignments(sess.get(f"course_{course_id}_{kind}"), MANABA_BASE)
        except LoginFailed:
            raise
        except Exception:
            continue
        for url, a in found.items():
            a["course"] = a["course"] or name
            note = process_assignment(sess, a, state["assignments"].get(url), first_run)
            state["assignments"][url] = a
            if note:
                notes.append(note)
    return notes


def full_patrol(sess, state):
    first_run = not state.get("full_baseline")   # しっかり巡回の初回だけ「記録するだけ」（軽い巡回の baseline とは別に持つ）
    state["courses"] = parse_courses(sess.get("home_course"), MANABA_BASE) or state["courses"]
    notes = []
    for cid in state["courses"]:
        notes += course_pass(sess, state, cid, first_run)
    state["baseline"] = True
    state["full_baseline"] = True
    if first_run:
        notes.append(f"manabaの{len(state['courses'])}科目を取り込みました。")
    return notes


def run_once(mode):
    """巡回 1 回分。ログインに失敗したら止めて通知する。"""
    if os.path.exists(FAIL_FLAG):
        return
    state = load_json(STATE_FILE, None) or new_state()
    for k, v in new_state().items():
        state.setdefault(k, v)
    sess = None
    try:
        sess = Session()
        notes, new_courses = light_patrol(sess, state)
        if mode == "full":
            notes += full_patrol(sess, state)
        else:
            for cid in new_courses:   # 軽い巡回で新着を見つけたコースは、その場でそのコースだけ取得
                notes += course_pass(sess, state, cid, not state.get("full_baseline"))
        if mode == "full":
            state["full_slot"] = due_full_slot(state) or state.get("full_slot", "")
        save_json(STATE_FILE, state)
        for n in notes:
            notify(n)
    except LoginFailed as e:
        log(f"ログイン失敗: {e}")
        os.makedirs(MANABA_DIR, exist_ok=True)
        open(FAIL_FLAG, "w").close()   # 失敗したら止める。直したら python manaba.py setup で再開
        notify("manabaにログインできません。")
    except Exception:
        log("巡回でエラー:\n" + traceback.format_exc())
    finally:
        if sess:
            try:
                sess.close()
            except Exception:
                pass


def dump_pages():
    """セレクタ調整用に、主なページの HTML を保存する。"""
    os.makedirs(DEBUG_DIR, exist_ok=True)
    sess = Session()
    try:
        courses = {}
        for page in ("home", "home_course", "home_summary_report", "home_summary_query", "home_summary_survey"):
            html = sess.get(page)
            with open(os.path.join(DEBUG_DIR, page + ".html"), "w", encoding="utf-8") as f:
                f.write(html)
            if page == "home_course":
                courses = parse_courses(html, MANABA_BASE)
            log(f"保存: {page}")
        for cid in list(courses)[:1]:   # 最初のコースだけ（構造を見るため）
            for suffix in ("", "_news", "_page", "_report"):
                html = sess.get(f"course_{cid}{suffix}")
                with open(os.path.join(DEBUG_DIR, f"course{suffix or '_top'}.html"), "w", encoding="utf-8") as f:
                    f.write(html)
                log(f"保存: course_{cid}{suffix}")
    finally:
        sess.close()


def run_forever():
    """常駐。時間割に合わせた間隔で軽い巡回、決まった時刻にしっかり巡回。夜間は止まる。"""
    import ctypes
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\JarvisManaba")  # noqa: F841 二重起動の防止
    if ctypes.windll.kernel32.GetLastError() == 183:
        return
    log("manaba 巡回を始めます")
    last_light = None
    while True:
        try:
            now = datetime.now()
            if not is_quiet(now) and not os.path.exists(FAIL_FLAG) and has_credentials():
                state = load_json(STATE_FILE, None) or {}
                if due_full_slot(state, now):
                    run_once("full")
                    last_light = now
                elif last_light is None or now - last_light >= timedelta(minutes=next_interval_min(now)):
                    run_once("light")
                    last_light = now
        except Exception:
            log("巡回の外側でエラー:\n" + traceback.format_exc())
        time.sleep(60)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["setup", "light", "full", "dump", "run"])
    args = ap.parse_args()
    if args.command == "setup":
        import keyring
        for k in ("userid", "password"):
            try:
                keyring.delete_password(KEYRING_SERVICE, k)
            except Exception:
                pass
        if get_credentials(ask=True):
            if os.path.exists(FAIL_FLAG):
                os.remove(FAIL_FLAG)
            print("登録しました")
    elif args.command == "dump":
        dump_pages()
    elif args.command == "run":
        run_forever()
    else:
        run_once(args.command)


if __name__ == "__main__":
    main()
