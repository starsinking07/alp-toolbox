# -*- coding: utf-8 -*-
"""主窗口外壳: 悬浮圆角侧栏 + 自定义标题栏 + 页面切换 + 托盘 + 快捷键。"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMainWindow, QMenu, QPushButton,
    QSizePolicy, QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget,
)

from brb02.hotkeys import HK_CYCLE_GEAR, HK_TOGGLE_SMART, HotkeyManager
from brb02.logbuf import LOGBUF

from .theme import build_palette, build_qss, temp_color
from .widgets import Badge, FanIcon, Toggle, nav_icon, style_combo_popup
from .pages import (AboutPage, ControlPage, CurvePage, DevicePage, ScreenPage,
                    StatusPage, MAX_RPM)

APP_NAME = 'Alp 工具箱'
DOCK_COLLAPSED = 76
DOCK_EXPANDED = 200

NAV = [('状态', 'status', 'status'), ('曲线', 'curve', 'curve'),
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
        self.setFixedHeight(52)
        self.setObjectName('TitleBar')
        h = QHBoxLayout(self)
        h.setContentsMargins(20, 0, 10, 0)
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
            b.setFixedSize(42, 32)
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
                 'screen': 'screen', 'devices': 'devices', ABOUT_KEY: 'about'}
    LABEL = {'status': '状态', 'curve': '曲线', 'control': '设置',
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
        b.setIcon(nav_icon(kind, '#98a2b3'))
        b.setIconSize(QSize(21, 21))
        b.setToolTip(name)
        b._page_key = key
        b.clicked.connect(lambda _, k=key: self.win.switch_page(k))
        return b

    def _apply_collapsed_text(self):
        for b in self.nav_btns:
            name = self.LABEL[b._page_key]
            b.setText(('　' + name) if self.expanded else '')

    def apply_theme(self, dark: bool):
        self.dark = dark
        active = '#ff4b26' if dark else '#2f6df6'
        for b in self.nav_btns:
            kind = self.ICON_KIND[b._page_key]
            b.setIcon(nav_icon(kind, active if b.isChecked() else '#98a2b3'))

    def mark(self, key: str):
        for b in self.nav_btns:
            b.setChecked(b._page_key == key)
        self.apply_theme(self.dark)

    def toggle(self):
        self.expanded = not self.expanded
        self.setFixedWidth(DOCK_EXPANDED if self.expanded else DOCK_COLLAPSED)
        self.brand.setText('Alp')
        self.btn_toggle.setText('« 收起' if self.expanded else '»')
        self._apply_collapsed_text()
        for b in self.nav_btns:
            name = self.LABEL[b._page_key]
            b.setToolTip('' if self.expanded else name)


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
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        # 无边框 + 半透明背景 => 窗口四角真正圆角
        self.setAttribute(Qt.WA_TranslucentBackground)

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
            ABOUT_KEY: AboutPage(self.ctx),
        }
        self.wrappers = {}
        for key, page in self.pages.items():
            self.wrappers[key] = self._wrap_scroll(page)
            self.stack.addWidget(self.wrappers[key])
        self.switch_page('status')

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
        a_curve.toggled.connect(self._tray_curve)
        menu.addAction(a_curve)
        menu.addSeparator()

        a_quit = QAction('退出 Alp 工具箱', self)
        a_quit.triggered.connect(self.real_quit)
        menu.addAction(a_quit)

        self.tray.setContextMenu(menu)
        self.tray.setToolTip(APP_NAME)
        self.tray.activated.connect(self._tray_activated)
        self._tray_curve_action = a_curve
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
        # 所有下拉弹出层: 圆角无边框 + 主题化配色
        from PySide6.QtWidgets import QComboBox
        for combo in self.findChildren(QComboBox):
            style_combo_popup(combo, cfg.dark)

        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self._ui_heartbeat)
        self.ui_timer.start(3000)

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

    def switch_page(self, key: str):
        if key in self.wrappers:
            self.stack.setCurrentWidget(self.wrappers[key])   # 修复: 用 wrapper 查找
            self.sidebar.mark(key)

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
            self._tray_curve(not self.cfg.curve_enabled)

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
        conn = self.worker.device.connected
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
        self.cfg.curve_enabled = on
        self.cfg.save()
        self.pages['status'].sync_smart(on)
        self.pages['curve'].sync_smart(on)
        self.pages['control'].sync_smart(on)
        self._tray_curve_action.setChecked(on)
        self.titlebar.on_mode(on)

    def sync_curve_toggle(self, on: bool):
        self._tray_curve(on)

    def refresh_curve_cache(self):
        self._curve_cache = None

    def curve_pct(self):
        if self._curve_cache is None:
            self._curve_cache = [min(100, round(c[1] / MAX_RPM * 100)) for c in self.cfg.curve]
        return self._curve_cache

    def _ui_heartbeat(self):
        cpu, gpu = self.worker.temps.snapshot()
        self.tray.setToolTip(
            f'{APP_NAME} {"智能变频" if self.cfg.curve_enabled else "手动模式"}\n'
            f'风扇 {self.last_rpm} RPM\n'
            f'CPU {cpu:.0f}°C · GPU {gpu:.0f}°C')
        self.titlebar.on_mode(self.cfg.curve_enabled)   # 模式徽章以 cfg 为唯一数据源 (3s 心跳兜底)
        self.pages['curve'].tick_ui()

    # ---- worker 信号 ----
    def _on_status(self, st: dict):
        self.last_rpm = st.get('rpm', 0)
        self.titlebar.on_status(st, self.cfg.dark)
        self.pages['status'].on_status(st)

    def _on_temps(self, cpu, gpu):
        self.titlebar.on_temps(cpu, gpu, self.cfg.dark)
        self.pages['status'].on_temps(cpu, gpu)
        self.pages['control'].on_temps(cpu, gpu)

    def _on_device_gear(self, level: int, rpm: int):
        """散热器按钮换档同步 / 退出智能变频档位恢复 (worker 发起)。"""
        # 无边框窗口没有状态栏: 换档事件走日志 + 托盘气泡 (防御式, 托盘可能不可用)
        LOGBUF.write(f'[换档] 已同步: FAN L{level} · {rpm} RPM')
        try:
            self.tray.showMessage('挡位切换', f'FAN L{level} · {rpm} RPM',
                                  QSystemTrayIcon.Information, 1500)
        except Exception:
            pass
        self.tray.setToolTip(f'{APP_NAME} — L{level} · {rpm} RPM')
        try:
            self.pages['status'].refresh_mode_labels()   # 手动固定转速标签随 cfg 更新
        except Exception:
            pass

    def _on_conn(self, ok, msg):
        import time as _t
        self.is_connected = ok
        if not ok:
            # 断线: 转速数据源失效, 复位显示并立即刷新托盘信息行
            self.last_rpm = 0
            self._update_tray_info()
        # 相同消息节流: 5 秒内不重复写日志
        if msg == getattr(self, '_last_conn_msg', None) and \
                _t.time() - getattr(self, '_last_conn_msg_ts', 0) < 5.0:
            self.titlebar.on_conn(ok)
            return
        self._last_conn_msg = msg
        self._last_conn_msg_ts = _t.time()
        LOGBUF.write(f'[连接] {msg}')
        self.titlebar.on_conn(ok)
        self.pages['status'].on_info(self._info_cache if ok else {})

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
