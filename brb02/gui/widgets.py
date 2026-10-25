# -*- coding: utf-8 -*-
"""基础控件: 半圆仪表 / 风扇图标 / 导航图标绘制。"""
from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QIcon, QPainter,
                           QPainterPath, QPen, QPixmap, QPolygonF)
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget


def temp_color(temp, dark=False) -> str:
    if not temp:
        return '#98a2b3'
    if temp > 85:
        return '#ef4444'
    if temp > 75:
        return '#fb923c' if dark else '#f97316'
    return '#ff4b26' if dark else '#2f6df6'


def _pen(color, w):
    pen = QPen(QColor(color), w)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def _screen_dpr(dpr: float = 0.0) -> float:
    if dpr and dpr > 0:
        return float(dpr)
    try:
        from PySide6.QtGui import QGuiApplication
        scr = QGuiApplication.primaryScreen()
        if scr is not None:
            return float(scr.devicePixelRatio() or 1.0)
    except Exception:
        pass
    return 1.0


def draw_caption_glyph(kind: str, size: int, color, pen_w: float, dpr: float = 0.0) -> QPixmap:
    """窗口按钮字形 (矢量绘制, 线宽可控, 原生物理像素 DPR 1:1 命中): min 横线 / max 方框 / close 叉。"""
    dpr = _screen_dpr(dpr)
    px = max(1, int(round(size * dpr)))
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = float(px)
    p.setPen(QPen(QColor(color), pen_w * dpr, Qt.SolidLine, Qt.RoundCap))
    p.setBrush(Qt.NoBrush)
    m = s * 0.20                      # 内边距
    if kind == 'min':
        y = s * 0.58
        p.drawLine(QPointF(m, y), QPointF(s - m, y))
    elif kind == 'max':
        p.drawRect(QRectF(m, m, s - 2 * m, s - 2 * m))
    else:                              # close
        p.drawLine(QPointF(m, m), QPointF(s - m, s - m))
        p.drawLine(QPointF(s - m, m), QPointF(m, s - m))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def draw_icon_pixmap(kind: str, size: int, color: str,
                     dpr: float = 0.0, scale: float = 1.0) -> QPixmap:
    """Lucide 风格线性图标 (stroke 1.85 近似, 按屏幕 DPR 直接光栅化到物理像素, 零二次缩放)。"""
    dpr = _screen_dpr(dpr)
    px = max(1, int(round(size * dpr)))
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = float(px)
    p.translate(s / 2.0, s / 2.0)
    u = (s / 24.0) * float(scale)          # lucide 24 网格单位 (支持中心缩放 pop, 不改变画布尺寸)
    p.setPen(_pen(color, 1.85 * u))
    p.setBrush(Qt.NoBrush)
    k = kind
    if k == 'status':                      # layout-grid: 4 squares
        for dx in (-1, 1):
            for dy in (-1, 1):
                x, y = dx * 4.5 * u, dy * 4.5 * u
                w2 = 6 * u if (dx * dy > 0) else 6 * u
                p.drawRoundedRect(QRectF(x - w2 / 2, y - w2 / 2, w2, w2), 1.2 * u, 1.2 * u)
    elif k == 'buttons':                   # gamepad: 手柄轮廓 + 十字键 + 两颗按钮
        p.drawRoundedRect(QRectF(-9 * u, -5.5 * u, 18 * u, 11 * u), 4 * u, 4 * u)
        p.drawLine(QPointF(-5.5 * u, -1.8 * u), QPointF(-5.5 * u, 2.2 * u))
        p.drawLine(QPointF(-7.5 * u, 0.2 * u), QPointF(-3.5 * u, 0.2 * u))
        p.drawEllipse(QPointF(5 * u, -1.2 * u), 1.15 * u, 1.15 * u)
        p.drawEllipse(QPointF(7.2 * u, 1.6 * u), 1.15 * u, 1.15 * u)
    elif k == 'bluetooth':
        p.drawLine(QPointF(-2 * u, -8 * u), QPointF(4.5 * u, -3.5 * u))
        p.drawLine(QPointF(4.5 * u, -3.5 * u), QPointF(-4 * u, 3 * u))
        p.drawLine(QPointF(-4 * u, 3 * u), QPointF(4 * u, 8 * u))
        p.drawLine(QPointF(-2 * u, 8 * u), QPointF(-2 * u, -8 * u))
    elif k == 'wifi_off':
        p.drawEllipse(QPointF(0, 2 * u), 8 * u, 8 * u)
        p.drawLine(QPointF(-9 * u, -8 * u), QPointF(9 * u, 9 * u))
        p.drawEllipse(QPointF(0, 2 * u), 1.4 * u, 1.4 * u)
    elif k == 'sparkles':
        path = QPainterPath()
        path.moveTo(0, -8 * u)
        path.cubicTo(1.2 * u, -2 * u, 3 * u, -0.5 * u, 7 * u, 0)
        path.cubicTo(3 * u, 1 * u, 1.2 * u, 2.5 * u, 0, 8 * u)
        path.cubicTo(-1.2 * u, 2.5 * u, -3 * u, 1 * u, -7 * u, 0)
        path.cubicTo(-3 * u, -0.5 * u, -1.2 * u, -2 * u, 0, -8 * u)
        p.drawPath(path)
        p.drawEllipse(QPointF(6.5 * u, -6.5 * u), 1.1 * u, 1.1 * u)
    elif k == 'thermometer':
        p.drawLine(QPointF(0, -8 * u), QPointF(0, 2 * u))
        p.drawEllipse(QPointF(0, 5 * u), 3 * u, 3 * u)
        p.drawLine(QPointF(3 * u, 5 * u), QPointF(3 * u, -6 * u))
        p.drawLine(QPointF(0, -8 * u), QPointF(0, -6 * u))
    elif k == 'fan':
        p.drawEllipse(QPointF(0, 0), 1.4 * u, 1.4 * u)
        for i in range(3):
            a = math.radians(i * 120 - 90)
            path2 = QPainterPath(QPointF(math.cos(a) * 1.4 * u, math.sin(a) * 1.4 * u))
            path2.cubicTo(QPointF(math.cos(a - 0.5) * 6 * u, math.sin(a - 0.5) * 6 * u),
                          QPointF(math.cos(a + 0.9) * 8 * u, math.sin(a + 0.9) * 8 * u),
                          QPointF(math.cos(a + 1.4) * 3 * u, math.sin(a + 1.4) * 3 * u))
            p.drawPath(path2)
    elif k == 'cpu':
        p.drawRoundedRect(QRectF(-5 * u, -5 * u, 10 * u, 10 * u), 1.5 * u, 1.5 * u)
        p.drawRoundedRect(QRectF(-2.2 * u, -2.2 * u, 4.4 * u, 4.4 * u), 0.8 * u, 0.8 * u)
        for d in (-3.2 * u, 0, 3.2 * u):
            p.drawLine(QPointF(d, -5 * u), QPointF(d, -8 * u))
            p.drawLine(QPointF(d, 5 * u), QPointF(d, 8 * u))
            p.drawLine(QPointF(-5 * u, d), QPointF(-8 * u, d))
            p.drawLine(QPointF(5 * u, d), QPointF(8 * u, d))
    elif k == 'gpu':
        p.drawRoundedRect(QRectF(-8 * u, -6 * u, 16 * u, 12 * u), 1.5 * u, 1.5 * u)
        p.drawEllipse(QPointF(-2 * u, 0), 3.2 * u, 3.2 * u)
        p.drawEllipse(QPointF(4.5 * u, 0), 2 * u, 2 * u)
        p.drawLine(QPointF(-8 * u, 6 * u), QPointF(-8 * u, 8.5 * u))
        p.drawLine(QPointF(2 * u, 6 * u), QPointF(2 * u, 8.5 * u))
    elif k == 'curve':                     # line-chart
        p.drawPolyline([QPointF(-9 * u, 8 * u), QPointF(-4 * u, 2 * u),
                        QPointF(1 * u, 5 * u), QPointF(9 * u, -7 * u)])
        p.drawRect(QRectF(-9 * u, -9 * u, 18 * u, 18 * u))
    elif k == 'control':                   # settings-2 gear
        p.drawEllipse(QPointF(0, 0), 3.2 * u, 3.2 * u)
        path = QPainterPath()
        import math as _m
        pts = []
        for i in range(8):
            a = _m.radians(i * 45 + 22.5)
            r1, r2 = 5.6 * u, 8.6 * u
            pts.append(QPointF(_m.cos(a) * r1, _m.sin(a) * r1))
            pts.append(QPointF(_m.cos(a + 0.28) * r2, _m.sin(a + 0.28) * r2))
            pts.append(QPointF(_m.cos(a + 0.85) * r2, _m.sin(a + 0.85) * r2))
        poly = QPolygonF(pts)
        p.drawPolygon(poly)
    elif k == 'devices':                   # boxes
        p.drawRect(QRectF(-8 * u, -8 * u, 7 * u, 7 * u))
        p.drawRect(QRectF(1 * u, -8 * u, 7 * u, 7 * u))
        p.drawRect(QRectF(-8 * u, 1 * u, 7 * u, 7 * u))
        p.drawRect(QRectF(1 * u, 1 * u, 7 * u, 7 * u))
    elif k == 'about':                     # info
        p.drawEllipse(QPointF(0, 0), 9 * u, 9 * u)
        p.drawLine(QPointF(0, -1 * u), QPointF(0, 4 * u))
        p.drawPoint(QPointF(0, -4.5 * u))
    elif k == 'screen':                    # monitor + picture
        p.drawRoundedRect(QRectF(-9 * u, -7.5 * u, 18 * u, 13 * u), 1.4 * u, 1.4 * u)
        p.drawLine(QPointF(-3 * u, 5.5 * u), QPointF(3 * u, 5.5 * u))
        p.drawLine(QPointF(0, 5.5 * u), QPointF(0, 8.5 * u))
        p.drawEllipse(QPointF(-3.5 * u, -3 * u), 1.1 * u, 1.1 * u)
        mpath = QPainterPath(QPointF(-6.5 * u, 4.5 * u))
        mpath.lineTo(QPointF(-1.5 * u, -1 * u))
        mpath.lineTo(QPointF(2.5 * u, 2.5 * u))
        mpath.lineTo(QPointF(6.5 * u, -2 * u))
        mpath.lineTo(QPointF(6.5 * u, 4.5 * u))
        mpath.closeSubpath()
        p.drawPath(mpath)
    p.end()
    pm.setDevicePixelRatio(dpr)
    pm._icon_meta = (kind, size, color, dpr)
    return pm


