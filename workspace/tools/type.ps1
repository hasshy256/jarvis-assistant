# 文字列を入力する。日本語も崩れないよう、クリップボード経由で貼り付ける（元のクリップボードは戻す）
param([Parameter(Mandatory)][string]$Text)
Add-Type -AssemblyName System.Windows.Forms
$old = Get-Clipboard -Raw -ErrorAction SilentlyContinue
Set-Clipboard -Value $Text
Start-Sleep -Milliseconds 100
[System.Windows.Forms.SendKeys]::SendWait('^v')
Start-Sleep -Milliseconds 200
if ($null -ne $old) { Set-Clipboard -Value $old }
"typed"
