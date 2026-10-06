# -*- coding: utf-8 -*-
"""页面: 状态 / 曲线 / 设置(控制) / 设备 / 关于 — 对齐 FanControl 真机截图视觉。"""
from __future__ import annotations

import colorsys
import os
import subprocess
import sys
import tempfile
import time

from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap, QIcon
from PySide6.QtWidgets import QGraphicsOpacityEffect
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
    QProgressBar, QPushButton, QApplication,
    QDialog, QSlider, QSizePolicy, QSpinBox, QStackedWidget,
    QVBoxLayout, QWidget,
)

from .curve_editor import CurveEditor, TEMPS, DEFAULT_PCT
from brb02 import protocol
from brb02.diagnostics import APP_VERSION
from brb02.logbuf import LOGBUF
from .theme import chart_color, temp_color
from .widgets import FanIcon, SemiGauge, Toggle, make_card

GEARS = [
    ('静音', '#10b981'), ('标准', '#3b82f6'),
    ('强劲', '#a855f7'), ('超频', '#f97316'),
    ('极限', '#ef4444'),
]
MAX_RPM = 4800
GEAR_SUB = [0.70, 0.85, 1.0]


def _card(title=None, hint=None):
    return make_card(title, hint)


def _stat_tile(caption: str, sub: str | None = None):
    tile = QFrame()
    tile.setObjectName('StatTile')
    wl = QVBoxLayout(tile)
    wl.setContentsMargins(16, 12, 16, 12)
    wl.setSpacing(3)
    cap = QLabel(caption)
    cap.setObjectName('StatCap')
    val = QLabel('--')
    val.setObjectName('StatVal')
    wl.addWidget(cap)
    wl.addWidget(val)
    if sub is not None:
        s = QLabel(sub)
        s.setObjectName('StatCap')
        wl.addWidget(s)
    return tile, val


def _icon_box(kind: str) -> QFrame:
    """图标框: kind 支持 CPU/GPU/FAN 矢量图标或任意字符字形"""
    box = QFrame()
    box.setObjectName('IconBox')
    box.setFixedSize(40, 40)
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    if kind.lower() in ('cpu', 'gpu', 'fan', 'cooler'):
        from .widgets import draw_icon_pixmap
        color = '#66758a'
        ch = QLabel()
        ch.setAlignment(Qt.AlignCenter)
        ch.setStyleSheet('background: transparent;')
        pm = draw_icon_pixmap(kind.lower(), 20, '#66758a')
        ch.setPixmap(pm.scaled(20, 20, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        lay.addWidget(ch)
    else:
        ch = QLabel(kind)
        ch.setObjectName('IconChar')
        ch.setAlignment(Qt.AlignCenter)
        lay.addWidget(ch)
    return box


def _setting_row(title: str, desc: str = '', icon: str = '⚙', widget: QWidget | None = None):
    w = QFrame()
    w.setObjectName('SettingRow')
    h = QHBoxLayout(w)
    h.setContentsMargins(14, 12, 14, 12)
    h.setSpacing(12)
    h.addWidget(_icon_box(icon))
    col = QVBoxLayout()
    col.setSpacing(2)
    t = QLabel(title)
    t.setObjectName('RowTitle')
    col.addWidget(t)
    if desc:
        d = QLabel(desc)
        d.setObjectName('RowDesc')
        d.setWordWrap(True)
        col.addWidget(d)
    h.addLayout(col, 1)
    if widget is not None:
        widget.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        h.addWidget(widget, 0, Qt.AlignVCenter)
    return w


def _pill(text: str, color: str, bg_alpha=0.12) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f'color:{color}; background:rgba({int(color[1:3],16)},{int(color[3:5],16)},'
        f'{int(color[5:7],16)},{bg_alpha}); border-radius:11px; padding:3px 12px;')
    lbl.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    return lbl


def _dim_widget(w: QWidget, dim: bool, tip: str = ''):
    """依赖禁用的可视化: 透明度压暗 + tooltip 说明 (自定义 QSS 不渲染 disabled 态)。"""
    w.setEnabled(not dim)
    w.setToolTip(tip)
    if dim:
        eff = w.graphicsEffect()
        if not isinstance(eff, QGraphicsOpacityEffect):
            eff = QGraphicsOpacityEffect(w)
            w.setGraphicsEffect(eff)
        eff.setOpacity(0.45)
    elif w.graphicsEffect() is not None:
        w.setGraphicsEffect(None)


# ================= 状态页 =================
class StatusPage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.dark = ctx.get('dark', False)
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 4, 6, 6)
        v.setSpacing(16)

        # hero
        hero, _, hv = _card()
        row = QHBoxLayout()
        row.setSpacing(14)
        icon_wrap = QFrame()
        icon_wrap.setFixedSize(64, 64)
        self.icon_wrap = icon_wrap
        icon_wrap.setStyleSheet(
            f"background:{'#1a2029' if self.dark else '#e8eefc'}; border-radius:18px;")
        il = QVBoxLayout(icon_wrap)
        il.setContentsMargins(0, 0, 0, 0)
        self.fan_icon = FanIcon(36, '#ff4b26' if self.dark else '#2f6df6')
        il.addWidget(self.fan_icon, 0, Qt.AlignCenter)
        row.addWidget(icon_wrap, 0, Qt.AlignVCenter)

        col = QVBoxLayout()
        col.setSpacing(5)
        name_row = QHBoxLayout()
        name_row.setSpacing(10)
        self.lbl_name = QLabel('黑鲨风神Pro')
        self.lbl_name.setStyleSheet('font-size:18px; font-weight:800; background:transparent;')
        name_row.addWidget(self.lbl_name)
        self.lbl_conn = _pill('离线', '#ef4444')
        name_row.addWidget(self.lbl_conn)
        self.lbl_ready = QLabel('')
        name_row.addWidget(self.lbl_ready)
        name_row.addStretch(1)
        col.addLayout(name_row)
        self.lbl_mode = QLabel('手动模式')
        self.lbl_mode.setObjectName('CardHint')
        col.addWidget(self.lbl_mode)
        row.addLayout(col, 1)

        smart_wrap = QVBoxLayout()
        smart_wrap.setSpacing(4)
        cap = QLabel('智能变频')
        cap.setAlignment(Qt.AlignCenter)
        cap.setObjectName('StatCap')
        smart_wrap.addWidget(cap)
        theme = self.ctx['theme']
        self.tgl_smart = Toggle(ctx['cfg'].curve_enabled,
                                '#ff4b26' if self.dark else '#2f6df6')
        self.tgl_smart.toggled = self._smart_toggled
        smart_wrap.addWidget(self.tgl_smart, 0, Qt.AlignHCenter)
        row.addLayout(smart_wrap)

        col2 = QVBoxLayout()
        self.btn_disconnect = QPushButton('断开')
        self.btn_disconnect.setObjectName('Primary')
        self.btn_disconnect.setCursor(Qt.PointingHandCursor)
        self.btn_disconnect.clicked.connect(self._disconnect)
        col2.addWidget(self.btn_disconnect)
        row.addLayout(col2)
        hv.addLayout(row)
        v.addWidget(hero)

        # 三联仪表
        gauges = QFrame()
        gauges.setProperty('card', True)
        g = QGridLayout(gauges)
        g.setContentsMargins(18, 14, 18, 14)
        self.gauge_cpu = SemiGauge('CPU 温度', '°C', self.dark, icon_kind='cpu')
        self.gauge_gpu = SemiGauge('GPU 温度', '°C', self.dark, icon_kind='gpu')
        self.gauge_fan = SemiGauge('风扇转速', 'RPM', self.dark, icon_kind='fan')
        for i, gw in enumerate((self.gauge_cpu, self.gauge_gpu, self.gauge_fan)):
            g.addWidget(gw, 0, i)
        v.addWidget(gauges)

        # 控制与保护 (头部带型号胶囊)
        stats, _, sv = _card()
        stats_head = QHBoxLayout()
        gear_ic = QLabel('◔')
        gear_ic.setStyleSheet('font-size:15px; background:transparent;')
        stats_head.addWidget(gear_ic)
        st_title = QLabel('控制与保护')
        st_title.setObjectName('CardTitle')
        stats_head.addWidget(st_title)
        stats_head.addStretch(1)
        self.pill_cpu_model = _pill('未识别', '#2f6df6')
        self.pill_gpu_model = _pill('未识别', '#2f6df6')
        stats_head.addWidget(self.pill_cpu_model)
        stats_head.addWidget(self.pill_gpu_model)
        sv.addLayout(stats_head)
        grid = QGridLayout()
        grid.setSpacing(12)
        self.stat_lbls = {}
        stat_defs = (('控制模式', '✦'), ('温度状态', '◎'), ('工作模式', '❋'),
                     ('目标转速', '❊'), ('当前转速', '✳'))
        for i, (key, glyph) in enumerate(stat_defs):
            tile = QFrame()
            tile.setObjectName('StatTile')
            wl = QVBoxLayout(tile)
            wl.setContentsMargins(16, 12, 16, 12)
            wl.setSpacing(4)
            cap_row = QHBoxLayout()
            cap_row.setSpacing(6)
            g_ic = QLabel(glyph)
            g_ic.setStyleSheet('font-size:12px; color:#98a2b3; background:transparent;')
            cap_row.addWidget(g_ic)
            cap = QLabel(key)
            cap.setObjectName('StatCap')
            cap_row.addWidget(cap)
            cap_row.addStretch(1)
            wl.addLayout(cap_row)
            val = QLabel('--')
            val.setObjectName('StatVal')
            wl.addWidget(val)
            self.stat_lbls[key] = val
            grid.addWidget(tile, 0, i)
        sv.addLayout(grid)
        v.addWidget(stats)

        # 底部: 迷你曲线 (RPM 轴) + 迷你历史 两列
        bottom = QHBoxLayout()
        bottom.setSpacing(16)
        mini, _, mv = _card('风扇速度曲线')
        sub = QLabel('RPM')
        sub.setObjectName('CardHint')
        mv.addWidget(sub)
        self.mini_curve = CurveEditor(self.dark, rpm_axis=True)
        self.mini_curve.setMinimumHeight(200)
        mv.addWidget(self.mini_curve)
        bottom.addWidget(mini, 5)

        # 功耗统计卡 (CPU / GPU / 散热器风扇: 当前 + 平均)
        minh, _, mhv = _card('功耗统计')
        pgrid = QGridLayout()
        pgrid.setSpacing(12)
        self.pw_cur, self.pw_avg = {}, {}
        for i, (key, cap) in enumerate((('cpu', 'CPU 功耗'), ('gpu', 'GPU 功耗'),
                                        ('fan', '散热器风扇'))):
            tile = QFrame()
            tile.setObjectName('StatTile')
            tv = QVBoxLayout(tile)
            tv.setContentsMargins(14, 10, 14, 10)
            tv.setSpacing(3)
            c = QLabel(cap)
            c.setObjectName('StatCap')
            tv.addWidget(c)
            cur = QLabel('-- W')
            cur.setObjectName('StatVal')
            tv.addWidget(cur)
            avg = QLabel('平均 --')
            avg.setObjectName('StatCap')
            tv.addWidget(avg)
            pgrid.addWidget(tile, 0, i)
            self.pw_cur[key] = cur
            self.pw_avg[key] = avg
        mhv.addLayout(pgrid)
        mh_hint = QLabel('平均值为最近 30 分钟统计 · 散热器功耗为功耗计实测标定 (按转速插值)')
        mh_hint.setObjectName('CardHint')
        mhv.addWidget(mh_hint)
        bottom.addWidget(minh, 3)
        v.addLayout(bottom, 1)

    def set_dark(self, dark):
        self.dark = dark
        self.fan_icon.color = '#ff4b26' if dark else '#2f6df6'
        self.icon_wrap.setStyleSheet(
            f"background:{'#1a2029' if dark else '#e8eefc'}; border-radius:18px;")
        self.mini_curve.dark = dark          # 风扇速度曲线 (自绘, 颜色随 dark)
        for gw in (self.gauge_cpu, self.gauge_gpu, self.gauge_fan):
            gw.dark = dark

    def sync_smart(self, on: bool):
        self.tgl_smart.blockSignals(True)
        self.tgl_smart.setChecked(on)
        self.tgl_smart.blockSignals(False)
        self.refresh_mode_labels()

    def refresh_mode_labels(self):
        """模式相关标签以 cfg.curve_enabled 为唯一数据源, 开关切换时立即刷新"""
        smart = self.ctx['cfg'].curve_enabled
        if smart:
            self.lbl_mode.setText('智能变频 · 根据实时温度自动调节转速')
            self.stat_lbls['控制模式'].setText('智能变频')
            self.stat_lbls['工作模式'].setText('曲线目标')
            target = self.ctx['worker']._last_sent_rpm
            if target:
                self.stat_lbls['目标转速'].setText(f'{target} RPM')
        else:
            self.lbl_mode.setText(f'手动模式 · 当前固定 {self.ctx["cfg"].fixed_rpm} RPM')
            self.stat_lbls['控制模式'].setText('手动模式')
            self.stat_lbls['工作模式'].setText('固定转速')
            self.stat_lbls['目标转速'].setText(f'{self.ctx["cfg"].fixed_rpm} RPM')

    def _disconnect(self):
        if self.ctx['main'].is_connected:
            self.ctx['worker'].pause_connection()
            self.btn_disconnect.setText('连接')
        else:
            self.ctx['worker'].resume_connection()
            self.btn_disconnect.setText('断开')

    def _smart_toggled(self, on):
        self.ctx['cfg'].curve_enabled = on
        self.ctx['cfg'].save()
        self.ctx['main'].sync_curve_toggle(on)

    def on_status(self, st: dict):
        rpm = st.get('rpm', 0)
        self.fan_icon.set_rpm(rpm)
        pct = rpm / MAX_RPM * 100
        target = self.ctx['worker']._last_sent_rpm
        word = (f'目标 {target}RPM' if target is not None
                else ('过热' if pct > 90 else ('偏高' if pct > 75 else ('正常' if pct > 30 else '良好'))))
        self.gauge_fan.set_value(pct, str(rpm), word, '#ff4b26' if self.dark else '#2f6df6')
        self.stat_lbls['当前转速'].setText(f'{rpm} RPM')
        self.update_power(rpm)

    _POWER_CURVE = ((0, 1.5), (1200, 1.5), (2134, 3.0), (2800, 7.6), (2912, 7.6), (3534, 10.6), (4000, 12.5), (4800, 15.0))

    @staticmethod
    def _est_cooler_power(rpm: int) -> float:
        """散热器功耗估算 (W): 2026-10-02 功耗计实测标定 (蓝牙独立供电 15.1V, 无热负载)。
        实测点: 1200→1.5 / 2134→3.0 / 2800→7.6 / 2912→7.6 / 3534→10.6, 分段线性插值;
        4000→12.5 为外推值 (实测最高到 3534)。
        注: 功耗由转速决定 (档位 = 风扇性能预设, 设备无制冷片), 与档位无直接关系。"""
        pts = StatusPage._POWER_CURVE
        r = max(0, min(int(rpm), pts[-1][0]))
        for (r0, w0), (r1, w1) in zip(pts, pts[1:]):
            if r0 <= r <= r1:
                if r1 == r0:
                    return w0
                return round(w0 + (w1 - w0) * (r - r0) / (r1 - r0), 1)
        return pts[-1][1]

    def update_power(self, rpm: int):
        """功耗统计卡 (每秒随状态刷新)"""
        t = self.ctx['worker'].temps
        cpu_now = t.cpu_power
        gpu_now = t.gpu_power
        self.pw_cur['cpu'].setText(f'{cpu_now:.0f} W' if cpu_now and cpu_now > 0 else '- W')
        self.pw_cur['gpu'].setText(f'{gpu_now:.0f} W' if gpu_now and gpu_now > 0 else '- W')
        self.pw_cur['fan'].setText(f'{self._est_cooler_power(rpm)} W')
        # 平均 (最近 30 分钟历史)
        hist = list(self.ctx['worker'].history)
        window = time.time() - 1800
        cpu_ps = [cp for (tt, _c, _g, _r, cp, _gp) in hist if tt >= window and cp and cp > 0]
        gpu_ps = [gp for (tt, _c, _g, _r, _cp, gp) in hist if tt >= window and gp and gp > 0]
        fan_est = [self._est_cooler_power(r) for (tt, _c, _g, r, _cp, _gp) in hist if tt >= window]
        self.pw_avg['cpu'].setText(f'平均 {sum(cpu_ps)/len(cpu_ps):.0f} W' if cpu_ps else '平均 --')
        self.pw_avg['gpu'].setText(f'平均 {sum(gpu_ps)/len(gpu_ps):.0f} W' if gpu_ps else '平均 --')
        self.pw_avg['fan'].setText(f'平均 {sum(fan_est)/len(fan_est):.0f} W (估算)'
                                   if fan_est else '平均 --')

    def on_temps(self, cpu, gpu):
        for gauge, val in ((self.gauge_cpu, cpu), (self.gauge_gpu, gpu)):
            c = temp_color(val, self.dark)
            word = '过热' if val > 85 else ('偏高' if val > 75 else ('正常' if val else ''))
            gauge.set_value(val or 0, f'{val:.0f}' if val else '--', word, c)
        self.mini_curve.set_current_temp(cpu or gpu or None)
        t = max(cpu or 0, gpu or 0)
        self.stat_lbls['温度状态'].setText(
            '过热' if t > 85 else ('偏高' if t > 75 else ('正常' if t else '--')))
        names = self.ctx['worker'].temps
        if names.cpu_name:
            self.pill_cpu_model.setText(names.cpu_name)
        if names.gpu_name:
            self.pill_gpu_model.setText(names.gpu_name)

    def on_info(self, info: dict):
        conn = self.ctx['main'].is_connected
        theme = self.ctx['theme']
        # 断开/连接按钮文案跟随真实连接态 (连接状态变化都会走到这里), 防与 _paused 脱钩
        self.btn_disconnect.setText('断开' if conn else '连接')
        self.lbl_conn.setText('已连接' if conn else '离线')
        self.lbl_conn.setStyleSheet(
            f'font-size:12px; padding:3px 12px; border-radius:11px;'
            + (f'color:{theme.primary}; background:{theme.chip_bg};'
               if conn else 'color:#ef4444; background:rgba(239,68,68,0.10);'))
        smart = self.ctx['cfg'].curve_enabled
        c = info.get('cooling')
        if smart:
            self.lbl_mode.setText('智能变频 · 根据实时温度自动调节转速')
        elif c:
            rpm_now = c.get('rpm')
            if not rpm_now:                      # 0x25 全量形态无 rpm / 异常护栏 → 显示配置值
                rpm_now = self.ctx['cfg'].fixed_rpm
            self.lbl_mode.setText(f'手动模式 · 当前固定 {rpm_now} RPM')
        self.stat_lbls['控制模式'].setText('智能变频' if smart else '手动模式')
        if c:
            rpm_now = c.get('rpm') or self.ctx['cfg'].fixed_rpm
            self.stat_lbls['工作模式'].setText('曲线目标' if smart else '固定转速')
            self.stat_lbls['目标转速'].setText(f'{rpm_now} RPM')
        self.mini_curve.set_curve(self.ctx['main'].curve_pct())


