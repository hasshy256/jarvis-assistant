"""マイクとスピーカーのミュート（ジャービスだけ。Windows 全体の音量やマイクには触らない）。

ダッシュボードのボタンで切り替え、起動し直しても状態は残る。
マイクをミュートすると audio.Mic が無音を返し、スピーカーをミュートすると tts / 効果音が鳴らなくなる。
"""
import json
import os
import re
import threading

from config import DATA_DIR

FILE = os.path.join(DATA_DIR, "mute.json")
_lock = threading.Lock()
# voiceprint は「声紋認証を止めている」（True = オフ。誰の声にも反応する）
_state = {"mic": False, "speaker": False, "voiceprint": False}

try:
    with open(FILE, encoding="utf-8") as f:
        _state.update({k: bool(v) for k, v in json.load(f).items() if k in _state})
except (OSError, json.JSONDecodeError):
    pass


def is_muted(kind):
    return _state[kind]


def state():
    return dict(_state)


def set_muted(kind, value):
    with _lock:
        _state[kind] = bool(value)
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(FILE, "w", encoding="utf-8") as f:
            json.dump(_state, f)
        return dict(_state)


def toggle(kind):
    return set_muted(kind, not _state[kind])


# 声での切り替え。短い一言（20 字以内）のときだけ。解除（オン）を先に判定する（「ミュート解除」は「ミュート」を含むため）
COMMANDS = [
    (r"(声紋|声の?)(認証|照合|チェック)(を)?(オン|有効|戻して|つけて|入れて)", ("voiceprint", False)),
    (r"(声紋|声の?)(認証|照合|チェック)(を)?(オフ|無効|切って|止めて|やめて|なし)", ("voiceprint", True)),
    (r"マイク(の|を)?(ミュート)?(を)?(解除|オン|戻して|つけて)", ("mic", False)),
    (r"(スピーカー|音|声)(の|を)?(ミュート)?(を)?(解除|オン|戻して|出して|つけて)", ("speaker", False)),
    (r"^(ミュート)?(を)?解除(して)?$", ("both", False)),
    (r"マイク(を)?(ミュート|オフ|切って|止めて)", ("mic", True)),
    (r"(スピーカー|音|声)(を)?(ミュート|オフ|消して|切って)|^ミュート(して)?$", ("speaker", True)),
]


def voice_command(text):
    """「マイクオン」「音を消して」などなら (対象, ミュートするか) を返す。対象は "mic" / "speaker" / "both"。"""
    plain = re.sub(r"[、。,.!！?？\s]", "", text)
    if len(plain) > 20:
        return None
    for pattern, result in COMMANDS:
        if re.search(pattern, plain):
            return result
    return None
