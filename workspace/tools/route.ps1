# 乗り換え案内の図を作って、ダッシュボードのポップアップに出す
# 使い方: route.ps1 -Json '<JSON>'
# JSON の形（駅は出発から到着の順、lines は駅と駅のあいだの路線で、駅の数より 1 つ少ない）:
# 乗る駅（dep がある駅）には "board" に乗車位置の文を入れる。例 "乗車位置 10両の前寄り"。乗換案内に載っていなければ "乗車位置 情報なし"
# {"title":"与野 → 舞浜 乗り換え案内",
#  "stations":[{"name":"与野","dep":"19:47","dep_pl":"2番線"},
#              {"name":"浦和","arr":"19:52","arr_pl":"1番線","dep":"19:56","dep_pl":"3番線"},
#              {"name":"舞浜","arr":"20:52","arr_pl":"1番線"}],
#  "lines":[{"name":"京浜東北線","dest":"大船行"},{"name":"京葉線快速","dest":"上総一ノ宮行"}],
#  "note":"東京駅は、降りたあと京葉線ホームまで歩いて約10分"}
param([Parameter(Mandatory)][string]$Json, [switch]$NoPopup)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Drawing
$chaku = [string][char]0x7740   # kanji for arrive (file has no BOM, so write kanji by code)
$hatsu = [string][char]0x767A   # kanji for depart
$d =$Json | ConvertFrom-Json
$n = @($d.stations).Count
$W = 1300; $H = 470; $y = 195
$b = New-Object System.Drawing.Bitmap $W, $H
$g = [System.Drawing.Graphics]::FromImage($b)
$g.SmoothingMode = 'AntiAlias'; $g.TextRenderingHint = 'AntiAliasGridFit'; $g.Clear([System.Drawing.Color]::FromArgb(2, 12, 20))
function F($s, $st = 'Regular') { New-Object System.Drawing.Font("Yu Gothic UI", $s, [System.Drawing.FontStyle]::$st, [System.Drawing.GraphicsUnit]::Pixel) }
$white = [System.Drawing.Brushes]::White
$cy = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(46, 230, 255))
$mut = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(150, 190, 200))
$palette = @([System.Drawing.Color]::FromArgb(0, 160, 230), [System.Drawing.Color]::FromArgb(255, 140, 0), [System.Drawing.Color]::FromArgb(230, 50, 60), [System.Drawing.Color]::FromArgb(60, 200, 120), [System.Drawing.Color]::FromArgb(180, 110, 230))
$sf = New-Object System.Drawing.StringFormat; $sf.Alignment = 'Center'
$left = 150; $right = $W - 150
$xs = @(); for ($i = 0; $i -lt $n; $i++) { $xs += [int]($left + ($right - $left) * $i / [Math]::Max(1, $n - 1)) }
$g.DrawString([string]$d.title, (F 28 Bold), $cy, 40, 22)
for ($i = 0; $i -lt $n - 1; $i++) {
    $c = $palette[$i % $palette.Count]
    $pen = New-Object System.Drawing.Pen $c, 12
    $g.DrawLine($pen, $xs[$i], $y, $xs[$i + 1], $y)
    $mx = ($xs[$i] + $xs[$i + 1]) / 2
    $ln = @($d.lines)[$i]
    if ($ln) {
        $g.DrawString([string]$ln.name, (F 24 Bold), (New-Object System.Drawing.SolidBrush $c), $mx, $y - 78, $sf)
        $g.DrawString([string]$ln.dest, (F 21), $white, $mx, $y - 45, $sf)
    }
}
for ($i = 0; $i -lt $n; $i++) {
    $s = @($d.stations)[$i]; $x = $xs[$i]
    $g.FillEllipse($white, $x - 17, $y - 17, 34, 34); $g.FillEllipse($cy, $x - 10, $y - 10, 20, 20)
    $g.DrawString([string]$s.name, (F 38 Bold), $white, $x, $y + 35, $sf)
    $arr = if ($s.arr) { "$($s.arr)$chaku  $($s.arr_pl)" } else { "" }
    $dep = if ($s.dep) { "$($s.dep)$hatsu  $($s.dep_pl)" } else { "" }
    $g.DrawString($arr, (F 25), $mut, $x, $y + 97, $sf)
    $g.DrawString($dep, (F 25), $cy, $x, $y + 130, $sf)
    if ($s.board) { $g.DrawString([string]$s.board, (F 21), $white, $x, $y + 170, $sf) }  # board: boarding-position text passed in the JSON
}
if ($d.note) { $g.DrawString([string]$d.note, (F 21), $mut, 40, $H - 48) }
$dir = Join-Path $env:LOCALAPPDATA "jarvis"
Get-ChildItem $dir -Filter "route_*.png" -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -lt (Get-Date).AddHours(-3) } | Remove-Item -ErrorAction SilentlyContinue
$p = Join-Path $dir ("route_" + (Get-Date -Format "HHmmss") + ".png")
$b.Save($p); $g.Dispose(); $b.Dispose()
if (-not $NoPopup) { & (Join-Path $PSScriptRoot "popup.ps1") -Url $p -Title ([string]$d.title) | Out-Null }
Write-Output "乗り換えの図を出しました"