_NAV_ICON_CACHE: dict[tuple, QIcon] = {}


def nav_icon(kind: str, color: str, size=18, dpr: float = 0.0) -> QIcon:
    dpr = _screen_dpr(dpr)
    key = (kind, color, int(size), round(dpr, 2))
    ic = _NAV_ICON_CACHE.get(key)
    if ic is None:
        ic = QIcon(draw_icon_pixmap(kind, size, color, dpr=dpr))
        _NAV_ICON_CACHE[key] = ic
    return ic


def _ui_font(size: int, bold: bool = False) -> QFont:
    """Manrope UI 字体 (CJK 自动回退微软雅黑), 数字启用等宽特性"""
    f = QFont('Manrope', size)
    f.setBold(bold)
    try:
        f.setFeature(QFont.Feature.TabularNums, 1)     # Qt 6.7+
    except Exception:
        pass
    return f


class Badge(QWidget):
    """标题栏状态徽章: 图标 + 文字 (对齐原版 StatusBadges)。"""

    def __init__(self, text='', icon_kind=None, dark=False, active=False, parent=None):
        super().__init__(parent)
        self.dark = dark
        self.active = active
        self.icon_kind = icon_kind
        self.setObjectName('TitleBadgeActive' if active else 'TitleBadge')
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 7, 14, 7)
        h.setSpacing(7)
        self.icon_lbl = QLabel()
        self.icon_lbl.setFixedSize(15, 15)
        self.icon_lbl.setStyleSheet('background: transparent;')
        if icon_kind:
            self.icon_lbl.setPixmap(draw_icon_pixmap(icon_kind, 15, self._color()))
            h.addWidget(self.icon_lbl)
        self.text_lbl = QLabel(text)
        # 必须显式上色: QSS 的 QLabel#TitleBadge 选择器匹配不到本控件 (是 QWidget),
        # 不写 color 文字会继承应用调色板的黑色, 深色标题栏下不可见
        self.text_lbl.setStyleSheet(
            f'background: transparent; font-size: 13px; font-weight: 600; color: {self._color()};')
        self._last = (text, active, icon_kind, dark)
        self._last_style = (active, icon_kind, dark)
        h.addWidget(self.text_lbl)

    def _color(self):
        if self.active:
            return '#ff4b26' if self.dark else '#2f6df6'
        return '#98a2b3'

    def set_state(self, text: str, active: bool, icon_kind: str | None = None, dark: bool | None = None):
        if dark is not None:
            self.dark = dark
        kind = icon_kind or self.icon_kind
        cur = (text, active, kind, self.dark)
        if cur == getattr(self, '_last', None):
            return
        self._last = cur
        if self.text_lbl.text() != text:
            self.text_lbl.setText(text)
        style_key = (active, kind, self.dark)
        if style_key != getattr(self, '_last_style', None):
            self._last_style = style_key
            self.active = active
            self.icon_kind = kind
            self.setObjectName('TitleBadgeActive' if active else 'TitleBadge')
            self.icon_lbl.setVisible(bool(kind))
            if kind:
                self.icon_lbl.setPixmap(draw_icon_pixmap(kind, 15, self._color()))
            self.text_lbl.setStyleSheet(
                f'background: transparent; font-size: 13px; font-weight: 600; color: {self._color()};')
            self.style().unpolish(self)
            self.style().polish(self)

    def set_dark(self, dark: bool):
        """主题切换后按上次状态重配色 (不传 dark 的 set_state 调用方不再残留旧主题色)。"""
        last = getattr(self, '_last', (self.text_lbl.text(), self.active, self.icon_kind, self.dark))
        self.set_state(last[0], last[1], last[2], dark)


