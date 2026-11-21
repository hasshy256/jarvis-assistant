# ジャービスのセットアップ（新しい PC で 1 回だけ実行する）。setup.bat をダブルクリックすると、これが動く。
#
# OneDrive で共有されるもの（プログラム・記憶・タスクなど）はそのまま使い、
# PC ごとに必要なもの（Python・ライブラリ・モデル・自動起動）を用意する。何度実行しても大丈夫。
#
#   -SkipOllama     PC 内の小さな AI（雑談担当、約 3.4GB）を入れない。入れなくても雑談は Claude が答える
#   -WithVoicevox   VOICEVOX（声の差し替え用、任意）も入れる
#   -NoAutostart    PC の起動時に自動で立ち上げない
param([switch]$SkipOllama, [switch]$WithVoicevox, [switch]$NoAutostart)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$data = Join-Path $env:LOCALAPPDATA "jarvis"
$models = Join-Path $data "models"
$venv = Join-Path $data ".venv"

function Step($n, $text) { Write-Host ""; Write-Host "[$n/7] $text" -ForegroundColor Cyan }
function Ok($text) { Write-Host "  OK  $text" -ForegroundColor Green }
function Warn($text) { Write-Host "  !!  $text" -ForegroundColor Yellow }
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}
function Winget-Install($id, $name) {
    Write-Host "  $name をインストールしています…"
    winget install --id $id -e --silent --accept-source-agreements --accept-package-agreements | Out-Null
    Refresh-Path
}

Write-Host "=== J.A.R.V.I.S. セットアップ ===" -ForegroundColor Cyan
Write-Host "ジャービスのフォルダ: $here"
Write-Host "この PC 用のデータ:   $data"
New-Item -ItemType Directory -Force $data, $models | Out-Null

# 1. Python 3.12
Step 1 "Python 3.12"
$hasPy = $false
try { & py -3.12 --version *> $null; $hasPy = ($LASTEXITCODE -eq 0) } catch {}
if (-not $hasPy) { Winget-Install "Python.Python.3.12" "Python 3.12" }
& py -3.12 --version
Ok "Python"

# 2. ライブラリ（venv）
Step 2 "ライブラリ（数分かかります）"
if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) { & py -3.12 -m venv $venv }
$py = Join-Path $venv "Scripts\python.exe"
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet -r (Join-Path $here "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "ライブラリのインストールに失敗しました" }
Ok "ライブラリ"

# 3. モデル（「ジャービス」の聞き取り・話者認識）。音声認識のモデルは初回起動時に自動でダウンロードされる
Step 3 "モデル"
$vosk = Join-Path $models "vosk-model-small-ja-0.22"
if (-not (Test-Path $vosk)) {
    $zip = Join-Path $models "vosk-ja.zip"
    Invoke-WebRequest "https://alphacephei.com/vosk/models/vosk-model-small-ja-0.22.zip" -OutFile $zip
    Expand-Archive $zip -DestinationPath $models -Force
    Remove-Item $zip
}
Ok "「ジャービス」の聞き取り（Vosk）"
$spk = Join-Path $models "speaker.onnx"
if (-not (Test-Path $spk)) {
    Invoke-WebRequest "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx" -OutFile $spk
}
Ok "話者認識"

# 4. Claude Code（頭脳）。ログインだけは手で行う
Step 4 "Claude Code"
$claude = Get-Command claude -ErrorAction SilentlyContinue
$ext = Get-ChildItem "$env:USERPROFILE\.vscode\extensions\anthropic.claude-code-*\resources\native-binary\claude.exe" -ErrorAction SilentlyContinue
if (-not $claude -and -not $ext) {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Winget-Install "Git.Git" "Git" }
    Write-Host "  Claude Code をインストールしています（公式のインストーラー）…"
    Invoke-RestMethod https://claude.ai/install.ps1 | Invoke-Expression
    Refresh-Path
    $claude = Get-Command claude -ErrorAction SilentlyContinue
}
if ($claude -or $ext) { Ok "Claude Code" } else { Warn "Claude Code が見つかりません。https://claude.com/claude-code から入れてください" }

# 5. PC 内の小さな AI（任意）
Step 5 "PC 内の小さな AI（Ollama + gemma3:4b）"
if ($SkipOllama) {
    Warn "入れませんでした（-SkipOllama）。雑談も Claude が答えます"
} else {
    $ollama = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
    if (-not (Test-Path $ollama)) { Winget-Install "Ollama.Ollama" "Ollama" }
    try { Invoke-RestMethod http://127.0.0.1:11434/api/version -TimeoutSec 3 | Out-Null }
    catch { Start-Process $ollama -ArgumentList "serve" -WindowStyle Hidden; Start-Sleep 5 }
    Write-Host "  モデルをダウンロードしています（約 3.4GB）…"
    & $ollama pull gemma3:4b
    Ok "PC 内の AI"
}

# 6. VOICEVOX（任意）
Step 6 "VOICEVOX（声の差し替え・任意）"
if ($WithVoicevox) { Winget-Install "HiroshibaKazuyuki.VOICEVOX.CPU" "VOICEVOX"; Ok "VOICEVOX（config.py の TTS_ENGINE で切り替え）" }
else { Write-Host "  入れませんでした（使うときは -WithVoicevox を付けて実行）" }

# 7. 自動起動
Step 7 "PC の起動時に自動で立ち上げる"
$vbs = Join-Path $here "ジャービス.vbs"
$lnkPath = Join-Path ([Environment]::GetFolderPath("Startup")) "ジャービス.lnk"
if ($NoAutostart) {
    Write-Host "  設定しませんでした（-NoAutostart）"
} else {
    $ws = New-Object -ComObject WScript.Shell
    $lnk = $ws.CreateShortcut($lnkPath)
    $lnk.TargetPath = Join-Path $env:WINDIR "System32\wscript.exe"
    $lnk.Arguments = "`"$vbs`""
    $lnk.WorkingDirectory = $here
    $lnk.Description = "ジャービス（AI 執事）"
    $lnk.Save()
    Ok "スタートアップに登録しました"
}

Write-Host ""
Write-Host "=== セットアップが終わりました ===" -ForegroundColor Green
Write-Host "残りの手順:"
Write-Host "  1. Claude Code にログインする（まだなら）: ターミナルで claude と打ち、表示に従ってログイン"
Write-Host "     Gmail・Google カレンダーは、同じアカウントの claude.ai の設定でつないであれば、そのまま使えます"
Write-Host "  2. ジャービス.vbs をダブルクリックして起動（初回は音声認識モデルのダウンロードで数分かかります）"
Write-Host "  3. 「ジャービス、私の声を覚えて」で、この PC のマイクで声を登録する"
Write-Host "  ※ 2 台で同時に起動すると、タイマーやお知らせが両方で鳴ります。使わない方は終了かスリープに"
