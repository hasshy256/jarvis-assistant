"""Google カレンダー。Google Calendar API で PC から直接、予定を読む・登録する。

準備（初回だけ）:
  1. Google Cloud で作った credentials.json を %LOCALAPPDATA%\\jarvis\\google に置く
  2. `python gcal.py login` でブラウザが開くので、Google アカウントでログインして許可する
  動作確認は `python gcal.py week`（今日から 7 日分の予定を表示）。

準備していなければ ready() が False になり、ジャービスはこれまでどおり Claude 経由でカレンダーを見る。
"""
import json
import os
import sys
from datetime import datetime, timedelta

from config import GOOGLE_CALENDAR_ID, GOOGLE_CREDENTIALS, GOOGLE_DIR, GOOGLE_TOKEN, TIMEZONE

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]  # 予定の読み書きだけ（カレンダーの設定や共有は触れない）
WEEKDAYS = "月火水木金土日"
_service = None


def ready():
    return os.path.exists(GOOGLE_TOKEN)


def _credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN, SCOPES)
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())  # 期限が切れたら自動で取り直す（ログインし直しは不要）
        with open(GOOGLE_TOKEN, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
    return creds


def service():
    global _service
    if _service is None:
        from googleapiclient.discovery import build
        _service = build("calendar", "v3", credentials=_credentials(), cache_discovery=False)
    return _service


def login():
    """ブラウザで Google にログインして、token.json を作る。"""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not os.path.exists(GOOGLE_CREDENTIALS):
        raise SystemExit(f"credentials.json が見つかりません。ここに置いてください: {GOOGLE_CREDENTIALS}")
    flow = InstalledAppFlow.from_client_secrets_file(GOOGLE_CREDENTIALS, SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    os.makedirs(GOOGLE_DIR, exist_ok=True)
    with open(GOOGLE_TOKEN, "w", encoding="utf-8") as f:
        f.write(creds.to_json())
    print(f"ログインしました。{GOOGLE_TOKEN} に保存しました")


def _local(dt):
    return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt


def list_events(first, last):
    """first〜last（日付）の予定。[{"iso":"2026-10-17","date":"10/17","wd":"土","start":"HH:MM","end":"HH:MM","title":"件名"}]
    （info.fetch_week と同じ形。終日の予定は start と end が "終日"）"""
    t0 = datetime(first.year, first.month, first.day).astimezone()
    t1 = datetime(last.year, last.month, last.day, 23, 59, 59).astimezone()
    items, page = [], None
    while True:
        res = service().events().list(calendarId=GOOGLE_CALENDAR_ID, timeMin=t0.isoformat(), timeMax=t1.isoformat(),
                                      singleEvents=True, orderBy="startTime", timeZone=TIMEZONE,
                                      pageToken=page, maxResults=250).execute()
        for ev in res.get("items", []):
            start, end = ev.get("start", {}), ev.get("end", {})
            if "date" in start:  # 終日の予定（複数日にまたがるものは、範囲内の日ごとに出す）
                d = max(datetime.strptime(start["date"], "%Y-%m-%d"), t0.replace(tzinfo=None))
                stop = min(datetime.strptime(end.get("date", start["date"]), "%Y-%m-%d"), t1.replace(tzinfo=None))
                days = []
                while d < stop or not days:
                    days.append(d)
                    d += timedelta(days=1)
                times = [(day, "終日", "終日") for day in days]
            else:
                s = _local(datetime.fromisoformat(start["dateTime"]))
                e = _local(datetime.fromisoformat(end["dateTime"])) if "dateTime" in end else s
                times = [(s, f"{s:%H:%M}", f"{e:%H:%M}")]
            for day, s_text, e_text in times:
                items.append({"iso": f"{day:%Y-%m-%d}", "date": f"{day.month}/{day.day}", "wd": WEEKDAYS[day.weekday()],
                              "start": s_text, "end": e_text, "title": ev.get("summary", "（件名なし）"),
                              "location": ev.get("location", "")})
        page = res.get("nextPageToken")
        if not page:
            break
    # 日付ごとに、終日 → 時刻の早い順
    return sorted(items, key=lambda i: (i["iso"], i["start"] != "終日", i["start"]))


def week(days=7):
    today = datetime.now()
    return list_events(today, today + timedelta(days=days - 1))


def add_event(title, start, end=None, location="", description=""):
    """予定を登録する。start / end は "2026-10-20T15:00" か、終日なら "2026-10-20"。end がなければ 1 時間（終日なら 1 日）。
    登録した予定の {"title","start","end","link"} を返す。"""
    body = {"summary": title}
    if location:
        body["location"] = location
    if description:
        body["description"] = description
    if len(start) == 10:  # 日付だけ = 終日
        s = datetime.strptime(start, "%Y-%m-%d")
        e = datetime.strptime(end, "%Y-%m-%d") if end else s + timedelta(days=1)
        body["start"], body["end"] = {"date": f"{s:%Y-%m-%d}"}, {"date": f"{e:%Y-%m-%d}"}
    else:
        s = datetime.fromisoformat(start)
        e = datetime.fromisoformat(end) if end else s + timedelta(hours=1)
        body["start"] = {"dateTime": s.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": TIMEZONE}
        body["end"] = {"dateTime": e.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": TIMEZONE}
    ev = service().events().insert(calendarId=GOOGLE_CALENDAR_ID, body=body).execute()
    return {"title": ev.get("summary"), "start": body["start"], "end": body["end"], "link": ev.get("htmlLink")}


# ---- Claude Code から使う道具（remote.py が Claude に渡す） ----
def sdk_server():
    """予定の確認・登録を Claude Code の道具にする。ツール名は mcp__gcal__list_events / mcp__gcal__create_event。"""
    from claude_agent_sdk import create_sdk_mcp_server, tool

    def text(obj):
        return {"content": [{"type": "text", "text": json.dumps(obj, ensure_ascii=False)}]}

    @tool("list_events", "Google カレンダーの予定を取得する（PC から Google Calendar API で直接）。"
          "日付は YYYY-MM-DD。終了日を省くと開始日の 1 日分。",
          {"type": "object", "properties": {"start_date": {"type": "string"}, "end_date": {"type": "string"}},
           "required": ["start_date"]})
    async def list_tool(args):
        first = datetime.strptime(args["start_date"], "%Y-%m-%d")
        last = datetime.strptime(args.get("end_date") or args["start_date"], "%Y-%m-%d")
        return text(list_events(first, last))

    @tool("create_event", "Google カレンダーに予定を登録する。start / end は 'YYYY-MM-DDTHH:MM'（終日なら 'YYYY-MM-DD'）。"
          "end を省くと 1 時間（終日なら 1 日）。",
          {"type": "object", "properties": {"title": {"type": "string"}, "start": {"type": "string"},
                                            "end": {"type": "string"}, "location": {"type": "string"},
                                            "description": {"type": "string"}},
           "required": ["title", "start"]})
    async def create_tool(args):
        return text(add_event(args["title"], args["start"], args.get("end"), args.get("location", ""),
                              args.get("description", "")))

    return create_sdk_mcp_server("gcal", tools=[list_tool, create_tool])


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "week"
    if command == "login":
        login()
    elif command == "week":
        for i in week():
            print(f"{i['date']}({i['wd']}) {i['start']}-{i['end']} {i['title']}")
    else:
        print("使い方: python gcal.py login | week")
