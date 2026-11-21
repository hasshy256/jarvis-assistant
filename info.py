"""ダッシュボードに出す情報。天気は Open-Meteo（キー不要）、予定は Claude Code 経由で Google カレンダーから。"""
import ctypes
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

import gcal
from brain import find_claude
from config import (DATA_DIR, NEWS_COUNT, NEWS_FEED_URL, NEWS_REFRESH_MIN, SCHEDULE_REFRESH_MIN,
                    STATS_REFRESH_SEC, WEATHER_LAT, WEATHER_LON, WEATHER_REFRESH_MIN)

WEATHER_TEXT = [
    ((0,), "快晴"), ((1,), "晴れ"), ((2,), "晴れ時々曇り"), ((3,), "曇り"), ((45, 48), "霧"),
    ((51, 53, 55, 56, 57), "霧雨"), ((61, 63, 65, 66, 67), "雨"), ((71, 73, 75, 77), "雪"),
    ((80, 81, 82), "にわか雨"), ((85, 86), "にわか雪"), ((95, 96, 99), "雷雨"),
]
CALENDAR_TOOLS = "ToolSearch,mcp__claude_ai_Google_Calendar__list_events,mcp__claude_ai_Google_Calendar__list_calendars"


def weather_text(code):
    return next((t for codes, t in WEATHER_TEXT if code in codes), "不明")


def fetch_weather():
    url = ("https://api.open-meteo.com/v1/forecast"
           f"?latitude={WEATHER_LAT}&longitude={WEATHER_LON}"
           "&current=temperature_2m,weather_code,relative_humidity_2m"
           "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
           "&timezone=Asia%2FTokyo&forecast_days=1")
    for attempt in range(3):  # 接続の握手が時々時間切れになるので、少し待って取り直す
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                d = json.load(r)
            break
        except OSError:
            if attempt == 2:
                raise
            time.sleep(3)
    cur, day = d["current"], d["daily"]
    return {"temp": cur["temperature_2m"], "text": weather_text(cur["weather_code"]),
            "humidity": cur["relative_humidity_2m"], "max": day["temperature_2m_max"][0],
            "min": day["temperature_2m_min"][0], "pop": day["precipitation_probability_max"][0]}


def fetch_week():
    """今日から 7 日分の予定。[{"date":"10/16","wd":"金","start":"HH:MM","title":"件名"}]。失敗したら None。
    Google Calendar API の準備（gcal.py login）が済んでいれば直接読む（速く、Claude の利用枠も使わない）。"""
    if gcal.ready():
        try:
            return gcal.week()
        except Exception:  # ログインの期限切れ（テスト中のアプリは 7 日）など。Claude 経由に切り替える
            pass
    first = datetime.now()
    last = first + timedelta(days=6)
    prompt = (f"Googleカレンダーから {first:%Y-%m-%d} から {last:%Y-%m-%d} までの予定をすべて取得し、JSON配列だけを出力してください。"
              f"検索範囲は {first:%Y-%m-%d}T00:00:00 から {last:%Y-%m-%d}T23:59:59、timeZone は Asia/Tokyo を指定する。"
              "繰り返しの予定も、その期間に該当する分をすべて含める。"
              '形式: [{"date":"YYYY-MM-DD","start":"HH:MM","end":"HH:MM","title":"件名"}]（日付、開始時刻の順）。'
              '終日の予定は start と end を "終日" にする。'
              "予定がなければ []。説明文やコードブロックは不要。")
    p = subprocess.run([find_claude(), "-p", prompt, "--output-format", "json", "--model", "haiku",
                        "--allowedTools", CALENDAR_TOOLS],
                       cwd=DATA_DIR, capture_output=True, text=True, encoding="utf-8",
                       creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        result = json.loads(p.stdout)["result"]
        items = json.loads(result[result.index("["):result.rindex("]") + 1])
        wd = "月火水木金土日"
        for it in items:
            d = datetime.strptime(it["date"], "%Y-%m-%d")
            it["iso"], it["date"], it["wd"] = it["date"], f"{d.month}/{d.day}", wd[d.weekday()]
        return items
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return None


def fetch_news():
    """NHK 主要ニュースの見出し（RSS）。[{"title": str, "time": "HH:MM"}]"""
    req = urllib.request.Request(NEWS_FEED_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        root = ET.fromstring(r.read())
    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        try:
            t = datetime.strptime(item.findtext("pubDate"), "%a, %d %b %Y %H:%M:%S %z")
        except (TypeError, ValueError):
            t = None
        items.append((t, title))
    items.sort(key=lambda x: x[0].timestamp() if x[0] else 0, reverse=True)
    return [{"title": title, "time": t.astimezone().strftime("%H:%M") if t else ""}
            for t, title in items[:NEWS_COUNT]]


class _FileTime(ctypes.Structure):
    _fields_ = [("lo", ctypes.c_uint32), ("hi", ctypes.c_uint32)]


class _MemStatus(ctypes.Structure):
    _fields_ = [("length", ctypes.c_uint32), ("load", ctypes.c_uint32), ("total", ctypes.c_uint64),
                ("avail", ctypes.c_uint64), ("pf_total", ctypes.c_uint64), ("pf_avail", ctypes.c_uint64),
                ("v_total", ctypes.c_uint64), ("v_avail", ctypes.c_uint64), ("ext", ctypes.c_uint64)]


_last_cpu = None


def _cpu_times():
    idle, kernel, user = _FileTime(), _FileTime(), _FileTime()
    ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
    f = lambda t: (t.hi << 32) | t.lo
    return f(idle), f(kernel) + f(user)  # kernel には idle も含まれる


def fetch_stats():
    """PC の CPU・メモリ・ディスクの使用率（%）。"""
    global _last_cpu
    idle, total = _cpu_times()
    cpu = 0
    if _last_cpu and total > _last_cpu[1]:
        cpu = round(100 * (1 - (idle - _last_cpu[0]) / (total - _last_cpu[1])))
    _last_cpu = (idle, total)
    mem = _MemStatus()
    mem.length = ctypes.sizeof(_MemStatus)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem))
    du = shutil.disk_usage(os.path.splitdrive(os.getcwd())[0] + "\\")
    return {"cpu": max(0, min(100, cpu)), "mem": mem.load, "disk": round(100 * du.used / du.total)}


