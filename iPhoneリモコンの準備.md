
# iPhone リモコンの準備

PC のジャービスに iPhone から話しかけるための準備。上から順に進める。

```
iPhone（ショートカット / ホーム画面の JARVIS）
   │  Tailscale（自分の機器だけの専用回線）
   ▼
PC の remote.py（FastAPI）─▶ Claude Code（ジャービスの頭脳）
   │                         └▶ Google Calendar API（予定の確認・登録）
   └ 返事 {"reply": "...", "action": ...} ─▶ iPhone が読み上げ、action があれば iPhone で実行
```

## 1. PC でサーバーを動かす

1. PC にログインすると自動で起動する（スタートアップの `ジャービス リモコン.lnk` → `リモコン.vbs`。黒い画面なし）。
   動作の記録を見ながら動かすときは `リモコン起動.bat`（自動起動のものが動いていれば先に止める）
2. 合言葉（トークン）は Windows の環境変数 `JARVIS_TOKEN` に登録済み。確かめるときは PowerShell で
   `[Environment]::GetEnvironmentVariable("JARVIS_TOKEN", "User")`
3. 待ち受けは `config.py` の `REMOTE_HOST` / `REMOTE_PORT`（既定は `127.0.0.1:8765`）。
   環境変数 `JARVIS_REMOTE_HOST` / `JARVIS_REMOTE_PORT` でも変えられる

PC の中での動作確認（PowerShell）:

```powershell
$tok = [Environment]::GetEnvironmentVariable("JARVIS_TOKEN", "User")
$body = [Text.Encoding]::UTF8.GetBytes('{"text":"3分のタイマー"}')
Invoke-RestMethod http://127.0.0.1:8765/command -Method Post -Headers @{Authorization="Bearer $tok"} `
  -ContentType "application/json; charset=utf-8" -Body $body
```

## 2. Tailscale でつなぐ

1. PC と iPhone の両方に Tailscale を入れ、同じアカウントでログインする
2. Tailscale の管理画面（login.tailscale.com）→ DNS で **MagicDNS** と **HTTPS Certificates** を有効にする
3. PC の PowerShell で `tailscale serve --bg 8765` を実行する
   - `https://YOUR-PC.YOUR-TAILNET.ts.net/` という住所が表示される。これが iPhone から使う URL
   - HTTPS になるので、ホーム画面のアプリが正しく動く。Tailscale の外（インターネット）からは見えない
   - 止めるときは `tailscale serve --https=443 off`

> `tailscale serve` を使わない場合は、`JARVIS_REMOTE_HOST=0.0.0.0` にして `http://100.x.x.x:8765`（PC の Tailscale の IP）で
> つなぐこともできる。そのときは Windows のファイアウォールで 8765 番を許可する必要があり、ホーム画面のアプリの一部（オフライン表示）は動かない。

## 3. ホーム画面に JARVIS を置く（PWA）

1. iPhone の Safari で `https://YOUR-PC.YOUR-TAILNET.ts.net/` を開く
2. 合言葉（トークン）を入力して「接続」
3. 共有ボタン →「ホーム画面に追加」

画面（2026-10-18 に最小版へ作り直し。前の版は `pwa_v1/` に保存）:
- 会話だけの画面（上に回る円と時刻）。文字で送るか、🎙 か円を**押している間だけ**聞き取り、離すと送る。
  文字起こしは iPhone（Safari の音声認識）がして、文字の依頼として PC に送る（録音を送る方式は、iPhone で録音が空になる問題が続いたのでやめた）
- 返事はいつも PC のジャービスと同じ声で読み上げる。右上のボタンでミュートできる
- アラームなど iPhone 本体の操作は、最小版ではまだ使えない（返事に注意書きが出る）
- PC 側で画面のファイルを直すと「新しい版があります」と出る（`/api/version`）。アプリを閉じて開き直すと反映。
  自動で読み込み直すと、iPhone ではアプリを閉じるまでマイクが使えなくなるため

## 4. Google カレンダー（PC から Google Calendar API で読む・書く）

### credentials.json を作る（初回だけ）
1. https://console.cloud.google.com/ を開き、上のプロジェクト選択 →「新しいプロジェクト」→ 名前「jarvis」で作成
2. 「API とサービス」→「ライブラリ」→ **Google Calendar API** を検索 →「有効にする」
3. 「Google Auth Platform」（または「OAuth 同意画面」）→「開始」
   - アプリ名「Jarvis」、サポートメールに自分の Gmail、対象は **外部**
   - 「対象」→ テストユーザーに自分の Gmail を追加
   - 公開ステータスは「テスト中」のまま（本番にするには公開 Web サイトとプライバシーポリシーの URL が要るため）。
     そのため **7 日ごとに `gcal.py login` でログインし直す**。切れている間は、予定は自動で Claude 経由に切り替わる
