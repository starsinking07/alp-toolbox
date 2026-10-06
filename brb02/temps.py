# -*- coding: utf-8 -*-
"""温度/功耗读取: pythonnet + LibreHardwareMonitorLib (与官方同源)。

笔记本双显卡策略: 有 NVIDIA/AMD 独显时优先独显, 否则取核显;
GPU 名字/温度/功耗都取选中的那块。
无管理员权限时 AMD CPU 温度可能为 0, 自动降级用 GPU 温度。
"""
from __future__ import annotations

import os
import sys
import threading
import time
from typing import Optional

LHM_DIR = ''   # 可选: 手动指定 LibreHardwareMonitorLib.dll 所在目录; 留空则按顺序自动搜索


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
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def stop(self):
        self._stop.set()

    def snapshot(self) -> tuple[float, float]:
        with self._lock:
            return self.cpu, self.gpu

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
                LOGBUF.write(f'[temps] LibreHardwareMonitor 初始化失败: {e} (15s 后重试)')
                if self._stop.wait(15.0):
                    return

    def _loop(self):
        import clr
        try:
            os.add_dll_directory(self._lhm_dir)
        except Exception:
            pass
        if self._lhm_dir not in sys.path:
            sys.path.append(self._lhm_dir)
        clr.AddReference('LibreHardwareMonitorLib')
        from LibreHardwareMonitor import Hardware

        computer = Hardware.Computer()
        computer.IsCpuEnabled = True
        computer.IsGpuEnabled = True
        try:                                  # 参数页 (0.1.8): 内存占用 + 磁盘活动
            computer.IsMemoryEnabled = True
            computer.IsStorageEnabled = True
        except Exception:
            pass
        computer.Open()

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
                cpu_hits = []
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
                # CPU
                if cpu_hits:
                    cpu = max(cpu_hits)
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

    def _scan_cpu(self, hw, cpu_out):
        for s in hw.Sensors:
            if 'Temperature' not in str(s.SensorType) or not self._valid(s.Value):
                continue
            n = str(s.Name)
            if 'Tctl' in n or 'Tdie' in n or 'Core' in n or 'Package' in n:
                cpu_out.append(float(s.Value))
        for sub in hw.SubHardware:
            try:
                sub.Update()
            except Exception:
                pass
            self._scan_cpu(sub, cpu_out)

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
                    'ram': self._ram_percent(), 'disk': self.disk_active}

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
