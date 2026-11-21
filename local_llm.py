"""PC の中で動く小さな AI（Ollama）。あいさつや雑談のような軽い会話だけを任せ、速く返して Claude の利用枠も節約する。

予定・メール・タスク・PC 操作・調べものなど、少しでも Claude が要りそうなものは Claude に回す。
PC 内の AI が自信のない話題だと判断したとき（"[CLAUDE]" を返したとき）も Claude に回す。
"""
import json
import re
import urllib.request
from datetime import datetime

from config import LOCAL_LLM_MODEL, OLLAMA_URL

# 雑談らしい言葉（これを含み、下の「Claude に回す言葉」を含まないときだけ PC 内の AI に任せる）
SMALL_TALK = re.compile(
    r"おはよう|こんにちは|こんばんは|ただいま|行ってきます|いってきます|疲れた|つかれた|眠い|ねむい|暇|ひま|"
    r"お腹(が)?(すいた|空いた|減った)|元気|調子(は|どう)|すごい|面白|楽しい|つまらない|頑張|がんば|褒めて|励まして|"
    r"雑談|話そう|話し相手|何か話して|なんか話して|最近どう|笑|うれしい|嬉しい|悲しい|寂しい|さみしい|だるい|しんどい")
# 少しでも Claude（ツールや最新の情報）が要りそうな言葉
NEEDS_CLAUDE = re.compile(
    r"予定|カレンダー|メール|タスク|やること|開いて|起動|調べ|検索|天気|ニュース|再生|かけて|送って|消して|作って|"
    r"設定|タイマー|アラーム|リマインド|お知らせ|覚えて|忘れて|ファイル|フォルダ|画面|クリック|パソコン|PC|音量|"
    r"教えて|何|なに|いつ|どこ|誰|だれ|どう(やって|すれば)|方法|やり方|明日|昨日|来週|今週|ゲーム|動画|曲")
PERSONA = ("あなたは「ジャービス」という AI 執事です。一人称は「私」で、落ち着いた丁寧な執事の口調で話します。"
           "相手の気持ちに寄り添い、自然な日本語で、話し言葉の 1〜2 文、60 字以内で返します。記号や絵文字は使いません。"
           "相手の名前や「様」は付けません。音楽をかける、料理を手配するなど、何かをして差し上げる申し出はしません。"
           "事実の確認、調べもの、PC やアプリの操作、予定や記憶に関わる話には答えず、その場合は [CLAUDE] とだけ返します。")


def instant_reply(text):
    """「今何時？」「今日何曜日？」のように、AI を使わずその場で答えられるもの。"""
    plain = re.sub(r"[、。,.!！?？\s]", "", text)
    if len(plain) > 15:
        return None
    now = datetime.now()
    if re.search(r"(今|いま)?(何時|なんじ)", plain):
        ampm = "午前" if now.hour < 12 else "午後"
        return f"{ampm}{now.hour % 12}時{now.minute}分です。"
    if re.search(r"(今日|きょう)(は|って)?(何日|なんにち|何曜|なんよう|日付)", plain):
        return f"{now.month}月{now.day}日、{'月火水木金土日'[now.weekday()]}曜日です。"
    return None


def is_small_talk(text):
    plain = re.sub(r"[、。,.!！?？\s]", "", text)
    return len(plain) <= 30 and bool(SMALL_TALK.search(plain)) and not NEEDS_CLAUDE.search(plain)


def available():
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=1) as r:
            return any(m["name"].startswith(LOCAL_LLM_MODEL) for m in json.load(r).get("models", []))
    except Exception:
        return False


def chat(text, history, on_text):
    """返事を少しずつ on_text に渡し、全文を返す。Claude に任せるべき話題なら None（何も on_text に渡さない）。"""
    messages = [{"role": "system", "content": PERSONA}]
    for user, reply in history[-4:]:
        messages += [{"role": "user", "content": user}, {"role": "assistant", "content": reply}]
    messages.append({"role": "user", "content": text})
    body = json.dumps({"model": LOCAL_LLM_MODEL, "messages": messages, "stream": True,
                       "options": {"temperature": 0.6, "num_predict": 120}, "keep_alive": "30m"}).encode()
    req = urllib.request.Request(f"{OLLAMA_URL}/api/chat", data=body, headers={"Content-Type": "application/json"})
    reply, held = "", ""
    with urllib.request.urlopen(req, timeout=60) as r:
        for line in r:
            delta = json.loads(line).get("message", {}).get("content", "")
            reply += delta
            if held is not None:  # 最初の数文字は、[CLAUDE] かどうか分かるまで読み上げに回さない
                held += delta
                if "[CLAUDE" in held.upper():
                    return None
                if len(held.strip()) >= 8 or re.search(r"[。！？]", held):
                    on_text(held)
                    held = None
            else:
                on_text(delta)
    if held:
        if "[CLAUDE" in held.upper():
            return None
        on_text(held)
    return reply.strip()


def warmup():
    """モデルを先に読み込んでおき、最初の返事を速くする。"""
    body = json.dumps({"model": LOCAL_LLM_MODEL, "prompt": "", "keep_alive": "30m"}).encode()
    req = urllib.request.Request(f"{OLLAMA_URL}/api/generate", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120):
        pass
