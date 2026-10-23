LibreHardwareMonitorLib.dll (v0.9.6) + 托管依赖
================================================

来源: LibreHardwareMonitor 项目
https://github.com/LibreHardwareMonitor/LibreHardwareMonitor

许可证: Mozilla Public License 2.0 (MPL-2.0)
https://mozilla.org/MPL/2.0/

均为未修改的原始发行文件, 用于读取 CPU/GPU 温度与功耗。
程序会自动在以下位置定位: 打包资源目录 / 本目录 (LHM/) / 程序根目录。
也可通过设置 brb02/temps.py 的 LHM_DIR 常量手动指定。

⚠️ 本目录下所有 dll 必须一起分发 —— 缺一个都会让 Computer.Open() 抛
   FileNotFoundException, 表现为 CPU/GPU 温度全部为 0。

文件清单
--------
LibreHardwareMonitorLib.dll               0.9.6 (net472 / win-x64) —— 本体
System.Memory.dll                         LHM 的托管依赖
System.Buffers.dll                        System.Memory 的依赖
System.Numerics.Vectors.dll               System.Memory 的依赖
System.Runtime.CompilerServices.Unsafe.dll
HidSharp.dll                              USB HID 设备枚举
DiskInfoToolkit.dll                       磁盘信息 (LHM 的 Storage 组)
BlackSharp.Core.dll                       DiskInfoToolkit 的依赖
RAMSPDToolkit-NDD.dll                     内存 SPD 读取 (LHM 的 Memory 组)

版本说明
--------
0.9.4 → 0.9.6 的原因: 0.9.4 读 AMD CPU 走 WinRing0 驱动, 而 WinRing0x64.sys
已被微软列入「易受攻击驱动」黑名单, Windows 11 24H2+ 默认拦截; 加载失败后
CPU 温度与功耗恒为 0 (GPU 走 NVAPI, 不经内核驱动, 所以不受影响)。

0.9.5 起上游改用 PawnIO 驱动 (https://pawnio.eu), 因此 **PawnIO 成为
CPU 温度的硬依赖** —— 0.9.6 不再有 WinRing0 回退。安装包会自动静默安装
PawnIO 2.2.0; 便携版可在应用内横幅一键安装。
