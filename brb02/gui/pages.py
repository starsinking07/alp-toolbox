# -*- coding: utf-8 -*-
"""页面: 状态 / 曲线 / 设置(控制) / 设备 / 关于 — 对齐 FanControl 真机截图视觉。"""
from __future__ import annotations

import colorsys
import sys
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QPlainTextEdit, QPushButton, QApplication, QSlider,
    QSizePolicy, QSpinBox, QStackedWidget,
    QVBoxLayout, QWidget,
)

from .curve_editor import CurveEditor, TEMPS, DEFAULT_PCT
from brb02 import protocol
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
        icon_wrap.setStyleSheet('background:#e8eefc; border-radius:18px;')
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
        注: 当前工具箱固定使用制冷档位 1 (0x24 level), 档位对功耗的影响待多档位联动接入。"""
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
            self.lbl_mode.setText(f'手动模式 · 当前固定 {c.get("rpm")} RPM')
        self.stat_lbls['控制模式'].setText('智能变频' if smart else '手动模式')
        if c:
            self.stat_lbls['工作模式'].setText('曲线目标' if smart else '固定转速')
            self.stat_lbls['目标转速'].setText(f'{c.get("rpm")} RPM')
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
        for frac, lbl in ((0.0, '0'), (0.25, '1200'), (0.5, '2400'), (0.75, '3600'), (1.0, '4800')):
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
        self.gear_btns[1].setChecked(True)
        gv.addLayout(gear_row)
        self.slider12 = GearSlider()
        self.slider12.levelSelected = self._level_selected
        gv.addWidget(self.slider12)
        v.addWidget(gears)

        # 曲线编辑器
        editor, _, ev_ = _card(None)
        self.curve = CurveEditor(self.dark)
        self.curve.changed = self._curve_changed
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

    def _level_selected(self, k: int):
        gear, sub = k // 3, k % 3
        rpm = int(self._gear_rpm(gear) * GEAR_SUB[sub])
        self.ctx['worker'].apply_fixed_rpm(rpm)
        self.ctx['worker'].gear_light_hook(gear)
        self.ctx['cfg'].fixed_rpm = rpm
        self.ctx['cfg'].save()

    def _smart_toggled(self, on):
        self.ctx['cfg'].curve_enabled = on
        self.ctx['cfg'].save()
        self.ctx['main'].sync_curve_toggle(on)

    def sync_smart(self, on):
        self.tgl_smart2.blockSignals(True)
        self.tgl_smart2.setChecked(on)
        self.tgl_smart2.blockSignals(False)

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
        self.cmb_smooth = QComboBox()
        self.cmb_smooth.addItems(['1 · 即时跟随', '2 · 弱平滑', '3 · 默认', '5 · 较强平滑', '10 · 强平滑'])
        self.cmb_smooth.setCurrentIndex({1: 0, 2: 1, 3: 2, 5: 3, 10: 4}.get(cfg.temp_smoothing, 2))
        self.cmb_smooth.currentIndexChanged.connect(self._smooth_changed)
        cv2.addWidget(_setting_row('温度平滑度', 'EMA 指数加权: 越大越稳, 越小越跟手', '🌊', self.cmb_smooth))
        self.tgl_spike = Toggle(cfg.spike_filter)
        self.tgl_spike.toggled = self._spike_changed
        cv2.addWidget(_setting_row('温度尖峰过滤', '忽略单次异常跳温, 避免误触发控制', '🛡️', self.tgl_spike))
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
        self.tgl_hotkey = Toggle(cfg.hotkeys_enabled)
        self.tgl_hotkey.toggled = self._hotkey_toggled
        cv3.addWidget(_setting_row('全局快捷键', 'Ctrl+Alt+F1 循环挡位 · Ctrl+Alt+F2 智能变频', '⌨️', self.tgl_hotkey))
        self.tgl_autostart = Toggle(self._get_autostart())
        self.tgl_autostart.toggled = self._autostart_toggled
        cv3.addWidget(_setting_row('开机自启动', '以管理员身份运行可读取 CPU 温度', '🚀', self.tgl_autostart))
        sv.addWidget(card3)
        sv.addStretch(1)

        return [('设备设置', dev), ('风扇控制', fan), ('灯效', light), ('系统设置', sysw)]

    # ---- 槽 ----
    def sync_smart(self, on):
        self.tgl_curve.blockSignals(True)
        self.tgl_curve.setChecked(on)
        self.tgl_curve.blockSignals(False)

    def on_info(self, info: dict):
        c = info.get('cooling')
        if c:
            self.lbl_speed.setText(f"实时速度  {self.ctx['main'].last_rpm} RPM")
        self.lbl_mode2.setText(f"控制模式  {'智能变频' if self.ctx['cfg'].curve_enabled else '手动'}")
        conn = self.ctx['main'].is_connected
        self.lbl_dev_state.setText('已连接' if conn else '未连接')

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

    def _smooth_changed(self, idx):
        self.ctx['cfg'].temp_smoothing = [1, 2, 3, 5, 10][idx]
        self.ctx['cfg'].save()

    def _spike_changed(self, on):
        self.ctx['cfg'].spike_filter = on
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

    def _autoswitch_changed(self, on):
        self.ctx['cfg'].auto_switch = on
        self.ctx['cfg'].save()

    def _theme_changed(self, idx):
        self.ctx['cfg'].dark = (idx == 1)
        self.ctx['cfg'].save()
        self.ctx['main'].apply_theme(self.ctx['cfg'].dark)

    def _hotkey_toggled(self, on):
        self.ctx['cfg'].hotkeys_enabled = on
        self.ctx['cfg'].save()
        self.ctx['main'].apply_hotkey_setting(on)

    def _get_autostart(self) -> bool:
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKCU, r'Software\Microsoft\Windows\CurrentVersion\Run')
            winreg.QueryValueEx(k, 'Brb02Toolbox')
            return True
        except Exception:
            return False

    def _autostart_toggled(self, on):
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKCU, r'Software\Microsoft\Windows\CurrentVersion\Run',
                               0, winreg.KEY_SET_VALUE)
            if on:
                winreg.SetValueEx(k, 'Brb02Toolbox', 0, winreg.REG_SZ,
                                  f'"{sys.executable}" "{sys.argv[0]}"')
            else:
                try:
                    winreg.DeleteValue(k, 'Brb02Toolbox')
                except FileNotFoundError:
                    pass
        except Exception as e:
            print('[autostart]', e)


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
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 4, 6, 6)
        v.setSpacing(16)
        card, _, cv = _card('关于')
        name = QLabel('黑鲨风神Pro 工具箱')
        name.setStyleSheet('font-size:22px; font-weight:800; background:transparent;')
        cv.addWidget(name)
        desc = QLabel('BRB02 磁吸散热器第三方控制工具 · USB / 蓝牙 BLE 双通道 · 协议全部实机逆向验证')
        desc.setWordWrap(True)
        desc.setObjectName('CardHint')
        cv.addWidget(desc)
        ver = QLabel('UI 设计参考: FanControlPortable (Eureka-o, MIT License) · 协议为本机独立逆向成果')
        ver.setObjectName('CardHint')
        cv.addWidget(ver)
        v.addWidget(card)

        # 运行日志 (方便反馈问题)
        logcard, _, lv = _card('运行日志', '程序异常与连接事件 · 反馈问题时请点击"复制全部"发给开发者')
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(280)
        self.log_view.setStyleSheet(
            'font-family: "Cascadia Mono", Consolas, monospace; font-size: 11px;'
            ' background: rgba(127,127,127,0.05); border: none; border-radius: 10px;')
        lv.addWidget(self.log_view)
        brow = QHBoxLayout()
        b_copy = QPushButton('复制全部')
        b_copy.clicked.connect(self._copy_log)
        b_clear = QPushButton('清空')
        b_clear.clicked.connect(self._clear_log)
        brow.addWidget(b_copy)
        brow.addWidget(b_clear)
        brow.addStretch(1)
        lv.addLayout(brow)
        v.addWidget(logcard, 1)

        self._log_version = -1
        self.log_timer = QTimer(self)
        self.log_timer.timeout.connect(self._refresh_log)
        self.log_timer.start(1000)
        v.addStretch(0)

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

    def _clear_log(self):
        from brb02.logbuf import LOGBUF as _LB
        _LB.clear()
