# -*- coding: utf-8 -*-
"""性能浮窗 (类 NVIDIA 统计数据浮层) —— 桌面与无边框窗口化游戏实时 HUD。

设计要点 (对齐《需求文档-性能浮窗.md》):
  1. 窗口属性: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
     | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus +
     WA_TranslucentBackground + WA_ShowWithoutActivating;
  2. 绝不使用 setWindowOpacity (会导致文字一起变淡发虚): 背景不透明度 0..100
     在 paintEvent 中自绘圆角底 + 细描边; 文字自带 1px 深色轮廓描边与投影,
     即使在 0% 全透明背景 + 纯白画面下依然清晰可读;
  3. 数字防抖: 数值列固定使用 tabular-nums 等宽数字特性并右对齐;
  4. 性能红线: 1 Hz 低频定时器刷新, 复用现有 TempReader 与 DeviceWorker 内存快照,
     GUI 主线程零同步 I/O; 浮窗关闭时自动停止 PresentMon 子进程 (零残留)。
"""
from __future__ import annotations

import os
import sys
import time
from typing import Optional

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QGuiApplication,
                           QPainter, QPen)
from PySide6.QtWidgets import QWidget

from brb02.config import ALL_OVERLAY_ITEMS, DEFAULT_OVERLAY_ITEMS, Config
from brb02.fps_monitor import FpsMonitor

# 显示项元数据: (键名, 浮窗短标签, 设置页完整名称, 默认单位说明)
OVERLAY_ITEM_DEFS = [
    ('fps',       'FPS',      'FPS 实时帧率',   '桌面无呈现事件时显示 --'),
    ('cpu_load',  'CPU 占用', 'CPU 占用率',     '%'),
    ('cpu_temp',  'CPU 温度', 'CPU 温度',       '°C'),
    ('cpu_power', 'CPU 功耗', 'CPU 功耗',       'W'),
    ('gpu_load',  'GPU 占用', 'GPU 占用率',     '%'),
    ('gpu_temp',  'GPU 温度', 'GPU 温度',       '°C'),
    ('gpu_power', 'GPU 功耗', 'GPU 功耗',       'W'),
    ('ram',       '内存',     '系统内存',       '已用 / 总量 GB'),
    ('vram',      '显存',     '显卡显存',       '已用 / 总量 GB'),
    ('fan_rpm',   '风扇转速', '散热器转速',     'RPM (来自 BRB02)'),
]

PRESET_COORDS = {
    'top_left':     (0, 0),
    'top_right':    (100, 0),
    'bottom_left':  (0, 100),
    'bottom_right': (100, 100),
}

FONT_SIZE_MAP = {
    'small':  11,
    'normal': 13,
    'large':  16,
}

FONT_COLOR_MAP = {
    'white':     '#ffffff',
    'lightgray': '#d1d5db',
    'green':     '#76b900',   # NVIDIA 经典荧光绿
    'blue':      '#60a5fa',   # 冰川蓝
    'orange':    '#fb923c',   # 琥珀橙
}


def _make_font(px_size: int, bold: bool = False, mono: bool = False) -> QFont:
    f = QFont('Manrope')
    f.setPixelSize(max(9, int(px_size)))
    f.setWeight(QFont.Weight.Bold if bold else QFont.Weight.DemiBold)
    try:
        f.setFeature(QFont.Feature.TabularNums, 1)
    except Exception:
        pass
    return f


def _apply_win32_click_through(hwnd: int, click_through: bool) -> None:
    """在 Windows 下直接同步修改 HWND 扩展样式 (WS_EX_TRANSPARENT | WS_EX_NOACTIVATE |
    WS_EX_TOPMOST), 避免单纯切换 Qt WindowFlag 引起窗口闪烁或抢焦点。"""
    if sys.platform != 'win32' or not hwnd:
        return
    try:
        import ctypes
        from ctypes import wintypes
        u32 = ctypes.windll.user32
        GWL_EXSTYLE = -20
        WS_EX_TRANSPARENT = 0x00000020
        WS_EX_LAYERED = 0x00080000
        WS_EX_NOACTIVATE = 0x08000000
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_TOPMOST = 0x00000008
        if hasattr(u32, 'GetWindowLongPtrW'):
            u32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
            u32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
            ex = u32.GetWindowLongPtrW(wintypes.HWND(int(hwnd)), GWL_EXSTYLE)
        else:
            ex = u32.GetWindowLongW(int(hwnd), GWL_EXSTYLE)
        ex |= (WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST)
        if click_through:
            ex |= WS_EX_TRANSPARENT
        else:
            ex &= ~WS_EX_TRANSPARENT
        if hasattr(u32, 'SetWindowLongPtrW'):
            u32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
            u32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
            u32.SetWindowLongPtrW(wintypes.HWND(int(hwnd)), GWL_EXSTYLE, ex)
        else:
            u32.SetWindowLongW(int(hwnd), GWL_EXSTYLE, ex)
        _keep_win32_topmost_noactivate(hwnd)
    except Exception:
        pass


