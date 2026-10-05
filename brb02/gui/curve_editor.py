# -*- coding: utf-8 -*-
"""曲线编辑器: 19 锚点 (温度 20-110°C 步长 5), 垂直拖拽, 非递减传播 (规格 §D1)。"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QFont
from PySide6.QtWidgets import QWidget

TEMPS = list(range(20, 111, 5))        # 19 个温度点
DEFAULT_PCT = [0, 10, 18, 24, 30, 36, 42, 48, 55, 62, 70, 78, 86, 92, 96, 100, 100, 100, 100]


class CurveEditor(QWidget):
    """speed 为 0..100(%);rpm = pct/100*max_rpm 由外部换算。"""

    changed = None          # callable(list[int])

    def __init__(self, dark=False, max_rpm=4800, rpm_axis=False, parent=None):
        super().__init__(parent)
        self.dark = dark
        self.max_rpm = max_rpm
        self.rpm_axis = rpm_axis
        self.pct = list(DEFAULT_PCT)
        self.drag_idx = -1
        self.current_temp = None      # 当前温度指示线
        self.setMinimumHeight(300)
        self.setMouseTracking(True)
        self._pad_l, self._pad_r, self._pad_t, self._pad_b = 52, 20, 26, 30

    # ---- 坐标换算 ----
    def _x(self, i, w):
        return self._pad_l + (w - self._pad_l - self._pad_r) * i / (len(TEMPS) - 1)

    def _y(self, pct, h):
        return self._pad_t + (h - self._pad_t - self._pad_b) * (1 - pct / 100.0)

    def _temp_at(self, x, w):
        span = w - self._pad_l - self._pad_r
        i = round((x - self._pad_l) / span * (len(TEMPS) - 1))
        return max(0, min(len(TEMPS) - 1, i))

    def _pct_at(self, y, h):
        span = h - self._pad_t - self._pad_b
        pct = (1 - (y - self._pad_t) / span) * 100
        return max(0.0, min(100.0, pct))

    # ---- 数据 ----
    @staticmethod
    def resample_pairs(pairs, max_rpm: int = 4800) -> list[int]:
        """任意 (温度, 转速) 对 -> 19 锚点百分比 (线性插值)"""
        pts = sorted((float(t), float(r)) for t, r in pairs) or [(40, 1000)]
        out = []
        for t in TEMPS:
            if t <= pts[0][0]:
                r = pts[0][1]
            elif t >= pts[-1][0]:
                r = pts[-1][1]
            else:
                for (t0, r0), (t1, r1) in zip(pts, pts[1:]):
                    if t0 <= t <= t1:
                        r = r0 + (r1 - r0) * (t - t0) / max(t1 - t0, 0.1)
                        break
            out.append(max(0, min(100, round(r / max_rpm * 100))))
        return out

    def set_curve(self, data):
        """接受 19 点百分比列表 或 [(温度, 转速)] 对 (自动重采样)"""
        if data and isinstance(data[0], (list, tuple)):
            self.pct = self.resample_pairs(data, self.max_rpm)
        else:
            pct = [int(v) for v in (data or [])]
            if not pct:
                pct = list(DEFAULT_PCT)
            if len(pct) < len(TEMPS):
                pct = pct + [pct[-1]] * (len(TEMPS) - len(pct))
            self.pct = [max(0, min(100, v)) for v in pct[:len(TEMPS)]]
        self.update()

    def get_curve(self) -> list[int]:
        return list(self.pct)

    def set_current_temp(self, temp: float | None):
        self.current_temp = temp
        self.update()

    def _propagate(self, idx: int):
        """非递减传播: 左边不能比选中点高, 右边不能比它低 (规格 §D1)"""
        v = self.pct[idx]
        for i in range(idx):
            self.pct[i] = min(self.pct[i], v)
        for i in range(idx + 1, len(self.pct)):
            self.pct[i] = max(self.pct[i], v)

    # ---- 鼠标 ----
    def mousePressEvent(self, ev):
        pos = ev.position()
        w, h = self.width(), self.height()
        i = self._temp_at(pos.x(), w)
        if abs(pos.x() - self._x(i, w)) < 16 and abs(pos.y() - self._y(self.pct[i], h)) < 18:
            self.drag_idx = i
            self._drag_to(pos, h)
        self.update()

    def mouseMoveEvent(self, ev):
        if self.drag_idx >= 0:
            self._drag_to(ev.position(), self.height())
        self.update()

    def mouseReleaseEvent(self, ev):
        if self.drag_idx >= 0 and self.changed:
            self.changed(self.get_curve())
        self.drag_idx = -1
        self.update()

    def _drag_to(self, pos, h):
        self.pct[self.drag_idx] = int(round(self._pct_at(pos.y(), h)))
        self._propagate(self.drag_idx)

    # ---- 绘制 ----
    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        grid = QColor('#252b35' if self.dark else '#e8edf5')
        tick = QColor('#98a2b3' if self.dark else '#6b7280')
        fg = QColor('#eef2f7' if self.dark else '#111827')
        primary = QColor('#ff4b26' if self.dark else '#2f6df6')
        f_small = QFont(); f_small.setPointSize(8); p.setFont(f_small)

        # 网格 + Y 刻度
        for pct in (0, 25, 50, 75, 100):
            y = self._y(pct, h)
            p.setPen(QPen(grid, 1))
            p.drawLine(int(self._pad_l), int(y), int(w - self._pad_r), int(y))
            lbl = f'{int(pct / 100 * self.max_rpm)}' if self.rpm_axis else f'{pct}%'
            p.setPen(QColor(tick))
            p.drawText(QRectF(0, y - 8, self._pad_l - 6, 16), Qt.AlignRight | Qt.AlignVCenter, lbl)
        # X 刻度
        for i, t in enumerate(TEMPS):
            x = self._x(i, w)
            p.setPen(QPen(grid, 1))
            p.drawLine(int(x), int(self._pad_t), int(x), int(h - self._pad_b))
            if i % 3 == 0 or i == len(TEMPS) - 1:
                p.setPen(QColor(tick))
                p.drawText(QRectF(x - 15, h - self._pad_b + 4, 30, 14), Qt.AlignCenter, str(t))
        p.setPen(QColor(tick))
        p.drawText(QRectF(w - 70, h - 16, 70, 14), Qt.AlignRight, '温度 (°C)')

        # 面积填充 + 曲线
        pts = [QPointF(self._x(i, w), self._y(self.pct[i], h)) for i in range(len(TEMPS))]
        path_pts = pts + [QPointF(w - self._pad_r, h - self._pad_b), QPointF(self._pad_l, h - self._pad_b)]
        from PySide6.QtGui import QPainterPath, QPolygonF
        area = QPainterPath(QPointF(pts[0]))
        area.addPolygon(QPolygonF(path_pts))
        fill = QColor(primary)
        fill.setAlpha(36)
        p.fillPath(area, fill)
        pen = QPen(primary, 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        for a, b in zip(pts, pts[1:]):
            p.drawLine(a, b)

        # 当前温度指示线
        if self.current_temp:
            tx = self._pad_l + (w - self._pad_l - self._pad_r) * (self.current_temp - 20) / 90
            tx = max(self._pad_l, min(w - self._pad_r, tx))
            hot = QColor('#ff7048' if self.dark else '#ef4444')
            p.setPen(QPen(hot, 2, Qt.DashLine))
            p.drawLine(int(tx), int(self._pad_t), int(tx), int(h - self._pad_b))
            p.setBrush(hot)
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(QRectF(tx - 44, self._pad_t - 18, 88, 17), 4, 4)
            p.setPen(QColor('white'))
            p.drawText(QRectF(tx - 44, self._pad_t - 18, 88, 17), Qt.AlignCenter, f'当前 {self.current_temp:.0f}°C')

        # 锚点
        for i, pt in enumerate(pts):
            r = 8 if i == self.drag_idx else 6
            c = QColor(primary).lighter(130) if i == self.drag_idx else primary
            p.setBrush(c)
            p.setPen(QPen(QColor('white' if not self.dark else '#11161d'), 2))
            p.drawEllipse(pt, r, r)
