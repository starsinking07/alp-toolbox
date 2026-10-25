# -*- coding: utf-8 -*-
"""主窗口外壳: 悬浮圆角侧栏 + 自定义标题栏 + 页面切换 + 托盘 + 快捷键。"""
from __future__ import annotations

import os
from PySide6.QtCore import (QAbstractAnimation, QPoint, QPropertyAnimation,
                            QRect, QSize, Qt, QTimer, QVariantAnimation)
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMainWindow, QMenu, QPushButton,
    QSizePolicy, QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget,
)

from brb02.hotkeys import HK_CYCLE_GEAR, HK_TOGGLE_SMART, HotkeyManager
from brb02.logbuf import LOGBUF

from .theme import build_palette, build_qss, temp_color
from . import motion
from .widgets import Badge, FanIcon, Toggle, nav_icon, style_combo_popup
from .pages import (AboutPage, ButtonPage, ControlPage, CurvePage, DevicePage,
                    ScreenPage, StatusPage, MAX_RPM)

APP_NAME = 'Alp 工具箱'
DOCK_COLLAPSED = 64      # §2: w-16
DOCK_EXPANDED = 192     # §2: 192px

NAV = [('状态', 'status', 'status'), ('曲线', 'curve', 'curve'),
       ('按键', 'buttons', 'buttons'),
       ('屏幕', 'screen', 'screen'),
       ('设置', 'control', 'control'), ('设备', 'devices', 'devices')]
ABOUT_KEY = 'about'


def _make_app_icon(color='#2f6df6') -> QIcon:
    return nav_icon('control', color, 32)


def _style_refresh(w: QWidget):
    w.style().unpolish(w)
    w.style().polish(w)


class TitleBar(QWidget):
    def __init__(self, win: 'MainWindow'):
        super().__init__(win)
        self.win = win
        self.setFixedHeight(40)      # §2: h-10 (40px 自绘标题栏)
        self.setObjectName('TitleBar')
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 0, 6, 0)   # §2: px-3
        h.setSpacing(10)

        self.badge_conn = Badge('离线', icon_kind='wifi_off', dark=win.cfg.dark)
        self.badge_mode = Badge('手动模式', icon_kind='sparkles', dark=win.cfg.dark)
        self.badge_temp = Badge('--°C', icon_kind='thermometer', dark=win.cfg.dark)
        self.badge_rpm = Badge('-- RPM', icon_kind='fan', dark=win.cfg.dark)
        h.addWidget(self.badge_conn)
        h.addWidget(self.badge_mode)
        h.addWidget(self.badge_temp)
        h.addWidget(self.badge_rpm)
        h.addStretch(1)

        self.win_btns = []
        for obj, kind, cb in (('WinBtn', 'min', self._min), ('WinBtn', 'max', self._max),
                              ('WinBtnClose', 'close', self._close)):
            b = QPushButton('')
            b.setObjectName(obj)
            b.setFixedSize(40, 32)    # §2: h-8 w-10
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(cb)
            h.addWidget(b)
            self.win_btns.append((b, kind))
        self._btn_mode = getattr(win.cfg, 'titlebar_btn', 'thin')
        self._apply_win_btn_style()

    def set_btn_style(self, mode: str = 'thin'):
        """窗口按钮字形档位 (用户设置): thin 纤细 (默认) / normal 加粗放大 (矢量绘制)。"""
        self._btn_mode = mode if mode in ('thin', 'normal') else 'thin'
        self._apply_win_btn_style()

    def _apply_win_btn_style(self):
        from .theme import DARK, LIGHT
        from .widgets import draw_caption_glyph
        t = DARK if self.win.cfg.dark else LIGHT
        normal = self._btn_mode == 'normal'
        size = 17 if normal else 13
        pen_w = 2.6 if normal else 1.5
        for b, kind in self.win_btns:
            b.setIcon(QIcon(draw_caption_glyph(kind, size, t['muted_fg'], pen_w)))
            b.setIconSize(QSize(size, size))

    def _min(self):
        self.win.showMinimized()

    def _max(self):
        self.win.showNormal() if self.win.isMaximized() else self.win.showMaximized()

    def _close(self):
        # ✕ = 隐藏到托盘 (后台温控不停); 真退出走托盘菜单「退出」
        self.win.hide_to_tray()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.win.windowHandle().startSystemMove()

    def mouseDoubleClickEvent(self, ev):
        self._max()

    def on_status(self, st: dict, dark: bool):
        self.badge_rpm.set_state(f"{st.get('rpm', 0)} RPM", False, 'fan', dark)

    def on_conn(self, ok: bool):
        self.badge_conn.set_state('已连接' if ok else '离线', ok,
                                  'usb' if ok else 'wifi_off', self.win.cfg.dark)

    def on_mode(self, smart: bool):
        self.badge_mode.set_state('智能变频' if smart else '手动模式', smart,
                                  'sparkles', self.win.cfg.dark)

    def on_temps(self, cpu, gpu, dark: bool):
        t = max(cpu or 0, gpu or 0)
        self.win.last_temp = t
        self.badge_temp.set_state(f'{t:.0f}°C' if t else '--°C', False, 'thermometer', dark)

    def set_dark(self, dark: bool):
        for b in (self.badge_conn, self.badge_mode, self.badge_rpm):
            b.set_dark(dark)
        t = getattr(self.win, 'last_temp', 0) or 0
        self.badge_temp.set_dark(dark)
        self.badge_temp.text_lbl.setStyleSheet(
            f'background: transparent; font-size: 13px; font-weight: 700;'
            f' color: {temp_color(t, dark)};')
        self.badge_rpm.set_state(f'{self.win.last_rpm} RPM', False, 'fan', dark)
        self._apply_win_btn_style()   # 窗口按钮字形颜色随主题


