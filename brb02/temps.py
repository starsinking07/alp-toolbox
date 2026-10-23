# -*- coding: utf-8 -*-
"""温度/功耗读取: pythonnet + LibreHardwareMonitorLib (与官方同源)。

笔记本双显卡策略: 有 NVIDIA/AMD 独显时优先独显, 否则取核显;
GPU 名字/温度/功耗都取选中的那块。

⚠️ 2026-10-22 (v0.1.16): LHM 0.9.4 → 0.9.6。
0.9.4 用 WinRing0 内核驱动读 AMD SMU, 而 WinRing0x64.sys 已被微软列入
"易受攻击驱动"黑名单, Win11 24H2+ 默认拦截 → CPU 温度/功耗恒为 0,
GPU 走 NVAPI 不受影响 (用户反馈"CPU 温度不显示"的真因)。
0.9.5 起 LHM 改用 PawnIO 驱动, 因此 **PawnIO 成为 CPU 温度的硬依赖**。
"""
from __future__ import annotations

import os
import sys
import threading
import time
from typing import Optional

LHM_DIR = ''   # 可选: 手动指定 LibreHardwareMonitorLib.dll 所在目录; 留空则按顺序自动搜索

# CPU 温度不可用的原因 (供 UI 给出可操作提示 / 诊断包定位)
REASON_OK = None
REASON_NO_ADMIN = 'no_admin'          # 未提权
REASON_NO_PAWNIO = 'no_pawnio'        # PawnIO 驱动未安装
REASON_DRIVER = 'driver'              # 驱动已装但读不到 (被拦截 / 未生效)
REASON_LHM = 'lhm_load'               # LHM 程序集加载失败


