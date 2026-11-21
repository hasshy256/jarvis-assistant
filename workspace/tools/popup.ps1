# 「ポップアップで開いて」用。サイト・画像・動画ページを、ダッシュボードの中の窓に出す（すぐ戻る。×でも閉じる）
# 例: popup.ps1 -Url "https://transit.yahoo.co.jp/..." -Title "乗り換え案内"
#     popup.ps1 -Url "C:\...\図.png" -Title "路線図"（画像・動画ファイルは絶対パス）
#     popup.ps1 -Close   （「消して」「閉じて」のとき。窓を閉じる）
# 「ブラウザで開いて」と言われたときは、これではなく今までどおり Start-Process で開く
param([string]$Url = "", [string]$Title = "", [switch]$Close)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$file = Join-Path $env:LOCALAPPDATA "jarvis\popup.json"
if ($Close) {
    $json = [pscustomobject]@{ close = $true; at = (Get-Date).ToString("o") } | ConvertTo-Json
    [System.IO.File]::WriteAllText($file, $json, (New-Object System.Text.UTF8Encoding($false)))
    Write-Output "ポップアップを閉じました"
    exit 0
}
if (-not $Url) { Write-Output "-Url か -Close を指定してください"; exit 1 }
if ($Url -notmatch '^https?://') { $Url = (Resolve-Path -LiteralPath $Url).Path }
$json = [pscustomobject]@{ url = $Url; title = $Title; at = (Get-Date).ToString("o") } | ConvertTo-Json
[System.IO.File]::WriteAllText($file, $json, (New-Object System.Text.UTF8Encoding($false)))
Write-Output "ポップアップを出しました"
