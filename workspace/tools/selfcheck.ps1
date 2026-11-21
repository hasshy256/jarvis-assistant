# ジャービスと周辺アプリの動作点検。画像を使わないので、Claude の利用枠をほとんど使わない（数秒で終わる）。
# 使い方: selfcheck.ps1            全部点検して「OK / 要確認」を一覧で出す
#         selfcheck.ps1 -Shot      さらに全画面を撮影してパスを出す（見た目を確かめたいときだけ。Read で見る）
# 点検するもの: 本体の起動と最近のエラー、リモコン（8765）と蔵書の API、蔵書の画面ファイルの文法、
#   設定の読み込み、定期タスク、記憶の大きさ
param([switch]$Shot)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$root = Split-Path (Split-Path $PSScriptRoot)
$py = "$env:LOCALAPPDATA\jarvis\.venv\Scripts\python.exe"
$bad = 0
function Show($ok, $name, $detail = "") {
    if (-not $ok) { $script:bad++ }
    "{0} {1} {2}" -f ($(if ($ok) { "OK  " } else { "要確認" }), $name, $detail)
}

# 1. 本体のログ（最後の起動から 30 分以内に動いているか、最近のエラー）
$log = Get-ChildItem "$root\logs\*.log" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
$tail = Get-Content $log.FullName -Tail 200 -Encoding UTF8
$last = $tail | Select-Object -Last 1
$age = (Get-Date) - $log.LastWriteTime
Show ($age.TotalMinutes -lt 30) "本体のログ更新" ("{0:N0} 分前 : {1}" -f $age.TotalMinutes, ($last -replace '^\S+ \S+ ', ''))
$errs = @($tail | Where-Object { $_ -match "^\d{4}-" -and $_ -match "失敗|エラー|Traceback|例外" -and $_ -notmatch "^\S+ \S+ (あなた|ジャービス|\[記憶\] 会話)" })
Show ($errs.Count -eq 0) "最近のエラー" ($(if ($errs.Count) { "{0} 件 / 最新: {1}" -f $errs.Count, $errs[-1] } else { "なし" }))

# 2. リモコンと蔵書
foreach ($u in @("/manga/", "/manga/app.js", "/manga/style.css", "/api/manga")) {
    try { $r = Invoke-WebRequest "http://127.0.0.1:8765$u" -UseBasicParsing -TimeoutSec 5; Show ($r.StatusCode -eq 200) "リモコン $u" "HTTP $($r.StatusCode)" }
    catch { Show $false "リモコン $u" ($_.Exception.Message -replace "\r?\n", " ") }
}

# 3. 蔵書の画面ファイルの文法（node があれば）
$node = Get-Command node -ErrorAction SilentlyContinue
if ($node) {
    $js = "$(Split-Path $root)\manga\site\app.js"
    $o = & node -e "new Function(require('fs').readFileSync(process.argv[1],'utf8'))" $js 2>&1
    Show ($LASTEXITCODE -eq 0) "蔵書 app.js の文法" ($o | Out-String).Trim()
}

# 4. 設定が読み込める
$o = & $py -c "import sys; sys.path.insert(0, r'$root'); import config, tts; print(config.TTS_ENGINE, config.VOICE, config.VOICE_PITCH)" 2>&1
Show ($LASTEXITCODE -eq 0) "設定と音声の読み込み" ($o | Out-String).Trim()

# 5. 蔵書の定期タスク
$t = Get-ScheduledTask -TaskName "MangaNewReleases" -ErrorAction SilentlyContinue
if ($t) { $i = $t | Get-ScheduledTaskInfo; Show ($i.LastTaskResult -in 0, 267011) "定期タスク MangaNewReleases" "前回 $($i.LastRunTime) 結果 $($i.LastTaskResult)" } else { Show $false "定期タスク MangaNewReleases" "見つからない" }
$t = Get-ScheduledTask -TaskName "JarvisMemoryTidy" -ErrorAction SilentlyContinue
if ($t) { $i = $t | Get-ScheduledTaskInfo; Show ($i.LastTaskResult -in 0, 267011) "定期タスク JarvisMemoryTidy" "前回 $($i.LastRunTime) 次回 $($i.NextRunTime)" } else { Show $false "定期タスク JarvisMemoryTidy" "未登録" }

# 6. 記憶の大きさ（会話の始めに渡す量の目安）
$mem = "$env:USERPROFILE\Documents\Obsidian\Jarvis\記憶"
if (Test-Path $mem) { $kb = [int]((Get-ChildItem $mem -Filter *.md | Measure-Object Length -Sum).Sum / 1024); Show ($kb -lt 120) "記憶ノートの合計" "$kb KB" }

if ($Shot) { & "$PSScriptRoot\shot.ps1" }
"---"
if ($bad) { "要確認 $bad 件" } else { "すべて問題なし" }
