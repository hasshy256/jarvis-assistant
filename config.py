"""ジャービスの設定。挙動を変えたいときはここだけ触ればよいようにしてある。"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.join(HERE, "workspace")       # Claude Code の作業フォルダ（CLAUDE.md = 人格と記憶）
LOG_DIR = os.path.join(HERE, "logs")
DATA_DIR = os.path.expandvars(r"%LOCALAPPDATA%\jarvis")  # venv・モデル・セッション情報（OneDrive の外）
STATE_FILE = os.path.join(DATA_DIR, "state.json")

# ---- マイク ----
MIC_NAME = "JBL Quantum Stream"   # 名前の一部でよい。空文字なら Windows の既定のマイク
SAMPLE_RATE = 16000
BLOCK_SEC = 0.1
SILENCE_SEC = 1.0          # 話し終わってからこの秒数黙ったら区切る
MAX_UTTERANCE_SEC = 20
COMMAND_WAIT_SEC = 5       # 「ジャービス」の後、用件を話し始めるまで待つ秒数
FOLLOWUP_SEC = 6           # 返事の後、呼びかけなしで続けて話せる秒数
CONFIRM_WAIT_SEC = 10      # 「よろしいですか」と聞いたあと返事を待つ秒数
# 確認への返事。否定を先に判定する（「いや、いいよ」は否定）
CONFIRM_NO = r"いいえ|いや|だめ|ダメ|やめ|キャンセル|待って|まって|ストップ|中止|しないで|やっぱり|ノー"
CONFIRM_YES = r"はい|ええ|うん|お願い|おねがい|いいよ|いいです|大丈夫|オッケー|オーケー|ok|やって|実行|どうぞ|頼む|たのむ|よろしく|進めて|イエス"
# これだけを言ったら会話を終えて待機に戻る（Claude には送らない）。値はそのときの返事
END_PHRASES = {
    "ありがとう": "どういたしまして。", "サンキュー": "どういたしまして。", "助かった": "どういたしまして。",
    "もういいよ": "かしこまりました。", "大丈夫": "かしこまりました。", "以上": "かしこまりました。",
    "おしまい": "かしこまりました。", "終わり": "かしこまりました。", "ストップ": "かしこまりました。",
    "おやすみ": "おやすみなさいませ。", "またね": "いつでもお呼びください。",
}

# ---- ウェイクワード（Vosk） ----
VOSK_MODEL = os.path.join(DATA_DIR, "models", "vosk-model-small-ja-0.22")
WAKE_WORD = "ジャービス"
# 音の似た単語を候補に混ぜて、そちらに吸収させることで誤反応を防ぐ
WAKE_DISTRACTORS = ["サービス", "ジャーナル", "ジャスミン", "ジャー", "ビス"]
WAKE_MIN_CONF = 0.8

# ---- 音声認識 ----
STT_ENGINE = "openvino"    # "openvino" = Arc GPU で動かす（速くて正確）/ "faster-whisper" = CPU で動かす
STT_OPENVINO_MODEL = "OpenVINO/whisper-medium-int8-ov"
WHISPER_MODEL = "small"    # CPU で動かすときのモデル（GPU が使えないときもこれに戻る）
# よく使う言葉。聞き間違いを減らすため、認識の前に Whisper へヒントとして渡す（増やしてよい。長くしすぎない）
WHISPER_VOCAB = ["ジャービス", "タスク", "スケジュール", "予定", "歯医者", "カレンダー", "リマインダー", "タイマー", "アラーム",
                 "スタクラ", "ゼミ", "課題", "テスト対策", "ダッシュボード", "ニュース", "天気",
                 "スポティファイ", "ディスコード", "LINE", "GitHub", "VS Code", "Chrome", "タイムツリー"]

# ---- 話者認識（はっしぃの声だけに反応する） ----
SPEAKER_MODEL = os.path.join(DATA_DIR, "models", "speaker.onnx")
SPEAKER_CHECK_ALWAYS = True   # 常時会話モードで、ほかの人やテレビの声を無視する
SPEAKER_CHECK_NORMAL = False  # 通常モード（「ジャービス」と呼んだとき）でも照合するか
SPEAKER_THRESHOLD = 0.30   # 本人とみなす似ている度合い（上げると厳しく、下げると甘くなる。本人 0.7 前後、別人 0.3 以下が目安）
SPEAKER_MIN_SAMPLES = 5       # この回数ぶん声を覚えるまでは照合しない
SPEAKER_MAX_SAMPLES = 40      # 覚えておく声の数（古いものから入れ替える）

# ---- PC の中の小さな AI（Ollama）。あいさつや雑談だけを任せる ----
LOCAL_LLM = True
LOCAL_LLM_MODEL = "gemma3:4b"   # 比べた中で日本語がいちばん自然（qwen2.5:7b は英語が混ざる、3b はぎこちない）
OLLAMA_URL = "http://127.0.0.1:11434"

# ---- 頭脳（Claude Code） ----
CLAUDE_MODEL = "sonnet"
SESSION_IDLE_HOURS = 3     # これ以上話さなかったら新しい会話にする（覚えたことは保管庫に残る）
MEMORY_MODEL = "haiku"     # 会話のあとに記憶を整理する係

# ---- 長期記憶（Obsidian の保管庫） ----
# 保管庫の場所。環境変数 JARVIS_VAULT があればそちらを使う。ジャービスが読み書きするのはこの中の VAULT_FOLDER だけ
OBSIDIAN_VAULT = os.environ.get("JARVIS_VAULT", os.path.expanduser(r"~\Documents\Obsidian"))
VAULT_FOLDER = "Jarvis"
VAULT_AI_NAME = "Claude"         # 記憶やログに書き込んだ AI の名前として残す
VAULT_CONTEXT_CHARS = 8000       # 会話の始めに渡す「記憶/」の最大文字数（多すぎると返事が遅くなる）
VAULT_RECENT_LOG_CHARS = 2000    # 会話の始めに渡す、最近の会話ログの最大文字数

# ---- 自分から話しかける ----
MORNING_BRIEF = True       # 朝のあいさつ（天気・予定・大事なメール）を 1 日 1 回読み上げる
MORNING_START_HOUR = 6     # 1 日の始まり。この時刻以降の「その日最初の起動」か「その日最初のスリープ解除」であいさつする
RING_MAX_SEC = 60          # タイマー・アラームが鳴り続ける最長の秒数
DISK_WARN_GB = 10          # C ドライブの空きがこれ（ギガバイト）を切ったら声で知らせる（1 日 1 回まで。0 で無効）
# 声で知らせる予定・タイマー・アラーム。予定の登録時などにジャービスが聞いて、頼まれたものだけここに書き込む
REMINDERS_FILE = os.path.join(WORKSPACE, "reminders.json")
# やること（タスク）の一覧。頼まれたジャービスが書き込み、ダッシュボードに表示される
TASKS_FILE = os.path.join(WORKSPACE, "tasks.json")
# ダッシュボードから添付されたファイルの置き場所（大きなファイルを OneDrive で同期しないよう、OneDrive の外）
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
UPLOAD_MAX_MB = 25
# 「ポップアップで開いて」: tools の popup.ps1 がこのファイルに書くと、ダッシュボードの中に窓が開く
POPUP_FILE = os.path.join(DATA_DIR, "popup.json")
POPUP_PORT = 18765  # サイトを窓に出すための、PC の中だけで動く中継（127.0.0.1 のみ）
# auto: ほぼすべての操作を許可しつつ、取り返しのつかない操作は Claude Code の安全チェックが止める
# bypassPermissions にすると安全チェックもなくなる（誤認識やテレビの音声でも何でも実行されうる）
PERMISSION_MODE = "auto"
# 頼まれて手を入れる自作アプリのフォルダ。Git で元に戻せるので、中のファイルの書き込みは確認なしで通す
# （削除・.env・.git の中・GitHub への公開は今までどおり確認する）
# 就活ノート（ジャービスの隣の shukatsu フォルダ）。調べた会社は data/companies.json、自分の進み具合とメモは data/mine.json。
# サイトはリモコン（remote.py）の /shukatsu/ で開く
SHUKATSU_DIR = os.path.join(os.path.dirname(HERE), "shukatsu")
# 蔵書管理（ジャービスの隣の manga フォルダ）。ISBN で本を登録し、続きの新刊を国会図書館サーチで探して声でお知らせする。
# サイトはリモコンの /manga/ で開く
MANGA_DIR = os.path.join(os.path.dirname(HERE), "manga")
DEV_PROJECTS = []  # 開発モードで編集を許す自分のプロジェクトのフォルダ（例: ["C:/Users/you/dev/myapp"]）

# ---- 音声合成 ----
TTS_ENGINE = "edge"            # "edge"（ネット経由の自然な声）か "voicevox"（PC の中で動く。声を選べる）
VOICE = "ja-JP-KeitaNeural"    # edge の声。女性の声なら ja-JP-NanamiNeural
VOICE_RATE = "+20%"
VOICE_PITCH = "+0Hz"           # edge の声の高さ。マイナスで低く落ち着いた声（映画のジャービスに寄せる）。"+0Hz" で元の高さ
VOICEVOX_SPEAKER = 13          # VOICEVOX の声の番号（jarvis\声のサンプル のファイル名の ID。13 = 青山龍星）
VOICEVOX_SPEED = 1.2           # VOICEVOX の話す速さ（1.0 が標準）
VOICEVOX_URL = "http://127.0.0.1:50021"

# ---- ダッシュボード ----
DASHBOARD_SCREEN = "secondary"   # "secondary" = メインでない方のモニター。0, 1 … で番号指定も可
WEATHER_LAT, WEATHER_LON = 35.6895, 139.6917   # 東京（自分の地域の緯度・経度に変える）
WEATHER_REFRESH_MIN = 15
SCHEDULE_REFRESH_MIN = 10     # 予定の取り直し間隔。ジャービスが予定を登録・変更したときはすぐ取り直す
NEWS_FEED_URL = "https://www.nhk.or.jp/rss/news/cat0.xml"   # NHK 主要ニュース
NEWS_REFRESH_MIN = 5
NEWS_COUNT = 8
STATS_REFRESH_SEC = 3      # CPU・メモリ・ディスクの表示更新間隔

# ---- モード ----
# normal: 「ジャービス」と呼ぶと反応 / always: 呼ばなくても話した言葉すべてに反応 / sleep: 何にも反応しない
MODE_NAMES = {"normal": "通常モード", "always": "常時会話モード", "sleep": "スリープモード"}
MODE_PHRASES = {  # 短い一言（20 字以内）のときだけモード切り替えとして扱う
    "normal": r"通常モード|普通のモード|ノーマルモード|元のモード|いつものモード",
    "always": r"常時(会話)?モード|会話モード|呼ばなくて(も)?いい|ずっと聞いて",
    "sleep": r"スリープ(モード)?(にして)?$|スリープして|おやすみモード|寝ていいよ|休んで|静かにして|黙ってて|おやすみ(なさい)?$|もう寝る|寝るね",
}
WAKE_FROM_SLEEP = r"起きて|おきて|スリープ解除|解除して|おはよう|戻って"
MIN_ALWAYS_UTTERANCE_SEC = 0.5   # 常時会話モードで、これより短い音（物音など）は無視

# ---- iPhone からのリモコン（remote.py） ----
# 待ち受けるアドレスとポート。環境変数 JARVIS_REMOTE_HOST / JARVIS_REMOTE_PORT があればそちらを使う。
# 127.0.0.1 = この PC の中だけ（Tailscale の「tailscale serve」で HTTPS にして iPhone に出す。おすすめ）
# 0.0.0.0 = ほかの機器からも直接つながる（Tailscale の IP で http://100.x.x.x:8765 に来る。Windows のファイアウォールの許可が要る）
REMOTE_HOST = os.environ.get("JARVIS_REMOTE_HOST", "127.0.0.1")
REMOTE_PORT = int(os.environ.get("JARVIS_REMOTE_PORT", "8765"))
REMOTE_TOKEN_ENV = "JARVIS_TOKEN"   # 合言葉（トークン）を入れておく環境変数の名前
REMOTE_STATE_FILE = os.path.join(DATA_DIR, "remote_state.json")      # iPhone 側の会話のつながり（PC の会話とは別）
REMOTE_HISTORY_FILE = os.path.join(DATA_DIR, "remote_history.json")  # iPhone の画面に出す会話ログ
REMOTE_HISTORY_MAX = 200
REMOTE_MEMORY_IDLE_SEC = 300  # iPhone からの依頼が途切れて、この秒数たったら会話を記憶係に渡す（PC の会話と同じ記憶に入る）
REMOTE_RESTART_FLAG = os.path.join(DATA_DIR, "remote_restart.flag")  # このファイルが置かれたら、リモコンが返事を返し終えてから自分で再起動する
REMOTE_ALIVE_SEC = 25   # 画面アプリからの「開いています」の合図がこの秒数途切れたら、画面は閉じていると見なす

# ---- iPhone への通知（push.py。無料アプリ ntfy） ----
# トピック名が合言葉代わり。ntfy.sh の公開サーバーなので、推測されにくい長い名前を push_topic.txt に入れておく（コードには書かない）。
# iPhone の ntfy アプリで、同じトピック名を購読する。ファイルが無ければ通知は送らない
NTFY_SERVER = "https://ntfy.sh"
NTFY_TOPIC_FILE = os.path.join(DATA_DIR, "push_topic.txt")

# ---- Google カレンダー（PC から Google Calendar API で直接読む・書く） ----
# credentials.json（Google Cloud で作る鍵）をここに置き、初回だけ `python gcal.py login` でログインする
GOOGLE_DIR = os.path.join(DATA_DIR, "google")
GOOGLE_CREDENTIALS = os.path.join(GOOGLE_DIR, "credentials.json")
GOOGLE_TOKEN = os.path.join(GOOGLE_DIR, "token.json")   # ログイン後に自動で作られる
GOOGLE_CALENDAR_ID = "primary"   # いつものカレンダー。別のカレンダーならその ID
TIMEZONE = "Asia/Tokyo"

# ---- 議事録 ----
MINUTES_DIR = os.path.join(WORKSPACE, "minutes")            # 文字起こしと議事録の置き場所
MINUTES_AUDIO_DIR = os.path.join(DATA_DIR, "minutes_audio")  # 録音した音声（大きいので OneDrive の外）
MINUTES_SEG_MIN_SEC = 6     # 文字起こしの区切りの最短秒数（この長さを超えてから無音で区切る）
MINUTES_SEG_MAX_SEC = 25    # 区切りの最長秒数（Whisper は 30 秒までなので余裕を持たせる）

HOTKEY = "ctrl+alt+j"      # 声の代わりにキーでも呼べる。読み上げ中に押すと止まる
DASHBOARD_HOTKEY = "ctrl+alt+d"  # ダッシュボードを出す・隠す（×で閉じても隠れるだけで、ジャービスは動き続ける）

# ---- manaba 巡回（manaba.py） ----
# ID・パスワードはコードにも設定にも書かず、Windows の資格情報（keyring）に入れる。登録は `python manaba.py setup`
MANABA_ENABLED = True
MANABA_BASE = "https://YOUR-SCHOOL.manaba.jp/ct/"
MANABA_DIR = os.path.join(DATA_DIR, "manaba")                       # ログイン状態・前回の結果・調査用の保存（OneDrive の外）
MANABA_FILES_DIR = os.path.expanduser(r"~\Documents\manaba")         # 資料のダウンロード先（科目名のフォルダに分ける）
MANABA_TIMETABLE = os.path.join(HERE, "config", "timetable.json")   # 授業時間。書き換えると再起動なしで反映される
MANABA_INTERVAL_MIN = 30          # 軽い巡回（課題一覧だけ）の間隔
MANABA_CLASS_INTERVAL_MIN = 10    # 授業時間中の間隔
MANABA_FULL_TIMES = ["12:00", "18:00"]   # しっかり巡回（全コースの資料・ニュース）の時刻
MANABA_QUIET = (1, 7)             # 1 時〜7 時は巡回しない
MANABA_PAGE_WAIT_SEC = (2.0, 4.0) # 1 ページごとに空ける秒数（この範囲でランダム）
MANABA_SUMMARY_MODEL = "sonnet"   # 新着の要約に使う Claude のモデル
MANABA_VAULT_FOLDER = "manaba"    # Obsidian の Jarvis フォルダの中の置き場所

# ---- 開発モード（devmode.py） ----
# ボタン・声・ホットキーで入る。中では、ガードが止める操作を確認なしで通す（メール送信・シャットダウン・開発フォルダの外の削除などは止めたまま）
DEV_HOTKEY = "ctrl+alt+v"
DEV_AUTO_OFF_HOURS = 3        # この時間、操作がなければ自動で通常に戻る（0 で無効）。再起動でも必ず切れる
DEV_PHRASE_ON = r"開発モード|デベロッパーモード|開発に集中|開発を始め"
DEV_PHRASE_OFF = r"開発モード(を)?(終わ|おわ|終了|やめ|解除|オフ|切|出|抜)|開発(を)?(終わ|おわ|終了)|(通常|普通)(の)?モード(に|へ)?戻|開発モードじゃなく"
DEV_TASKS_FILE = os.path.join(WORKSPACE, "dev_tasks.json")   # 開発のやること（形式は tasks.json と同じ）
DEV_PROJECT_REFRESH_SEC = 10  # 開発モード中、git の状態を読み直す間隔
# 開発モード中に確認なしで通した操作の記録。拡張子は .log にしない（会話ログ欄は logs の *.log を読むので、混ざると会話が出なくなる）
DEV_GUARD_LOG = os.path.join(LOG_DIR, "dev_guard.txt")
# 再起動をまたいで開発モードを続けるための保存ファイル（本体だけが書く。guard.py が、AI による書き換えを止める）
DEV_STATE_FILE = os.path.join(DATA_DIR, "dev_state.json")
DEV_RESUME_SEC = 300          # 再起動のために保存した状態が戻る猶予（この秒数以内に起動し直したら開発モードを続ける）