class Sidebar(QWidget):
    """悬浮圆角面板: 图标导航, 激活项药丸高亮。"""

    ICON_KIND = {'status': 'status', 'curve': 'curve', 'control': 'control',
                 'buttons': 'buttons',
                 'screen': 'screen', 'devices': 'devices', ABOUT_KEY: 'about'}
    LABEL = {'status': '状态', 'curve': '曲线', 'control': '设置',
             'buttons': '按键',
             'screen': '屏幕', 'devices': '设备', ABOUT_KEY: '关于'}

    def __init__(self, win: 'MainWindow'):
        super().__init__(win)
        self.win = win
        self.setObjectName('Sidebar')
        self.setFixedWidth(DOCK_COLLAPSED)
        self.expanded = False
        self.dark = False
        self.setAttribute(Qt.WA_StyledBackground, True)   # QSS 圆角/背景生效
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 18, 10, 16)
        v.setSpacing(6)

        self.brand = QLabel('Alp')
        self.brand.setObjectName('Brand')
        self.brand.setAlignment(Qt.AlignCenter)
        v.addWidget(self.brand)
        v.addSpacing(18)

        # §2 滑块指示器: 独立绝对定位 pill, 置于按钮底层 (lower), mark() 时签名缓动滑到选中项
        self.indicator = QWidget(self)
        self.indicator.setObjectName('NavIndicator')
        self.indicator.setAttribute(Qt.WA_StyledBackground, True)
        self.indicator.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.indicator.hide()

        self.nav_btns = []
        for name, key, kind in NAV:
            b = self._make_btn(name, key, kind)
            v.addWidget(b)
            self.nav_btns.append(b)
        v.addStretch(1)
        self.nav_btns.append(self._make_btn('关于', ABOUT_KEY, 'about'))
        v.addWidget(self.nav_btns[-1])
        self.btn_toggle = QPushButton('»')
        self.btn_toggle.setObjectName('DockBtn')
        self.btn_toggle.setCursor(Qt.PointingHandCursor)
        self.btn_toggle.setFixedHeight(44)
        self.btn_toggle.clicked.connect(self.toggle)
        self._apply_collapsed_text()          # 收起态默认无文字 (防省略号残留)
        v.addWidget(self.btn_toggle)

    def _make_btn(self, name, key, kind) -> QPushButton:
        b = QPushButton(name)
        b.setObjectName('DockBtn')
        b.setCheckable(True)
        b.setCursor(Qt.PointingHandCursor)
        b.setIcon(nav_icon(kind, '#98a2b3', 21))   # 画布=显示尺寸, 杜绝 18→21 升采样糊
        b.setIconSize(QSize(21, 21))
        b.setToolTip(name)
        b._page_key = key
        b.clicked.connect(lambda _, k=key: self.win.switch_page(k))
        return b

    def _apply_collapsed_text(self):
        for b in self.nav_btns:
            name = self.LABEL[b._page_key]
            b.setText(('　' + name) if self.expanded else '')
            # QSS 按 [collapsed="true"] 切换居中/左对齐 (收起态必须居中, 否则图标偏心)
            b.setProperty('collapsed', 'false' if self.expanded else 'true')
            b.style().unpolish(b)
            b.style().polish(b)

    def apply_theme(self, dark: bool):
        self.dark = dark
        active = '#ff4b26' if dark else '#2f6df6'
        for b in self.nav_btns:
            kind = self.ICON_KIND[b._page_key]
            b.setIcon(nav_icon(kind, active if b.isChecked() else '#98a2b3', 21))

    def mark(self, key: str):
        for b in self.nav_btns:
            b.setChecked(b._page_key == key)
        self.apply_theme(self.dark)
        self._slide_indicator(key)

    def _target_rect(self, btn: QPushButton) -> QRect:
        if self.layout() is not None:
            self.layout().activate()
        pos = btn.mapTo(self, QPoint(0, 0))
        bh = btn.height() or 48
        # 按钮本身已由 Sidebar 布局左右留白 10px (pos.x()=10, 宽=self.width()-20),
        # 滑块与按钮同框对齐 (旧版多加了 +11 导致滑块整体右偏 11px)
        return QRect(pos.x(), pos.y(), max(24, self.width() - 20), bh)

    def _slide_indicator(self, key: str):
        """滑块滑到选中项 (§2: transform 签名缓动)。置于按钮下层 (lower), 不遮挡图标与文字。"""
        old_anim = getattr(self, '_ind_anim', None)
        if old_anim is not None:
            try:
                old_anim.stop()
            except RuntimeError:
                pass
            self._ind_anim = None

        btn = next((b for b in self.nav_btns if b._page_key == key), None)
        if btn is None:
            self.indicator.hide()
            return
        target = self._target_rect(btn)
        self.indicator.lower()                 # 始终在按钮下层, 绝不遮挡按钮图标/文字
        from . import motion
        ms = motion.duration(180)
        if not self.indicator.isVisible() or not self.isVisible() or ms == 0:
            self.indicator.setGeometry(target)
            self.indicator.show()
            self.indicator.lower()
            return
        start = QRect(self.indicator.geometry())
        start.setX(target.x())
        start.setWidth(target.width())
        start.setHeight(target.height())

        # 用 C++ QPropertyAnimation 直接驱动 geometry, 避免局部 Python 闭包被 GC 回收导致滑块不走
        anim = QPropertyAnimation(self.indicator, b'geometry', self)
        anim.setStartValue(start)
        anim.setEndValue(target)
        anim.setDuration(ms)
        anim.setEasingCurve(motion.ease(motion.EASE_SIGNATURE))
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        self._ind_anim = anim
        # 手册 §12: 选中项过冲 pop, 只弹新选中的那个
        from .widgets import draw_icon_pixmap
        active = '#ff4b26' if self.dark else '#2f6df6'
        motion.pop_icon(btn, draw_icon_pixmap(self.ICON_KIND[key], 21, active))

    def showEvent(self, ev):
        super().showEvent(ev)
        btn = next((b for b in self.nav_btns if b.isChecked()), None)
        if btn is not None and self.indicator.isVisible():
            self.indicator.setGeometry(self._target_rect(btn))
            self.indicator.lower()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        # 宽度跟随 (展开/收起动画期间持续校正滑块宽度与位置)
        anim = getattr(self, '_ind_anim', None)
        running = False
        if anim is not None:
            try:
                running = anim.state() == QAbstractAnimation.State.Running
            except RuntimeError:
                running = False
        btn = next((b for b in self.nav_btns if b.isChecked()), None)
        if not running and btn is not None and self.indicator.isVisible():
            self.indicator.setGeometry(self._target_rect(btn))
        else:
            g = self.indicator.geometry()
            self.indicator.setGeometry(10, g.y(), max(24, self.width() - 20), g.height())
        self.indicator.lower()

    def toggle(self):
        self.expanded = not self.expanded
        # 展开方向: 先出文字随宽度逐渐露出; 收起方向: 宽度收完再清文字/居中,
        # 避免动画中途图标跳位。200ms 签名缓动 (减少动态效果时 animate_width 自动瞬时)。
        self.btn_toggle.setText('« 收起' if self.expanded else '»')
        for b in self.nav_btns:
            name = self.LABEL[b._page_key]
            b.setToolTip('' if self.expanded else name)
        if self.expanded:
            self._apply_collapsed_text()
            motion.animate_width(self, DOCK_EXPANDED)
        else:
            motion.animate_width(
                self, DOCK_COLLAPSED,
                on_done=lambda: self._apply_collapsed_text())
        # 展开方向: 滑块 x 也随宽度走 (resizeEvent 已持续校正)


