# 特殊キーやショートカットを送る。SendKeys 形式: Enter は {ENTER}、Ctrl+L は ^l、Alt+F4 は %{F4}、Shift は +
param([Parameter(Mandatory)][string]$Keys)
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.SendKeys]::SendWait($Keys)
"sent $Keys"