# ================= 曲线页 =================
class GearSlider(QWidget):
    levelSelected = None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.level = 4
        self.n = len(GEARS) * 3
        self.setMinimumHeight(72)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        y = h / 2 - 10
        track_l, track_r = 30, w - 30
        p.setPen(QPen(QColor('#c8ced9'), 5, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(int(track_l), int(y), int(track_r), int(y))
        f = QFont()
        f.setPointSize(8)
        p.setFont(f)
        n = self.n
        for k in range(n):
            x = track_l + (track_r - track_l) * k / (n - 1)
            color = GEARS[k // 3][1]
            r = 10 if k == self.level else 7
            p.setBrush(QColor(color))
            p.setPen(QPen(QColor('white'), 2))
            from PySide6.QtCore import QPointF
            p.drawEllipse(QPointF(x, y), r, r)
            p.setPen(QColor('#98a2b3'))
            from PySide6.QtCore import QRectF
            p.drawText(QRectF(x - 18, y + 16, 36, 14), Qt.AlignCenter, f'{k + 1}档')

    def mousePressEvent(self, ev):
        w = self.width()
        track_l, track_r = 30, w - 30
        k = round((ev.position().x() - track_l) / (track_r - track_l) * (self.n - 1))
        self.level = max(0, min(self.n - 1, k))
        self.update()
        if self.levelSelected:
            self.levelSelected(self.level)

    def set_level(self, k: int):
        """程序化设置选中点 (挡位卡 ↔ 滑条双向同步用)。"""
        self.level = max(0, min(self.n - 1, int(k)))
        self.update()


class SeriesPill(QPushButton):
    def __init__(self, label: str, color: str):
        super().__init__(f'● {label}')
        self.setObjectName('SeriesPill')
        self.color = color
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self._apply()
        self.toggled.connect(lambda _on: self._apply())

    def _apply(self):
        on = self.isChecked()
        rgb = f"{int(self.color[1:3],16)},{int(self.color[3:5],16)},{int(self.color[5:7],16)}"
        bg = f'rgba({rgb},0.14)' if on else 'transparent'
        self.setStyleSheet(
            f'QPushButton {{ border:none; border-radius:13px; padding:5px 14px; font-size:12px;'
            f' color:{self.color if on else "#98a2b3"};'
            f' background:{bg}; }}')


class HistoryChart(QWidget):
    """趋势图 + 系列胶囊 + 摘要瓦片 (功能 #3)。"""

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.dark = False
        self.show = {'cpu': True, 'gpu': True, 'fan': True}
        self.setMinimumHeight(260)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_dark(self, dark):
        self.dark = dark

    def paintEvent(self, ev):
        hist = list(self.ctx['worker'].history)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        from PySide6.QtCore import QPointF, QRectF
        import datetime as _dt
        grid = QColor(chart_color('grid', self.dark))
        tick = QColor(chart_color('tick', self.dark))
        pad_l, pad_r, pad_t, pad_b = 56, 52, 12, 26
        plot_w, plot_h = w - pad_l - pad_r, h - pad_t - pad_b

        if len(hist) >= 2:
            t_end = hist[-1][0]
            window = min(1800.0, max(t_end - hist[0][0], 60.0))
            t_start = t_end - window
        else:
            t_end, t_start, window = time.time(), time.time() - 1800.0, 1800.0

        def x_of(t):
            return pad_l + plot_w * (t - t_start) / window

        def y_rpm(r):
            return pad_t + plot_h * (1 - max(0.0, min(1.0, r / MAX_RPM)))

        def y_temp(v):
            return pad_t + plot_h * (1 - max(0.0, min(1.0, (v - 20) / 90.0)))

        f = QFont(); f.setPointSize(8); p.setFont(f)
        for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
            y = pad_t + plot_h * frac
            p.setPen(QPen(grid, 1))
            p.drawLine(int(pad_l), int(y), int(w - pad_r), int(y))
        # RPM 纵轴: 数据映射 y_rpm 高转速在顶部, 标签必须 4800(顶) → 0(底) (v3.56 修正颠倒)
        for frac, lbl in ((0.0, '4800'), (0.25, '3600'), (0.5, '2400'), (0.75, '1200'), (1.0, '0')):
            y = pad_t + plot_h * frac
            p.setPen(tick)
            p.drawText(QRectF(0, y - 8, pad_l - 8, 16), Qt.AlignRight | Qt.AlignVCenter, lbl + 'RPM')
        for frac, lbl in ((0.0, '110'), (0.25, '88'), (0.5, '65'), (0.75, '43'), (1.0, '20')):
            y = pad_t + plot_h * frac
            p.setPen(tick)
            p.drawText(QRectF(w - pad_r + 6, y - 8, pad_r - 8, 16), Qt.AlignLeft | Qt.AlignVCenter, lbl + '°C')
        for i in range(5):
            t = t_start + window * i / 4
            x = x_of(t)
            p.setPen(QPen(grid, 1))
            p.drawLine(int(x), int(pad_t), int(x), int(pad_t + plot_h))
            p.setPen(tick)
            lbl = _dt.datetime.fromtimestamp(t).strftime('%H:%M')
            p.drawText(QRectF(x - 26, pad_t + plot_h + 5, 52, 14), Qt.AlignCenter, lbl)

        if len(hist) < 2:
            p.setPen(tick)
            p.drawText(self.rect(), Qt.AlignCenter, '等待更多样本…')
            return

        series = [('cpu', 1, chart_color('cpu', self.dark)),
                  ('gpu', 2, chart_color('gpu', self.dark)),
                  ('fan', 3, chart_color('fan', self.dark))]
        GAP = 5.0
        for key, idx, color in series:
            if not self.show[key]:
                continue
            pts = []
            for (t, c, g, r, _cp, _gp) in hist:
                if t < t_start - 5:
                    continue
                v = {1: c, 2: g, 3: r}[idx]
                if v is None:
                    continue
                y = y_rpm(v) if key == 'fan' else y_temp(v)
                pts.append((t, x_of(t), y))
            if len(pts) < 2:
                continue
            p.setPen(QPen(QColor(color), 2.4))
            prev = None
            for (t, x, y) in pts:
                if prev is None or t - prev[0] > GAP:
                    prev = (t, x, y)
                    continue
                p.drawLine(QPointF(prev[1], prev[2]), QPointF(x, y))
                prev = (t, x, y)

class MiniHistory(QWidget):
    """状态页迷你历史图 (对齐原版 温度与风扇历史 卡)。"""

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.dark = False
        self.setMinimumHeight(210)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_dark(self, dark):
        self.dark = dark

    def paintEvent(self, ev):
        hist = list(self.ctx['worker'].history)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        from PySide6.QtCore import QPointF, QRectF
        import datetime as _dt
        grid = QColor(chart_color('grid', self.dark))
        tick = QColor(chart_color('tick', self.dark))
        pad_l, pad_r, pad_t, pad_b = 10, 10, 8, 8
        plot_w, plot_h = w - pad_l - pad_r, h - pad_t - pad_b
        p.setPen(QPen(grid, 1))
        p.drawRoundedRect(QRectF(pad_l, pad_t, plot_w, plot_h), 10, 10)
        if len(hist) < 2:
            p.setPen(tick)
            p.drawText(self.rect(), Qt.AlignCenter, '等待数据…')
            return
        t_end = hist[-1][0]
        window = min(1800.0, max(t_end - hist[0][0], 60.0))
        t_start = t_end - window

        def x_of(t):
            return pad_l + plot_w * (t - t_start) / window

        def y_of(frac):
            return pad_t + plot_h * (1 - frac)

        f = QFont(); f.setPointSize(8); p.setFont(f)
        for key, idx, ck in (('cpu', 1, 'cpu'), ('gpu', 2, 'gpu'),
                             ('fan', 3, 'fan'), ('total', 4, 'cpu')):
            color = chart_color(ck, self.dark)
            pts = []
            for (t, c, g, r, _cp, _gp) in hist:
                if key == 'total':
                    v = (c or 0) + (g or 0)      # 温度和 (示意)
                    frac = max(0.0, min(1.0, (v - 40) / 100))
                else:
                    v = {1: c, 2: g, 3: r}[idx]
                    if not v:
                        continue
                    frac = (max(0.0, min(1.0, v / MAX_RPM)) if key == 'fan'
                            else max(0.0, min(1.0, (v - 25) / 70)))
                pts.append((t, x_of(t), y_of(frac)))
            if len(pts) >= 2:
                p.setPen(QPen(QColor(color), 1.6))
                prev = None
                for (t, x, y) in pts:
                    if prev is None or t - prev[0] > 5:
                        prev = (t, x, y)
                        continue
                    p.drawLine(QPointF(prev[1], prev[2]), QPointF(x, y))
                    prev = (t, x, y)
        p.setPen(tick)
        for i in (0, 1):
            t = t_start + window * i
            x = x_of(t)
            lbl = _dt.datetime.fromtimestamp(t).strftime('%H:%M')
            p.drawText(QRectF(min(x, w - 60), pad_t + plot_h - 18, 60, 14),
                       Qt.AlignLeft, lbl)


class CurvePage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.dark = ctx.get('dark', False)
        cfg = self.ctx['cfg']
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 4, 6, 6)
        v.setSpacing(16)

        # 页头: 标题 + 方案胶囊
        head = QHBoxLayout()
        title = QLabel('风扇曲线')
        title.setObjectName('PageTitle')
        head.addWidget(title)
        head.addStretch(1)
        v.addLayout(head)

        # 方案胶囊行
        prof, _, pv = _card(None)
        prow = QHBoxLayout()
        prow.setSpacing(8)
        lbl = QLabel('方案')
        lbl.setObjectName('RowTitle')
        prow.addWidget(lbl)
        self.cmb_profile = QComboBox()
        self.cmb_profile.setMinimumWidth(150)
        self._reload_profiles()
        self.cmb_profile.currentTextChanged.connect(self._profile_switched)
        prow.addWidget(self.cmb_profile)
        b_new = QPushButton('＋ 新建')
        b_new.clicked.connect(self._profile_new)
        b_del = QPushButton('删除')
        b_del.clicked.connect(self._profile_del)
        prow.addWidget(b_new)
        prow.addWidget(b_del)
        prow.addStretch(1)
        self.tgl_smart2 = Toggle(cfg.curve_enabled, '#ff4b26' if self.dark else '#2f6df6')
        self.tgl_smart2.toggled = self._smart_toggled
        prow.addWidget(QLabel('智能变频'))
        prow.addWidget(self.tgl_smart2)
        btn_reset = QPushButton('重置')
        btn_reset.clicked.connect(self._reset)
        prow.addWidget(btn_reset)
        pv.addLayout(prow)
        v.addWidget(prof)

        # 手动挡位
        gears, _, gv = _card('手动挡位', '点击挡位或 15 点滑条立即下发固定转速')
        gear_row = QHBoxLayout()
        gear_row.setSpacing(12)
        self.gear_btns = []
        self.gear_group = QButtonGroup(self)
        self.gear_group.setExclusive(True)
        for i, (name, color) in enumerate(GEARS):
            b = QPushButton()
            b.setObjectName('GearBtn')
            b.setCheckable(True)
            b.setMinimumHeight(64)
            b.setStyleSheet(
                f'QPushButton#GearBtn:checked {{ border-color:{color}; }}')
            b.setText(f'{name}\n{self._gear_rpm(i)} RPM')
            if name == '极限':
                b.setToolTip('超出官方规格 (4000 RPM), 风扇满载磨损自负; 运行时命令不写 flash')
            self.gear_group.addButton(b, i)
            gear_row.addWidget(b)
            self.gear_btns.append(b)
        self.gear_group.idClicked.connect(self._gear_clicked)
        # 挡位/滑条回填: 按 cfg.fixed_rpm 就近匹配预设档 (v3.61 修复: 此前硬编码「标准」,
        # 重启后显示与实际下发转速不符)
        try:
            gear_idx = min(range(5), key=lambda i: abs(self._gear_rpm(i) - cfg.fixed_rpm))
        except Exception:
            gear_idx = 1
        self.gear_btns[gear_idx].setChecked(True)
        gv.addLayout(gear_row)
        self.slider12 = GearSlider()
        self.slider12.levelSelected = self._level_selected
        self.slider12.set_level(gear_idx * 3 + 2)
        gv.addWidget(self.slider12)
        self.lbl_manual_hint = QLabel('智能变频运行中 — 手动挡位暂不可调 (关闭「智能变频」后可调)')
        self.lbl_manual_hint.setObjectName('CardHint')
        self.lbl_manual_hint.setWordWrap(True)
        gv.addWidget(self.lbl_manual_hint)
        v.addWidget(gears)
        self._sync_manual_enabled()

        # 曲线编辑器
        editor, _, ev_ = _card(None)
        self.curve = CurveEditor(self.dark)
        self.curve.changed = self._curve_changed
        self.curve.set_curve(cfg.curve)   # 回填已保存曲线 (v3.61 修复: 此前显示默认曲线,
        #  用户一拖动就会用默认值整条覆盖 cfg.curve)
        ev_.addWidget(self.curve)
        tip = QLabel('拖动圆点调整各温度点的转速 (%) · 实际转速 = 百分比 × 4800')
        tip.setObjectName('CardHint')
        ev_.addWidget(tip)
        v.addWidget(editor)

        # 智能启停 + 学习 + 预判 (网格两列)
        row_cards = QHBoxLayout()
        row_cards.setSpacing(16)
        ss, _, sv = _card('智能启停', '低温自动停转, 回升自动恢复')
        self.tgl_ss = Toggle(cfg.start_stop_enabled)
        self.tgl_ss.toggled = self._ss_toggled
        srow = QHBoxLayout()
        srow.addWidget(QLabel('低于'))
        self.spin_off = QSpinBox()
        self.spin_off.setRange(30, 80)
        self.spin_off.setSuffix('°C 关')
        self.spin_off.setValue(int(cfg.start_stop_off_below))
        self.spin_off.valueChanged.connect(self._ss_save)
        srow.addWidget(self.spin_off)
        srow.addWidget(QLabel('高于'))
        self.spin_on = QSpinBox()
        self.spin_on.setRange(35, 90)
        self.spin_on.setSuffix('°C 开')
        self.spin_on.setValue(int(cfg.start_stop_on_above))
        self.spin_on.valueChanged.connect(self._ss_save)
        srow.addWidget(self.spin_on)
        srow.addStretch(1)
        srow.addWidget(self.tgl_ss)
        sv.addLayout(srow)
        row_cards.addWidget(ss)

        pred, _, pdv = _card('温升预判 Beta', '温度快速上升时提前拉高转速')
        self.tgl_pred = Toggle(cfg.prediction_enabled)
        self.tgl_pred.toggled = self._pred_toggled
        prow2 = QHBoxLayout()
        prow2.addStretch(1)
        prow2.addWidget(self.tgl_pred)
        pdv.addLayout(prow2)
        row_cards.addWidget(pred)
        v.addLayout(row_cards)

        # 学习卡
        learn, _, lv = _card('自适应学习', '稳态温度自动贴近目标温度 · 长期运行生效')
        row2 = QHBoxLayout()
        row2.setSpacing(10)
        row2.addWidget(QLabel('目标'))
        self.spin_target = QSpinBox()
        self.spin_target.setRange(45, 90)
        self.spin_target.setSuffix('°C')
        self.spin_target.setValue(int(cfg.learning_target))
        self.spin_target.valueChanged.connect(self._learn_save)
        row2.addWidget(self.spin_target)
        row2.addWidget(QLabel('倾向'))
        self.cmb_bias = QComboBox()
        self.cmb_bias.addItems(['均衡', '散热优先', '静音优先'])
        self.cmb_bias.setCurrentIndex({'balanced': 0, 'cooling': 1, 'quiet': 2}[cfg.learning_bias])
        self.cmb_bias.currentIndexChanged.connect(self._learn_save)
        row2.addWidget(self.cmb_bias)
        row2.addStretch(1)
        b_reset = QPushButton('重置学习')
        b_reset.clicked.connect(self._learn_reset)
        row2.addWidget(b_reset)
        self.tgl_learn = Toggle(cfg.learning_enabled)
        self.tgl_learn.toggled = self._learn_toggled
        row2.addWidget(self.tgl_learn)
        lv.addLayout(row2)
        self.lbl_offsets = QLabel('暂无学习偏移')
        self.lbl_offsets.setObjectName('CardHint')
        lv.addWidget(self.lbl_offsets)
        v.addWidget(learn)

        # 历史详情
        hist, _, hv = _card('温度与风扇历史详情', '最近 30 分钟 · 每 1 秒采样')
        grid = QGridLayout()
        grid.setSpacing(12)
        self.hist_tiles = {}
        for i, key in enumerate(('CPU 峰值', 'GPU 峰值', '风扇峰值')):
            tile, val = _stat_tile(key, '平均 --')
            self.hist_tiles[key] = val.parent()
            self.hist_tiles[key]._val = val
            grid.addWidget(tile, 0, i)
        hv.addLayout(grid)
        pillrow = QHBoxLayout()
        pillrow.setSpacing(8)
        pillrow.addStretch(1)
        self.series_pills = {}
        for key, label in (('cpu', 'CPU'), ('gpu', 'GPU'), ('fan', '转速')):
            cp = SeriesPill(label, chart_color(key, self.dark))
            cp.setChecked(True)   # 默认全显示: 胶囊点亮与图表一致 (否则刚启动全是灰色分不清)
            cp.clicked.connect(lambda _, k=key, b=cp: self._toggle_series(k, b))
            pillrow.addWidget(cp)
            self.series_pills[key] = cp
        hv.addLayout(pillrow)
        self.hist_chart = HistoryChart(ctx)
        hv.addWidget(self.hist_chart, 1)
        v.addWidget(hist, 1)

    def set_dark(self, dark):
        self.dark = dark
        self.curve.dark = dark
        self.hist_chart.set_dark(dark)

    # ---- 系列 ----
    def _toggle_series(self, key, btn):
        self.hist_chart.show[key] = btn.isChecked()
        self.hist_chart.update()

    # ---- 方案 ----
    def _reload_profiles(self):
        cfg = self.ctx['cfg']
        self.cmb_profile.blockSignals(True)
        self.cmb_profile.clear()
        names = ['默认'] + sorted(cfg.curve_profiles.keys())
        if cfg.curve_active not in names:
            cfg.curve_active = '默认'
        self.cmb_profile.addItems(names)
        self.cmb_profile.setCurrentText(cfg.curve_active)
        self.cmb_profile.blockSignals(False)

    def _profile_switched(self, name: str):
        cfg = self.ctx['cfg']
        if not name:
            return
        cfg.curve_active = name
        if name != '默认':
            # 具名方案: 载入该方案的曲线
            cfg.curve = [list(x) for x in cfg.curve_profiles.get(name, cfg.curve)]
        # '默认' = 当前工作曲线, 不重置 (恢复出厂曲线请点"重置"按钮)
        cfg.save()
        self.ctx['main'].refresh_curve_cache()
        self.curve.set_curve(cfg.curve)
        self._reload_profiles()   # 同步下拉框 (托盘/外部切换时页面跟随)

    def _profile_new(self):
        cfg = self.ctx['cfg']
        name = f'方案{len(cfg.curve_profiles) + 1}'
        cfg.curve_profiles[name] = [list(x) for x in cfg.curve]
        cfg.curve_active = name
        cfg.save()
        self._reload_profiles()
        self.cmb_profile.setCurrentText(name)

    def _profile_del(self):
        cfg = self.ctx['cfg']
        name = cfg.curve_active
        if name == '默认':
            return
        cfg.curve_profiles.pop(name, None)
        cfg.curve_active = '默认'
        cfg.save()
        self._reload_profiles()
        self._profile_switched('默认')

    # ---- 挡位 ----
    def _gear_rpm(self, gear: int) -> int:
        return self.ctx['cfg'].presets.get(GEARS[gear][0], [1100, 1600, 2100, 4000, 4800][gear])

    def _gear_clicked(self, gear: int):
        rpm = self._gear_rpm(gear)
        self.ctx['worker'].apply_fixed_rpm(rpm)
        self.ctx['worker'].gear_light_hook(gear)
        self.ctx['cfg'].fixed_rpm = rpm
        self.ctx['cfg'].save()
        self.slider12.set_level(gear * 3 + 2)        # 挡位卡 → 滑条同步 (预设满档点)

    def _level_selected(self, k: int):
        gear, sub = k // 3, k % 3
        rpm = int(self._gear_rpm(gear) * GEAR_SUB[sub])
        self.ctx['worker'].apply_fixed_rpm(rpm)
        self.ctx['worker'].gear_light_hook(gear)
        self.ctx['cfg'].fixed_rpm = rpm
        self.ctx['cfg'].save()
        self.gear_btns[gear].setChecked(True)        # 滑条 → 挡位卡同步 (基础挡位)

    def _sync_manual_enabled(self):
        """智能变频开启时手动挡位禁用 (自动控温时手动调挡会被曲线覆盖)。"""
        on = bool(self.ctx['cfg'].curve_enabled)
        for b in self.gear_btns:
            b.setEnabled(not on)
        self.slider12.setEnabled(not on)
        self.lbl_manual_hint.setVisible(on)

    def _smart_toggled(self, on):
        self.ctx['cfg'].curve_enabled = on
        self.ctx['cfg'].save()
        self.ctx['main'].sync_curve_toggle(on)
        self._sync_manual_enabled()

    def sync_smart(self, on):
        self.tgl_smart2.blockSignals(True)
        self.tgl_smart2.setChecked(on)
        self.tgl_smart2.blockSignals(False)
        self._sync_manual_enabled()

    def _curve_changed(self, pct_list):
        cfg = self.ctx['cfg']
        cfg.curve = [[t, int(p / 100 * MAX_RPM)] for t, p in zip(TEMPS, pct_list)]
        if cfg.curve_active != '默认':
            cfg.curve_profiles[cfg.curve_active] = [list(x) for x in cfg.curve]
        cfg.save()
        self.ctx['main'].refresh_curve_cache()
        self._update_offsets_label()

    def _reset(self):
        cfg = self.ctx['cfg']
        cfg.curve = [[t, int(p / 100 * MAX_RPM)] for t, p in zip(TEMPS, DEFAULT_PCT)]
        if cfg.curve_active != '默认':
            cfg.curve_profiles[cfg.curve_active] = [list(x) for x in cfg.curve]
        cfg.save()
        self.curve.set_curve(cfg.curve)
        self.ctx['main'].refresh_curve_cache()

    # ---- 启停/学习/预判 ----
    def _ss_toggled(self, on):
        self.ctx['cfg'].start_stop_enabled = on
        self.ctx['cfg'].save()

    def _ss_save(self, *_):
        cfg = self.ctx['cfg']
        cfg.start_stop_off_below = float(self.spin_off.value())
        cfg.start_stop_on_above = max(self.spin_on.value(), self.spin_off.value() + 2)
        cfg.save()
        # 钳制后回填 "高于" spinbox (低于+2 才允许开), 否则界面值与实际生效值不符
        self.spin_on.blockSignals(True)
        self.spin_on.setValue(int(cfg.start_stop_on_above))
        self.spin_on.blockSignals(False)

    def _learn_toggled(self, on):
        self.ctx['cfg'].learning_enabled = on
        self.ctx['cfg'].save()

    def _learn_save(self, *_):
        cfg = self.ctx['cfg']
        cfg.learning_target = float(self.spin_target.value())
        cfg.learning_bias = ['balanced', 'cooling', 'quiet'][self.cmb_bias.currentIndex()]
        cfg.save()

    def _learn_reset(self):
        self.ctx['worker'].reset_learning()
        self.lbl_offsets.setText('已重置学习偏移')

    def _update_offsets_label(self):
        offs = self.ctx['cfg'].learning_offsets or []
        nz = [(TEMPS[i], o) for i, o in enumerate(offs) if abs(o) > 0.01]
        self.lbl_offsets.setText(
            '学习偏移: ' + '  '.join(f'{t}°C {o:+.0f}%' for t, o in nz[:8]) if nz else '暂无学习偏移')

    def _pred_toggled(self, on):
        self.ctx['cfg'].prediction_enabled = on
        self.ctx['cfg'].save()

    def tick_ui(self):
        self.hist_chart.update()
        self._update_offsets_label()
        self._update_hist_tiles()

    def _update_hist_tiles(self):
        """峰值/平均瓦片: 从最近 30 分钟历史计算 (v3.55 补齐: 构造后从未更新过)。"""
        hist = list(getattr(self.ctx['worker'], 'history', []) or [])
        if len(hist) < 2:
            return
        t_start = hist[-1][0] - 1800.0
        recent = [h for h in hist if h[0] >= t_start]

        def stats(idx, temp_mode):
            vals = [h[idx] for h in recent
                    if h[idx] is not None and (h[idx] > 1 if temp_mode else True)]
            return (max(vals), sum(vals) / len(vals)) if vals else (None, None)

        cpu_pk, cpu_av = stats(1, True)
        gpu_pk, gpu_av = stats(2, True)
        fan_pk, fan_av = stats(3, False)
        for key, pk, av, unit in (('CPU 峰值', cpu_pk, cpu_av, '°C'),
                                  ('GPU 峰值', gpu_pk, gpu_av, '°C'),
                                  ('风扇峰值', fan_pk, fan_av, ' RPM')):
            tile = self.hist_tiles.get(key)
            if not tile:
                continue
            tile._val.setText(f'{pk:.0f}{unit}' if pk is not None else '--')
            for lbl in tile.findChildren(QLabel):
                if lbl.text().startswith('平均'):
                    lbl.setText(f'平均 {av:.0f}' if av is not None else '平均 --')


# ================= 设置页 (控制页) =================
class ControlPage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        cfg = self.ctx['cfg']
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 4, 6, 6)
        v.setSpacing(16)

        # 实时概览
        ov, _, ovv = _card('实时概览')
        grid = QGridLayout()
        grid.setSpacing(14)

        temp_card = QFrame()
        temp_card.setObjectName('StatTile')
        tv = QVBoxLayout(temp_card)
        tv.setContentsMargins(16, 12, 16, 12)
        tv.setSpacing(10)

        def _cpu_gpu_row(glyph, name, model_lbl):
            r = QHBoxLayout()
            r.setSpacing(10)
            r.addWidget(_icon_box(glyph), 0, Qt.AlignVCenter)
            t = QLabel(name)
            t.setObjectName('RowTitle')
            r.addWidget(t)
            r.addStretch(1)
            model_lbl.setStyleSheet(
                'font-size:11px; color:#98a2b3; background:rgba(127,127,127,0.08);'
                'border-radius:10px; padding:3px 10px;')
            r.addWidget(model_lbl, 0, Qt.AlignVCenter)
            tv.addLayout(r)
            vr = QHBoxLayout()
            vr.setSpacing(24)
            vr.addSpacing(50)
            vlbl = QLabel('🌡 温度')
            vlbl.setObjectName('StatCap')
            vr.addWidget(vlbl)
            val_lbl = QLabel('--°C')
            val_lbl.setObjectName('StatVal')
            vr.addWidget(val_lbl)
            plbl = QLabel('⚡ 功耗')
            plbl.setObjectName('StatCap')
            vr.addWidget(plbl)
            pow_lbl = QLabel('- W')
            pow_lbl.setObjectName('StatVal')
            vr.addWidget(pow_lbl)
            vr.addStretch(1)
            tv.addLayout(vr)
            return val_lbl, pow_lbl

        self.lbl_cpu_model = QLabel('未识别')
        self.lbl_gpu_model = QLabel('未识别')
        self.lbl_cpu_val, self.lbl_cpu_pow = _cpu_gpu_row('cpu', 'CPU', self.lbl_cpu_model)
        self.lbl_gpu_val, self.lbl_gpu_pow = _cpu_gpu_row('gpu', 'GPU', self.lbl_gpu_model)
        grid.addWidget(temp_card, 0, 0)

        dev_card = QFrame()
        dev_card.setObjectName('StatTile')
        dv = QVBoxLayout(dev_card)
        dv.setContentsMargins(16, 12, 16, 12)
        dv.setSpacing(10)
        self.lbl_dev_name = QLabel('黑鲨风神Pro (BRB02)')
        self.lbl_dev_name.setObjectName('RowTitle')
        self.lbl_dev_state = _pill('未连接', '#98a2b3')
        dev_head = QHBoxLayout()
        dev_head.setSpacing(10)
        dev_head.addWidget(_icon_box('fan'), 0, Qt.AlignVCenter)
        dev_head.addWidget(self.lbl_dev_name)
        dev_head.addWidget(self.lbl_dev_state)
        dev_head.addStretch(1)
        dv.addLayout(dev_head)
        dev_grid = QGridLayout()
        dev_grid.setHorizontalSpacing(20)
        s1 = QLabel('实时速度')
        s1.setObjectName('StatCap')
        self.lbl_speed = QLabel('-- RPM')
        self.lbl_speed.setObjectName('StatVal')
        s2 = QLabel('控制模式')
        s2.setObjectName('StatCap')
        self.lbl_mode2 = QLabel('手动')
        self.lbl_mode2.setObjectName('StatVal')
        dev_grid.addWidget(s1, 0, 0)
        dev_grid.addWidget(self.lbl_speed, 1, 0)
        dev_grid.addWidget(s2, 0, 1)
        dev_grid.addWidget(self.lbl_mode2, 1, 1)
        dv.addLayout(dev_grid)
        grid.addWidget(dev_card, 0, 1)
        ovv.addLayout(grid)
        v.addWidget(ov)

        # 分段页签
        segbar = QWidget()
        segbar.setObjectName('SegmentBar')
        segbar.setAttribute(Qt.WA_StyledBackground, True)
        sh = QHBoxLayout(segbar)
        sh.setContentsMargins(6, 6, 6, 6)
        sh.setSpacing(4)
        self.seg_group = QButtonGroup(self)
        self.seg_group.setExclusive(True)
        self.stack = QStackedWidget()
        for i, (name, w) in enumerate(self._make_tabs()):
            b = QPushButton(name)
            b.setObjectName('Segment')
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            self.seg_group.addButton(b, i)
            b.clicked.connect(lambda _, idx=i: self.stack.setCurrentIndex(idx))
            sh.addWidget(b, 1)
            self.stack.addWidget(w)
        self.seg_group.button(0).setChecked(True)
        v.addWidget(segbar)
        v.addWidget(self.stack, 1)

    def _make_tabs(self):
        cfg = self.ctx['cfg']
        # --- 设备设置 ---
        dev = QWidget()
        dv = QVBoxLayout(dev)
        dv.setContentsMargins(0, 0, 0, 0)
        dv.setSpacing(10)
        card1, _, cv1 = _card('设备设置')
        self.cmb_conn = QComboBox()
        self.cmb_conn.addItems(['USB', '蓝牙 BLE'])
        self.cmb_conn.setCurrentIndex(1 if cfg.conn_type == 'ble' else 0)
        self.cmb_conn.currentIndexChanged.connect(self._conn_changed)
        cv1.addWidget(_setting_row('连接方式', '蓝牙: 点"扫描设备"后 45 秒内长按散热器按键 3-5 秒', '🔌', self.cmb_conn))
        b = QPushButton('扫描设备')
        b.setObjectName('Primary')
        b.clicked.connect(lambda: self.ctx['worker'].request_reconnect(cfg.conn_type))
        cv1.addWidget(_setting_row('设备连接', '扫描并连接当前连接方式下的散热器', '📡', b))
        dv.addWidget(card1)

        # 调试面板 (高级)
        dbg, _, dgv = _card('调试面板', '发送原始协议命令 · 仅在确认命令含义后使用, 错误命令可能导致设备异常')
        self.edit_dbg = QLineEdit()
        self.edit_dbg.setPlaceholderText('A5 03 01 A9')
        self.edit_dbg.setClearButtonEnabled(True)
        b_send = QPushButton('发送')
        b_send.setObjectName('Primary')
        b_send.clicked.connect(self._dbg_send)
        drow = QHBoxLayout()
        drow.setSpacing(10)
        drow.addWidget(self.edit_dbg, 1)
        drow.addWidget(b_send)
        dgv.addLayout(drow)
        self.lbl_dbg = QLabel('命令结果会显示在这里')
        self.lbl_dbg.setObjectName('CardHint')
        self.lbl_dbg.setWordWrap(True)
        self.lbl_dbg.setTextInteractionFlags(Qt.TextSelectableByMouse)
        dgv.addWidget(self.lbl_dbg)
        dv.addWidget(dbg)
        dv.addStretch(1)

        # --- 风扇控制 ---
        fan = QWidget()
        fv = QVBoxLayout(fan)
        fv.setContentsMargins(0, 0, 0, 0)
        fv.setSpacing(10)
        card2, _, cv2 = _card('风扇控制')
        self.tgl_curve = Toggle(cfg.curve_enabled)
        self.tgl_curve.toggled = self._curve_toggled
        cv2.addWidget(_setting_row('自动温度控制', '根据温度曲线自动调节风扇速度', '🎚️', self.tgl_curve))
        # 档位自动切换 (官方档名: 低噪/平衡/强效/超频; 设备为纯风冷, 无制冷片)
        self.tgl_tec = Toggle(cfg.tec_auto_enabled)
        self.tgl_tec.toggled = self._tec_toggled
        cv2.addWidget(_setting_row('档位自动切换', '风扇档位随温度自动升/降 (低噪→平衡→强效→超频)', '🌀', self.tgl_tec))
        thr_w = QWidget()
        th = QHBoxLayout(thr_w)
        th.setContentsMargins(0, 0, 0, 0)
        th.setSpacing(6)
        self.tec_spins = []
        for lbl in ('L2 ≥', 'L3 ≥', 'L4 ≥'):
            cap = QLabel(lbl)
            cap.setObjectName('StatVal')
            th.addWidget(cap)
            sp = QSpinBox()
            sp.setRange(30, 100)
            sp.setSuffix('°C')
            sp.setFixedWidth(66)
            sp.setAlignment(Qt.AlignCenter)
            self.tec_spins.append(sp)
            th.addWidget(sp)
        for sp, v in zip(self.tec_spins, (cfg.tec_thresholds or [55, 65, 75])):
            sp.setValue(v)
        for sp in self.tec_spins:
            sp.valueChanged.connect(self._tec_thr_changed)
        thr_w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.row_tec_thr = _setting_row('升档阈值', '达到温度升档, 回落 2°C 降档', '📐', thr_w)
        cv2.addWidget(self.row_tec_thr)
        self.cmb_smooth = QComboBox()
        self.cmb_smooth.addItems(['1 · 即时跟随', '2 · 弱平滑', '3 · 默认', '5 · 较强平滑', '10 · 强平滑'])
        self.cmb_smooth.setCurrentIndex({1: 0, 2: 1, 3: 2, 5: 3, 10: 4}.get(cfg.temp_smoothing, 2))
        self.cmb_smooth.currentIndexChanged.connect(self._smooth_changed)
        self.row_smooth = _setting_row('温度平滑度', 'EMA 指数加权: 越大越稳, 越小越跟手', '🌊', self.cmb_smooth)
        cv2.addWidget(self.row_smooth)
        self.tgl_spike = Toggle(cfg.spike_filter)
        self.tgl_spike.toggled = self._spike_changed
        self.row_spike = _setting_row('温度尖峰过滤', '忽略单次异常跳温, 避免误触发控制', '🛡️', self.tgl_spike)
        cv2.addWidget(self.row_spike)
        # 温度墙 (0.1.9): 过热保护, 优先级高于一切控制
        self.tgl_wall = Toggle(cfg.temp_wall_enabled)
        self.tgl_wall.toggled = self._wall_toggled
        wall_w = QWidget()
        wh = QHBoxLayout(wall_w)
        wh.setContentsMargins(0, 0, 0, 0)
        wh.setSpacing(6)
        self.spin_wall = QSpinBox()
        self.spin_wall.setRange(70, 100)
        self.spin_wall.setSuffix('°C')
        self.spin_wall.setFixedWidth(76)
        self.spin_wall.setAlignment(Qt.AlignCenter)
        self.spin_wall.setValue(int(cfg.temp_wall_temp))
        self.spin_wall.valueChanged.connect(self._wall_temp_changed)
        wh.addWidget(self.spin_wall)
        wall_w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.row_wall = _setting_row('温度墙保护', '过热时无视一切设置强制拉满, 降温 3°C 解除', '🧱', wall_w)
        cv2.addWidget(self.row_wall)
        self._sync_setting_deps()
        self.tgl_restore = Toggle(cfg.restore_on_start)
        self.tgl_restore.toggled = self._restore_toggled
        cv2.addWidget(_setting_row('启动时应用上次转速', '', '⚡', self.tgl_restore))
        self.tgl_gearlight = Toggle(cfg.gear_light)
        self.tgl_gearlight.toggled = self._gearlight_changed
        cv2.addWidget(_setting_row('挡位灯联动', '切挡位时同步灯色 (静音绿/标准蓝/强劲紫/超频橙)', '🎨', self.tgl_gearlight))
        self.tgl_autoswitch = Toggle(cfg.auto_switch)
        self.tgl_autoswitch.toggled = self._autoswitch_changed
        cv2.addWidget(_setting_row('USB / 蓝牙自动切换', '拔线自动切蓝牙, 插线自动切回 USB', '🔀', self.tgl_autoswitch))
        fv.addWidget(card2)

        # --- 情景配置 (0.1.9, 官方"情景"同款+曲线维度) ---
        card_s, _, cvs = _card(
            '情景配置',
            '前台进程命中进程子串时自动应用该配置 (转速 / 曲线方案 / 灯效模式), '
            '离开后自动恢复。留空或填 0 表示"该项不变"。')
        self.tgl_scene = Toggle(cfg.scene_enabled)
        self.tgl_scene.toggled = self._scene_toggled
        cvs.addWidget(_setting_row('启用情景联动', '按前台进程自动切换风扇/灯效配置', '🎯', self.tgl_scene))
        self.scene_edits = []
        from ..protocol import (RGB_MODE_FLOW, RGB_MODE_CYCLE, RGB_MODE_BREATH,
                                RGB_MODE_STEADY, RGB_MODE_BLINK, RGB_MODE_REACTIVE,
                                RGB_MODE_REFRESH)
        light_opts = [('(灯效不变)', -1), ('流动', RGB_MODE_FLOW), ('彩色循环', RGB_MODE_CYCLE),
                      ('呼吸', RGB_MODE_BREATH), ('常亮', RGB_MODE_STEADY), ('闪烁', RGB_MODE_BLINK),
                      ('响应', RGB_MODE_REACTIVE), ('刷新', RGB_MODE_REFRESH)]
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        headers = ['配置', '启用', '进程子串 (逗号分隔)', '固定转速', '曲线方案', '灯效模式']
        for col, h in enumerate(headers):
            cap = QLabel(h)
            cap.setObjectName('StatVal')
            grid.addWidget(cap, 0, col)
        scheme_names = ['(方案不变)'] + list((cfg.curve_profiles or {}).keys())
        light_pairs = dict(light_opts)
        for row_i, prof in enumerate(cfg.scene_profiles or []):
            lab = QLabel(prof.get('name', f'配置{chr(65 + row_i)}'))
            grid.addWidget(lab, row_i + 1, 0)
            tg = Toggle(prof.get('enabled', False))
            tg.toggled = (lambda on, i=row_i: self._scene_prof_changed(i, 'enabled', on))
            wrap = QWidget(); wl = QHBoxLayout(wrap); wl.setContentsMargins(0, 0, 0, 0)
            wl.addWidget(tg); wrap.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            grid.addWidget(wrap, row_i + 1, 1)
            procs = QLineEdit(', '.join(prof.get('processes') or []))
            procs.setPlaceholderText('如: cyberpunk, steam')
            procs.setMinimumWidth(150)
            procs.textChanged.connect(lambda txt, i=row_i: self._scene_prof_changed(
                i, 'processes', [s.strip() for s in txt.split(',') if s.strip()]))
            grid.addWidget(procs, row_i + 1, 2)
            sp = QSpinBox()
            sp.setRange(0, 4800)
            sp.setSpecialValueText('不变')
            sp.setFixedWidth(74)
            sp.setAlignment(Qt.AlignCenter)
            sp.setValue(int(prof.get('rpm', 0) or 0))
            sp.valueChanged.connect(lambda v, i=row_i: self._scene_prof_changed(i, 'rpm', int(v)))
            grid.addWidget(sp, row_i + 1, 3)
            cb_scheme = QComboBox()
            cb_scheme.addItem('(方案不变)', '')
            for nm in (cfg.curve_profiles or {}):
                cb_scheme.addItem(nm, nm)
            cb_scheme.setCurrentIndex(max(0, cb_scheme.findData(prof.get('scheme', ''))))
            cb_scheme.currentIndexChanged.connect(
                lambda idx, c=cb_scheme, i=row_i: self._scene_prof_changed(i, 'scheme', c.itemData(idx)))
            grid.addWidget(cb_scheme, row_i + 1, 4)
            cb_light = QComboBox()
            for txt, val in light_opts:
                cb_light.addItem(txt, int(val))
            cb_light.setCurrentIndex(max(0, cb_light.findData(int(prof.get('light_mode', -1)))))
            cb_light.currentIndexChanged.connect(
                lambda idx, c=cb_light, i=row_i: self._scene_prof_changed(i, 'light_mode', int(c.itemData(idx))))
            grid.addWidget(cb_light, row_i + 1, 5)
            self.scene_edits.append((tg, procs, sp, cb_scheme, cb_light))
        cvs.addLayout(grid)
        fv.addWidget(card_s)
        fv.addStretch(1)

        # --- 灯效 ---
        light = QWidget()
        lv = QVBoxLayout(light)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(10)
        card4, _, cv4 = _card('灯效设置', '与官方装备箱同款参数: 模式 / 颜色 / 亮度 / 速度')
        self.tgl_light_on = Toggle(True)
        self.tgl_light_on.toggled = self._light_switch_changed
        cv4.addWidget(_setting_row('灯光开关', '关闭后灯环熄灭; 挡位灯联动开启时会自动开灯', '🔆', self.tgl_light_on))
        self._light_modes = [
            ('彩色流动', protocol.RGB_MODE_FLOW), ('彩色循环', protocol.RGB_MODE_CYCLE),
            ('呼吸', protocol.RGB_MODE_BREATH), ('常亮', protocol.RGB_MODE_STEADY),
            ('闪烁', protocol.RGB_MODE_BLINK), ('响应', protocol.RGB_MODE_REACTIVE),
            ('刷新', protocol.RGB_MODE_REFRESH)]
        self.cmb_light_mode = QComboBox()
        self.cmb_light_mode.addItems([n for n, _ in self._light_modes])
        self.cmb_light_mode.currentIndexChanged.connect(self._light_apply)
        cv4.addWidget(_setting_row('灯效模式', '除音频同步外与官方全量一致', '✨', self.cmb_light_mode))
        self.cmb_light_cm = QComboBox()
        self.cmb_light_cm.addItems(['单色', '彩色'])
        self.cmb_light_cm.currentIndexChanged.connect(self._light_apply)
        cv4.addWidget(_setting_row('颜色模式', '彩色=循环变色 (色调忽略), 单色=使用下方色调', '🎚️', self.cmb_light_cm))

        def _light_slider_row(title, hint, icon, rng, init, lbl):
            # 滑条行: 滑条撑满 + 右侧定宽标签。_setting_row 会把控件设成 Fixed,
            # 需在之后改回 Expanding, 否则滑条被压窄、手柄在右端被裁切。
            s = QSlider(Qt.Horizontal)
            s.setRange(*rng)
            s.setValue(init)
            s.setMinimumWidth(140)
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(10)
            h.addWidget(s, 1)
            lbl.setMinimumWidth(40)
            lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            h.addWidget(lbl)
            row = _setting_row(title, hint, icon, w)
            w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            s.sliderReleased.connect(self._light_apply)
            s.valueChanged.connect(self._light_preview)
            return row, s

        self.lbl_swatch = QLabel()
        self.lbl_swatch.setFixedSize(36, 18)
        self.lbl_swatch.setStyleSheet('border-radius: 4px;')
        hue_row, self.sld_hue = _light_slider_row(
            '色调', '松手生效; 挡位灯联动会临时覆盖颜色', '🎨', (0, 359), 255, self.lbl_swatch)
        cv4.addWidget(hue_row)
        self.lbl_bri = QLabel('80')
        self.lbl_bri.setObjectName('StatVal')
        bri_row, self.sld_bri = _light_slider_row('亮度', '0-100', '💡', (0, 100), 80, self.lbl_bri)
        cv4.addWidget(bri_row)
        self.lbl_spd = QLabel('3.0s')
        self.lbl_spd.setObjectName('StatVal')
        spd_row, self.sld_spd = _light_slider_row('速度', '左慢右快', '⚡', (0, 100), 50, self.lbl_spd)
        cv4.addWidget(spd_row)
        b_apply = QPushButton('应用到设备')
        b_apply.setObjectName('Primary')
        b_apply.clicked.connect(self._light_apply)
        cv4.addWidget(b_apply)
        lv.addWidget(card4)
        lv.addStretch(1)
        QTimer.singleShot(400, self._light_refresh)

        # --- 系统设置 ---
        sysw = QWidget()
        sv = QVBoxLayout(sysw)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(10)
        card3, _, cv3 = _card('系统设置')
        self.cmb_theme = QComboBox()
        self.cmb_theme.addItems(['浅色', '深色'])
        self.cmb_theme.setCurrentIndex(1 if cfg.dark else 0)
        self.cmb_theme.currentIndexChanged.connect(self._theme_changed)
        cv3.addWidget(_setting_row('界面主题', '默认浅色, 可切换深色', '🌓', self.cmb_theme))
        self.cmb_btn_style = QComboBox()
        self.cmb_btn_style.addItems(['细档', '正常档'])
        self.cmb_btn_style.setCurrentIndex(
            1 if getattr(cfg, 'titlebar_btn', 'thin') == 'normal' else 0)
        self.cmb_btn_style.currentIndexChanged.connect(self._btn_style_changed)
        cv3.addWidget(_setting_row('标题栏按钮', '右上角窗口按钮的粗细档位', '➖', self.cmb_btn_style))
        self.tgl_hotkey = Toggle(cfg.hotkeys_enabled)
        self.tgl_hotkey.toggled = self._hotkey_toggled
        cv3.addWidget(_setting_row('全局快捷键', 'Ctrl+Alt+F1 循环挡位 · Ctrl+Alt+F2 智能变频', '⌨️', self.tgl_hotkey))
        self.tgl_autostart = Toggle(self._get_autostart())
        self.tgl_autostart.toggled = self._autostart_toggled
        cv3.addWidget(_setting_row('开机自启动', '任务计划最高权限开机静默启动 (免 UAC 弹窗, CPU 温度可用)', '🚀', self.tgl_autostart))
        self.tgl_autostart_min = Toggle(getattr(cfg, 'autostart_minimized', False))
        self.tgl_autostart_min.toggled = self._autostart_min_toggled
        self.row_autostart_min = _setting_row('启动后最小化到托盘', '开机自启动时不弹主窗口, 静默驻留后台温控', '🛸', self.tgl_autostart_min)
        cv3.addWidget(self.row_autostart_min)
        _dim_widget(self.row_autostart_min, not self._get_autostart(), '先开启「开机自启动」')
        sv.addWidget(card3)
        sv.addStretch(1)

        # --- 散热器屏幕 (信息卡默认; 情境卡片开关; 自定义在"屏幕图片"页) ---
        hint = QWidget()
        hv = QVBoxLayout(hint)
        hv.setContentsMargins(0, 0, 0, 0)
        hv.setSpacing(10)
        card5, _, cv5 = _card(
            '散热器屏幕',
            '信息卡 (CPU / GPU 型号 + 日期) 自动上屏默认关闭, 可在下方开启; 开启后每次连接自动更新 (一天至多两次写入)。')
        self.tgl_cards = Toggle(getattr(cfg, 'screen_cards', True))
        self.tgl_cards.toggled = self._cards_toggled
        cv5.addWidget(_setting_row(
            '情境卡片',
            '全屏游戏时自动切游戏卡; CPU/GPU ≥85°C 推警报卡; 关闭则恒显信息卡',
            '🃏', self.tgl_cards))
        self.tgl_card_auto = Toggle(getattr(cfg, 'screen_card_auto', True))
        self.tgl_card_auto.toggled = self._card_auto_toggled
        cv5.addWidget(_setting_row(
            '自动上屏',
            '连接时自动恢复屏幕内容 (信息卡或上次上传的图片); 关闭后仅手动操作才更新屏幕',
            '🪪', self.tgl_card_auto))
        tip = QLabel(
            '想放自己的图片? 到侧栏「屏幕图片」页上传 —— 上传后屏幕保持你的图片, '
            '随时点该页的「恢复信息卡」切回默认。任意两次上屏间隔 ≥2 分钟 (保护屏幕 flash)。'
            '仅 USB 通道支持屏幕上传。')
        tip.setObjectName('CardHint')
        tip.setWordWrap(True)
        cv5.addWidget(tip)
        n = int(getattr(cfg, 'screen_upload_count', 0) or 0)
        self.lbl_wear = QLabel(
            f'📊 屏幕闪存已上屏 {n} 次' + (f' ≈ 寿命消耗 {n / 10.0:.1f}% (按 10 万次擦写/页 估算)' if n else ''))

        self.lbl_wear.setObjectName('CardHint')
        self.lbl_wear.setWordWrap(True)
        cv5.addWidget(self.lbl_wear)
        try:
            self.ctx['worker'].uploadCountChanged.connect(self._on_upload_count)
        except Exception:
            pass
        hv.addWidget(card5)
        hv.addStretch(1)

        return [('设备设置', dev), ('风扇控制', fan), ('灯效', light),
                ('系统设置', sysw), ('散热器屏幕', hint)]

    # ---- 槽 ----
    def sync_smart(self, on):
        self.tgl_curve.blockSignals(True)
        self.tgl_curve.setChecked(on)
        self.tgl_curve.blockSignals(False)
        # 防御式: 同步依附开关的置灰态 (平滑度/尖峰过滤依附自动温度控制)
        if hasattr(self, '_sync_setting_deps'):
            self._sync_setting_deps()

    def _tec_toggled(self, on):
        cfg = self.ctx['cfg']
        cfg.tec_auto_enabled = on
        cfg.save()
        self._sync_setting_deps()

    def _sync_setting_deps(self):
        """设置项连带禁用 (用户原则: 子设置依附主开关):
        温度平滑度/尖峰过滤 依附 自动温度控制; 升档阈值 依附 档位自动切换。"""
        cfg = self.ctx['cfg']
        curve_on = bool(cfg.curve_enabled)
        tec_on = bool(cfg.tec_auto_enabled)
        _dim_widget(self.row_smooth, not curve_on, '开启「自动温度控制」后可调')
        _dim_widget(self.row_spike, not curve_on, '开启「自动温度控制」后可调')
        _dim_widget(self.row_tec_thr, not tec_on, '开启「档位自动切换」后可调')
        for sp in self.tec_spins:
            sp.setEnabled(tec_on)

    def _tec_thr_changed(self, *_):
        cfg = self.ctx['cfg']
        thr = sorted(sp.value() for sp in self.tec_spins)
        cfg.tec_thresholds = thr
        cfg.save()
        # 排序后回填三个 spinbox (如 65/55/75 → 55/65/75), 否则界面显示与实际不符
        for sp, v in zip(self.tec_spins, thr):
            sp.blockSignals(True)
            sp.setValue(v)
            sp.blockSignals(False)

    # ---- 散热器屏幕 ----
    # (v3.32: 屏幕内容 = 情境卡片(信息卡/游戏卡/警报卡, 默认) / 自定义图片(屏幕图片页))

    def _cards_toggled(self, on):
        cfg = self.ctx['cfg']
        cfg.screen_cards = on
        cfg.save()

    def _card_auto_toggled(self, on):
        cfg = self.ctx['cfg']
        cfg.screen_card_auto = on
        cfg.save()

    def on_info(self, info: dict):
        c = info.get('cooling')
        if c:
            self.lbl_speed.setText(f"实时速度  {self.ctx['main'].last_rpm} RPM")
        self.lbl_mode2.setText(f"控制模式  {'智能变频' if self.ctx['cfg'].curve_enabled else '手动'}")
        conn = self.ctx['main'].is_connected
        self.lbl_dev_state.setText('已连接' if conn else '未连接')
        # 灯光开关初始态: 连接建立后用设备真实值刷新一次
        # (构造时设备多半还没连上, 开关停留在默认值)
        if conn and not getattr(self, '_light_synced', False):
            self._light_synced = True
            QTimer.singleShot(400, self._light_refresh_safe)
        elif not conn:
            self._light_synced = False
        self._sync_setting_deps()

    def on_temps(self, cpu, gpu):
        self.lbl_cpu_val.setText(f'{cpu:.0f}°C' if cpu else '--°C')
        self.lbl_gpu_val.setText(f'{gpu:.0f}°C' if gpu else '--°C')
        t = self.ctx['worker'].temps
        cp, gp = t.cpu_power, t.gpu_power
        self.lbl_cpu_pow.setText(f'{cp:.0f} W' if cp and cp > 0 else '- W')
        self.lbl_gpu_pow.setText(f'{gp:.0f} W' if gp and gp > 0 else '- W')
        names = self.ctx['worker'].temps
        if names.cpu_name:
            self.lbl_cpu_model.setText(names.cpu_name)
        if names.gpu_name:
            self.lbl_gpu_model.setText(names.gpu_name)

    def _dbg_send(self):
        result = self.ctx['worker'].debug_send(self.edit_dbg.text().strip())
        self.lbl_dbg.setText(result)

    def _conn_changed(self, idx):
        self.ctx['cfg'].conn_type = 'ble' if idx == 1 else 'usb'
        self.ctx['cfg'].save()
        self.ctx['worker'].request_reconnect(self.ctx['cfg'].conn_type)

    def _curve_toggled(self, on):
        self.ctx['cfg'].curve_enabled = on
        self.ctx['cfg'].save()
        self.ctx['main'].sync_curve_toggle(on)
        self._sync_setting_deps()

    def _smooth_changed(self, idx):
        self.ctx['cfg'].temp_smoothing = [1, 2, 3, 5, 10][idx]
        self.ctx['cfg'].save()

    def _spike_changed(self, on):
        self.ctx['cfg'].spike_filter = on
        self.ctx['cfg'].save()

    def _scene_toggled(self, on):
        self.ctx['cfg'].scene_enabled = bool(on)
        self.ctx['cfg'].save()

    def _scene_prof_changed(self, i: int, field: str, val):
        cfg = self.ctx['cfg']
        try:
            profs = cfg.scene_profiles
            if 0 <= i < len(profs) and isinstance(profs[i], dict):
                profs[i][field] = val
                cfg.save()
        except Exception:
            pass



    def _on_upload_count(self, n):
        try:
            self.lbl_wear.setText(
                f'📊 屏幕闪存已上屏 {int(n)} 次' +
                (f' ≈ 寿命消耗 {int(n) / 10.0:.1f}% (按 10 万次擦写/页 估算)' if n else ''))
        except Exception:
            pass

    def _wall_toggled(self, on):
        self.ctx['cfg'].temp_wall_enabled = bool(on)
        self.ctx['cfg'].save()

    def _wall_temp_changed(self, v):
        self.ctx['cfg'].temp_wall_temp = float(v)
        self.ctx['cfg'].save()

    def _restore_toggled(self, on):
        self.ctx['cfg'].restore_on_start = on
        self.ctx['cfg'].save()

    def _gearlight_changed(self, on):
        self.ctx['cfg'].gear_light = on
        self.ctx['cfg'].save()
        if on:
            self.ctx['worker'].set_rgb(True)   # 先把灯打开, 联动才可见

    def _light_switch_changed(self, on):
        self.ctx['worker'].set_rgb(on)
        if on:
            self._light_apply()

    # ---- 灯效面板 ----
    def _light_ui_values(self):
        mode = self._light_modes[max(0, self.cmb_light_mode.currentIndex())][1]
        cm = protocol.RGB_COLOR_SINGLE if self.cmb_light_cm.currentIndex() == 0 else protocol.RGB_COLOR_MULTI
        r, g, b = (int(c * 255) for c in colorsys.hsv_to_rgb(self.sld_hue.value() / 360.0, 1, 1))
        speed = 5000 - self.sld_spd.value() * 40      # 左慢右快: 5000ms .. 1000ms
        return mode, speed, self.sld_bri.value(), cm, (r, g, b)

    def _light_preview(self, *_):
        _, speed, _, _, (r, g, b) = self._light_ui_values()
        self.lbl_swatch.setStyleSheet(f'background: rgb({r},{g},{b}); border-radius: 4px;')
        self.lbl_bri.setText(str(self.sld_bri.value()))
        self.lbl_spd.setText(f'{speed / 1000:.1f}s')

    def _light_apply(self, *_):
        self._light_preview()
        self.ctx['worker'].set_lighting(*self._light_ui_values())

    def _light_refresh(self):
        sw = self.ctx['worker'].get_rgb_switch()
        if sw is not None:
            self.tgl_light_on.blockSignals(True)
            self.tgl_light_on.setChecked(sw)
            self.tgl_light_on.blockSignals(False)
        eff = self.ctx['worker'].get_lighting()
        if not eff:
            return
        ctrls = [self.cmb_light_mode, self.cmb_light_cm, self.sld_hue, self.sld_bri, self.sld_spd]
        for c in ctrls:
            c.blockSignals(True)
        try:
            idx = {m: i for i, (_, m) in enumerate(self._light_modes)}.get(eff['mode'], 2)
            self.cmb_light_mode.setCurrentIndex(idx)
            self.cmb_light_cm.setCurrentIndex(0 if eff['color_mode'] == protocol.RGB_COLOR_SINGLE else 1)
            r, g, b = eff['rgb']
            hue = int(colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)[0] * 360) if max(r, g, b) else 0
            self.sld_hue.setValue(max(0, min(359, hue)))
            self.sld_bri.setValue(max(0, min(100, eff['brightness'])))
            self.sld_spd.setValue(max(0, min(100, (5000 - eff['speed']) // 40)))
        finally:
            for c in ctrls:
                c.blockSignals(False)
        self._light_preview()

    def _light_refresh_safe(self, *_):
        """防御式刷新灯效状态: 设备查询失败只记日志, 不影响 UI。"""
        try:
            self._light_refresh()
        except Exception as e:
            LOGBUF.write(f'[灯效] 状态刷新失败: {e!r}')

    def _autoswitch_changed(self, on):
        self.ctx['cfg'].auto_switch = on
        self.ctx['cfg'].save()

    def _theme_changed(self, idx):
        self.ctx['cfg'].dark = (idx == 1)
        self.ctx['cfg'].save()
        self.ctx['main'].apply_theme(self.ctx['cfg'].dark)

    def _btn_style_changed(self, idx):
        cfg = self.ctx['cfg']
        cfg.titlebar_btn = 'normal' if idx == 1 else 'thin'
        cfg.save()
        self.ctx['main'].titlebar.set_btn_style(cfg.titlebar_btn)

    def _hotkey_toggled(self, on):
        self.ctx['cfg'].hotkeys_enabled = on
        self.ctx['cfg'].save()
        self.ctx['main'].apply_hotkey_setting(on)

    _AUTOSTART_TASK = 'AlpToolbox'   # 任务计划名 (ASCII, 规避 schtasks/控制台编码坑)

    def _autostart_exe(self) -> str:
        """要注册的自启动目标: 冻结 exe 直启; 源码模式 = python + main.py。"""
        if getattr(sys, 'frozen', False):
            return sys.executable
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return f'"{sys.executable}" "{os.path.join(root, "main.py")}"'

    def _get_autostart(self) -> bool:
        """任务计划 AlpToolbox 存在 (RL HIGHEST, 登录静默提权) 即视为开启。

        旧版写 HKCU Run 的遗留值也算数 (切换时会顺手清理, 避免双启动 + 每次弹 UAC)。"""
        try:
            import winreg
            winreg.QueryValueEx(winreg.OpenKey(
                winreg.HKCU, r'Software\Microsoft\Windows\CurrentVersion\Run'),
                'Brb02Toolbox')
            return True
        except Exception:
            pass
        try:
            r = subprocess.run(f'schtasks /Query /TN "{self._AUTOSTART_TASK}"',
                               capture_output=True, creationflags=0x08000000)
            return r.returncode == 0
        except Exception as e:
            LOGBUF.write(f'[自启动] 查询失败: {e!r}')
            return False

    def _autostart_legacy_run_clear(self):
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKCU,
                               r'Software\Microsoft\Windows\CurrentVersion\Run',
                               0, winreg.KEY_SET_VALUE)
            try:
                winreg.DeleteValue(k, 'Brb02Toolbox')
            except FileNotFoundError:
                pass
        except Exception as e:
            LOGBUF.write(f'[自启动] 清理旧 Run 键失败: {e!r}')

    def _autostart_toggled(self, on):
        """任务计划 (schtasks, RL HIGHEST): 登录时以管理员身份静默运行, 不弹 UAC。

        失败必须可看见 —— 走 LOGBUF (无控制台 exe 里 print 是黑洞), 并回滚开关视觉。"""
        try:
            if on:
                exe = self._autostart_exe()
                # v3.64 修复: --tray 必须在引号外 —— 之前把 flag 拼进了被引号包裹的
                # "程序路径"里, 任务执行的程序名变成 "...\Alp工具箱.exe --tray"
                # (不存在的文件) → 开机自启动静默失效
                tray = ' --tray' if getattr(self.ctx['cfg'], 'autostart_minimized', False) else ''
                cmd = (f'schtasks /Create /F /TN "{self._AUTOSTART_TASK}" '
                       f'/SC ONLOGON /RL HIGHEST /TR "\\"{exe}\\"{tray}"')
            else:
                cmd = f'schtasks /Delete /F /TN "{self._AUTOSTART_TASK}"'
            r = subprocess.run(cmd, capture_output=True, creationflags=0x08000000)
            if r.returncode == 0:
                if on:
                    self._autostart_legacy_run_clear()
                LOGBUF.write(f'[自启动] {"已开启 (任务计划: 登录时管理员身份静默运行)" if on else "已关闭"}')
                self._sync_autostart_min_dim()
                return
            err = (r.stderr or r.stdout or b'').decode('gbk', 'replace').strip() \
                or f'exit={r.returncode}'
            LOGBUF.write(f'[自启动] {"开启" if on else "关闭"}失败: {err}')
            self.tgl_autostart.setChecked(self._get_autostart())   # 回滚开关视觉
            self._sync_autostart_min_dim()
        except Exception as e:
            LOGBUF.write(f'[自启动] 操作异常: {e!r}')
            self.tgl_autostart.setChecked(self._get_autostart())
            self._sync_autostart_min_dim()

    def _autostart_min_toggled(self, on):
        """「启动后最小化到托盘」: 仅当任务已注册时同步重注册任务命令 (加/去 --tray)。"""
        cfg = self.ctx['cfg']
        cfg.autostart_minimized = on
        cfg.save()
        if self._get_autostart():
            self._autostart_toggled(True)   # 任务在: 重注册以带上/去掉 --tray

    def _sync_autostart_min_dim(self):
        """最小化开关的可用性跟随任务计划实际状态。"""
        _dim_widget(self.row_autostart_min, not self._get_autostart(), '先开启「开机自启动」')


# ================= 设备页 =================
class DevicePage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 4, 6, 6)
        v.setSpacing(16)

        head = QLabel('已支持设备')
        head.setObjectName('PageTitle')
        v.addWidget(head)

        card, _, cv = _card(None)
        row = QHBoxLayout()
        row.setSpacing(14)
        icon = _icon_box('❄')
        row.addWidget(icon, 0, Qt.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(4)
        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        n = QLabel('黑鲨风神Pro (BRB02)')
        n.setObjectName('RowTitle')
        name_row.addWidget(n)
        name_row.addWidget(_pill('内置设备', '#2f6df6'))
        name_row.addWidget(_pill('USB / 蓝牙 BLE', '#10b981'))
        name_row.addStretch(1)
        col.addLayout(name_row)
        d = QLabel('通道: USB (libusb 中断传输) · 蓝牙 BLE (AE41→AE04 透传)\n'
                   '能力: 转速读取 · 转速下发 · 曲线 · RGB 开关 · 固件版本')
        d.setObjectName('RowDesc')
        d.setWordWrap(True)
        col.addWidget(d)
        row.addLayout(col, 1)
        cv.addLayout(row)
        v.addWidget(card)
        v.addStretch(1)


# ================= 关于页 =================
class AboutPage(QWidget):
    """关于页: 应用信息 + 检查更新 (GitHub Releases) + 项目链接 + FAQ + 运行日志 (v0.1.5)。"""

    _PILL_NEUTRAL = '#66758a'
    _PILL_NEW = '#10b981'
    _MIN_CHECK_INTERVAL = 60          # 检查防抖 (秒): 手别抖也别烧配额

    _FAQS = (
        ('为什么 CPU 温度显示 "--" 或 0°C?',
         'CPU 温度需要管理员权限 + PawnIO 驱动 (setup 版安装时已集成)。未提权时会自动降级显示 '
         'GPU 温度; 装好驱动后重启工具箱即可。'),
        ('找不到散热器 / 连不上?',
         '优先插 USB 线; 无线使用请长按设备按键 3-5 秒进入蓝牙配对模式, 两个通道会自动切换。'
         '官方装备箱运行时会独占设备, 请先完全退出官方软件。'),
        ('图片上传按钮是灰的?',
         '图片上传仅支持 USB 连接 (蓝牙通道已禁用图传), 且需要先连接设备并选择图片。'),
        ('升级会丢失配置吗?',
         '不会。配置和上次选图保存在 %APPDATA%\\Brb02Toolbox, 升级/重装都不受影响。'
         'setup 覆盖安装时会自动关闭正在运行的旧版本。'),
        ('屏幕上的内容怎么换?',
         '连接后默认显示信息卡 (CPU/GPU 型号 + 日期); 在「屏幕」页上传任意图片即可替换, '
         '随时点「恢复信息卡」切回。'),
        ('检查更新提示 403 / 限流?',
         'GitHub 对未登录请求限制为每 IP 每小时 60 次, 挂代理时共享出口 IP 更容易用完。'
         '「正式版」通道不走该接口 (无配额); 「预发布」通道仍走 API, 遇到就等几分钟再试。'),
    )

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.dark = ctx.get('dark', False)
        from brb02 import update_check as _uc
        from brb02.update_check import AssetDownloader, UpdateChecker
        self._uc = _uc
        self._release = None            # 最近一次检查到的 release 摘要
        self._checking = False
        self._last_check_ts = 0.0

        v = QVBoxLayout(self)
        v.setContentsMargins(6, 4, 6, 6)
        v.setSpacing(16)

        # --- 行 1: 关于卡 + 项目链接卡 ---
        row1 = QHBoxLayout()
        row1.setSpacing(16)

        card, _, cv = _card('关于 Alp 工具箱')
        head = QHBoxLayout()
        icon = QLabel()
        icon.setFixedSize(72, 72)
        icon.setPixmap(self._app_icon_pixmap())
        head.addWidget(icon)
        head.addSpacing(12)
        hcol = QVBoxLayout()
        name = QLabel('Alp 工具箱')
        name.setStyleSheet('font-size:22px; font-weight:800; background:transparent;')
        hcol.addWidget(name)
        desc = QLabel('BRB02 压风式散热器第三方控制工具 · USB / 蓝牙 BLE 双通道 · 协议全部实机逆向验证')
        desc.setWordWrap(True)
        desc.setObjectName('CardHint')
        hcol.addWidget(desc)
        head.addLayout(hcol, 1)
        cv.addLayout(head)

        chips = QHBoxLayout()
        chips.setSpacing(8)
        chips.addWidget(_pill(f'当前 v{APP_VERSION}', self._PILL_NEUTRAL))
        self.pill_latest = _pill('最新 —', self._PILL_NEUTRAL)
        chips.addWidget(self.pill_latest)
        chips.addStretch(1)
        chips.addWidget(QLabel('更新通道'))
        self.cmb_channel = QComboBox()
        self.cmb_channel.addItems(['正式版', '预发布'])
        self.cmb_channel.currentIndexChanged.connect(self._on_channel_changed)
        chips.addWidget(self.cmb_channel)
        cv.addLayout(chips)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        self.b_install = QPushButton('下载并安装')
        self.b_install.setEnabled(False)
        self.b_install.clicked.connect(self._install_update)
        self.b_check = QPushButton('⟳ 检查更新')
        self.b_check.clicked.connect(self._check_update)
        self.b_open = QPushButton('打开发布页')
        self.b_open.clicked.connect(self._open_releases)
        btns.addWidget(self.b_install)
        btns.addWidget(self.b_check)
        btns.addWidget(self.b_open)
        btns.addStretch(1)
        cv.addLayout(btns)

        btns2 = QHBoxLayout()
        self.b_sponsor = QPushButton('♡ 赞助支持')
        self.b_sponsor.clicked.connect(self._sponsor)
        btns2.addWidget(self.b_sponsor)
        btns2.addStretch(1)
        cv.addLayout(btns2)

        self.lbl_update_hint = QLabel('检查更新只读取 GitHub Releases 公开信息, 不上传任何本机数据。')
        self.lbl_update_hint.setObjectName('CardHint')
        self.lbl_update_hint.setWordWrap(True)
        cv.addWidget(self.lbl_update_hint)
        row1.addWidget(card, 3)

        links, _, lv2 = _card('项目链接')
        self._add_link_row(
            lv2, '🚀', '开源仓库',
            f'<a href="{_uc.REPO_URL}">github.com/{_uc.REPO_OWNER}/{_uc.REPO_NAME}</a>')
        self._add_copy_row(lv2, '💬', 'QQ 交流群', '1071985211', '点击复制群号')
        self._add_copy_row(lv2, '👤', '作者', '苏晓沉 (StarSinking)', '点击复制')
        links_hint = QLabel('新版本发布与问题反馈都在 GitHub; 本页底部可复制运行日志 / 导出诊断包。')
        links_hint.setObjectName('CardHint')
        links_hint.setWordWrap(True)
        lv2.addWidget(links_hint)
        row1.addWidget(links, 2)
        v.addLayout(row1)

        # --- 常见问题 ---
        faq, _, fv = _card('常见问题解答')
        for q, a in self._FAQS:
            ql = QLabel(q)
            ql.setStyleSheet('font-size:13px; font-weight:700; background:transparent;')
            ql.setWordWrap(True)
            fv.addWidget(ql)
            al = QLabel(a)
            al.setObjectName('CardHint')
            al.setWordWrap(True)
            fv.addWidget(al)
        v.addWidget(faq)

        # --- 运行日志 (方便反馈问题) ---
        logcard, _, lv = _card('运行日志', '程序异常与连接事件 · 反馈问题时请点击"复制全部"发给开发者')
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(240)
        self.log_view.setStyleSheet(
            'font-family: "Cascadia Mono", Consolas, monospace; font-size: 11px;'
            ' background: rgba(127,127,127,0.05); border: none; border-radius: 10px;')
        lv.addWidget(self.log_view)
        brow = QHBoxLayout()
        b_copy = QPushButton('复制全部')
        b_copy.clicked.connect(self._copy_log)
        b_diag = QPushButton('导出诊断包')
        b_diag.clicked.connect(self._export_diag)
        b_clear = QPushButton('清空')
        b_clear.clicked.connect(self._clear_log)
        brow.addWidget(b_copy)
        brow.addWidget(b_diag)
        brow.addWidget(b_clear)
        brow.addStretch(1)
        lv.addLayout(brow)
        v.addWidget(logcard, 1)

        self._log_version = -1
        self.log_timer = QTimer(self)
        self.log_timer.timeout.connect(self._refresh_log)
        self.log_timer.start(1000)

        # --- 更新器 (QtNetwork 异步, 不阻塞 UI) ---
        self._checker = UpdateChecker(self)
        self._checker.done.connect(self._on_release)
        self._checker.fail.connect(self._on_check_fail)
        self._downloader = AssetDownloader(self)
        self._downloader.progress.connect(self._on_dl_progress)
        self._downloader.done.connect(self._on_dl_done)
        self._downloader.fail.connect(self._on_dl_fail)

    # ---- 外观 ----
    def _app_icon_pixmap(self) -> QPixmap:
        pm = QPixmap(72, 72)
        pm.fill(Qt.transparent)
        candidates = []
        meipass = getattr(sys, '_MEIPASS', '')
        if meipass:
            candidates.append(os.path.join(meipass, 'assets', 'app.ico'))
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        candidates.append(os.path.join(root, 'assets', 'app.ico'))
        for p in candidates:
            if os.path.isfile(p):
                ico = QPixmap(p)
                if not ico.isNull():
                    return ico.scaled(72, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        from .widgets import draw_icon_pixmap
        return draw_icon_pixmap('fan', 72, '#ff4b26' if self.dark else '#2f6df6')

    @staticmethod
    def _set_pill(lbl: QLabel, text: str, color: str, bg_alpha=0.12):
        lbl.setText(text)
        lbl.setStyleSheet(
            f'color:{color}; background:rgba({int(color[1:3],16)},{int(color[3:5],16)},'
            f'{int(color[5:7],16)},{bg_alpha}); border-radius:11px; padding:3px 12px;')

    @staticmethod
    def _add_link_row(lay, icon: str, title: str, value_html: str):
        row = QHBoxLayout()
        cap = QLabel(f'{icon}  {title}')
        row.addWidget(cap)
        row.addStretch(1)
        val = QLabel(value_html)
        val.setOpenExternalLinks(True)
        row.addWidget(val)
        lay.addLayout(row)

    def _add_copy_row(self, lay, icon: str, title: str, value: str, tip: str = ''):
        """行: 图标 + 标题 + 右侧值按钮 (点击复制到剪贴板)。"""
        row = QHBoxLayout()
        cap = QLabel(f'{icon}  {title}')
        row.addWidget(cap)
        row.addStretch(1)
        btn = QPushButton(value)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet('border: none; background: transparent; font-weight: 600;')
        if tip:
            btn.setToolTip(tip)
        btn.clicked.connect(lambda _checked=False, v=value: self._copy_value(v))
        row.addWidget(btn)
        lay.addLayout(row)

    def _copy_value(self, value: str):
        QApplication.clipboard().setText(value)
        btn = self.sender()
        if isinstance(btn, QPushButton):
            old = btn.text()
            btn.setText('已复制 ✓')
            QTimer.singleShot(1200, lambda: btn.setText(old))
        LOGBUF.write(f'[复制] 已复制: {value}')

    # ---- 更新检查 ----
    def _on_channel_changed(self, *_):
        self._release = None
        self.b_install.setEnabled(False)
        self.b_install.setText('下载并安装')
        self._set_pill(self.pill_latest, '最新 —', self._PILL_NEUTRAL)
        self.lbl_update_hint.setText('更新通道已切换, 点击「检查更新」。')

    def _check_update(self):
        if self._checking:
            return
        now = time.time()
        if now - self._last_check_ts < self._MIN_CHECK_INTERVAL:
            wait = int(self._MIN_CHECK_INTERVAL - (now - self._last_check_ts)) + 1
            self.lbl_update_hint.setText(
                f'检查太频繁 —— GitHub 对未登录请求有限制, 请约 {wait} 秒后再试。')
            return
        self._last_check_ts = now
        self._checking = True
        self.b_check.setEnabled(False)
        self.b_check.setText('检查中…')
        self.lbl_update_hint.setText('正在从 GitHub 获取最新版本…')
        self._checker.check('stable' if self.cmb_channel.currentIndex() == 0 else 'pre')

    def _on_release(self, rel):
        self._checking = False
        self.b_check.setEnabled(True)
        self.b_check.setText('⟳ 检查更新')
        if not rel:
            self._set_pill(self.pill_latest, '最新 —', self._PILL_NEUTRAL)
            self.lbl_update_hint.setText('GitHub 上还没有发布版本。')
            return
        self._release = rel
        newer = self._uc.is_newer(rel['version'], APP_VERSION)
        suffix = '（预发布）' if rel['prerelease'] else ''
        self._set_pill(self.pill_latest, f"最新 v{rel['version']}{suffix}",
                       self._PILL_NEW if newer else self._PILL_NEUTRAL)
        if newer and rel.get('setup'):
            self.b_install.setEnabled(True)
            self.lbl_update_hint.setText(
                f"发现新版本 v{rel['version']} —— 点击「下载并安装」, 下载完成后自动运行安装器。")
        elif newer:
            self.b_install.setEnabled(False)
            self.lbl_update_hint.setText(
                f"发现新版本 v{rel['version']} —— 未找到安装包附件, 请点「打开发布页」手动下载。")
        else:
            self.b_install.setEnabled(False)
            self.lbl_update_hint.setText('当前已是最新版本。')

    def _on_check_fail(self, msg):
        self._checking = False
        self.b_check.setEnabled(True)
        self.b_check.setText('⟳ 检查更新')
        self._set_pill(self.pill_latest, '最新 —', self._PILL_NEUTRAL)
        self.lbl_update_hint.setText(f'检查失败: {msg} —— 请确认网络后重试, 或点「打开发布页」。')
        LOGBUF.write(f'[更新] 检查失败: {msg}')

    def _install_update(self):
        rel = self._release
        if not rel or not rel.get('setup') or self.b_install.text().startswith('下载中'):
            return
        dest = os.path.join(tempfile.gettempdir(), rel['setup']['name'])
        self.b_install.setEnabled(False)
        self.b_install.setText('准备下载…')
        LOGBUF.write(f"[更新] 开始下载 v{rel['version']} 安装包…")
        self._downloader.download(rel['setup']['url'], dest,
                                  expect_size=rel['setup'].get('size') or 0)

    def _on_dl_progress(self, received: int, total: int):
        if total > 0:
            self.b_install.setText(f'下载中 {received * 100 // total}%')
        else:
            self.b_install.setText(f'下载中 {received // 1048576}MB…')

    def _on_dl_done(self, path: str):
        self.b_install.setEnabled(True)
        self.b_install.setText('重新下载')
        LOGBUF.write(f'[更新] 安装包已下载: {path}')
        try:
            os.startfile(path)     # 安装器自带 UAC 提权, 会自动关闭运行中的旧版本
            self.lbl_update_hint.setText('安装器已启动 —— 它会自动关闭本程序并完成升级。')
            LOGBUF.write('[更新] 已启动安装器。')
        except OSError as e:
            self.lbl_update_hint.setText(f'安装包已下载, 但启动失败: {e!r} —— 请手动运行 {path}')
            LOGBUF.write(f'[更新] 启动安装器失败: {e!r}')

    def _on_dl_fail(self, msg: str):
        self.b_install.setEnabled(True)
        self.b_install.setText('下载并安装')
        self.lbl_update_hint.setText(f'下载失败: {msg} —— 可点「打开发布页」手动下载。')
        LOGBUF.write(f'[更新] 下载失败: {msg}')

    def _open_releases(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(self._uc.RELEASES_URL))

    def _sponsor(self):
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(self, '赞助 Alp 工具箱', '你的使用就是最好的赞助')

    # ---- 运行日志 ----
    def _refresh_log(self):
        from brb02.logbuf import LOGBUF as _LB
        if _LB.version() != self._log_version:
            self._log_version = _LB.version()
            self.log_view.setPlainText(_LB.text())
            sb = self.log_view.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _copy_log(self):
        from brb02.logbuf import LOGBUF as _LB
        QApplication.clipboard().setText(_LB.text())

    def _export_diag(self):
        from PySide6.QtWidgets import QFileDialog
        default = f'alp-diagnostics-{time.strftime("%Y%m%d-%H%M%S")}.zip'
        path, _ = QFileDialog.getSaveFileName(self, '导出诊断包', default, '诊断包 (*.zip)')
        if not path:
            return
        try:
            from brb02.diagnostics import export_diagnostics
            out = export_diagnostics(self.ctx['cfg'], self.ctx['worker'],
                                     LOGBUF.text(), path)
            LOGBUF.write(f'[诊断] 已导出: {out}')
        except Exception as e:
            LOGBUF.write(f'[诊断] 导出失败: {e!r}')

    def _clear_log(self):
        from brb02.logbuf import LOGBUF as _LB
        _LB.clear()


# ================= 屏幕图片页 =================
# 设备方向标定 (2026-10-04 设备侧 v3.13 看屏定案: 恒等, 无翻转)。
# 同时作用于 **预览** 与 **上传画布**: 预览 = 设备实际显示效果; 上传按同向翻转 → 所见即所得。
FLIP_H = False      # 水平翻转 (左右镜像)
FLIP_V = False      # 垂直翻转 (上下镜像)
SCREEN_W, SCREEN_H = 428, 142          # 设备画布 (与 screen_upload.CANVAS_W/H 一致)
IMG_FILTER = '图片 (*.png *.jpg *.jpeg *.bmp *.webp)'
IMG_EXTS = ('.png', '.jpg', '.jpeg', '.bmp', '.webp')   # 拖入白名单 (与 IMG_FILTER 一致)

_FIT_STRETCH = '拉伸铺满 (不裁边; 比例不符会轻微形变)'
_FIT_COVER = '等比裁边 (不形变; 居中裁掉超出部分)'


# 参数选项 (0.1.8, 官方"参数选项"同款): key → (显示标签, 槽位值样式)
PARAM_LABELS = [
    ('cpu_temp', 'CPU 温度', 'CPU ℃'),
    ('gpu_temp', 'GPU 温度', 'GPU ℃'),
    ('cpu_load', 'CPU 负载', 'CPU /%'),
    ('gpu_load', 'GPU 负载', 'GPU /%'),
    ('fan_rpm',  '风扇转速', 'RPM'),
    ('disk',     '磁盘占用率', 'DSK /%'),
    ('ram',      '运行使用率', 'RAM /%'),
    ('time',     '时间', 'PM'),
]


def fit_image(img: QImage, fit: str) -> QImage:
    """把任意图变成 428×142 画布预览 (与上传画布同一规则)。

    stretch = 拉伸铺满; cover = 等比放大到覆盖画布后居中裁边。
    """
    if img.isNull():
        return img
    if fit == 'cover':
        s = img.scaled(SCREEN_W, SCREEN_H, Qt.KeepAspectRatioByExpanding,
                       Qt.SmoothTransformation)
        x = max(0, (s.width() - SCREEN_W) // 2)
        y = max(0, (s.height() - SCREEN_H) // 2)
        return s.copy(x, y, SCREEN_W, SCREEN_H)
    return img.scaled(SCREEN_W, SCREEN_H, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)


class ScreenPage(QWidget):
    """屏幕图片页: 选图/拖入 (PNG/JPG) → 预览 (428:142) → 上传到散热器屏幕。"""

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.cfg = ctx['cfg']
        self._path = None
        self._img_valid = False       # 预览可解码才允许上传 (默认无效, 防坏图混入)
        self._busy = False
        self.setAcceptDrops(True)

        v = QVBoxLayout(self)
        v.setContentsMargins(6, 4, 6, 6)
        v.setSpacing(16)

        head = QLabel('屏幕图片')
        head.setObjectName('PageTitle')
        v.addWidget(head)

        card, _, cv = _card(
            '上传图片',
            '选择或拖入 PNG / JPG → 预览 (428:142) → 上传到散热器屏幕。'
            '整程约 16 秒, 期间风扇控制暂停, 请勿拔线或操作设备。\n'
            '默认屏幕显示信息卡 (CPU/GPU 型号 + 日期, 连接时自动更新); '
            '手动上传后屏幕保持你的图片, 可随时点「恢复信息卡」切回。'
            '**图片上传仅支持 USB 连接** (蓝牙通道被硬性禁用)。')

        self.preview = QLabel('将图片拖到这里, 或点击"选择图片"')
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setFixedSize(SCREEN_W, SCREEN_H)
        self.preview.setStyleSheet(
            'background: rgba(127,127,127,0.10); border: 1px dashed rgba(127,127,127,0.45);'
            ' border-radius: 12px; color: #98a2b3;')
        cv.addWidget(self.preview, 0, Qt.AlignHCenter)

        fit_row = QHBoxLayout()
        fit_row.setSpacing(10)
        fit_row.addWidget(QLabel('缩放方式'))
        self.fit_combo = QComboBox()
        self.fit_combo.addItem(_FIT_STRETCH, 'stretch')
        self.fit_combo.addItem(_FIT_COVER, 'cover')
        self.fit_combo.setMinimumWidth(330)
        self.fit_combo.setToolTip('图片比例与屏幕 (428:142) 不符时的处理方式')
        self.fit_combo.setCurrentIndex(max(0, self.fit_combo.findData(
            getattr(self.cfg, 'image_fit', 'stretch'))))
        self.fit_combo.currentIndexChanged.connect(self._on_fit_changed)
        fit_row.addWidget(self.fit_combo)
        fit_row.addStretch(1)
        cv.addLayout(fit_row)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.btn_pick = QPushButton('选择图片')
        self.btn_pick.clicked.connect(self._pick)
        self.btn_up = QPushButton('上传到屏幕')
        self.btn_up.clicked.connect(self._upload)
        self.btn_up.setEnabled(False)
        self.btn_cancel = QPushButton('取消')
        self.btn_cancel.clicked.connect(self._cancel)
        self.btn_cancel.setEnabled(False)
        self.btn_card = QPushButton('恢复信息卡')
        self.btn_card.clicked.connect(self._restore_card)
        self.btn_card.setToolTip('切回默认信息卡 (CPU/GPU 型号 + 日期), 已连接时立即上屏')
        row.addWidget(self.btn_pick)
        row.addWidget(self.btn_up)
        row.addWidget(self.btn_cancel)
        row.addWidget(self.btn_card)
        row.addStretch(1)
        cv.addLayout(row)

        self.bar = QProgressBar()
        self.bar.setRange(0, 2096)
        self.bar.setValue(0)
        self.bar.setFormat('%v / %m 帧')
        cv.addWidget(self.bar)

        self.result = QLabel('')
        self.result.setObjectName('CardHint')
        self.result.setWordWrap(True)
        cv.addWidget(self.result)
        v.addWidget(card)

        # --- 官方历史图片 (0.1.9): 读官方软件的画布缓存, 零转换直传 ---
        self.hist_list = None
        hist_bins = self._scan_history_bins()
        if hist_bins:
            card2, _, hv2 = _card(
                '历史图片',
                '官方软件 (黑鲨装备箱) 缓存过的屏幕图片, 点击选中后直接上传 (格式互认, 零转换)。')
            self.hist_list = QListWidget()
            self.hist_list.setViewMode(QListWidget.IconMode)
            self.hist_list.setIconSize(QSize(128, 42))
            self.hist_list.setResizeMode(QListWidget.Adjust)
            self.hist_list.setSpacing(8)
            self.hist_list.setFixedHeight(96)
            self.hist_list.itemClicked.connect(self._hist_clicked)
            for path, ts in hist_bins:
                img = self._load_canvas_bin(path)
                if img.isNull():
                    continue
                import datetime
                when = datetime.datetime.fromtimestamp(ts).strftime('%m-%d %H:%M')
                item = QListWidgetItem(QIcon(QPixmap.fromImage(img)), when)
                item.setData(Qt.UserRole, path)
                item.setToolTip(os.path.basename(path) + '  ' + when)
                self.hist_list.addItem(item)
            hv2.addWidget(self.hist_list)
            v.addWidget(card2)
        v.addStretch(1)

        w = ctx['worker']
        w.uploadProgress.connect(self._on_progress)
        w.uploadFinished.connect(self._on_finished)
        w.connectionChanged.connect(self._on_conn_changed)
        self._sync_buttons()

        last = getattr(self.cfg, 'last_image_path', '') or ''
        if last and os.path.isfile(last):
            self._set_path(last, remember=False)     # 恢复上次选图

    # ---- 选择 / 拖入 ----
    @property
    def _fit(self) -> str:
        return self.fit_combo.currentData() or 'stretch'

    def _set_path(self, path: str, remember: bool = True):
        self._path = path
        self._render_preview()
        self.bar.setValue(0)
        self.result.setText('')
        if remember:
            try:
                self.cfg.last_image_path = path
                self.cfg.save()
            except Exception:
                pass
        self._sync_buttons()

    @staticmethod
    def _scan_history_bins():
        """官方画布缓存目录 → [(path, mtime)] 按时间倒序 (无目录返回空)。"""
        d = r'C:\ProgramData\BlackSharkEquipmentBox\Brb02Image'
        try:
            if not os.path.isdir(d):
                return []
            out = []
            for name in os.listdir(d):
                if name.lower().endswith('.bin'):
                    p = os.path.join(d, name)
                    try:
                        if os.path.getsize(p) == SCREEN_W * SCREEN_H * 2:
                            out.append((p, os.path.getmtime(p)))
                    except OSError:
                        pass
            out.sort(key=lambda x: -x[1])
            return out[:24]                  # 最多 24 张 (UI 密度)
        except Exception:
            return []

    def _hist_clicked(self, item):
        path = item.data(Qt.UserRole)
        if path:
            self._set_path(path)             # .bin 直传通道, fit 无效但预览原样

    def _pick(self):
        from PySide6.QtWidgets import QFileDialog
        start = os.path.dirname(self._path) if self._path else ''
        path, _ = QFileDialog.getOpenFileName(self, '选择图片', start, IMG_FILTER)
        if path:
            self._set_path(path)

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls() and any(
                u.isLocalFile()
                and os.path.splitext(u.toLocalFile())[1].lower() in IMG_EXTS
                for u in ev.mimeData().urls()):
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dropEvent(self, ev):
        for u in ev.mimeData().urls():
            if u.isLocalFile():
                path = u.toLocalFile()
                # 只接受图片扩展名的本地文件 (拖入 .exe/.zip 等一律忽略)
                if os.path.splitext(path)[1].lower() in IMG_EXTS:
                    self._set_path(path)
                    ev.acceptProposedAction()
                    return

    # ---- 预览 ----
    @staticmethod
    def _load_canvas_bin(path: str) -> QImage:
        """官方缓存画布 (121,552B RGB565 **大端**) → QImage。

        ⚠️ Format_RGB16 是小端解读 —— 必须先 byteswap, 否则每像素通道错乱 (花屏)。"""
        try:
            data = open(path, 'rb').read()
        except Exception:
            return QImage()
        if len(data) != SCREEN_W * SCREEN_H * 2:
            return QImage()
        import array
        a = array.array('H', data)
        a.byteswap()                         # BE → LE (Qt Format_RGB16 期望小端)
        img = QImage(bytes(a.tobytes()), SCREEN_W, SCREEN_H, SCREEN_W * 2,
                     QImage.Format_RGB16)
        return img.copy()                    # 脱离 data 缓冲

    def _render_preview(self):
        if self._path and self._path.lower().endswith('.bin'):
            img = self._load_canvas_bin(self._path)
        else:
            img = QImage(self._path) if self._path else QImage()
        self._img_valid = not img.isNull()   # 解码失败 → 置无效, _sync_buttons 据此禁上传
        if img.isNull():
            self.preview.setPixmap(QPixmap())
            self.preview.setText('无法解码该图片 (请用 PNG/JPG)')
            return
        img = fit_image(img, self._fit)
        if FLIP_H or FLIP_V:
            img = img.mirrored(FLIP_H, FLIP_V)
        self.preview.setText('')
        self.preview.setPixmap(QPixmap.fromImage(img))

    def _on_fit_changed(self, _idx):
        try:
            self.cfg.image_fit = self._fit
            self.cfg.save()
        except Exception:
            pass
        if self._path:
            self._render_preview()

    # ---- 上传 ----
    def _sync_buttons(self):
        """按钮态统一计算: 上传按钮要求 已连接 + USB 通道 + 已选图有效 + 非上传中
        (自动上屏执行/排队时也算忙, 防忙态击穿)。"""
        w = self.ctx['worker']
        usb_ok = bool(getattr(w.device, 'connected', False)) \
            and getattr(w.device, 'conn_type', 'usb') == 'usb'
        screen_busy = bool(getattr(w, 'screen_active', False))
        self.btn_up.setEnabled(usb_ok and self._path is not None and self._img_valid
                               and not self._busy and not screen_busy)
        if not getattr(w.device, 'connected', False):
            tip = '设备未连接'
        elif not usb_ok:
            tip = '图片上传仅支持 USB 连接 (蓝牙通道已禁用)'
        elif screen_busy:
            tip = '自动上屏执行中, 完成后再上传'
        else:
            tip = ''
        self.btn_up.setToolTip(tip)

    def _on_conn_changed(self, ok, msg):
        self._sync_buttons()

    def _upload(self):
        if not self._path or self._busy or not self._img_valid:
            return
        w = self.ctx['worker']
        if not w.device.connected:
            self.result.setText('设备未连接 —— 请先在设备页连接散热器。')
            return
        if w.device.conn_type != 'usb':
            # 硬性禁止 (v3.28): 蓝牙图传实测 ~100s/张 且未经验证, 防误点出残图
            self.result.setText('图片上传仅支持 USB 连接 —— 蓝牙通道已禁用。请插上 USB 线后重试。')
            return
        if getattr(w, 'screen_active', False):
            # 自动上屏 (信息卡/情境卡) 正在执行或排队: 设备被独占, 拒绝并发
            self.result.setText('自动上屏执行中, 请等完成后再次上传。')
            return
        self._busy = True
        self.btn_pick.setEnabled(False)
        self.btn_up.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.bar.setValue(0)
        self.result.setText('上传中... (约 16 秒, 请勿操作设备)')
        w.start_image_upload(self._path, flip_h=FLIP_H, flip_v=FLIP_V, fit=self._fit)

    def _cancel(self):
        self.ctx['worker'].cancel_image_upload()
        self.btn_cancel.setEnabled(False)
        self.result.setText('正在取消...')

    def _restore_card(self):
        """切回默认信息卡: 已连接 (USB) 时立即上屏, 否则保存偏好待连接后生效。"""
        w = self.ctx['worker']
        w.restore_info_card()
        if w.uploading:
            self.result.setText('设备正在上传, 完成后自动上信息卡。')
        elif not w.device.connected:
            self.result.setText('已切回信息卡模式 (连接时点本按钮即可上屏)。')
        elif w.device.conn_type == 'ble':
            self.result.setText('已切回信息卡模式 —— 蓝牙通道无法上传, 请改用 USB。')
        else:
            self.result.setText('信息卡上屏中... (约 11 秒)')

    # ---- worker 回调 ----
    def _on_progress(self, done, total):
        self.bar.setMaximum(total)
        self.bar.setValue(done)

    def _on_finished(self, res: dict):
        # 自动上屏 (信息卡/情境卡) 的结果不进手动结果框, 也不复位手动上传忙态/按钮
        # (LOGBUF 已有对应日志, 见 service._run_screen_upload)
        if getattr(self.ctx['worker'], 'last_upload_kind', 'manual') != 'manual':
            self._sync_buttons()
            return
        self._busy = False
        self.btn_pick.setEnabled(True)
        self._sync_buttons()
        self.btn_cancel.setEnabled(False)
        if res.get('ok'):
            self.bar.setValue(self.bar.maximum())
        hist = res.get('status_hist') or {}
        hist_txt = ', '.join(f'{k:#04x}×{v}' for k, v in sorted(hist.items())) or '无'
        lines = [f'结果: {res.get("reason", "")}',
                 f'C6 应答 {res.get("c6_total", 0)} 条 · 状态 {{{hist_txt}}}',
                 f'C4 ACK {res.get("c4_ack_ms", 0.0):.0f}ms · '
                 f'耗时 {res.get("elapsed_ms", 0.0) / 1000:.1f}s']
        fb = res.get('first_bad')
        if fb:
            lines.append(f'首个非 00: C6#{fb[0]} = {fb[1]:#04x}')
        self.result.setText('\n'.join(lines))