4. 「クライアント」→「クライアントを作成」→ 種類 **デスクトップ アプリ** → 作成 → **JSON をダウンロード**
5. ダウンロードしたファイルを `credentials.json` という名前にして、`%LOCALAPPDATA%\jarvis\google\` に置く
   （OneDrive で同期しない場所。フォルダがなければ作る）

### ログイン（初回だけ）
```powershell
cd "（このリポジトリのフォルダ）"
& "$env:LOCALAPPDATA\jarvis\.venv\Scripts\python.exe" gcal.py login
```
ブラウザが開くので、Google アカウントを選ぶ →「このアプリは Google で確認されていません」→「詳細」→「Jarvis（安全ではないページ）に移動」→「続行」。
`%LOCALAPPDATA%\jarvis\google\token.json` ができれば完了。`gcal.py week` で 7 日分の予定が出るか確かめる。

そのあと `リモコン起動.bat` を起動し直すと、予定の確認・登録が Google Calendar API 経由になる（画面の予定表示も同じデータ）。
声のジャービス（PC のダッシュボード）も、次の起動から予定の表示に同じ API を使う（速くなり、Claude の利用枠も使わない）。
準備していない間は、これまでどおり Claude 経由でカレンダーを見る。

## 5. iPhone のショートカット

> 2026-10-18 から、画面アプリのボタンは次の 2 つの小さなショートカットを使う（下の「ジャービス実行」は Siri 用のショートカット「ジャービス」を使うときだけ必要）。
> - **ジャービスアラーム**: 「入力から日付を取得」→「アラームを作成」（時刻 = 日付）。入力は "07:00" のような時刻
> - **ジャービスタイマー**: 「タイマーを開始」（長さ = ショートカットの入力、単位 = 秒）。入力は秒数
> - アプリを開くボタンは、LINE などの URL（line:// など）で画面アプリから直接開く（`pwa/app.js` の `APP_URL`）


2 つ作る。「ジャービス」が話しかけ役、「ジャービス実行」が iPhone の操作役（ホーム画面のアプリからも使う）。

### 返事の形
```json
{"reply": "7時に起こします", "action": "alarm", "time": "07:00", "date": "2026-10-18"}
```

| action | 一緒に来る値 | iPhone ですること |
|---|---|---|
| `null` | なし | 読み上げるだけ |
| `alarm` | `time`（"07:00"）、`date` | アラームを作る |
| `timer` | `seconds`（秒数） | タイマーを始める |
| `open_app` | `app`（"LINE" など） | アプリを開く |
| `open_url` | `url` | URL を開く |

`"awaiting": true` が付いているときは、ジャービスが「よろしいですか」と確認している（削除・送信など）。続けて「はい」か「いいえ」を送る。

### ショートカット「ジャービス実行」（先に作る）
1. ショートカット App →「＋」→ 名前を **ジャービス実行**
2. 「入力から辞書を取得」（入力 = ショートカットの入力）
3. 「辞書の値を取得」キー `action` → 名前を「操作」に変える（変数名は長押しで変更）
4. 「if」 操作 が `alarm` と等しい
   - 「辞書の値を取得」キー `time` →「入力から日付を取得」→「アラームを作成」（時刻 = その日付、ラベル「ジャービス」）
5. 「if」 操作 が `timer` と等しい
   - 「辞書の値を取得」キー `seconds` →「タイマーを開始」（長さ = その値、単位 **秒**）
6. 「if」 操作 が `open_url` と等しい
   - 「辞書の値を取得」キー `url` →「URL を開く」
7. 「if」 操作 が `open_app` と等しい
   - 「辞書の値を取得」キー `app` → 名前を「アプリ名」に
   - 「if」 アプリ名 が `LINE` と等しい →「アプリを開く」LINE。よく使うアプリの数だけ「if」を足す
     （「アプリを開く」は文字でアプリを選べないため）
8. 設定（ⓘ）で「共有シートに表示」はオフでよい

### ショートカット「ジャービス」
1. 「＋」→ 名前を **ジャービス**（Siri に「ジャービス」と言えば起動する）
2. 「テキストを音声入力」（言語: 日本語、聞き取りを停止: 一時停止後）
3. 「辞書」→ キー `text`（テキスト）、値 = 音声入力したテキスト
4. 「URL の内容を取得」
   - URL: `https://YOUR-PC.YOUR-TAILNET.ts.net/command`
   - 方法: **POST**
   - ヘッダ: `Authorization` = `Bearer <トークン>`
   - 本文を要求: **ファイル** → 上の「辞書」（または「JSON」を選んでキー `text` に音声入力の値）
5. 「辞書の値を取得」キー `reply` →「テキストを読み上げる」
6. 「辞書の値を取得」キー `action`（入力 =「URL の内容」）
7. 「if」 action に **値がある**
   - 「ショートカットを実行」ジャービス実行（入力 =「URL の内容」）
8. 「辞書の値を取得」キー `awaiting`（入力 =「URL の内容」）
9. 「if」 awaiting に **値がある** →「ショートカットを実行」ジャービス（確認への返事をそのまま話せる）

うまくいかないとき:
- 「URL の内容を取得」の結果を「クイックルック」で表示すると、返事の中身が見える
- `401` = 合言葉が違う。`Bearer ` のあとの空白を忘れていないか
- つながらない = PC の `リモコン起動.bat` と Tailscale が動いているか、`tailscale serve` を実行したか

## 仕組みのメモ

- `remote.py` は声のジャービス（`jarvis.py`）とは別に動く。会話のつながりも別（PC で話したことと混ざらない）だが、
  人格と記憶（`workspace/CLAUDE.md`）、タスク、取り返しのつかない操作のガードは共通
- 「3分のタイマー」「7時に起こして」「今何時？」「ありがとう」は Claude を通さず一瞬で返す。雑談は PC 内の AI（Ollama）
- 再起動は `%LOCALAPPDATA%\jarvis\remote_restart.flag` を置くだけ（返事を返し終えてから自分で起動し直す）。
  リモコンの中から Stop-Process で止めると、起動し直す処理も一緒に止まってつながらなくなる（10/18 に実際に起きた）
- 🎙 の会話: 録音を `POST /api/voice` で送り、PC の Whisper（声のジャービスと同じ）で聞き取る。返事の声は `POST /api/tts`（edge-tts）
- 会話の記録は `logs/remote/`、iPhone の画面の会話ログは `%LOCALAPPDATA%\jarvis\remote_history.json`