def _is_admin() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _pawnio_version() -> str | None:
    """PawnIO 已安装则返回版本号 (LHM 0.9.6 读 AMD SMU 必需)。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO') as k:
            return str(winreg.QueryValueEx(k, 'DisplayVersion')[0])
    except FileNotFoundError:
        return None
    except Exception:
        return None


def _default_lhm_dir() -> str:
    """定位 LibreHardwareMonitorLib.dll: 手动指定 → 打包资源 → 项目 LHM/ → 上一级目录。"""
    cands = []
    if LHM_DIR:
        cands.append(LHM_DIR)
    base = getattr(sys, '_MEIPASS', None)              # PyInstaller 解包目录
    if base:
        cands.append(os.path.join(base, 'LHM'))
    here = os.path.dirname(os.path.abspath(__file__))
    cands.append(os.path.join(os.path.dirname(here), 'LHM'))
    cands.append(os.path.dirname(here))
    for c in cands:
        if os.path.isfile(os.path.join(c, 'LibreHardwareMonitorLib.dll')):
            return c
    return here

_sensor_names_cpu = ('Tctl', 'Tdie', 'Core (Tctl', 'CPU Package', 'Core')
_sensor_names_gpu = ('GPU Core', 'GPU Temperature')


class TempReader:
    """后台线程更新温度/功耗, 属性线程安全读取。"""

    def __init__(self, interval: float = 1.0, lhm_dir: str | None = None):
        self.interval = interval
        self.cpu: float = 0.0
        self.gpu: float = 0.0
        self.gpu_hotspot: float | None = None   # NVIDIA 热点温度 (屏幕 ID06 疑似来源)
        self.cpu_name: str = ''
        self.gpu_name: str = ''
        self.cpu_power: float | None = None    # W
        self.gpu_power: float | None = None    # W
        self.cpu_ok = False
        # 诊断 (v0.1.16): CPU 温度读不到时给出确定原因, 不再用"非管理员或驱动未就绪"糊过去
        self.cpu_reason: str | None = REASON_NO_ADMIN
        self.is_admin = _is_admin()
        self.pawnio_version = _pawnio_version()
        self.lhm_version = ''
        self.lhm_error = ''
        # 负载体 (0.1.8 参数页): CPU/GPU 负载% / 内存占用% / 磁盘活动%
        self.cpu_load: float | None = None
        self.gpu_load: float | None = None
        self.ram_used: float | None = None
        self.disk_active: float | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._t: Optional[threading.Thread] = None
        self._lhm_dir = lhm_dir or _default_lhm_dir()

    def start(self):
        # 幂等: 重复 start 会起两个 LHM 读者 (双倍 CPU 占用 + 双份驱动句柄)
        if getattr(self, '_t', None) is not None and self._t.is_alive():
            return
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def stop(self):
        self._stop.set()

    def snapshot(self) -> tuple[float, float]:
        with self._lock:
            return self.cpu, self.gpu

    def _cpu_fail_reason(self) -> str:
        """CPU 温度读不到时, 判定到底卡在哪一层 (提权 / 驱动缺失 / 驱动被拦截)。

        每秒重判一次, 用户中途装好 PawnIO 或改用管理员启动都能自动恢复。
        """
        self.is_admin = _is_admin()
        if not self.is_admin:
            return REASON_NO_ADMIN
        self.pawnio_version = _pawnio_version()
        return REASON_DRIVER if self.pawnio_version else REASON_NO_PAWNIO

    def driver_info(self) -> dict:
        """驱动层状态 (诊断包用): 一眼看出是权限、驱动还是程序集的问题。"""
        with self._lock:
            return {
                'isAdmin': self.is_admin,
                'pawnioVersion': self.pawnio_version,
                'lhmVersion': self.lhm_version,
                'cpuOk': self.cpu_ok,
                'cpuReason': self.cpu_reason,
                'lhmError': self.lhm_error or None,
            }

    # ---- 内部 ----
    def _run(self):
        """LHM 初始化失败不再一次性退出: LOGBUF 记录后每 15s 重试,
        直到成功或 self._stop (审计 G4) —— 免管理员启动等场景稍后自愈。"""
        from .logbuf import LOGBUF
        while not self._stop.is_set():
            try:
                self._loop()
                return                       # _loop 仅在 _stop 置位后正常返回
            except Exception as e:
                with self._lock:
                    self.cpu_ok = False
                    self.cpu_reason = REASON_LHM
                    self.lhm_error = f'{type(e).__name__}: {e}'
                LOGBUF.write(f'[temps] LibreHardwareMonitor 初始化失败: {e} (15s 后重试)')
                if self._stop.wait(15.0):
                    return

    def _loop(self):
        from .logbuf import LOGBUF
        t_start = time.time()
        import clr
        try:
            os.add_dll_directory(self._lhm_dir)
        except Exception:
            pass
        if self._lhm_dir not in sys.path:
            sys.path.append(self._lhm_dir)
        clr.AddReference('LibreHardwareMonitorLib')
        from LibreHardwareMonitor import Hardware
        t_clr = time.time() - t_start
        try:                                  # 版本进诊断包, 便于确认驱动层能力
            from System.Reflection import Assembly
            self.lhm_version = str(Assembly.LoadFrom(
                os.path.join(self._lhm_dir, 'LibreHardwareMonitorLib.dll')
            ).GetName().Version)
        except Exception:
            self.lhm_version = '?'

        computer = Hardware.Computer()
        computer.IsCpuEnabled = True
        computer.IsGpuEnabled = True
        try:                                  # 参数页 (0.1.8): 内存占用 + 磁盘活动
            computer.IsMemoryEnabled = True
            computer.IsStorageEnabled = True
        except Exception:
            pass
        t_open = time.time()
        computer.Open()
        # 启动耗时可见化 (2026-10-23): 用户看到"--°C"时, 到底慢在 CLR 冷启动还是
        # Open 里的硬件枚举, 看这一行日志即可判断, 不必再靠截图猜。
        LOGBUF.write(f'[温度] LHM {self.lhm_version} 就绪: '
                     f'CLR {t_clr:.1f}s + Open {time.time() - t_open:.1f}s '
                     f'= 合计 {time.time() - t_start:.1f}s')

        while not self._stop.is_set():
            cpu = 0.0
            gpu = 0.0
            gpu_hs = None
            cpu_p = None
            gpu_p = None
            gpus = []          # [(name, temp, hotspot, power)]
            cpu_name = ''
            gpu_name = ''
            try:
                cpu_hits = {}
                for hw in computer.Hardware:
                    hw.Update()
                    ht = str(hw.HardwareType)
                    hw_name = str(hw.Name)
                    if 'Cpu' in ht:
                        if not cpu_name:
                            cpu_name = hw_name
                        self._scan_cpu(hw, cpu_hits)
                    if 'Gpu' in ht:
                        g_temp, g_hs, g_power = self._scan_gpu(hw)
                        gpus.append((hw_name, g_temp, g_hs, g_power))
                # CPU (Package 优先; 不再把所有传感器混起来取 max, 见 _pick_cpu)
                cpu = self._pick_cpu(cpu_hits)
                # GPU 选择: NVIDIA 优先, 其次 AMD, 再 Intel; 同厂商取温度高的
                def _prio(name: str) -> int:
                    n = name.lower()
                    if 'nvidia' in n or 'geforce' in n or 'rtx' in n or 'gtx' in n:
                        return 0
                    if 'radeon' in n or 'amd' in n:
                        return 1
                    return 2
                if gpus:
                    gpus.sort(key=lambda x: (_prio(x[0]), -(x[1] or -1)))
                    sel = gpus[0]
                    gpu = sel[1] or 0.0
                    gpu_name = sel[0]
                    gpu_hs = sel[2]
                    gpu_p = sel[3]
                else:
                    gpu_name = ''
                # CPU 功耗单独扫
                cpu_p = self._scan_cpu_power(computer)
                # 负载体扫描 (0.1.8): CPU/GPU 负载 + 内存 + 磁盘
                cpu_l, gpu_l, ram_u, disk_a = self._scan_extra(computer)
            except Exception:
                import traceback
                traceback.print_exc()
            with self._lock:
                self.cpu, self.gpu = cpu, gpu
                self.gpu_hotspot = gpu_hs
                self.cpu_ok = cpu > 0
                self.cpu_reason = REASON_OK if self.cpu_ok else self._cpu_fail_reason()
                if cpu_name:
                    self.cpu_name = cpu_name
                if gpu_name:
                    self.gpu_name = gpu_name
                self.cpu_load, self.gpu_load = cpu_l, gpu_l
                self.ram_used, self.disk_active = ram_u, disk_a
                self.cpu_power = cpu_p
                self.gpu_power = gpu_p
            self._stop.wait(self.interval)

    def _valid(self, v) -> bool:
        return v is not None and 1.0 < float(v) < 120.0

    def _scan_cpu(self, hw, cpu_out: dict):
        """收集 CPU 温度候选, **按类型分类** (由 `_pick_cpu` 决定用哪个)。

        ⚠️ 2026-10-18 用户反馈"CPU 温度一直偏高一些"(工具箱 64°C vs AIDA64 Package 51°C):
        旧版把所有匹配项 (Package / Tctl/Tdie / Core #N (SMU)) 混在一个列表里取 **max**,
        而 AMD 的 SMU 单核读数与 Tctl 可能带偏移 ⇒ 最大值比真实封装温度高 10°C+。
        """
        for s in hw.Sensors:
            if 'Temperature' not in str(s.SensorType) or not self._valid(s.Value):
                continue
            n = str(s.Name)
            v = float(s.Value)
            if 'Package' in n:
                cpu_out.setdefault('package', []).append(v)
            elif 'Tdie' in n:
                cpu_out.setdefault('tdie', []).append(v)
            elif 'Tctl' in n:
                cpu_out.setdefault('tctl', []).append(v)
            elif 'Core' in n:
                cpu_out.setdefault('core', []).append(v)
        for sub in hw.SubHardware:
            try:
                sub.Update()
            except Exception:
                pass
            self._scan_cpu(sub, cpu_out)

    @staticmethod
    def _pick_cpu(out: dict) -> float:
        """CPU 温度取值优先级: **Package > Tdie > Tctl > 各核中位数**。

        Package 是 AMD/Intel 都标准的封装温度, 与 AIDA64 / HWiNFO 的 "CPU Package" 口径一致。
        各核中位数作为兜底 (抗单核尖刺)。绝不把不同量纲的传感器混在一起取最大值。
        """
        for key in ('package', 'tdie', 'tctl'):
            vals = out.get(key) or []
            if vals:
                return max(vals)
        cores = sorted(out.get('core') or [])
        if cores:
            return cores[len(cores) // 2]
        return 0.0

    def _scan_gpu(self, hw) -> tuple[float | None, float | None, float | None]:
        """返回 (温度, 热点温度, 功耗W)"""
        temp = None
        hotspot = None
        power = None
        for s in hw.Sensors:
            st = str(s.SensorType)
            if st == 'Temperature' and self._valid(s.Value):
                n = str(s.Name)
                if 'GPU Core' in n or 'GPU Temperature' in n:
                    temp = float(s.Value)
                if 'Hot Spot' in n:
                    hotspot = float(s.Value)
            if st == 'Power':
                try:
                    v = float(s.Value)
                    if v > 0:
                        power = max(power or 0.0, v)
                except (TypeError, ValueError):
                    pass
        for sub in hw.SubHardware:
            try:
                sub.Update()
            except Exception:
                pass
            t2, h2, p2 = self._scan_gpu(sub)
            if temp is None:
                temp = t2
            if hotspot is None:
                hotspot = h2
            if power is None:
                power = p2
        return temp, hotspot, power

    def _scan_extra(self, computer):
        """负载体 (0.1.8 参数页): 返回 (CPU负载%, GPU负载%, 内存占用%, 磁盘活动%)。
        任一项读不到为 None。LHM SensorType: Load / Data。"""
        cpu_l = gpu_l = ram_u = disk_a = None
        for hw in computer.Hardware:
            ht = str(hw.HardwareType)
            if 'Cpu' in ht:
                for s in hw.Sensors:
                    if str(s.SensorType) == 'Load' and s.Value is not None:
                        n = str(s.Name).lower()
                        if n in ('cpu total', 'total', 'core #1 - total') or 'total' in n:
                            try:
                                cpu_l = float(s.Value)
                            except (TypeError, ValueError):
                                pass
            elif 'Gpu' in ht:
                for s in hw.Sensors:
                    if str(s.SensorType) == 'Load' and s.Value is not None:
                        n = str(s.Name).lower()
                        if 'core' in n or 'gpu total' in n or n == 'load':
                            try:
                                v = float(s.Value)
                                gpu_l = v if gpu_l is None else max(gpu_l, v)
                            except (TypeError, ValueError):
                                pass
            elif 'Storage' in ht:
                for s in hw.Sensors:
                    if str(s.SensorType) == 'Load' and s.Value is not None:
                        try:
                            v = float(s.Value)
                            disk_a = v if disk_a is None else max(disk_a, v)
                        except (TypeError, ValueError):
                            pass
        return cpu_l, gpu_l, ram_u, disk_a

    @staticmethod
    def _disk_percent() -> float | None:
        """系统盘空间占用% (ctypes GetDiskFreeSpaceEx, 对齐官方"磁盘占用率"语义)。"""
        import ctypes
        try:
            total = ctypes.c_ulonglong()
            free = ctypes.c_ulonglong()
            r = ctypes.windll.kernel32.GetDiskFreeSpaceExW(
                os.environ.get('SystemDrive', 'C:') + os.sep,
                None, ctypes.byref(total), ctypes.byref(free))
            if r and total.value > 0:
                return round((total.value - free.value) * 100.0 / total.value, 1)
        except Exception:
            pass
        return None

    @staticmethod
    def _ram_percent() -> float | None:
        """内存占用% (ctypes GlobalMemoryStatusEx, 零依赖)。"""
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong),
                        ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
                        ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
                        ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong),
                        ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]
        try:
            st = MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(st)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return float(st.dwMemoryLoad)
        except Exception:
            pass
        return None

    def load_snapshot(self) -> dict:
        """负载体快照 (0.1.8 参数页): {'cpu_load','gpu_load','ram','disk'}, 无读数为 None。
        内存走 GlobalMemoryStatusEx (零依赖); CPU/GPU 负载与磁盘活动走 LHM (需管理员)。"""
        with self._lock:
            return {'cpu_load': self.cpu_load, 'gpu_load': self.gpu_load,
                    'ram': self._ram_percent(), 'disk': self._disk_percent(),
                    'disk_active': self.disk_active}

    def _scan_cpu_power(self, computer) -> float | None:
        for hw in computer.Hardware:
            if 'Cpu' not in str(hw.HardwareType):
                continue
            # 主循环已对顶层硬件做过 Update(), 此处不再重复 (审计 G9)
            for s in hw.Sensors:
                if str(s.SensorType) == 'Power' and s.Value is not None:
                    n = str(s.Name).lower()
                    if 'package' in n:
                        return float(s.Value)
            for sub in hw.SubHardware:
                try:
                    sub.Update()
                except Exception:
                    pass
                for s in sub.Sensors:
                    if str(s.SensorType) == 'Power' and s.Value is not None:
                        return float(s.Value)
        return None


if __name__ == '__main__':
    r = TempReader(interval=0.8)
    r.start()
    for _ in range(8):
        time.sleep(1)
        print(f"CPU={r.cpu:.1f} ({r.cpu_name}) {r.cpu_power}W | "
              f"GPU={r.gpu:.1f} ({r.gpu_name}) {r.gpu_power}W")
    r.stop()