class ConnPill(QWidget):
    """连接状态胶囊: 圆点 + 文字。已连接时圆点外圈 1s 呼吸扩散 (手册 §9 pulse-ring:
    scale 1→1.3 + 淡出, 1s cubic-bezier(.4,0,.6,1) infinite), 离线为静态红点。"""

    def __init__(self, dark=False, parent=None):
        super().__init__(parent)
        self.dark = dark
        self.conn = False
        self.text = '离线'
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(80)             # 1s 一轮的呼吸圈, 12.5fps 足够顺
        self._timer.timeout.connect(self._tick)
        self.setFixedHeight(24)
        self._refresh_size()

    def _colors(self):
        """胶囊底/文字/圆点色: 在线走主题 primary, 离线红。"""
        if self.conn:
            prim = '#ff4b26' if self.dark else '#2f6df6'
            c = QColor(prim)
            return prim, QColor(c.red(), c.green(), c.blue(), 31), c
        return '#ef4444', QColor(239, 68, 68, 26), QColor('#ef4444')

    def set_state(self, conn: bool, dark: bool | None = None):
        self.conn = bool(conn)
        if dark is not None:
            self.dark = dark
        self.text = '已连接' if self.conn else '离线'
        self._refresh_size()
        self.update()
        from . import motion
        if self.conn and not motion.reduced_motion():
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()

    def set_dark(self, dark: bool):
        self.set_state(self.conn, dark)

    def _tick(self):
        self._phase = (self._phase + 0.05) % 1.0
        self.update()

    def _refresh_size(self):
        f = _ui_font(12)
        f.setWeight(QFont.Weight.DemiBold)
        fm = QFontMetrics(f)
        self.setFixedWidth(14 + 16 + 8 + fm.horizontalAdvance(self.text) + 14)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        text_c, bg_c, dot_c = self._colors()
        p.setPen(Qt.NoPen)
        p.setBrush(bg_c)
        p.drawRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)
        cx, cy = 14 + 8, h / 2
        if self.conn and self._timer.isActive():
            # 呼吸圈: 由内向外放大到 1.3× 并淡出
            rr = 4.0 * (1.0 + 0.3 * self._phase)
            ring = QColor(dot_c)
            ring.setAlpha(int(96 * (1.0 - self._phase)))
            p.setBrush(ring)
            p.drawEllipse(QPointF(cx, cy), rr, rr)
        p.setBrush(dot_c)
        p.drawEllipse(QPointF(cx, cy), 4.0, 4.0)
        f = _ui_font(12)
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(QColor(text_c))
        p.drawText(QRectF(14 + 16 + 8, 0, w - 14 - 16 - 8, h), Qt.AlignVCenter | Qt.AlignLeft, self.text)