def _keep_win32_topmost_noactivate(hwnd: int) -> bool:
    """保持置顶且绝不激活窗口 (SWP_NOACTIVATE)。

    关键: 在 64 位 Windows 下必须显式声明 SetWindowPos 的 argtypes, 且将
    HWND_TOPMOST 强转为 wintypes.HWND(-1) = 0xFFFFFFFFFFFFFFFF;
    否则 Python ctypes 默认传 32 位 -1 = 0x00000000FFFFFFFF, 会被 Win32 内核
    当作非法 HWND 默默返回 0 (静默失败), 导致浮窗被前台全屏游戏抢顶压住。
    """
    if sys.platform != 'win32' or not hwnd:
        return False
    try:
        import ctypes
        from ctypes import wintypes
        u32 = ctypes.windll.user32
        u32.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.UINT
        ]
        u32.SetWindowPos.restype = wintypes.BOOL
        HWND_TOPMOST = wintypes.HWND(-1)
        SWP_NOSIZE = 0x0001
        SWP_NOMOVE = 0x0002
        SWP_NOACTIVATE = 0x0010
        SWP_SHOWWINDOW = 0x0040
        return bool(u32.SetWindowPos(
            wintypes.HWND(int(hwnd)), HWND_TOPMOST, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW
        ))
    except Exception:
        return False


FPS_HOLD_S = 1.5     # 采集空窗时保持上一次读数 (PresentMon 每 ~1s 才 flush 一批)


def _query_display_mode() -> tuple[int, int, int]:
    """查询主显示器当前模式 (宽, 高, 刷新率)。Windows 下独占全屏若切模式, 读数会变化。"""
    if sys.platform != 'win32':
        return (0, 0, 0)
    try:
        import ctypes
        from ctypes import wintypes
        class _DEVMODEW(ctypes.Structure):
            _fields_ = [
                ('dmDeviceName', ctypes.c_wchar * 32),
                ('dmSpecVersion', wintypes.WORD),
                ('dmDriverVersion', wintypes.WORD),
                ('dmSize', wintypes.WORD),
                ('dmDriverExtra', wintypes.WORD),
                ('dmFields', wintypes.DWORD),
                ('dmPositionX', wintypes.LONG),
                ('dmPositionY', wintypes.LONG),
                ('dmDisplayOrientation', wintypes.DWORD),
                ('dmDisplayFixedOutput', wintypes.DWORD),
                ('dmColor', wintypes.SHORT),
                ('dmDuplex', wintypes.SHORT),
                ('dmYResolution', wintypes.SHORT),
                ('dmTTOption', wintypes.SHORT),
                ('dmCollate', wintypes.SHORT),
                ('dmFormName', ctypes.c_wchar * 32),
                ('dmLogPixels', wintypes.WORD),
                ('dmBitsPerPel', wintypes.DWORD),
                ('dmPelsWidth', wintypes.DWORD),
                ('dmPelsHeight', wintypes.DWORD),
                ('dmDisplayFlags', wintypes.DWORD),
                ('dmDisplayFrequency', wintypes.DWORD),
                ('dmICMMethod', wintypes.DWORD),
                ('dmICMIntent', wintypes.DWORD),
                ('dmMediaType', wintypes.DWORD),
                ('dmDitherType', wintypes.DWORD),
                ('dmReserved1', wintypes.DWORD),
                ('dmReserved2', wintypes.DWORD),
                ('dmPanningWidth', wintypes.DWORD),
                ('dmPanningHeight', wintypes.DWORD),
            ]
        dm = _DEVMODEW()
        dm.dmSize = ctypes.sizeof(_DEVMODEW)
        if ctypes.windll.user32.EnumDisplaySettingsW(None, -1, ctypes.byref(dm)):
            return (int(dm.dmPelsWidth), int(dm.dmPelsHeight), int(dm.dmDisplayFrequency))
    except Exception:
        pass
    return (0, 0, 0)


class PerformanceOverlay(QWidget):
    """可自定义性能统计浮窗 (对齐 NVIDIA 统计数据浮层)。"""

    positionChanged = Signal(int, int, str)    # (x_pct, y_pct, preset)
    enabledChanged = Signal(bool)              # 开关状态变化
    metricsUpdated = Signal(dict)              # 每次 1Hz 采样后广播 (设置页状态行用)
    occlusionChanged = Signal(bool, str)       # (被遮挡, 遮挡窗口描述) —— 全屏排障用
    exclusiveFullscreenChanged = Signal(bool, str)  # (是否处于独占全屏, 描述) —— 独占感知与引导
    selfHealed = Signal(int)                   # 采集链自愈重启 (第 N 次)
    _fpsBatch = Signal()                       # CSV 批次到达 (tail 线程发 → 主线程刷新)

    SCREEN_MARGIN = 16

    def __init__(self, cfg: Config, worker=None, fps_monitor: Optional[FpsMonitor] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.cfg = cfg
        self.worker = worker
        self.fps_monitor = fps_monitor or FpsMonitor()
        # 2026-10-27 延迟优化: 批次一到就刷新, 不等下一个 150ms 定时器。
        # on_batch 由 tail 线程调用 → emit 信号 → Qt 自动排队到主线程执行刷新。
        self._fpsBatch.connect(self.refresh_metrics)
        self.fps_monitor.on_batch = self._fpsBatch.emit
        self._draggable = False
        self._drag_offset: Optional[QPoint] = None
        self._metrics: dict = {}
        self._rows: list[tuple[str, str]] = []   # [(label, value_str), ...]
        self._is_exclusive_fullscreen = False
        self._exclusive_desc = ''

        self.setObjectName('PerformanceOverlay')
        self.setWindowTitle('Alp 性能浮窗')
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, True)

        # 刷新节奏: 原为 1 Hz, 但实测 PresentMon 每 ~1.0s flush 一批 stdout,