calendar_refresh = threading.Event()  # ジャービスが予定を登録・変更したら set して、すぐ取り直させる


def push_calendar(ui, items):
    """今週の予定を 1 回取って、今日の分と今週の分の両方に出す。"""
    if items is None:
        ui.schedule(None)
        ui.week(None)
        return
    today = datetime.now().strftime("%Y-%m-%d")
    ui.schedule([i for i in items if i.get("iso") == today])
    ui.week([i for i in items if i.get("iso") != today])  # 今日の分は TODAY の欄にあるので、THIS WEEK からは外す


def watch_files(paths_and_pushes, log):
    """ファイルが書き換わったら（ジャービスでも、画面のクリックでも、手で編集しても）0.5 秒以内に画面へ反映する。"""
    seen = {}
    while True:
        for path, push in paths_and_pushes:
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                mtime = 0
            if seen.get(path) != mtime:
                seen[path] = mtime
                try:
                    push()
                except Exception as e:
                    log(f"{os.path.basename(path)} の反映に失敗: {e}")
        time.sleep(0.5)


def start_updaters(ui, log, file_watches=()):
    def loop(fetch, push, minutes, name, wake=None):
        shown = False
        while True:
            wait = minutes
            try:
                push(fetch())
                shown = True
            except Exception as e:
                log(f"{name}の取得に失敗: {e}")
                if not shown:  # 一度も出せていないときだけ空にする（前の表示は残す）
                    push(None)
                wait = min(minutes, 2)  # 失敗したら 2 分後に取り直す
            if wake is None:
                time.sleep(wait * 60)
            else:  # 定期的に、または頼まれたらすぐに取り直す
                wake.wait(wait * 60)
                wake.clear()

    threading.Thread(target=loop, args=(fetch_weather, ui.weather, WEATHER_REFRESH_MIN, "天気"), daemon=True).start()
    threading.Thread(target=loop, args=(fetch_week, lambda items: push_calendar(ui, items), SCHEDULE_REFRESH_MIN,
                                        "予定", calendar_refresh), daemon=True).start()
    threading.Thread(target=loop, args=(fetch_news, ui.news, NEWS_REFRESH_MIN, "ニュース"), daemon=True).start()
    if file_watches:
        threading.Thread(target=watch_files, args=(file_watches, log), daemon=True).start()

    def stats_loop():
        while True:
            try:
                ui.stats(fetch_stats())
            except Exception as e:
                log(f"PC状態の取得に失敗: {e}")
            time.sleep(STATS_REFRESH_SEC)

    threading.Thread(target=stats_loop, daemon=True).start()