class SemiGauge(QWidget):
    """半圆仪表: 上半圆弧 (180°→0°), 标题带图标, 数字居于弧内。

    指针追赶 = 动效手册 §9: 600ms 签名缓动 (cubic-bezier(0.22,1,0.36,1)),
    数据每 tick 到达时从**当前值**重启 —— 与 FanControl 仪表环的
    `transition: stroke-dashoffset 600ms` 重启语义一致。"""

    def __init__(self, title: str, unit: str, dark=False, icon_kind=None, parent=None):
        super().__init__(parent)
        self.title = title
        self.unit = unit
        self.dark = dark
        self.icon_kind = icon_kind
        self.value = None
        self.display_text = '--'
        self.status_word = ''
        self.color = '#ff4b26' if dark else '#2f6df6'   # 主题 primary (深橙/浅蓝)
        self._anim_value = 0.0
        self.setMinimumHeight(170)
        # 骨架屏 (手册 §9): 温度首帧 5-15s 才有值 (LHM 初始化), 这段时间
        # 数字区画脉冲占位块而不是干等的 '--'。2s 周期, 对应 CSS animate-pulse。
        self._skel_timer = QTimer(self)
        self._skel_timer.setInterval(125)          # 8fps: 脉冲周期 2s, 更快无感
        self._skel_timer.timeout.connect(self.update)
        self._skel_sync()

    def set_value(self, value: float | None, display_text: str, status_word: str, color: str):
        self.value = value
        self.display_text = display_text
        self.status_word = status_word
        self.color = color
        self._skel_sync()
        self._chase()

    def _skel_sync(self):
        """有值停脉冲, 无值启动 (减少动态效果时不启动, 画静态块)。"""
        from . import motion
        if self.value is None and not motion.reduced_motion():
            if not self._skel_timer.isActive():
                self._skel_timer.start()
        else:
            self._skel_timer.stop()
        self.update()

    def _chase(self):
        from . import motion
        target = self.value or 0.0
        start = self._anim_value

        self._chase_skip = False

        def _tick(v):
            self._anim_value = start + (target - start) * v
            # 绘制二抽一 (≤30fps): 数值推进不变, 只减重绘 —— 仪表住在带阴影
            # effect 的卡里, 每次 update 都是整卡离屏重合成
            self._chase_skip = not self._chase_skip
            if v >= 1.0 or not self._chase_skip:
                self.update()

        motion.chase(self, start, target, motion.MS_GAUGE, _tick)

    def set_color(self, color: str):
        """只更新强调色并重绘 (主题切换时由 StatusPage 同步调用, 不等下一 tick)。"""
        self.color = color
        self.update()

    def set_dark(self, dark: bool):
        """主题切换: 更新 dark 并重绘 (弧色由 StatusPage._reapply_gauge_colors 同步
        重刷 —— 此前无 set_dark, 切主题后弧色滞留旧主题 primary 直到下一 tick)。"""
        self.dark = dark
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        t = QColor('#1a2029' if self.dark else '#edf2f9')
        muted = QColor('#98a2b3' if self.dark else '#66758a')
        cx = w / 2
        arc_cy = h - 26                    # 圆心在下方, 弧是上半圆
        r = min(w / 2 - 26, (h - 78) / 2 + 20)
        # 标题 + 图标
        f = _ui_font(9)
        p.setFont(f)
        p.setPen(muted)
        if self.icon_kind:
            ic_color = '#2f6df6' if not self.dark else '#ff4b26'
            ic_key = (self.icon_kind, ic_color)
            if getattr(self, '_cached_ic_key', None) != ic_key:
                self._cached_ic_key = ic_key
                self._cached_ic_pm = draw_icon_pixmap(self.icon_kind, 14, ic_color)
            p.drawPixmap(int(w / 2 - p.fontMetrics().horizontalAdvance(self.title) / 2 - 21),
                         10, self._cached_ic_pm)
        p.drawText(QRectF(0, 8, w, 18), Qt.AlignCenter, self.title)
        # 背景弧: 上半圆 = 从 180° 顺时针走 180° (Qt: 负跨度)
        rect = QRectF(cx - r, arc_cy - r, r * 2, r * 2)
        p.setPen(_pen(t, 10))
        p.drawArc(rect, 180 * 16, -180 * 16)
        ratio = max(0.0, min(1.0, self._anim_value / 100.0))
        if ratio > 0.004:
            p.setPen(_pen(QColor(self.color), 10))
            p.drawArc(rect, 180 * 16, int(-180 * 16 * ratio))
        # 数字 (弧内, Manrope 半粗 + 等宽数字); 无数据时画脉冲骨架块 (§9)
        if self.value is None:
            from . import motion
            import time as _t
            if motion.reduced_motion():
                alpha = 0.75
            else:
                ph = (_t.monotonic() % 2.0) / 2.0        # 2s 周期 1→0.5→1
                alpha = 0.75 - 0.25 * math.cos(2 * math.pi * ph)
            bw = min(96.0, w * 0.42)
            bh = 24.0
            bx = (w - bw) / 2
            by = arc_cy - r * 0.62 + 5
            skel = QColor('#1a2029' if self.dark else '#e6ebf3')
            skel.setAlphaF(alpha)
            p.setPen(Qt.NoPen)
            p.setBrush(skel)
            p.drawRoundedRect(QRectF(bx, by, bw, bh), 7.0, 7.0)
        else:
            p.setPen(QColor(self.color))
            f2 = _ui_font(21, True)            # 中心值: 21px 粗体 (28px 在本仪表比例下偏大)
            p.setFont(f2)
            p.drawText(QRectF(0, arc_cy - r * 0.64, w, 32), Qt.AlignCenter, self.display_text)
        f3 = _ui_font(8)
        p.setFont(f3)
        p.drawText(QRectF(0, arc_cy - r * 0.62 + 32, w, 15), Qt.AlignCenter, self.unit)
        if self.status_word:
            p.drawText(QRectF(0, arc_cy + 8, w, 15), Qt.AlignCenter, self.status_word)


