# -*- coding: utf-8 -*-
"""基础控件: 半圆仪表 / 风扇图标 / 导航图标绘制。"""
from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget


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


def _pen(color, w):
    pen = QPen(QColor(color), w)
    pen.setCapStyle(Qt.RoundCap)
    return pen


def draw_caption_glyph(kind: str, size: int, color, pen_w: float) -> QPixmap:
    """窗口按钮字形 (矢量绘制, 线宽可控): min 横线 / max 方框 / close 叉。

    替代文字字形 (—/□/✕): 文字笔画粗细由字体决定, 档位切换无效。"""
    pm = QPixmap(size * 4, size * 4)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = size * 4
    p.setPen(QPen(QColor(color), pen_w * 4, Qt.SolidLine, Qt.RoundCap))
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
    return pm
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def draw_icon_pixmap(kind: str, size: int, color: str) -> QPixmap:
    """Lucide 风格线性图标 (stroke 1.85 近似)。kind: status/curve/control/devices/about"""
    pm = QPixmap(size * 4, size * 4)      # 4x 超采样抗锯齿
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = size * 4
    p.translate(s / 2, s / 2)
    u = s / 24                             # lucide 24 网格单位
    p.setPen(_pen(color, 1.85 * u))
    p.setBrush(Qt.NoBrush)
    k = kind
    if k == 'status':                      # layout-grid: 4 squares
        for dx in (-1, 1):
            for dy in (-1, 1):
                x, y = dx * 4.5 * u, dy * 4.5 * u
                w2 = 6 * u if (dx * dy > 0) else 6 * u
                p.drawRoundedRect(QRectF(x - w2 / 2, y - w2 / 2, w2, w2), 1.2 * u, 1.2 * u)
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
    return pm


def nav_icon(kind: str, color: str, size=18) -> QIcon:
    return QIcon(draw_icon_pixmap(kind, size, color))


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
            self.icon_lbl.setPixmap(draw_icon_pixmap(icon_kind, 15, self._color()).scaled(
                15, 15, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            h.addWidget(self.icon_lbl)
        self.text_lbl = QLabel(text)
        # 必须显式上色: QSS 的 QLabel#TitleBadge 选择器匹配不到本控件 (是 QWidget),
        # 不写 color 文字会继承应用调色板的黑色, 深色标题栏下不可见
        self.text_lbl.setStyleSheet(
            f'background: transparent; font-size: 13px; font-weight: 600; color: {self._color()};')
        self._last = (text, active, icon_kind)
        h.addWidget(self.text_lbl)

    def _color(self):
        if self.active:
            return '#ff4b26' if self.dark else '#2f6df6'
        return '#98a2b3'

    def set_state(self, text: str, active: bool, icon_kind: str | None = None, dark: bool | None = None):
        if dark is not None:
            self.dark = dark
        self.active = active
        self.text_lbl.setText(text)
        kind = icon_kind or self.icon_kind
        self.icon_kind = kind
        self.setObjectName('TitleBadgeActive' if active else 'TitleBadge')
        self.icon_lbl.setVisible(bool(kind))
        if kind:
            self.icon_lbl.setPixmap(draw_icon_pixmap(kind, 15, self._color()).scaled(
                15, 15, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.text_lbl.setStyleSheet(
            f'background: transparent; font-size: 13px; font-weight: 600; color: {self._color()};')
        self.style().unpolish(self)
        self.style().polish(self)
        self._last = (text, active, kind)

    def set_dark(self, dark: bool):
        """主题切换后按上次状态重配色 (不传 dark 的 set_state 调用方不再残留旧主题色)。"""
        text, active, kind = getattr(self, '_last', (self.text_lbl.text(), self.active, self.icon_kind))
        self.set_state(text, active, kind, dark)


class SemiGauge(QWidget):
    """半圆仪表: 上半圆弧 (180°→0°), 标题带图标, 数字居于弧内。"""

    def __init__(self, title: str, unit: str, dark=False, icon_kind=None, parent=None):
        super().__init__(parent)
        self.title = title
        self.unit = unit
        self.dark = dark
        self.icon_kind = icon_kind
        self.value = None
        self.display_text = '--'
        self.status_word = ''
        self.color = '#2f6df6'
        self._anim_value = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate)
        self.setMinimumHeight(170)

    def set_value(self, value: float | None, display_text: str, status_word: str, color: str):
        self.value = value
        self.display_text = display_text
        self.status_word = status_word
        self.color = color
        if not self._timer.isActive():
            self._timer.start(16)

    def _animate(self):
        target = self.value or 0.0
        self._anim_value += (target - self._anim_value) * 0.18
        if abs(target - self._anim_value) < 0.5:
            self._anim_value = target
            self._timer.stop()
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
            pm = draw_icon_pixmap(self.icon_kind, 14, '#2f6df6' if not self.dark else '#ff4b26')
            pm = pm.scaled(14, 14, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            p.drawPixmap(int(w / 2 - p.fontMetrics().horizontalAdvance(self.title) / 2 - 21),
                         10, pm)
        p.drawText(QRectF(0, 8, w, 18), Qt.AlignCenter, self.title)
        # 背景弧: 上半圆 = 从 180° 顺时针走 180° (Qt: 负跨度)
        rect = QRectF(cx - r, arc_cy - r, r * 2, r * 2)
        p.setPen(_pen(t, 10))
        p.drawArc(rect, 180 * 16, -180 * 16)
        ratio = max(0.0, min(1.0, self._anim_value / 100.0))
        if ratio > 0.004:
            p.setPen(_pen(QColor(self.color), 10))
            p.drawArc(rect, 180 * 16, int(-180 * 16 * ratio))
        # 数字 (弧内, Manrope 半粗 + 等宽数字)
        p.setPen(QColor(self.color if self.value is not None else muted))
        f2 = _ui_font(16, True)
        p.setFont(f2)
        p.drawText(QRectF(0, arc_cy - r * 0.62, w, 34), Qt.AlignCenter, self.display_text)
        f3 = _ui_font(8)
        p.setFont(f3)
        p.drawText(QRectF(0, arc_cy - r * 0.62 + 32, w, 15), Qt.AlignCenter, self.unit)
        if self.status_word:
            p.drawText(QRectF(0, arc_cy + 8, w, 15), Qt.AlignCenter, self.status_word)


class FanIcon(QWidget):
    """旋转风扇图标 (转速越快转得越快)。"""

    def __init__(self, size=20, color='#2f6df6', parent=None):
        super().__init__(parent)
        self.angle = 0.0
        self.color = color
        self.setFixedSize(size, size)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._rpm = 0

    def set_rpm(self, rpm: int, max_rpm: int = 4800):
        self._rpm = rpm
        pct = max(0.0, min(1.0, rpm / max_rpm)) if max_rpm else 0
        period = 0 if pct <= 0 else (0.48 if pct >= 0.9 else 0.72 if pct >= 0.7 else 1.0 if pct >= 0.45 else 1.35)
        if period and not self._timer.isActive():
            self._timer.start(max(16, int(period * 1000 / 30)))
        elif not period:
            self._timer.stop()

    def _tick(self):
        self.angle = (self.angle + 12) % 360
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
    from PySide6.QtWidgets import QFrame, QVBoxLayout
    from PySide6.QtWidgets import QGraphicsDropShadowEffect
    frame = QFrame()
    frame.setProperty('card', True)
    eff = QGraphicsDropShadowEffect(frame)
    eff.setBlurRadius(32)
    eff.setOffset(0, 8)
    eff.setColor(QColor(0, 0, 0, 42))
    frame.setGraphicsEffect(eff)
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
