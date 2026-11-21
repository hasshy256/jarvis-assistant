# 画面の絶対座標 (X,Y) をクリックする。-Double でダブルクリック
param([int]$X,[int]$Y,[switch]$Double)
Add-Type -TypeDefinition @'
using System.Runtime.InteropServices;
public class Mouse {
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(int f, int dx, int dy, int d, int e);
}
'@
[void][Mouse]::SetProcessDPIAware()
[void][Mouse]::SetCursorPos($X,$Y)
Start-Sleep -Milliseconds 100
$n = if ($Double) { 2 } else { 1 }
for ($i=0; $i -lt $n; $i++) { [Mouse]::mouse_event(2,0,0,0,0); [Mouse]::mouse_event(4,0,0,0,0); Start-Sleep -Milliseconds 80 }
"clicked $X,$Y"