class MainWindow(QMainWindow):
    def __init__(self, cfg, worker):
        super().__init__()
        self.cfg = cfg
        self.worker = worker
        self.last_temp = 0.0
        self.last_rpm = 0
        self.is_connected = False
        self._curve_cache = None
        self._info_cache = {}
        self.setWindowTitle(APP_NAME)
        self.resize(1240, 820)
        self._cur_page_key = ''          # 当前页键 (页面切换算方向用)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        # 圆角方案定稿 (2026-10-25): 默认**不透明窗口 + Win11 DWM 原生圆角**
        # (系统合成零成本)。半透明分层窗口 (WA_TranslucentBackground) 在切换
        # 动画期间每帧全窗走 DWM 软件路径 —— 是"切入切出卡"的最后元凶,
        # perf_log 两轮数据 + 直角对照均指向它。ALP_ROUND=1 可切回旧半透明
        # 方案对照; ALP_SQUARE=1 纯直角。
        mode = os.environ.get('ALP_ROUND', '') == '1' and 'round' or             (os.environ.get('ALP_SQUARE', '') == '1' and 'square' or 'native')
        if mode == 'round':
            self.setAttribute(Qt.WA_TranslucentBackground)
        elif mode == 'square':
            self.setObjectName('AppShellSquare')
        else:
            self.setObjectName('AppShellSquare')
            QTimer.singleShot(0, self._apply_native_round)

        shell = QWidget()
        shell.setObjectName('AppShell')
        shell.setAttribute(Qt.WA_StyledBackground, True)   # 让 QSS 圆角生效
        root = QHBoxLayout(shell)
        root.setContentsMargins(12, 12, 16, 12)     # 侧栏悬浮留边
        root.setSpacing(14)

        self.sidebar = Sidebar(self)
        root.addWidget(self.sidebar)

        content = QWidget()
        content.setObjectName('ContentArea')
        cv = QVBoxLayout(content)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)
        self.titlebar = TitleBar(self)
        cv.addWidget(self.titlebar)
        self.stack = QStackedWidget()
        self.stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        cv.addWidget(self.stack, 1)
        root.addWidget(content, 1)
        self.setCentralWidget(shell)

        # 页面 + 滚动容器 (切换时用 wrapper 查找!)
        self.ctx = {'cfg': cfg, 'worker': worker, 'main': self, 'dark': cfg.dark,
                    'theme': self.theme_tokens()}
        self.pages = {
            'status': StatusPage(self.ctx),
            'curve': CurvePage(self.ctx),
            'screen': ScreenPage(self.ctx),
            'control': ControlPage(self.ctx),
            'devices': DevicePage(self.ctx),
            'buttons': ButtonPage(self.ctx),
            ABOUT_KEY: AboutPage(self.ctx),
        }
        self.wrappers = {}
        for key, page in self.pages.items():
            self.wrappers[key] = self._wrap_scroll(page)
            self.stack.addWidget(self.wrappers[key])
        self.switch_page('status')
        self.sync_mode_exclusivity()     # 启动即按互斥状态置灰各页的模式控件
        QTimer.singleShot(500, self._prewarm_pages)   # 启动空档预热各页, 首次进入不顿

        # 托盘 (原生 Win11 菜单: 信息行 + 温控曲线子菜单 + 智能变频)
        self.tray = QSystemTrayIcon(_make_app_icon())
        menu = QMenu()
        a_show = QAction('打开 Alp 工具箱', self)
        a_show.triggered.connect(self._show)
        menu.addAction(a_show)
        menu.addSeparator()

        # 信息行 (常亮显示, 点击无动作)
        self.tray_info = {}
        for key in ('device', 'cpu_t', 'gpu_t', 'cpu_p', 'gpu_p', 'rpm'):
            a = QAction('—', self)
            self.tray_info[key] = a
            menu.addAction(a)
        menu.addSeparator()

        self.menu_curve = QMenu('温控曲线', self)
        self.menu_curve.aboutToShow.connect(self._rebuild_curve_menu)
        menu.addMenu(self.menu_curve)

        a_curve = QAction('智能变频', self)
        a_curve.setCheckable(True)
        a_curve.setChecked(cfg.curve_enabled)
        a_curve.toggled.connect(lambda on: self._tray_mode('curve', on))
        menu.addAction(a_curve)

        # 控制方式三选一 (2026-10-11 用户要求): 与工具箱同步, 开一个另两个置灰
        a_tec = QAction('档位自动切换', self)
        a_tec.setCheckable(True)
        a_tec.setChecked(bool(cfg.tec_auto_enabled))
        a_tec.toggled.connect(lambda on: self._tray_mode('tec', on))
        menu.addAction(a_tec)

        a_follow = QAction('物理按键控制', self)
        a_follow.setCheckable(True)
        a_follow.setChecked(bool(cfg.button_follow_enabled))
        a_follow.toggled.connect(lambda on: self._tray_mode('follow', on))
        menu.addAction(a_follow)
        menu.addSeparator()

        # 流畅优先 (2026-10-25): perf_log 判读 —— 该机器对窗口光栅路径极慢,
        # 任何动画帧都 60-300ms。此开关一键把全部动效退化为瞬时 (motion._REDUCED
        # 即全部 duration()=0 的既有通道), 切换/交错/hover/仪表追赶全部零成本。
        a_fast = QAction('流畅优先 (关闭全部动效)', self)
        a_fast.setCheckable(True)
        a_fast.setChecked(getattr(motion, '_REDUCED', False) is True)
        a_fast.toggled.connect(self._set_fast_mode)
        menu.addAction(a_fast)
        menu.addSeparator()

        a_quit = QAction('退出 Alp 工具箱', self)
        a_quit.triggered.connect(self.real_quit)
        menu.addAction(a_quit)

        self.tray.setContextMenu(menu)
        self.tray.setToolTip(APP_NAME)
        self.tray.activated.connect(self._tray_activated)
        self._tray_curve_action = a_curve
        self._tray_tec_action = a_tec
        self._tray_follow_action = a_follow
        self.tray.show()

        self._tray_timer = QTimer(self)
        self._tray_timer.timeout.connect(self._update_tray_info)
        self._tray_timer.start(1000)
        # 关闭行为 (v3.24): ✕/Alt+F4 = 隐藏到托盘; 真退出只走托盘菜单
        self._really_quit = False
        self._tray_hint_shown = False

        # 快捷键
        self.hotkeys = None
        self._gear_idx = 1
        self.apply_hotkey_setting(cfg.hotkeys_enabled)

        # worker 信号
        worker.statusChanged.connect(self._on_status)
        worker.tempsChanged.connect(self._on_temps)
        worker.connectionChanged.connect(self._on_conn)
        worker.deviceGearChanged.connect(self._on_device_gear)
        worker.infoChanged.connect(self._on_info)

        self.apply_theme(cfg.dark)
        self.switch_page('status')

        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self._ui_heartbeat)
        self.ui_timer.start(3000)

    def _apply_native_round(self):
        """Win11 DWM 原生窗口圆角 (DWMWA_WINDOW_CORNER_PREFERENCE=ROUND)。
        系统级合成、零性能成本; Win10 不支持时静默保持直角。"""
        try:
            import ctypes
            hwnd = int(self.winId())
            pref = ctypes.c_int(2)              # DWMWCP_ROUND
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4)
        except Exception:
            pass

    # ---- 滚动容器 ----
    def _wrap_scroll(self, w: QWidget) -> QWidget:
        from PySide6.QtWidgets import QFrame, QScrollArea
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.NoFrame)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sa.setWidget(w)
        return sa

    def theme_tokens(self):
        from . import theme

        class T:
            DARK = theme.DARK

            def __init__(self, dark):
                self.dark = dark
                self.t = theme.DARK if dark else theme.LIGHT

            def __getattr__(self, k):
                return self.t[k]
        return T(self.cfg.dark)

    def apply_theme(self, dark: bool):
        self.cfg.dark = dark
        self.ctx['dark'] = dark
        self.ctx['theme'] = self.theme_tokens()
        for page in (self.pages['status'], self.pages['curve']):
            page.set_dark(dark)
        self.sidebar.apply_theme(dark)
        self.titlebar.set_dark(dark)
        self.titlebar.on_mode(self.cfg.curve_enabled)   # 徽章配色+文案随主题重置
        from PySide6.QtWidgets import QApplication, QComboBox
        QApplication.instance().setPalette(build_palette(dark))   # 裸 QLabel 文字兜底
        QApplication.instance().setStyleSheet(build_qss(dark))   # 运行时切主题: 整份 QSS 重建
        for combo in self.findChildren(QComboBox):
            style_combo_popup(combo, dark)
        for tg in self.findChildren(Toggle):
            tg.set_dark(dark)   # 自绘开关轨道色随主题 (浅色蓝 / 深色橙)

    def _prewarm_pages(self, _keys=None):
        """逐页预热 (2026-10-25 流畅度优化)。

        每个页面第一次显示都要现场做布局 + 首绘, 在慢一点的机器上就是"第一次点某个页
        顿一下"。这里在启动后的空档里, 每 60ms 让一页按栈实际尺寸离屏渲染一次, 把这份成本提前消化;
        分摊到各页而不是一次做完, 免得启动时反而卡一下。
        """
        keys = _keys if _keys is not None else [k for k in self.wrappers if k != self._cur_page_key]
        if not keys:
            return
        w = self.wrappers.get(keys[0])
        if w is not None and w is not self.stack.currentWidget():
            try:
                sz = self.stack.size()
                if sz.width() > 0 and sz.height() > 0:
                    w.resize(sz)
                w.grab()          # 离屏渲染一次 = 强制走完真实尺寸布局 + 字体/样式缓存预热
            except Exception:
                pass
        rest = keys[1:]
        if rest:
            QTimer.singleShot(60, lambda: self._prewarm_pages(rest))

    def switch_page(self, key: str):
        if key not in self.wrappers:
            return
        old = self.stack.currentWidget()
        new = self.wrappers[key]
        if old is new:
            self._cur_page_key = key      # 启动时首屏就是当前页, 也要记住, 否则后续方向恒为 0
            self.sidebar.mark(key)
            return
        # 侧栏高亮**立即**归一: 按钮是 checkable 的, 点下去它自己就先亮了,
        # 若等页面动画结束再 mark, 会出现"两个同时亮、过一会儿才归一"的错觉。
        self.sidebar.mark(key)
        # 方向感知切换 (复刻 FanControl 2.9.0 §2): 旧页 150ms 淡出上移 → 新页 180ms 淡入下落。
        # 方向按页面序号算; 程序内跳转 (启动/热键) 用 0 → 纯淡入, 不产生"来回窜"的错觉。
        keys = list(self.wrappers)
        try:
            d = 1 if keys.index(key) > keys.index(self._cur_page_key) else -1
        except (ValueError, AttributeError):
            d = 0
        self._cur_page_key = key

        # 卡片交错 (§3) 与整页淡入**并行**触发 —— 手册里 §2 的入场和 §3 的
        # data-page-reveal 是同时挂上的; 旧版把 stagger 排在过渡全部结束之后,
        # 80ms×N 张串行追加, 页面卡片多时整体拖过 1s, 观感就是"慢半拍"。
        def _enter():
            motion.stagger_reveal(new)          # §3: 55ms 步进 / 前 5 张 / 代际取消

        if os.environ.get('ALP_SLIDE') == '1':
            motion.page_transition(self.stack, old, new, direction=d, enter=_enter)
        else:
            motion.page_swap(self.stack, old, new, direction=d, enter=_enter)

    # ---- 快捷键 ----
    def _hotkey_join(self):
        """停掉旧热键线程并等它退出 (线程不退会一直占着 RegisterHotKey)。"""
        if self.hotkeys is None:
            return
        self.hotkeys.stop()
        try:
            self.hotkeys.join(1.0)
        except Exception:
            pass
        self.hotkeys = None

    def apply_hotkey_setting(self, on: bool):
        if on:
            # 重复开启: 先彻底停掉旧实例再新建, 防旧线程残留导致注册失败
            self._hotkey_join()
            self.hotkeys = HotkeyManager()
            self.hotkeys.triggered.connect(self._on_hotkey)
            self.hotkeys.start()
        elif not on and self.hotkeys is not None:
            self._hotkey_join()

    def _on_hotkey(self, hk_id: int):
        if hk_id == HK_CYCLE_GEAR:
            self._gear_idx = (self._gear_idx + 1) % 5
            presets = ['低噪', '平衡', '强效', '超频', '极限']
            rpm = self.cfg.presets.get(presets[self._gear_idx], 1600)
            self.worker.apply_fixed_rpm(rpm)
            self.worker.gear_light_hook(self._gear_idx)
            # 与曲线页 _gear_clicked 收尾一致: 挡位落盘 + GUI 同步 (防御式, 页面可能未就绪)
            self.cfg.fixed_rpm = rpm
            self.cfg.save()
            # 与曲线页 _gear_clicked 收尾一致: 滑条/挡位卡/状态页标签同步 (防御式)
            try:
                self.pages['curve'].slider12.set_level(self._gear_idx * 3 + 2)   # 满档点同步
                self.pages['curve'].gear_btns[self._gear_idx].setChecked(True)   # 挡位卡选中
            except Exception:
                pass
            try:
                self.pages['status'].refresh_mode_labels()
            except Exception:
                pass
            self.tray.showMessage('挡位切换', f'{presets[self._gear_idx]} · {rpm} RPM',
                                  QSystemTrayIcon.Information, 1500)
        elif hk_id == HK_TOGGLE_SMART:
            # 快捷键切智能变频也走互斥入口 (会自动关掉按键控制/档位自动切换)
            self.set_exclusive_mode('manual' if self.cfg.curve_enabled else 'curve')

    # ---- 托盘 ----
    def _show(self):
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
        self.raise_()
        self.activateWindow()

    def hide_to_tray(self):
        """✕ / Alt+F4: 隐藏到托盘, 后台温控不停 (真退出走托盘菜单)。首次给气泡提示。"""
        self.hide()
        if not self._tray_hint_shown:
            self._tray_hint_shown = True
            self.tray.showMessage('Alp 工具箱仍在运行',
                                  '散热器控制持续后台运行。点托盘图标打开窗口, '
                                  '退出请右键托盘菜单选「退出」。',
                                  QSystemTrayIcon.Information, 3000)

    def _set_fast_mode(self, on: bool):
        """托盘一键: 全动效瞬时 (含风扇图标静止)。慢光栅机器的逃生通道。"""
        from . import motion
        motion._REDUCED = bool(on)
        try:
            from .widgets import FanIcon
            for fi in self.findChildren(FanIcon):
                if on:
                    fi._timer.stop()
                fi._fast_mode = on
        except Exception:
            pass
        LOGBUF.write(f'[动效] 流畅优先 = {on}')

    def real_quit(self):
        """托盘「退出」: 优雅真退出 (走 closeEvent 停 worker/热键, 不留后台)。"""
        self._really_quit = True
        self.close()
        QApplication.quit()
        self.raise_()
        self.activateWindow()

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self._show()

    def _update_tray_info(self):
        """托盘信息行 1s 刷新: 设备状态 / 温度 / 功耗 / 转速"""
        t = self.worker.temps
        conn = bool(self.is_connected)
        rpm = self.last_rpm or 0

        def fmt(v, unit):
            return f'{v:.0f}{unit}' if v and v > 0 else '无数据'

        self.tray_info['device'].setText(f'风神 Pro: {"已连接" if conn else "未连接"}')
        self.tray_info['cpu_t'].setText(f'CPU 温度: {fmt(t.cpu, "°C")}')
        self.tray_info['gpu_t'].setText(f'GPU 温度: {fmt(t.gpu, "°C")}')
        self.tray_info['cpu_p'].setText(f'CPU 功耗: {fmt(t.cpu_power, " W")}')
        self.tray_info['gpu_p'].setText(f'GPU 功耗: {fmt(t.gpu_power, " W")}')
        self.tray_info['rpm'].setText(f'风扇转速: {rpm} RPM' if rpm else '风扇转速: 无数据')

    def _rebuild_curve_menu(self):
        """温控曲线子菜单: 每次展开时按当前方案重建"""
        m = self.menu_curve
        m.clear()
        active = self.cfg.curve_active
        for name in ['默认'] + sorted(self.cfg.curve_profiles.keys()):
            a = QAction(name, self)
            a.setCheckable(True)
            a.setChecked(name == active)
            a.triggered.connect(lambda _=False, n=name: self._switch_profile(n))
            m.addAction(a)

    def _switch_profile(self, name):
        """托盘切换曲线方案: 与曲线页同一入口, 编辑器/缓存自动同步"""
        try:
            self.pages['curve']._profile_switched(name)
        except Exception:
            pass

    def _tray_curve(self, on: bool):
        """同步托盘/标题栏/各页开关的智能变频显示 (**不改配置** —— 配置统一由
        set_exclusive_mode 管, 免得绕过互斥)。"""
        self.pages['status'].sync_smart(on)
        self.pages['curve'].sync_smart(on)
        self.pages['control'].sync_smart(on)
        self._tray_curve_action.setChecked(on)
        self.titlebar.on_mode(on)

    def _tray_mode(self, mode: str, on: bool):
        """托盘切换控制方式: 开启某个模式 (自动关掉其余); 取消则回到手动。"""
        self.set_exclusive_mode(mode if on else 'manual')

    def _sync_tray_mode_actions(self):
        """托盘三项与配置同步; 已激活的保持可点 (以便取消), 其余置灰 (用户要求)。"""
        cfg = self.cfg
        active = ('curve' if cfg.curve_enabled else
                  'tec' if cfg.tec_auto_enabled else
                  'follow' if cfg.button_follow_enabled else None)
        for key, act, val in (('curve', self._tray_curve_action, cfg.curve_enabled),
                              ('tec', self._tray_tec_action, cfg.tec_auto_enabled),
                              ('follow', self._tray_follow_action, cfg.button_follow_enabled)):
            act.blockSignals(True)
            act.setChecked(bool(val))
            act.blockSignals(False)
            act.setEnabled(active in (None, key))

    def sync_curve_toggle(self, on: bool):
        self._tray_curve(on)
        self.sync_mode_exclusivity()

    def set_exclusive_mode(self, mode: str):
        """把某个控制方式设为**唯一**生效, 其余自动关闭 (2026-10-11 用户要求)。

        三种控制方式互斥: 物理按键控制 (档位归设备) / 智能变频 (曲线定目标) /
        档位自动切换 (温度定档位)。"手动固定转速" = 三者都不开的兜底状态, 没有独立开关。
        """
        cfg = self.cfg
        if mode == 'follow':
            cfg.button_follow_enabled = True
            cfg.curve_enabled = False
            cfg.tec_auto_enabled = False
        elif mode == 'curve':
            cfg.curve_enabled = True
            cfg.button_follow_enabled = False
            cfg.tec_auto_enabled = False
        elif mode == 'tec':
            cfg.tec_auto_enabled = True
            cfg.button_follow_enabled = False
            cfg.curve_enabled = False
        elif mode == 'manual':
            cfg.curve_enabled = False
            cfg.button_follow_enabled = False
            cfg.tec_auto_enabled = False
        cfg.save()
        self.sync_mode_exclusivity()

    def sync_mode_exclusivity(self):
        """让每个页面按当前互斥状态刷新自己的控件 (置灰 + 说明原因), 托盘菜单一并同步。"""
        for key in ('status', 'curve', 'control', 'buttons'):
            pg = self.pages.get(key)
            fn = getattr(pg, 'sync_mode_state', None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass
        try:
            self._tray_curve(self.cfg.curve_enabled)      # 标题栏模式徽章 + 各页开关回填
            self._sync_tray_mode_actions()
        except Exception:
            pass

    def refresh_curve_cache(self):
        self._curve_cache = None

    def curve_pct(self):
        """曲线 → **19 点**百分比, 与曲线编辑器**同一重采样** (`CurveEditor.resample_pairs`)。

        ⚠️ 2026-10-08 修复: 旧实现直接 `[round(rpm/4800*100) for (t,rpm) in cfg.curve]` ——
        当 `cfg.curve` 少于 19 点 (默认曲线 / 曲线方案预设就是 4 个锚点) 时只返回 4 个值,
        状态页的 `set_curve` 只好用最后一个值补满 19 点 ⇒ **图形被压扁在前 4 个温度点
        (20/25/30/35°C) 之后一路拉平**, 与曲线页画的不是同一条曲线 (用户截图实测:
        4 锚点 `[[40,2000],[60,3000],[80,4000],[90,4500]]` → 状态页 `[42,62,83,94,94,…]`,
        曲线页 `[42,42,42,42,42,47,…,94]`)。改为复用编辑器的线性插值, 两处显示从此一致。
        """
        if self._curve_cache is None:
            from .curve_editor import CurveEditor      # 延迟导入, 避免与 pages 的导入环
            self._curve_cache = CurveEditor.resample_pairs(self.cfg.curve, MAX_RPM)
        return self._curve_cache

    def _ui_heartbeat(self):
        cpu, gpu = self.worker.temps.snapshot()
        self.tray.setToolTip(
            f'{APP_NAME} {"智能变频" if self.cfg.curve_enabled else "手动模式"}\n'
            f'风扇 {self.last_rpm} RPM\n'
            f'CPU {cpu:.0f}°C · GPU {gpu:.0f}°C')
        self.titlebar.on_mode(self.cfg.curve_enabled)   # 模式徽章以 cfg 为唯一数据源 (3s 心跳兜底)
        self.pages['curve'].tick_ui()
        self.pages['buttons'].tick_ui()      # 物理按键页: 刷新"当前设备档位"显示

    # ---- worker 信号 ----
    def _on_status(self, st: dict):
        self.last_rpm = st.get('rpm', 0)
        self.titlebar.on_status(st, self.cfg.dark)
        self.pages['status'].on_status(st)
        # 控制页「实时速度」也走每 tick 通道 (2026-10-08: 旧版只在 15s 的 on_info 刷, 明显滞后)
        self.pages['control'].on_status(st)

    def _on_temps(self, cpu, gpu):
        self.titlebar.on_temps(cpu, gpu, self.cfg.dark)
        self.pages['status'].on_temps(cpu, gpu)
        self.pages['control'].on_temps(cpu, gpu)

    def _on_device_gear(self, level: int, rpm: int):
        """物理按钮换档被工具箱接管 (worker 发起, 仅告知)。"""
        # 物理按键控制模式: 是"跟随"而不是"接管", 日志与提示文案要区分开 (否则误导用户)
        follow = bool(getattr(self.cfg, 'button_follow_enabled', False))
        if follow:
            LOGBUF.write(f'[按键] 已跟随物理按键: LV{level} · {rpm} RPM')
        else:
            LOGBUF.write(f'[档位] 物理按钮换档 (L{level} · {rpm} RPM) —— 已按当前模式接管')
        try:
            self.tray.showMessage(
                '挡位切换',
                f'物理按键 LV{level} · {rpm} RPM' + ('（工具箱跟随）' if follow else '（已接管）'),
                QSystemTrayIcon.Information, 1500)
        except Exception:
            pass
        self.tray.setToolTip(f'{APP_NAME} — L{level} · {rpm} RPM')
        try:
            self.pages['status'].refresh_mode_labels()   # 手动固定转速标签随 cfg 更新
        except Exception:
            pass
        try:
            self.pages['buttons']._update_level()        # 按键页的"当前设备档位"立即刷新
        except Exception:
            pass

    def _on_conn(self, ok, msg):
        import time as _t
        self.is_connected = ok
        if not ok:
            # 断线: 转速数据源失效, 复位显示并立即刷新托盘信息行
            self.last_rpm = 0
            self._update_tray_info()
        self.titlebar.on_conn(ok)
        # ⚠️ 连接态必须**立即**反映到两个页面 —— 放在日志节流**之前**
        # (旧版把 pages['status'].on_info 放在节流之后, 且完全不通知控制页 ⇒ 连接/断开后
        #  最长 15s 页面还显示旧状态)
        _info = self._info_cache if ok else {}
        self.pages['status'].on_info(_info)
        self.pages['control'].on_info(_info)
        # 相同消息节流: 5 秒内不重复写日志 (只节流日志, 不再影响界面刷新)
        if msg == getattr(self, '_last_conn_msg', None) and \
                _t.time() - getattr(self, '_last_conn_msg_ts', 0) < 5.0:
            return
        self._last_conn_msg = msg
        self._last_conn_msg_ts = _t.time()
        LOGBUF.write(f'[连接] {msg}')

    def _on_info(self, info: dict):
        self._info_cache = info
        self.pages['status'].on_info(info)
        self.pages['control'].on_info(info)

    def closeEvent(self, e):
        # 注销/关机: 系统会话保存阶段也发 close 事件, 藏托盘会被整体吞掉 → 强制真退出
        if not self._really_quit and QApplication.instance().isSavingSession():
            self._really_quit = True
        if not self._really_quit and e.spontaneous():
            e.ignore()                      # ✕ / Alt+F4: 隐藏到托盘, 不退出
            self.hide_to_tray()
            return
        self.worker.stop()
        self.worker.wait(2000)
        if self.worker.isRunning():
            # 屏幕上传整程 ~16s: 首段 2s 不够时再给硬超时, 避免留僵尸线程
            self.worker.wait(8000)
        if self.hotkeys:
            self.hotkeys.stop()
        e.accept()
