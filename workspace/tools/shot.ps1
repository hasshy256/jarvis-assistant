# 全画面を撮影して PNG に保存する。出力: パス 左 上 幅 高さ
param([string]$Path = "$env:TEMP\jarvis_shot.png")
Add-Type -TypeDefinition 'using System.Runtime.InteropServices; public class Dpi { [DllImport("user32.dll")] public static extern bool SetProcessDPIAware(); }'
[void][Dpi]::SetProcessDPIAware()
Add-Type -AssemblyName System.Windows.Forms,System.Drawing
$b = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bmp = New-Object System.Drawing.Bitmap $b.Width,$b.Height
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($b.Left,$b.Top,0,0,$bmp.Size)
$bmp.Save($Path,[System.Drawing.Imaging.ImageFormat]::Png)
"$Path $($b.Left) $($b.Top) $($b.Width) $($b.Height)"
