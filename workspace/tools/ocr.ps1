# 画面（または範囲）の文字を Windows の文字認識で読み、「x,y: 文字」の形で出す。座標はそのままクリックに使える画面座標
# 例: ocr.ps1                      （全画面）
#     ocr.ps1 -Left 0 -Top 0 -Width 1920 -Height 1080   （範囲指定）
param([int]$Left = [int]::MinValue, [int]$Top = 0, [int]$Width = 0, [int]$Height = 0, [string]$Lang = 'ja')
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -TypeDefinition 'using System.Runtime.InteropServices; public class DpiO { [DllImport("user32.dll")] public static extern bool SetProcessDPIAware(); }'
[void][DpiO]::SetProcessDPIAware()
Add-Type -AssemblyName System.Windows.Forms,System.Drawing,System.Runtime.WindowsRuntime
$v = [System.Windows.Forms.SystemInformation]::VirtualScreen
if ($Left -eq [int]::MinValue) { $Left = $v.Left; $Top = $v.Top; $Width = $v.Width; $Height = $v.Height }
$tmp = Join-Path $env:TEMP 'jarvis_ocr.png'
$bmp = New-Object System.Drawing.Bitmap $Width,$Height
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($Left,$Top,0,0,$bmp.Size)
$bmp.Save($tmp,[System.Drawing.Imaging.ImageFormat]::Png); $g.Dispose(); $bmp.Dispose()

[void][Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
[void][Windows.Graphics.Imaging.BitmapDecoder,Windows.Foundation,ContentType=WindowsRuntime]
[void][Windows.Storage.StorageFile,Windows.Foundation,ContentType=WindowsRuntime]
[void][Windows.Globalization.Language,Windows.Foundation,ContentType=WindowsRuntime]
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
function Await($op, $type) { $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); [void]$t.Wait(-1); $t.Result }

$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage((New-Object Windows.Globalization.Language $Lang))
if (-not $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
if (-not $engine) { Write-Output "OCRエンジンを作れませんでした（言語パックが必要かもしれません）"; exit 1 }
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($tmp)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$dec = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$sb = Await ($dec.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$res = Await ($engine.RecognizeAsync($sb)) ([Windows.Media.Ocr.OcrResult])
foreach ($line in $res.Lines) {
  $words = @($line.Words)
  if ($words.Count -eq 0) { continue }
  $rect = $words[0].BoundingRect
  $px = [int]([double]$rect.X + $Left); $py = [int]([double]$rect.Y + $Top)
  $text = ($words | ForEach-Object { $_.Text }) -join ' '
  $text = [regex]::Replace($text, '(?<=[^\x00-\x7F]) (?=[^\x00-\x7F])', '')
  "${px},${py}: $text"
}