class FanIcon(QWidget):
    """旋转风扇图标 (转速越快转得越快)。"""

    def __init__(self, size=20, color='#2f6df6', parent=None, bg=None):
        super().__init__(parent)
        self.angle = 0.0
        self.color = color
        # bg 已废弃 (2026-10-25 定稿): 不透明底 + 圆角填充会留四个未定义角
        # (WA_OpaquePaintEvent 要求全区域涂满, 圆角必然违反) —— 两轮可见 bug
        # 的根源。风扇图标回到透明绘制原版; 整窗风暴由 50ms 节流独立解决。
        self._bg = None
        self.setFixedSize(size, size)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._rpm = 0

    def set_rpm(self, rpm: int, max_rpm: int = 4800):
        self._rpm = rpm
        pct = max(0.0, min(1.0, rpm / max_rpm)) if max_rpm else 0
        period = 0 if pct <= 0 else (0.48 if pct >= 0.9 else 0.72 if pct >= 0.7 else 1.0 if pct >= 0.45 else 1.35)
        # ≥50ms/帧 (perf_log 实证: 33ms 的半透明小控件重绘会以同节奏拖整窗刷新,
        # 连接设备后从启动持续到退出 —— "整体一直卡"的元凶)
        from . import motion
        if getattr(self, '_fast_mode', False) or motion.reduced_motion():
            self._timer.stop()               # 流畅优先/无障碍: 图标静止
            return
        if period and not self._timer.isActive():
            self._timer.start(max(50, int(period * 1000 / 24)))
        elif not period:
            self._timer.stop()

    def _tick(self):
        self.angle = (self.angle + 15) % 360
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = min(self.width(), self.height())
        p.translate(s / 2, s / 2)
        p.rotate(self.angle)
        p.setPen(_pen(self.color, max(1.4, s * 0.09)))
        r = s * 0.30
        for i in range(3):
            a = math.radians(i * 120)
            p.drawLine(QPointF(math.cos(a) * r * 0.15, math.sin(a) * r * 0.15),
                       QPointF(math.cos(a - 0.6) * r, math.sin(a - 0.6) * r))
        p.drawEllipse(QPointF(0, 0), s * 0.045, s * 0.045)