# 两个 1 秒周期互拍相位 → 显示值最长滞后近 1 秒。降到 300ms 后, 数据一到
# 下一拍就能上屏 (滞后被压到 ≤0.3s), 而 refresh_metrics 全是内存读取,
# 4 Hz 的自绘开销对一个 380x108 的图层可以忽略。
        self._timer = QTimer(self)
        # 2026-10-27 延迟优化: 300ms → 150ms。与 fps_monitor 的 tail 轮询 (60ms) 配合,
        # 数据一到最多 150ms 就上屏; refresh_metrics 全是内存读取, 6.7Hz 开销可忽略。
        self._timer.setInterval(150)
        self._timer.timeout.connect(self.refresh_metrics)

        # 4 Hz 置顶保活: 游戏 (尤其无边框全屏) 常调用 SetForegroundWindow /
        # SetWindowPos 抢顶; 另外在**前台窗口切换的瞬间**立刻压一次, 不等下个 tick。
        # 该调用开销极小 (不重绘、不激活、不移动), 正是云音乐桌面歌词与 NVIDIA Overlay
        # 能稳定压在全屏游戏上的同款做法。
        self._top_timer = QTimer(self)
        self._top_timer.setInterval(250)
        self._top_timer.timeout.connect(self._assert_topmost)
        self._last_fg_hwnd = 0

        self._rebuild_rows()
        self._recalc_size()
        self.apply_position()

    # ---- 开关与生命周期 ----
    @property
    def is_draggable(self) -> bool:
        return self._draggable

    def set_draggable(self, draggable: bool) -> None:
        """切换『允许鼠标拖动定位』模式:
        draggable=False (默认): 开启鼠标穿透 (WindowTransparentForInput = True)
        draggable=True: 临时关闭鼠标穿透, 允许用户直接拖拽浮窗改变屏幕位置。"""
        draggable = bool(draggable)
        if self._draggable == draggable:
            return
        self._draggable = draggable
        was_vis = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, not draggable)
        self.setCursor(Qt.CursorShape.SizeAllCursor if draggable else Qt.CursorShape.ArrowCursor)
        if was_vis:
            self.show()
            _apply_win32_click_through(int(self.winId()), not draggable)
            _keep_win32_topmost_noactivate(int(self.winId()))
        self._recalc_size()
        self.apply_position()
        self.update()

    def set_overlay_enabled(self, on: bool, persist: bool = True) -> None:
        """开启或关闭性能浮窗 (同步启停 1Hz 定时器与 PresentMon 子进程)。"""
        on = bool(on)
        self.cfg.overlay_enabled = on
        if persist:
            self.cfg.save()
        if on:
            if 'fps' in (self.cfg.overlay_items or []) or self.cfg.overlay_game_only:
                self.fps_monitor.request_running(True)
            self.refresh_metrics()
            if not self._timer.isActive():
                self._timer.start()
            self._sync_visibility()
        else:
            self._timer.stop()
            self._top_timer.stop()
            self.fps_monitor.request_running(False)
            if self._draggable:
                self.set_draggable(False)
            self.hide()
        self.enabledChanged.emit(on)

    def shutdown(self) -> None:
        """应用退出时调用: 停定时器并彻底终止 PresentMon 子进程。"""
        self._timer.stop()
        self._top_timer.stop()
        self.fps_monitor.shutdown()
        self.hide()

    def _assert_topmost(self) -> None:
        """周期把浮窗压回最顶层 (不移动/不改尺寸/不激活), 对抗全屏游戏抢顶。

        前台窗口一变就立刻再压一次 —— 游戏切进全屏的那一刻是最容易被顶掉的时机。
        每约 1s 顺带做一次**遮挡探测**: 从 Z 序顶端往下找第一个盖住浮窗中心的窗口,
        若那不是自己, 说明浮窗被游戏/其全屏合成路径挡住了, 落日志供排障。
        """
        if not self.isVisible() or not self.cfg.overlay_enabled:
            return
        try:
            fg = ctypes.windll.user32.GetForegroundWindow() if sys.platform == 'win32' else 0
        except Exception:
            fg = 0
        hwnd = int(self.winId())
        if fg and fg == hwnd:          # 自己在前台 (拖拽模式) 时没必要重复压顶
            self._last_fg_hwnd = fg
            return
        changed = (fg != self._last_fg_hwnd)
        self._last_fg_hwnd = fg
        _keep_win32_topmost_noactivate(hwnd)
        if changed:
            # 前台切换瞬间再补一次 (游戏进入全屏后会立刻重排 Z 序)
            _apply_win32_click_through(hwnd, not self._draggable)
            _keep_win32_topmost_noactivate(hwnd)
        self._occl_tick = getattr(self, '_occl_tick', 0) + 1
        if self._occl_tick % 4 == 0:
            occluded, cover = self.probe_occlusion()
            if occluded:
                # 探测到被游戏窗口盖住: 立即连压置顶保活, 抢回顶层
                _keep_win32_topmost_noactivate(hwnd)
                self._top_burst = 3
                self._burst_topmost()
            if occluded != getattr(self, '_occluded', None):
                self._occluded = occluded
                self._occluded_by = cover
                self.occlusionChanged.emit(occluded, cover)
            is_excl, excl_desc = self._detect_exclusive_fullscreen()
            if is_excl != getattr(self, '_is_exclusive_fullscreen', False):
                self._is_exclusive_fullscreen = is_excl
                self._exclusive_desc = excl_desc
                self.exclusiveFullscreenChanged.emit(is_excl, excl_desc)

    def is_exclusive_fullscreen(self) -> bool:
        return bool(getattr(self, '_is_exclusive_fullscreen', False))

    def exclusive_desc(self) -> str:
        return str(getattr(self, '_exclusive_desc', ''))

    def _query_display_mode(self) -> tuple[int, int, int]:
        return _query_display_mode()

    def _detect_exclusive_fullscreen(self) -> tuple[bool, str]:
        """探测当前前台是否处于 DirectX 独占全屏 (Exclusive Fullscreen)。

        判据设计:
        1. shell32.SHQueryUserNotificationState 返回 3 (QUNS_RUNNING_D3D_FULL_SCREEN)
           这是 Windows 官方判定 Direct3D 独占全屏的权威 API;
        2. 结合前台窗口是否满屏覆盖且非系统/本程序窗口 (过滤 explorer/桌面);
        3. 返回 (is_exclusive, 描述)。
        """
        if sys.platform != 'win32':
            return (False, '')
        try:
            import ctypes
            from ctypes import wintypes
            # 1. 查询系统级 D3D 独占状态 (权威 API)
            state = ctypes.c_int(0)
            if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) == 0:
                if state.value == 3:  # QUNS_RUNNING_D3D_FULL_SCREEN
                    u32 = ctypes.windll.user32
                    fg = u32.GetForegroundWindow()
                    if fg:
                        ttl = ctypes.create_unicode_buffer(128)
                        u32.GetWindowTextW(fg, ttl, 128)
                        cls = ctypes.create_unicode_buffer(128)
                        u32.GetClassNameW(fg, cls, 128)
                        return (True, f'D3D独占全屏: {cls.value}|{ttl.value[:30]}')
                    return (True, 'D3D独占全屏')

            # 2. 备用判定: 前台窗口满屏几何覆盖且为 WS_POPUP (无边框/全屏) 且非桌面
            u32 = ctypes.windll.user32
            fg = u32.GetForegroundWindow()
            if not fg:
                return (False, '')
            cls = ctypes.create_unicode_buffer(128)
            u32.GetClassNameW(fg, cls, 128)
            cname = cls.value
            if cname in ('Progman', 'WorkerW', 'Shell_TrayWnd'):
                return (False, '')
            pid = wintypes.DWORD(0)
            u32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
            if pid.value == os.getpid():
                return (False, '')

            r = wintypes.RECT()
            if u32.GetWindowRect(fg, ctypes.byref(r)):
                scr = self.screen().geometry() if self.screen() else None
                if scr:
                    covers = (r.left <= 2 and r.top <= 2 and
                              r.right >= scr.width() - 2 and r.bottom >= scr.height() - 2)
                    style = u32.GetWindowLongW(fg, -16) & 0xffffffff
                    if covers and (style & 0x80000000) and not (style & 0x20000000):
                        ttl = ctypes.create_unicode_buffer(128)
                        u32.GetWindowTextW(fg, ttl, 128)
                        if getattr(self, '_occluded', False):
                            return (True, f'全屏覆盖: {cname}|{ttl.value[:30]}')
        except Exception:
            pass
        return (False, '')

    # ---- 遮挡探测 (全屏看不到浮窗时给出确定结论, 而不是靠猜) ----
    def probe_occlusion(self) -> tuple:
        """返回 (是否被遮挡, 遮挡窗口描述)。

        注意: 我们的窗口带 WS_EX_TRANSPARENT, `WindowFromPoint` 会**跳过**它,
        所以不能用命中测试; 这里沿 Z 序自上而下遍历顶层窗口, 找第一个
        可见、未最小化、且矩形覆盖浮窗中心的窗口 —— 只要不是自己就是被挡了。
        """
        if sys.platform != 'win32' or not self.isVisible():
            return (False, '')
        try:
            import ctypes
            from ctypes import wintypes
            u32 = ctypes.windll.user32
            hwnd = int(self.winId())
            g = self.frameGeometry()
            pt = wintypes.POINT(g.center().x(), g.center().y())
            own_pid = os.getpid()
            GW_HWNDNEXT = 2
            cur = u32.GetTopWindow(None)
            guard = 0
            while cur and guard < 400:
                guard += 1
                if cur == hwnd:
                    return (False, '')
                if u32.IsWindowVisible(cur) and not u32.IsIconic(cur):
                    # 跳过本进程自己的窗口: Qt 给每个 Tool 窗口另建了一个
                    # "QWindowToolSaveBits" 辅助窗口, 否则会被误判成遮挡者
                    wpid = wintypes.DWORD(0)
                    u32.GetWindowThreadProcessId(cur, ctypes.byref(wpid))
                    if wpid.value == own_pid:
                        cur = u32.GetWindow(cur, GW_HWNDNEXT)
                        continue
                    r = wintypes.RECT()
                    if u32.GetWindowRect(cur, ctypes.byref(r)):
                        if (r.left <= pt.x < r.right) and (r.top <= pt.y < r.bottom):
                            cls = ctypes.create_unicode_buffer(128)
                            u32.GetClassNameW(cur, cls, 128)
                            ttl = ctypes.create_unicode_buffer(128)
                            u32.GetWindowTextW(cur, ttl, 128)
                            return (True, f'{cls.value}|{ttl.value[:40]}')
                cur = u32.GetWindow(cur, GW_HWNDNEXT)
        except Exception:
            pass
        return (False, '')

    def _sync_visibility(self) -> None:
        if not self.cfg.overlay_enabled:
            if self.isVisible():
                self.hide()
            self._top_timer.stop()
            return
        # 若开启了「仅在游戏运行时显示」且当前未处于拖拽调整模式:
        if self.cfg.overlay_game_only and not self._draggable:
            overlay_targets = (getattr(self.cfg, 'overlay_game_processes', []) or [])
            has_game_fps = self.fps_monitor.snapshot(
                process_names=overlay_targets or None).get('fps') is not None
            if not has_game_fps:
                if self.isVisible():
                    self.hide()
                self._top_timer.stop()
                return
        if not self.isVisible():
            self.show()
            _apply_win32_click_through(int(self.winId()), not self._draggable)
        _keep_win32_topmost_noactivate(int(self.winId()))
        # 刚显示时连压 3 拍: 抢在游戏/全屏切换的重排之前落位
        if not getattr(self, '_top_burst', 0):
            self._top_burst = 3
            QTimer.singleShot(0, self._burst_topmost)
        if not self._top_timer.isActive():
            self._top_timer.start()

    def _burst_topmost(self) -> None:
        if self._top_burst <= 0 or not self.isVisible():
            self._top_burst = 0
            return
        self._top_burst -= 1
        _keep_win32_topmost_noactivate(int(self.winId()))
        QTimer.singleShot(120, self._burst_topmost)

    # ---- 数据采样 (复用现有 TempReader / DeviceWorker / FpsMonitor) ----
    def refresh_metrics(self) -> dict:
        """1 Hz 采样当前内存快照并重绘 (绝不在主线程做阻塞 I/O)。"""
        items = self.cfg.overlay_items or []
        if self.cfg.overlay_enabled and ('fps' in items or self.cfg.overlay_game_only):
            if not self.fps_monitor.is_running and self.fps_monitor.status not in ('no_admin', 'no_binary'):
                self.fps_monitor.request_running(True)
        elif not self.cfg.overlay_game_only and self.fps_monitor.is_running and 'fps' not in items:
            self.fps_monitor.request_running(False)

        # ---- 采集链自愈看门狗 ----
        # 「采一会就断, 再也起不来」的现场是线程还活着、但 raw_lines 不再增长
        # (PresentMon 卡在会话上/game 呈现源切换/分块轮次卡死)。
        # 无呈现帧也可能只是玩家退出了游戏。只有采集器明确报错且持续
        # 30s 无进展才重启; running + 无新行是正常空闲，不消耗重试次数。
        try:
            diag = self.fps_monitor.diagnose()
        except Exception:
            diag = {}
        raw_now = int(diag.get('raw_lines') or 0)
        t_now = time.monotonic()
        if raw_now != getattr(self, '_raw_seen', -1):
            self._raw_seen = raw_now
            self._raw_ts = t_now
            if raw_now > 0:
                self._ever_rows = True
        elif (diag.get('status') == 'error'
              and diag.get('last_error')
              and getattr(self, '_ever_rows', False)
              and t_now - getattr(self, '_raw_ts', t_now) > 30.0
              and getattr(self, '_selfheal_n', 0) < 5):
            self._selfheal_n = getattr(self, '_selfheal_n', 0) + 1
            self._raw_ts = t_now
            self.fps_monitor.request_running(True, restart=True)
            try:
                self.selfHealed.emit(self._selfheal_n)
            except Exception:
                pass

        # FPS 可单独按目标进程显隐数值，与整个浮窗的游戏显隐策略互不影响。
        fps_targets = (getattr(self.cfg, 'fps_game_processes', []) or [])
        if not getattr(self.cfg, 'fps_game_only', False):
            fps_targets = None
        fps_snap = self.fps_monitor.snapshot(process_names=fps_targets)
        held_app = getattr(self, '_fps_hold_app', '') or ''
        if fps_targets and held_app.strip().lower() not in {str(x).lower() for x in fps_targets}:
            self._fps_hold_val = None
            self._fps_hold_ts = 0.0
        fps_targets = {str(name).strip().lower() for name in
                       (getattr(self.cfg, 'fps_game_processes', []) or [])
                       if str(name).strip()} if getattr(self.cfg, 'fps_game_only', False) else set()
        if getattr(self.cfg, 'fps_game_only', False):
            self._fps_numeric_gate = fps_snap.get('fps') is not None
            if self._fps_numeric_gate:
                self._fps_hold_val = int(fps_snap['fps'])
                self._fps_hold_low = fps_snap.get('fps1low')
                self._fps_hold_app = fps_snap.get('app', '')
                self._fps_hold_ts = time.monotonic()
            else:
                self._fps_hold_val = None
                self._fps_hold_ts = 0.0
        else:
            self._fps_numeric_gate = True
        fps_val = fps_snap.get('fps')
        if fps_val:
            self._fps_hold_val = int(fps_val)
            self._fps_hold_low = fps_snap.get('fps1low')
            self._fps_hold_app = fps_snap.get('app', '')
            self._fps_hold_ts = time.monotonic()
        elif (getattr(self, '_fps_hold_val', None) is not None
              and time.monotonic() - getattr(self, '_fps_hold_ts', 0.0) <= FPS_HOLD_S):
            # 空窗不超过 1.5s: 沿用上次读数 (游戏过场/切图时不应忽然变成 --)。
            # ⚠️ fps1low / app 必须**一起**沿用 —— 真机发现过 fps 有值而这两列为空的
            # 行: 那是保持逻辑只补了 fps 本体, 另两个字段取自空快照, 于是 trace 里
            # 出现 "fps=250 1%low= app=" 的空洞。
            fps_snap = dict(fps_snap, fps=self._fps_hold_val,
                            fps1low=getattr(self, '_fps_hold_low', None),
                            app=getattr(self, '_fps_hold_app', '') or fps_snap.get('app', ''),
                            held=True)
        else:
            self._fps_hold_val = None
        cpu_t = gpu_t = 0.0
        cpu_p = gpu_p = None
        load_d = {}
        rpm = 0
        connected = False

        w = self.worker
        if w is not None:
            t_reader = getattr(w, 'temps', None)
            if t_reader is not None:
                try:
                    cpu_t, gpu_t = t_reader.snapshot()
                except Exception:
                    pass
                try:
                    load_d = t_reader.load_snapshot() or {}
                except Exception:
                    load_d = {}
                cpu_p = getattr(t_reader, 'cpu_power', None)
                gpu_p = getattr(t_reader, 'gpu_power', None)
            st = getattr(w, '_last_status', None) or {}
            rpm = int(st.get('rpm', 0) or 0)
            dev = getattr(w, 'device', None)
            # 优先读已缓存连接状态, 避免在 GUI 线程枚举 USB
            if hasattr(w, '_last_status_ts') and getattr(w, '_last_status_ts', 0) > 0:
                connected = True
            elif dev is not None:
                connected = bool(getattr(dev, '_t', None) is not None)

        self._metrics = {
            'fps': (fps_snap.get('fps') if self._fps_numeric_gate else None),
            'fps1low': fps_snap.get('fps1low'),
            'app': fps_snap.get('app', ''),
            'fps_app': fps_snap.get('app', ''),
            'fps_status': fps_snap.get('status', 'stopped'),
            'cpu_load': load_d.get('cpu_load'),
            'cpu_temp': cpu_t,
            'cpu_power': cpu_p,
            'gpu_load': load_d.get('gpu_load'),
            'gpu_temp': gpu_t,
            'gpu_power': gpu_p,
            'ram_pct': load_d.get('ram'),
            'ram_used_gb': load_d.get('ram_used_gb'),
            'ram_total_gb': load_d.get('ram_total_gb'),
            'vram_used_gb': load_d.get('vram_used_gb'),
            'vram_total_gb': load_d.get('vram_total_gb'),
            'fan_rpm': rpm if (connected or rpm > 0) else None,
        }
        self._rebuild_rows()
        self._recalc_size()
        self.apply_position()
        self._sync_visibility()
        self.update()
        out = dict(self._metrics)
        try:
            self.metricsUpdated.emit(out)
        except Exception:
            pass
        return out

    def set_metrics_for_test(self, metrics: dict) -> None:
        """供离线自动化测试直接注入确定性指标。"""
        self._metrics = dict(metrics)
        self._rebuild_rows()
        self._recalc_size()
        self.apply_position()
        self.update()

    def _format_item(self, key: str) -> str:
        m = self._metrics
        if key == 'fps':
            v = m.get('fps')
            return f'{int(v)}' if v is not None and v > 0 else '--'
        if key == 'cpu_load':
            v = m.get('cpu_load')
            return f'{float(v):.0f}%' if v is not None else '--'
        if key == 'cpu_temp':
            v = m.get('cpu_temp')
            return f'{float(v):.0f}°C' if v and float(v) > 0 else '--'
        if key == 'cpu_power':
            v = m.get('cpu_power')
            return f'{float(v):.0f} W' if v and float(v) > 0 else '--'
        if key == 'gpu_load':
            v = m.get('gpu_load')
            return f'{float(v):.0f}%' if v is not None else '--'
        if key == 'gpu_temp':
            v = m.get('gpu_temp')
            return f'{float(v):.0f}°C' if v and float(v) > 0 else '--'
        if key == 'gpu_power':
            v = m.get('gpu_power')
            return f'{float(v):.0f} W' if v and float(v) > 0 else '--'
        if key == 'ram':
            u, t = m.get('ram_used_gb'), m.get('ram_total_gb')
            if u is not None and t:
                return f'{float(u):.1f}/{float(t):.1f} GB'
            pct = m.get('ram_pct')
            return f'{float(pct):.0f}%' if pct is not None else '--'
        if key == 'vram':
            u, t = m.get('vram_used_gb'), m.get('vram_total_gb')
            if u is not None and t:
                return f'{float(u):.1f}/{float(t):.1f} GB'
            if u is not None:
                return f'{float(u):.1f} GB'
            return '--'
        if key == 'fan_rpm':
            v = m.get('fan_rpm')
            return f'{int(v)} RPM' if v is not None and int(v) > 0 else '--'
        return '--'

    def _rebuild_rows(self) -> None:
        enabled_keys = set(self.cfg.overlay_items or [])
        rows = []
        for key, short_lbl, _full, _unit in OVERLAY_ITEM_DEFS:
            if key in enabled_keys:
                rows.append((short_lbl, self._format_item(key)))
        if not rows:
            rows.append(('性能浮窗', '未勾选显示项'))
        self._rows = rows

    # ---- 尺寸与位置计算 ----
    def resolve_font_color(self) -> QColor:
        c_key = getattr(self.cfg, 'overlay_font_color', 'white')
        if c_key == 'custom':
            qc = QColor(getattr(self.cfg, 'overlay_custom_color', '#00ffcc'))
            return qc if qc.isValid() else QColor('#ffffff')
        return QColor(FONT_COLOR_MAP.get(c_key, '#ffffff'))

    def _metrics_geometry(self):
        sz_key = getattr(self.cfg, 'overlay_font_size', 'normal')
        px = FONT_SIZE_MAP.get(sz_key, 13)
        f_lbl = _make_font(px, bold=False, mono=False)
        f_val = _make_font(px, bold=True, mono=True)
        fm_lbl = QFontMetrics(f_lbl)
        fm_val = QFontMetrics(f_val)

        pad_x = int(round(px * 1.0))
        pad_y = int(round(px * 0.75))
        row_h = max(fm_lbl.height(), fm_val.height()) + int(round(px * 0.38))
        col_gap = int(round(px * 1.35))

        max_lbl_w = max((fm_lbl.horizontalAdvance(lbl) for lbl, _ in self._rows), default=40)
        # 预留等宽数值最小宽度, 防止 2 位数切 3 位数时外框宽度跳动
        min_val_w = fm_val.horizontalAdvance('88.8/88.8 GB')
        max_val_w = max(
            max((fm_val.horizontalAdvance(val) for _, val in self._rows), default=40),
            int(min_val_w * 0.68),
        )
        cell_w = max_lbl_w + col_gap + max_val_w
        header_h = (row_h + 2) if self._draggable else 0

        layout_mode = getattr(self.cfg, 'overlay_layout', 'linear')
        n = len(self._rows)
        if layout_mode == 'dual' and n > 1:
            n_rows = (n + 1) // 2
            w = pad_x * 2 + cell_w * 2 + col_gap
            h = pad_y * 2 + header_h + n_rows * row_h
        elif layout_mode == 'compact':
            item_widths = [
                fm_lbl.horizontalAdvance(lbl) + int(px * 0.5) + fm_val.horizontalAdvance(val)
                for lbl, val in self._rows
            ]
            w = pad_x * 2 + sum(item_widths) + max(0, n - 1) * col_gap
            h = pad_y * 2 + header_h + row_h
        else:
            w = pad_x * 2 + cell_w
            h = pad_y * 2 + header_h + n * row_h

        if self._draggable:
            w = max(w, fm_lbl.horizontalAdvance('✥ 拖拽移动浮窗') + pad_x * 2)
        return f_lbl, f_val, pad_x, pad_y, row_h, col_gap, max_lbl_w, max_val_w, cell_w, header_h, w, h

    def _recalc_size(self) -> None:
        *_, w, h = self._metrics_geometry()
        if self.size() != QSize(w, h):
            self.setFixedSize(w, h)

    def _screen_rect(self):
        scr = self.screen() or QGuiApplication.primaryScreen()
        if scr is not None:
            return scr.geometry()
        return QRectF(0, 0, 1920, 1080).toRect()

    def apply_position(self) -> None:
        """按 cfg.overlay_preset / overlay_x_pct / overlay_y_pct 将浮窗定位到屏幕对应坐标。"""
        preset = getattr(self.cfg, 'overlay_preset', 'top_right')
        if preset in PRESET_COORDS:
            x_pct, y_pct = PRESET_COORDS[preset]
            self.cfg.overlay_x_pct = x_pct
            self.cfg.overlay_y_pct = y_pct
        else:
            x_pct = max(0, min(100, int(getattr(self.cfg, 'overlay_x_pct', 100))))
            y_pct = max(0, min(100, int(getattr(self.cfg, 'overlay_y_pct', 0))))

        srect = self._screen_rect()
        m = self.SCREEN_MARGIN
        avail_w = max(0, srect.width() - 2 * m - self.width())
        avail_h = max(0, srect.height() - 2 * m - self.height())
        x = srect.x() + m + int(round(avail_w * (x_pct / 100.0)))
        y = srect.y() + m + int(round(avail_h * (y_pct / 100.0)))
        if self.pos() != QPoint(x, y):
            self.move(x, y)

    def reload_from_config(self) -> None:
        """设置页修改外观/显示项后立即同步浮窗。"""
        self._rebuild_rows()
        self._recalc_size()
        self.apply_position()
        if self.cfg.overlay_enabled:
            if 'fps' in (self.cfg.overlay_items or []) or self.cfg.overlay_game_only:
                self.fps_monitor.request_running(True)
            else:
                self.fps_monitor.request_running(False)
        self._sync_visibility()
        self.update()

    # ---- 鼠标拖拽定位 (仅在 draggable=True 时响应) ----
    def mousePressEvent(self, ev):
        if self._draggable and ev.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._draggable and self._drag_offset is not None and (ev.buttons() & Qt.MouseButton.LeftButton):
            srect = self._screen_rect()
            new_pos = ev.globalPosition().toPoint() - self._drag_offset
            x = max(srect.left(), min(srect.right() - self.width(), new_pos.x()))
            y = max(srect.top(), min(srect.bottom() - self.height(), new_pos.y()))
            self.move(x, y)
            ev.accept()
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if self._draggable and self._drag_offset is not None and ev.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = None
            srect = self._screen_rect()
            m = self.SCREEN_MARGIN
            avail_w = max(1, srect.width() - 2 * m - self.width())
            avail_h = max(1, srect.height() - 2 * m - self.height())
            x_pct = max(0, min(100, int(round((self.x() - srect.x() - m) * 100.0 / avail_w))))
            y_pct = max(0, min(100, int(round((self.y() - srect.y() - m) * 100.0 / avail_h))))
            self.cfg.overlay_x_pct = x_pct
            self.cfg.overlay_y_pct = y_pct
            self.cfg.overlay_preset = 'custom'
            self.cfg.save()
            self.positionChanged.emit(x_pct, y_pct, 'custom')
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    # ---- 自绘渲染 (高 DPI 抗锯齿 + 独立背景 Alpha + 文字描边阴影) ----
    @staticmethod
    def _draw_outlined_text(p: QPainter, rect: QRectF, flags: int,
                            text: str, fg_color: QColor, full_outline: bool = False) -> None:
        """绘制锐利文字:
        - 有深色背景底 (bg_opacity >= 30) 时采用 1px 清晰落影, 保持汉字与数字笔画干净利落;
        - 透明/低透明背景 (bg_opacity < 30) 时叠加四向 1px 深色描边 + 落影, 纯白画面下依然清晰可读;
        - 前景色强制 alpha=255 不透明, 防止底层黑边透出导致字芯发灰。"""
        fg = QColor(fg_color)
        fg.setAlpha(255)
        if full_outline:
            halo = QColor(0, 0, 0, 210)
            p.setPen(halo)
            for dx, dy in ((-1.0, 0.0), (1.0, 0.0), (0.0, -1.0), (0.0, 1.0), (1.0, 1.0)):
                p.drawText(rect.translated(dx, dy), flags, text)
        else:
            shadow = QColor(0, 0, 0, 185)
            p.setPen(shadow)
            p.drawText(rect.translated(1.0, 1.0), flags, text)
        p.setPen(fg)
        p.drawText(rect, flags, text)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        w, h = float(self.width()), float(self.height())
        bg_op = max(0, min(100, int(getattr(self.cfg, 'overlay_bg_opacity', 50))))
        eff_op = max(bg_op, 35) if self._draggable else bg_op
        full_outline = (eff_op < 30)

        # 1. 背景层: 仅通过 QColor alpha 自绘, 绝不用 setWindowOpacity
        if eff_op > 0:
            alpha = int(round(eff_op * 2.35))
            bg_c = QColor(11, 15, 22, alpha)
            p.setBrush(bg_c)
            if self._draggable:
                pen = QPen(QColor('#2f6df6'), 1.5, Qt.PenStyle.DashLine)
            else:
                border_alpha = min(90, max(24, alpha // 2))
                pen = QPen(QColor(255, 255, 255, border_alpha), 1.0)
            p.setPen(pen)
            p.drawRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0), 10.0, 10.0)

        # 2. 文字层
        (f_lbl, f_val, pad_x, pad_y, row_h, col_gap,
         max_lbl_w, max_val_w, cell_w, header_h, _w, _h) = self._metrics_geometry()

        val_color = self.resolve_font_color()
        val_color.setAlpha(255)
        lbl_color = QColor('#e2e8f0') if getattr(self.cfg, 'overlay_font_color', 'white') == 'green' else QColor(val_color)
        lbl_color.setAlpha(255)

        y0 = float(pad_y)
        if self._draggable:
            p.setFont(f_lbl)
            hdr_rect = QRectF(pad_x, y0, w - pad_x * 2, row_h)
            self._draw_outlined_text(
                p, hdr_rect,
                int(Qt.AlignmentFlag.AlignCenter),
                '✥ 拖拽移动浮窗', QColor('#60a5fa'), full_outline
            )
            y0 += header_h

        layout_mode = getattr(self.cfg, 'overlay_layout', 'linear')
        if layout_mode == 'compact':
            fm_lbl = QFontMetrics(f_lbl)
            fm_val = QFontMetrics(f_val)
            x_cur = float(pad_x)
            for lbl, val in self._rows:
                lw = float(fm_lbl.horizontalAdvance(lbl))
                vw = float(fm_val.horizontalAdvance(val))
                gap_in = float(max(4, int(f_lbl.pixelSize() * 0.45)))
                p.setFont(f_lbl)
                self._draw_outlined_text(
                    p, QRectF(x_cur, y0, lw + 2, row_h),
                    int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                    lbl, lbl_color, full_outline
                )
                p.setFont(f_val)
                self._draw_outlined_text(
                    p, QRectF(x_cur + lw + gap_in, y0, vw + 4, row_h),
                    int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                    val, val_color, full_outline
                )
                x_cur += lw + gap_in + vw + col_gap
        elif layout_mode == 'dual' and len(self._rows) > 1:
            for idx, (lbl, val) in enumerate(self._rows):
                r_idx = idx // 2
                c_idx = idx % 2
                cx = float(pad_x + c_idx * (cell_w + col_gap))
                cy = float(y0 + r_idx * row_h)
                p.setFont(f_lbl)
                self._draw_outlined_text(
                    p, QRectF(cx, cy, max_lbl_w + 4, row_h),
                    int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                    lbl, lbl_color, full_outline
                )
                p.setFont(f_val)
                self._draw_outlined_text(
                    p, QRectF(cx + max_lbl_w + col_gap, cy, max_val_w, row_h),
                    int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                    val, val_color, full_outline
                )
        else:
            # 线性单列 (默认): 左标签 + 右等宽数值
            inner_w = w - pad_x * 2
            for idx, (lbl, val) in enumerate(self._rows):
                cy = float(y0 + idx * row_h)
                p.setFont(f_lbl)
                self._draw_outlined_text(
                    p, QRectF(pad_x, cy, max_lbl_w + 4, row_h),
                    int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                    lbl, lbl_color, full_outline
                )
                p.setFont(f_val)
                self._draw_outlined_text(
                    p, QRectF(pad_x, cy, inner_w, row_h),
                    int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                    val, val_color, full_outline
                )
        p.end()
