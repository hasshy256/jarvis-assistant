# 座標 (X,Y) にマウスを置いてホイールを回す。-Amount は 正=上、負=下（1 = 1ノッチ）
param([int]$X,[int]$Y,[int]$Amount = -3)
Add-Type -TypeDefinition @'
using System.Runtime.InteropServices;
public class Wheel {
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(int f, int dx, int dy, int d, int e);
}
'@
[void][Wheel]::SetProcessDPIAware()
[void][Wheel]::SetCursorPos($X,$Y)
Start-Sleep -Milliseconds 100
$n = [Math]::Abs($Amount); $d = if ($Amount -lt 0) { -120 } else { 120 }
for ($i=0; $i -lt $n; $i++) { [Wheel]::mouse_event(0x0800,0,0,$d,0); Start-Sleep -Milliseconds 40 }
"scrolled $Amount at $X,$Y"