class Toggle(QWidget):
    """iOS 风格开关 (对应原版 ToggleSwitch)。"""

    toggled = None

    def __init__(self, checked=False, color='#2f6df6', parent=None):
        super().__init__(parent)
        self.checked = checked
        self.color = QColor(color)
        self._pos = 1.0 if checked else 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate)
        self.setFixedSize(46, 26)
        self.setCursor(Qt.PointingHandCursor)

    def isChecked(self):
        return self.checked

    def setChecked(self, on: bool):
        if on != self.checked:
            self.checked = on
            if not self._timer.isActive():
                self._timer.start(14)

    def set_dark(self, dark: bool):
        """主题切换: 轨道色跟随主题主色 (深色橙 / 浅色蓝)。apply_theme 全局遍历调用。"""
        self.color = QColor('#ff4b26' if dark else '#2f6df6')
        self.update()

    def mousePressEvent(self, ev):
        self.setChecked(not self.checked)
        if self.toggled:
            self.toggled(self.checked)

    def _animate(self):
        target = 1.0 if self.checked else 0.0
        self._pos += (target - self._pos) * 0.35
        if abs(target - self._pos) < 0.02:
            self._pos = target
            self._timer.stop()
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        track = QColor(self.color) if self._pos > 0.02 else QColor('#d5dae3')
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)
        knob_r = h - 8
        x = 4 + (w - h) * self._pos
        p.setBrush(QColor('white'))
        p.drawEllipse(QPointF(x + knob_r / 2, h / 2), knob_r / 2, knob_r / 2)


