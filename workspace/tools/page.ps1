# Chrome で今開いているページの文字を、アクセシビリティ機能（UI Automation）で読み出す。Chrome の設定変更や再起動は不要
param([int]$Max = 4000)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes
$root = [System.Windows.Automation.AutomationElement]::RootElement
$wins = $root.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
$chrome = $null
foreach ($w in $wins) { if ($w.Current.ClassName -eq 'Chrome_WidgetWin_1' -and $w.Current.Name -like '*Google Chrome*') { $chrome = $w; break } }
if (-not $chrome) { Write-Output "Chrome のウィンドウが見つかりません"; exit 1 }
"タイトル: " + $chrome.Current.Name
$cond = New-Object System.Windows.Automation.PropertyCondition ([System.Windows.Automation.AutomationElement]::ControlTypeProperty), ([System.Windows.Automation.ControlType]::Document)
$doc = $null
for ($i = 0; $i -lt 10 -and -not $doc; $i++) { $doc = $chrome.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond); if (-not $doc) { Start-Sleep -Milliseconds 500 } }
if (-not $doc) { Write-Output "ページ本体を取得できませんでした"; exit 1 }
$tc = New-Object System.Windows.Automation.PropertyCondition ([System.Windows.Automation.AutomationElement]::ControlTypeProperty), ([System.Windows.Automation.ControlType]::Text)
$texts = $doc.FindAll([System.Windows.Automation.TreeScope]::Descendants, $tc)
$out = New-Object System.Collections.Generic.List[string]
foreach ($t in $texts) { $n = $t.Current.Name; if ($n -and $n.Trim()) { $out.Add($n.Trim()) } }
$s = ($out -join "`n")
if ($s.Length -gt $Max) { $s = $s.Substring(0, $Max) + "`n…（以降省略）" }
$s