def style_combo_popup(combo, dark: bool = False):
    """下拉弹出层: 无边框 + 圆角 + 主题化配色。

    配色直接烤进容器/视图的控件级样式表 —— Qt 对 Popup 容器
    (QComboBoxPrivateContainer) 传全局 QSS 不可靠, 深色下曾露出白色系统底。
    顺序必须先设窗口旗标再设 WA_TranslucentBackground —— 反过来会被
    setWindowFlags 的窗口重建冲掉透明属性。主题切换后需对全部下拉重调。"""
    from .theme import DARK, LIGHT
    t = DARK if dark else LIGHT
    view = combo.view()
    win = view.window()
    win.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
    win.setAttribute(Qt.WA_TranslucentBackground, True)
    win.setAttribute(Qt.WA_NoSystemBackground, True)
    view.setStyleSheet(
        f'QListView {{ background: {t["card"]}; color: {t["fg"]};'
        f' border: 1px solid {t["border"]}; border-radius: 14px;'
        f' padding: 6px; outline: none; }}'
        f'QListView::item {{ min-height: 34px; border-radius: 9px;'
        f' padding: 4px 10px; margin: 1px 2px; color: {t["fg"]};'
        f' background: transparent; }}'
        f'QListView::item:selected, QListView::item:hover {{'
        f' background: {t["chip_bg"]}; color: {t["primary"]}; }}')
    win.setStyleSheet(f'background: {t["card"]}; border-radius: 14px;')


def make_card(title: str | None = None, hint: str | None = None):
    """卡片工厂 —— 2026-10-25 定稿: 恢复 v0.1.20 原版 QGraphicsDropShadowEffect。

    三轮自绘/烘焙/九宫格方案全部翻车 (浓度/错位/DPI 切片), 根因都是
    "阴影必须画在控件边界内"。effect 版没有这个问题且视觉从未被抱怨;
    当初的性能问题出在常驻动画 (33ms 风扇图标等) 反复触发它 —— 现在
    常驻风暴已清 (perf_log: 空闲期整窗刷新 ~1次/秒), effect 只在
    真正的界面变化时付一次成本。"""
    from PySide6.QtWidgets import QFrame, QVBoxLayout
    frame = QFrame()
    frame.setProperty('card', True)
    # 手册 §4.1: FanControl 卡片 = shadow-sm shadow-black/5 (1px 5% 黑) —— 本质
    # 平面+描边。此前的 32px 大模糊阴影是本应用自己的设计, 不是复刻对象;
    # 它是 QGraphicsEffect 管线的唯一宿主, 在慢机上造成首绘肥帧/常驻放大,
    # perf_log 多轮实证。摘掉后 effect 在本应用绝迹, 卡片=纯 QSS 绘制。
    # hover = QSS border 换色 (§4.1 卡片 hover 本就是 border/shadow 为主)。
    v = QVBoxLayout(frame)
    v.setContentsMargins(22, 18, 22, 20)
    v.setSpacing(12)
    title_lbl = None
    if title:
        title_lbl = QLabel(title)
        title_lbl.setObjectName('CardTitle')
        v.addWidget(title_lbl)
    if hint:
        h = QLabel(hint)
        h.setObjectName('CardHint')
        h.setWordWrap(True)
        v.addWidget(h)
    return frame, title_lbl, v
