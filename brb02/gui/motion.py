# -*- coding: utf-8 -*-
"""动效系统 —— 复刻 FanControl 2.9.0 的动效语言 (Qt 版)。

来源: `docs-ref/动效复刻手册.md` (从 FanControl Next.js 产物逐条还原的原始数值, 非估算)。

设计哲学 (手册 §0): **快 (120–320ms)、轻 (位移 ≤18px + 透明度)、只在一个签名缓动上统一**。

本模块只做动效, 不碰业务。Qt 移植要点 (交接文档 §7 实测结论):
  - 页面在 QStackedWidget 里由布局托管, `move()` 会被拉回 ⇒ **出场**用旧页快照叠加层,
    **入场**不能盖快照 (会挡住真实页面) ⇒ 新页先压成全透明, 出场结束后整页淡入;
  - QSS 不支持 transition ⇒ hover 走「动画阴影 + QSS :hover 态」组合;
  - QGraphicsOpacityEffect 用完**必须移除** (常驻会让子树离屏合成掉帧);
  - 一切时长过 `duration()` —— 系统开了"减少动态效果"全部退化为瞬时, ALP_NO_ANIM=1 可强制。
"""
from __future__ import annotations

import os
import time as _time

from PySide6.QtCore import (QAbstractAnimation, QEasingCurve, QEvent, QObject,
                            QPoint, QPointF, QPropertyAnimation, QRectF, Qt,
                            QTimer, QVariantAnimation, Property)
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import (QDialog, QFrame, QGraphicsDropShadowEffect,
                               QGraphicsOpacityEffect, QLabel, QScrollArea,
                               QWidget)

# ─────────────────────────── 令牌 (手册 §1) ───────────────────────────
EASE_SIGNATURE = (0.22, 1.0, 0.36, 1.0)      # 签名缓动 easeOutQuint 系, 全应用统一
EASE_EXPO = (0.16, 1.0, 0.3, 1.0)            # 长过渡 (420ms)
EASE_BACK = (0.18, 0.9, 0.26, 1.14)          # 主题层过冲回弹 (y>1)
EASE_DEFAULT = (0.4, 0.0, 0.2, 1.0)          # Tailwind 默认 150ms

MS_INSTANT = 0
MS_MICRO = 150       # hover / 颜色过渡
MS_PAGE_OUT = 190    # 出场 (140→190: 用户"可以慢但必须顺" —— 帧更便宜后拉长时间=更多帧, 更顺)
MS_PAGE_IN = 180
MS_DIALOG = 150
MS_PANEL = 200       # 浮动面板
MS_STAGGER = 55      # 卡片交错步进 (同上, 拉长换顺)
MS_STAGGER_DUR = 260
MS_STAGGER_MAX = 4
MS_GAUGE = 600       # 仪表环追赶 (§9)
MS_POP = 220         # 侧栏选中过冲 pop (§12)

# 卡片 hover 阴影档位 (blur, yOffset, alpha) —— 搭配 theme.py 的 QSS :hover 边框色
# 流畅度: 基准档与 make_card 同步为 blur 22 (卡内常驻动画经 effect 有 4× 合成乘数,
# 模糊半径是主成本; α46 下 22 与 32 肉眼几乎无差)
CARD_SHADOW_REST = (22, 7, 46)
CARD_SHADOW_HOVER = (32, 10, 78)


def ease(points=EASE_SIGNATURE) -> QEasingCurve:
    """把 CSS cubic-bezier(x1,y1,x2,y2) 还原成 QEasingCurve。"""
    x1, y1, x2, y2 = points
    c = QEasingCurve(QEasingCurve.Type.BezierSpline)
    c.addCubicBezierSegment(QPointF(x1, y1), QPointF(x2, y2), QPointF(1.0, 1.0))
    return c


# ─────────────────────────── 无障碍 (手册 §11) ───────────────────────────
_REDUCED: bool | None = None


def reduced_motion() -> bool:
    """系统是否要求减少动态效果。环境变量 ALP_NO_ANIM=1 可强制关闭 (排查用)。"""
    global _REDUCED
    if _REDUCED is not None:
        return _REDUCED
    if os.environ.get('ALP_NO_ANIM') == '1':
        _REDUCED = True
        return _REDUCED
    try:
        import ctypes
        # SPI_GETCLIENTAREAANIMATION = 0x1042 (Win10+): 设置 → 辅助功能 → 视觉效果 → 动画效果
        enabled = ctypes.c_int(1)
        ok = ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0)
        _REDUCED = (not ok) or (enabled.value == 0)
    except Exception:
        _REDUCED = False
    return _REDUCED


def duration(ms: int) -> int:
    return 0 if reduced_motion() else ms


# ─────────────────────────── 通用原子 ───────────────────────────
def _ensure_opacity(w: QWidget) -> QGraphicsOpacityEffect:
    eff = w.graphicsEffect()
    if isinstance(eff, QGraphicsOpacityEffect):
        return eff
    eff = QGraphicsOpacityEffect(w)
    eff.setOpacity(1.0)
    w.setGraphicsEffect(eff)
    return eff


def _drop_opacity(w: QWidget) -> None:
    """动画结束移除 effect —— 常驻 QGraphicsOpacityEffect 会让整棵子树走离屏合成。"""
    try:
        if isinstance(w.graphicsEffect(), QGraphicsOpacityEffect):
            w.setGraphicsEffect(None)
    except RuntimeError:
        pass          # C++ 对象已销毁 (窗口被删)


def fade(w: QWidget, start: float, end: float, ms: int = MS_PANEL,
         curve=EASE_SIGNATURE, on_done=None) -> QPropertyAnimation | None:
    if duration(ms) == 0 or not w.isVisible():
        try:
            if end <= 0.01 and w.isVisible():
                w.setVisible(False)
            elif end >= 0.99:
                # 交错定时器迟到 + 页面已被切走: 直接复位满透明并摘 effect,
                # 否则卡片会永远停在预隐藏的 0 透明度 (连点必现)。
                eff = w.graphicsEffect()
                if isinstance(eff, QGraphicsOpacityEffect):
                    eff.setOpacity(1.0)
                    _drop_opacity(w)
        except Exception:
            pass
        if on_done:
            on_done()
        return None
    eff = _ensure_opacity(w)
    eff.setOpacity(start)
    anim = QPropertyAnimation(eff, b'opacity', w)
    anim.setDuration(ms)
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.setEasingCurve(ease(curve))

    def _finish():
        # 淡入结束同样要摘 effect (旧版只在淡出时摘 —— 常驻 effect 是掉帧隐患)
        if end >= 0.99 or end <= 0.01:
            _drop_opacity(w)
        if on_done:
            on_done()

    anim.finished.connect(_finish)
    anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
    return anim


def slide_to(w: QWidget, target: QPoint, ms: int = MS_PAGE_IN,
             curve=EASE_SIGNATURE, on_done=None) -> QPropertyAnimation | None:
    if duration(ms) == 0:
        w.move(target)
        if on_done:
            on_done()
        return None
    anim = QPropertyAnimation(w, b'pos', w)
    anim.setDuration(ms)
    anim.setStartValue(w.pos())
    anim.setEndValue(target)
    anim.setEasingCurve(ease(curve))
    if on_done:
        anim.finished.connect(on_done)
    anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
    return anim


def chase(owner: QObject, start: float, end: float, ms: int, tick,
          curve=EASE_SIGNATURE) -> QVariantAnimation | None:
    """通用数值追赶动画 (手册 §9 仪表环 600ms 签名缓动): 每帧以缓动进度回调 tick(v)。

    数据每秒刷新时从**当前值**重启 —— 与 CSS transition 的重启语义一致,
    指针永远平滑追赶目标而不是跳变。"""
    old = getattr(owner, '_alp_chase', None)
    if old is not None:
        try:
            old.stop()
        except RuntimeError:
            pass
    if duration(ms) == 0 or start == end:
        tick(1.0)
        return None
    anim = QVariantAnimation(owner)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setDuration(ms)
    anim.setEasingCurve(ease(curve))
    anim.valueChanged.connect(lambda v: tick(v))
    anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
    owner._alp_chase = anim
    return anim


# ─────────────────────────── 页面切换 (手册 §2, mode="wait") ───────────────────────────
def _cancel_transition(stack: QWidget) -> int:
    """中止进行中的页面切换: 停动画、删快照、把被压透明的页面复原。返回新代际号。"""
    st = getattr(stack, '_alp_pt', None)
    if st:
        for a in st.get('anims', []):
            try:
                a.stop()
            except RuntimeError:
                pass
        ov = st.get('overlay')
        if ov is not None:
            try:
                ov.hide()
                ov.deleteLater()
            except RuntimeError:
                pass
        tgt = st.get('target')
        if tgt is not None:
            _drop_opacity(tgt)
        stack._alp_pt = None
    stack._alp_gen = getattr(stack, '_alp_gen', 0) + 1
    return stack._alp_gen


class _PageOverlay(QWidget):
    """页面切换覆盖层: 自绘旧页快照 (painter 透明度 + 纵向偏移), 一次 blit/帧。

    取代 QLabel+QGraphicsOpacityEffect+move 三件套 —— effect 会把全窗口快照
    拖进离屏合成管线, 这里的每帧成本只是一次 drawPixmap。"""

    def __init__(self, parent: QWidget, old_pm, dy: int):
        super().__init__(parent)
        self._pm = old_pm
        self._dy = dy
        self._k = 0.0
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setGeometry(parent.rect())

    def _get_k(self) -> float:
        return self._k

    def _set_k(self, v: float) -> None:
        self._k = v
        self.update()

    k = Property(float, _get_k, _set_k)

    def paintEvent(self, ev):
        if self._pm.isNull() or self._k >= 1.0:
            return
        p = QPainter(self)
        p.setOpacity(1.0 - self._k)
        p.drawPixmap(0, int(self._dy * self._k), self._pm)
        p.end()


def _cached_snapshot(wrapper: QWidget):
    """页面停留期间后台备好的快照 (≤3s 新鲜)。没有/过期 → None (现场 grab)。"""
    snap = getattr(wrapper, '_alp_snap', None)
    if snap is not None:
        pm, ts = snap
        if _time.time() - ts <= 10.0:      # 单次预热 (无续期): 10s 内的陈旧在 190ms 淡出里不可见
            return pm
    return None


def _prime_snapshot(stack: QWidget, wrapper: QWidget, gen: int) -> None:
    """落地 600ms 后抓一张快照存起, 之后每 3s 续期 (期间温标变化 1-2 度,
    在 140ms 交叉淡出里不可见); 离开该页即停。

    动机 (2026-10-25 用户实测"切出曲线页很卡"): 现场整页 grab 7-17ms,
    正好发生在点击切换的那一帧 —— 主线程停顿一下。提前备好即零成本。"""
    if duration(MS_PAGE_OUT) == 0:
        return

    def _do():
        if getattr(stack, '_alp_gen', 0) != gen or stack.currentWidget() is not wrapper:
            return                            # 已切走, 链终止
        try:
            pm = wrapper.grab()
            dpr = wrapper.devicePixelRatioF() or 1.0
            if abs(pm.devicePixelRatioF() - dpr) > 0.01:
                pm.setDevicePixelRatio(dpr)
            wrapper._alp_snap = (pm, _time.time())
        except RuntimeError:
            return
        # 不续期 (2026-10-25): 每 3s 整页 grab 在真机上是周期性绘制风暴;
        # 10s TTL 内切出直接用缓存, 更久则现场 grab 一次。

    QTimer.singleShot(600, _do)


def page_swap(stack, old: QWidget | None, new: QWidget, direction: int = 1,
               enter=None, on_done=None) -> None:
    """零整窗动画的页面切换 (2026-10-25 定稿默认路径)。

    perf_log 实证 (用户真机): 整窗覆盖层动画在慢机上以 ~80ms/帧 (12fps)
    爬行 —— 全窗动画没法在所有机器上保证顺。此路径只做:
    瞬时换栈 (单次绘制) + 帘子级联 (小圆角矩形动画, C++ 60fps 驱动)。
    方向感交由侧栏 pop 与卡片级联承担。滑走效果保留在 page_transition,
    set ALP_SLIDE=1 启用对照。"""
    _cancel_transition(stack)
    stack.setCurrentWidget(new)
    if enter:
        try:
            enter()
        except Exception:
            pass
    if on_done:
        on_done()


def page_transition(stack, old: QWidget | None, new: QWidget, direction: int = 1,
                    enter=None, on_done=None) -> None:
    """方向感知的页面切换 (2026-10-25 流畅度改版):

      单层合成: 旧页快照 140ms 淡出 + 反向滑走 `-14px×dir`, 新页**从第一帧就可见**;
      卡片交错 (`enter` 钩子) 与快照淡出**并行**起步。

    ⚠️ 旧版是串行的: 新页压成 opacity 0 → 等 150ms 出场结束 → 再 180ms 淡入。
    总耗时 330ms, 而且前 150ms 快照底下是**空白页**, 观感就是"先空一下再慢慢出来"
    (用户反馈"切进来切出去都卡")。串行还多一层全尺寸离屏合成。
    现在只动快照一层, 140ms 内新页已完全就位, 视觉上反而更快更稳。
    方向感仍由出场快照的位移建立 (Qt 无法给布局托管的新页做位移, 见交接文档 §7.1)。
    快照按 devicePixelRatioF 标注, 高 DPI 屏不糊。
    """
    if duration(MS_PAGE_IN) == 0 or old is None or old is new:
        stack.setCurrentWidget(new)
        if enter:
            enter()
        if on_done:
            on_done()
        return

    gen = _cancel_transition(stack)          # 快速连点: 旧代际立即作废

    pix = _cached_snapshot(old)
    if pix is None:
        dpr = old.devicePixelRatioF() or 1.0
        pix = old.grab()
        if abs(pix.devicePixelRatioF() - dpr) > 0.01:
            pix.setDevicePixelRatio(dpr)      # grab() 返回物理像素, 不标 DPR 高 DPI 下会糊
    overlay = _PageOverlay(stack, pix, -14 * direction)
    overlay.show()
    overlay.raise_()

    stack.setCurrentWidget(new)
    _cancel_stagger(new)                      # 新页若残留上次中断的半透明卡片, 先复原
    # ⚠️ 2026-10-25 流畅度优化: 旧版把新页压成 opacity 0, 等出场 150ms 结束才淡入 180ms
    # ⇒ 总耗时 330ms, 且前 150ms 快照底下是**空白页**, 观感是"先空一下再慢慢出来"(用户反馈卡)。
    # 现在: 新页立刻可见, 只有快照在淡出+滑走 (单层合成), 卡片入场与出场**并行** ⇒ 150ms 到位。
    _drop_opacity(new)

    st = {'gen': gen, 'overlay': overlay, 'target': new, 'anims': []}
    stack._alp_pt = st

    if enter:
        try:
            enter()                           # 卡片交错立即起步, 与快照淡出重叠
        except Exception:
            pass

    def _phase2():
        if getattr(stack, '_alp_gen', 0) != gen:
            return                            # 已被下一次切换作废
        try:
            overlay.hide()
            overlay.deleteLater()
        except RuntimeError:
            pass
        st['overlay'] = None
        _prime_snapshot(stack, new, gen)      # 停留期后台备好快照, 下次切出零 grab
        if on_done:
            on_done()

    a1 = QPropertyAnimation(overlay, b'k', overlay)
    a1.setStartValue(0.0)
    a1.setEndValue(1.0)
    a1.setDuration(MS_PAGE_OUT)
    a1.setEasingCurve(ease(EASE_SIGNATURE))
    a1.finished.connect(_phase2)
    a1.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
    st['anims'].append(a1)


# ─────────────────────────── 卡片交错入场 (手册 §3) ───────────────────────────
class _PaintGate(QObject):
    """卡片"封笔"器: 拦截卡片及其子树的 Paint 事件, 解封前不出像素。

    2026-10-25 设置页切入卡顿的机制修复: 换页首帧要把全部卡片挤在同一帧绘制 ——
    封笔后延迟档位的卡片占位但不绘, 到各自级联档位解封即刻摘除过滤器。"""

    def __init__(self, card: QWidget):
        super().__init__(card)
        self._card = card
        self._blocked = True
        self._targets = [card, *card.findChildren(QWidget)]
        for w in self._targets:
            w.installEventFilter(self)

    def eventFilter(self, obj, ev):
        if self._blocked and ev.type() == QEvent.Paint:
            return True                      # 吞掉: 本帧不出像素
        return False

    def open(self) -> None:
        if self._blocked:
            self._blocked = False
            self.detach()
            self._card.update()

    def detach(self) -> None:
        targets, self._targets = getattr(self, '_targets', ()), ()
        for w in targets:
            try:
                w.removeEventFilter(self)
            except RuntimeError:
                pass


class _Curtain(QWidget):
    """盖在卡片上的纯色圆角帘 —— 帘淡出 = "卡片淡入"。

    2026-10-25 结构手术: 旧交错给每张卡挂 QGraphicsOpacityEffect, 动画期间
    每帧都是整卡离屏合成; 帘子只画一个纯色圆角矩形 (≈0.05ms/帧), 卡片本体
    一次性正常绘制, 视觉与透明度淡入完全等价 (帘色=页面底色)。"""

    def __init__(self, host: QWidget, color: str):
        super().__init__(host)
        self._color = color
        self._op = 1.0
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setGeometry(host.rect())

    def _get_op(self) -> float:
        return self._op

    def _set_op(self, v: float) -> None:
        self._op = v
        if v <= 0.005:
            self.hide()
        else:
            self.update()

    op = Property(float, _get_op, _set_op)

    def paintEvent(self, ev):
        if self._op <= 0.005 or not self.isVisible():
            return
        p = QPainter(self)
        p.setOpacity(self._op)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self._color))
        p.drawRoundedRect(QRectF(0.0, 0.0, float(self.width()), float(self.height())),
                          22.0, 22.0)
        p.end()


def _page_of(container: QWidget) -> QWidget:
    """滚动容器 → 真实页面 (卡片挂在页面布局里, 不在 QScrollArea 自己的布局里)。"""
    if isinstance(container, QScrollArea):
        inner = container.widget()
        if inner is not None:
            return inner
    return container


def _stagger_cards(page: QWidget) -> list:
    # 只取对当前 page 实际可见的卡片 (排除 ControlPage 内部 QStackedWidget 隐藏子页签上的 5 张卡片,
    # 避免隐藏页签卡片抢占前序交错档位导致可见卡片被延迟 165ms 后同帧扎堆绘制)
    cards = [w for w in page.findChildren(QFrame)
             if bool(w.property('card')) and w.isVisibleTo(page)]
    try:
        if page.layout() is not None:
            page.layout().activate()
        seen = set()
        for w in cards:
            p = w.parentWidget()
            while p is not None and p is not page and id(p) not in seen:
                seen.add(id(p))
                if p.layout() is not None:
                    p.layout().activate()
                p = p.parentWidget()
    except Exception:
        pass
    def _pos(w):
        p = w.mapTo(page, QPoint(0, 0))
        return (p.y(), p.x())
    cards.sort(key=_pos)
    return cards


def _cancel_stagger(container: QWidget) -> None:
    """代际取消: 停掉旧定时器动画, 把被压透明的卡片**复原** (否则永远隐身)。"""
    page = _page_of(container)
    st = getattr(page, '_alp_reveal', None)
    if st:
        for a in st.get('anims', []):
            try:
                a.stop()
            except RuntimeError:
                pass
        for cur in st.get('curtains', []):
            try:
                cur.deleteLater()
            except RuntimeError:
                pass
        for gate in st.get('gates', []):
            try:
                gate.open()
                gate.detach()
            except RuntimeError:
                pass
        page._alp_reveal = None
    page._alp_reveal_gen = getattr(page, '_alp_reveal_gen', 0) + 1


def stagger_reveal(container: QWidget, step_ms: int = MS_STAGGER,
                   dur_ms: int = MS_STAGGER_DUR, max_slots: int = MS_STAGGER_MAX) -> None:
    """卡片交错淡入 (手册 §3 `data-page-reveal="cards"`): 200ms/张, 40ms 步进。

    ⚠️ **全部可见卡片都参与**: 前 max_slots 张依次入场, 其余共享最后一档延迟:
    整页都在动, 没有一张"抢跑", 总时长仍封在 (max_slots-1)×step + dur。

    代际计数 —— 快速连点时旧任务整体作废并复原; 页面不可见时直接跳过。"""
    if duration(dur_ms) == 0 or container is None:
        return
    page = _page_of(container)
    if page.isHidden():
        return
    _cancel_stagger(container)
    gen = page._alp_reveal_gen
    cards = _stagger_cards(page)
    if not cards:
        return
    # 帘色 = 页面底色 (帘子视觉上就是"从底色里淡入")
    win = page.window()
    dark = bool(getattr(getattr(win, 'cfg', None), 'dark', False))
    from .theme import DARK, LIGHT
    bg = DARK['bg'] if dark else LIGHT['bg']
    st = {'anims': [], 'cards': [], 'curtains': [], 'gates': []}
    page._alp_reveal = st
    for i, w in enumerate(cards):
        delay = min(i, max_slots - 1) * step_ms
        if delay <= 0:
            # 首张卡片本帧直接盖帘淡入, 免装/卸子树事件过滤器
            cur = _Curtain(w, bg)
            cur.show()
            cur.raise_()
            a = QPropertyAnimation(cur, b'op', cur)
            a.setStartValue(1.0)
            a.setEndValue(0.0)
            a.setDuration(dur_ms)
            a.setEasingCurve(ease(EASE_SIGNATURE))
            a.finished.connect(cur.deleteLater)
            a.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
            st['curtains'].append(cur)
            st['anims'].append(a)
            continue

        gate = _PaintGate(w)                 # 封笔: 延迟卡片首帧不绘, 到档位再解封
        st['gates'].append(gate)

        def _start(w=w, gate=gate):
            if getattr(page, '_alp_reveal_gen', -1) != gen:
                return                       # 新一代已接管, 本任务作废
            gate.open()                      # 本帧解封并立即卸除过滤器
            cur = _Curtain(w, bg)            # 盖帘 → 淡出 = 卡片淡入
            cur.show()
            cur.raise_()
            a = QPropertyAnimation(cur, b'op', cur)
            a.setStartValue(1.0)
            a.setEndValue(0.0)
            a.setDuration(dur_ms)
            a.setEasingCurve(ease(EASE_SIGNATURE))
            a.finished.connect(cur.deleteLater)
            a.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
            st['curtains'].append(cur)
            st['anims'].append(a)

        QTimer.singleShot(duration(delay), _start)


# ─────────────────────────── 卡片 hover 微交互 (手册 §8) ───────────────────────────
def attach_card_hover(frame: QWidget) -> None:
    """已退役 (2026-10-25): 卡片改平面+描边 (手册 §4.1), 无 effect 可驱动;
    hover 由 QSS border 换色承担。保留签名兼容调用。"""
    return None


# ─────────────────────────── 侧栏选中 pop (手册 §12) ───────────────────────────
def pop_icon(btn, src_pm, base: int = 21, ms: int = MS_POP) -> None:
    """选中图标过冲回弹: 在固定 base×base 逻辑画布 (按 DPR 1:1 物理像素标注) 内中心缩放,
    杜绝高 DPI 二次缩放发虚, 且不改变图标外框尺寸 (展开态文字零抖动)。"""
    if duration(ms) == 0:
        return
    old = getattr(btn, '_alp_pop_anim', None)
    if old is not None:
        try:
            old.stop()
        except RuntimeError:
            pass

    meta = getattr(src_pm, '_icon_meta', None)
    try:
        dpr = float(btn.devicePixelRatioF() or 1.0)
    except Exception:
        dpr = 1.0
    if meta is not None:
        kind, _sz, color, dpr = meta[0], meta[1], meta[2], float(meta[3] or dpr)
    else:
        kind = color = None

    last_q = [None]

    def _render_scale(k: float) -> QIcon:
        if kind is not None:
            from .widgets import draw_icon_pixmap, nav_icon
            if abs(k - 1.0) < 0.005:
                return nav_icon(kind, color, base, dpr=dpr)
            return QIcon(draw_icon_pixmap(kind, base, color, dpr=dpr, scale=k))
        from PySide6.QtGui import QPixmap
        px = max(2, int(round(base * dpr)))
        s = max(2, int(round(px * k)))
        scaled = src_pm.scaled(s, s, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        canvas = QPixmap(px, px)
        canvas.fill(Qt.transparent)
        p = QPainter(canvas)
        p.drawPixmap((px - scaled.width()) // 2, (px - scaled.height()) // 2, scaled)
        p.end()
        canvas.setDevicePixelRatio(dpr)
        return QIcon(canvas)

    def _scale(v):
        if v < 0.55:
            k = 0.88 + (1.08 - 0.88) * (v / 0.55)
        else:
            k = 1.08 - 0.08 * ((v - 0.55) / 0.45)
        q = round(k, 2)
        if q == last_q[0] and v < 1.0:
            return
        last_q[0] = q
        btn.setIcon(_render_scale(q))

    def _fin():
        btn.setIcon(_render_scale(1.0))

    anim = QVariantAnimation(btn)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setDuration(ms)
    anim.setEasingCurve(ease(EASE_SIGNATURE))
    anim.valueChanged.connect(_scale)
    anim.finished.connect(_fin)
    anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
    btn._alp_pop_anim = anim


# ─────────────────────────── 对话框淡入淡出 (手册 §4) ───────────────────────────
class DialogFader(QObject):
    """QDialog: 开 150ms ease-out / 关 150ms ease-in (纯 windowOpacity, 不缩放窗口)。

    拦截 Close/Esc 也走淡出; Ok/Cancel 按钮由调用方接 `fader.close(code)`
    (直接连 dlg.accept 会在淡出开始前就 done+hide, 看不到动画)。"""

    def __init__(self, dlg: QDialog, ms: int = MS_DIALOG):
        super().__init__(dlg)
        self.dlg = dlg
        self.ms = duration(ms)
        self._closing = False
        self._anim = None
        dlg.installEventFilter(self)
        if self.ms:
            dlg.setWindowOpacity(0.0)

    def eventFilter(self, obj, ev):
        if obj is not self.dlg or not self.ms:
            return False
        t = ev.type()
        if t == QEvent.Show:
            self._run(0.0, 1.0, QEasingCurve.Type.OutQuad)
        elif t == QEvent.Close and not self._closing:
            ev.ignore()
            self.close(QDialog.DialogCode.Rejected)
            return True
        elif t == QEvent.KeyPress and ev.key() == Qt.Key.Key_Escape and not self._closing:
            self.close(QDialog.DialogCode.Rejected)
            return True
        return False

    def _run(self, a: float, b: float, curve, on_done=None):
        if self._anim is not None:
            try:
                self._anim.stop()
            except RuntimeError:
                pass
        anim = QPropertyAnimation(self.dlg, b'windowOpacity', self.dlg)
        anim.setDuration(self.ms)
        anim.setStartValue(a)
        anim.setEndValue(b)
        anim.setEasingCurve(curve)
        if on_done:
            anim.finished.connect(on_done)
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        self._anim = anim

    def close(self, code) -> None:
        """淡出后再 done(code); exec() 的返回值语义不变。重复调用只生效一次。"""
        if self._closing:
            return
        self._closing = True
        if not self.ms:
            self.dlg.done(code)
            return
        self._run(1.0, 0.0, QEasingCurve.Type.InQuad,
                  on_done=lambda: self.dlg.done(code))


def dialog_fade(dlg: QDialog, ms: int = MS_DIALOG) -> DialogFader:
    return DialogFader(dlg, ms)


# ─────────────────────────── 条件内容出现/收起 (手册 §6 折叠 + §7 浮动面板) ───────────────────────────
_MAX_H = 16777215      # QWIDGETSIZE_MAX


def reveal_block(w: QWidget, show: bool, ms: int = 200, on_done=None) -> None:
    """条件性内容块的展开/收起: 高度 0↔内容高 + 透明度, 200ms 签名缓动。

    对应手册 §6 折叠区 (motion.div height:0↔auto + opacity) 与 §7 浮动面板
    (200ms) 的 Qt 合并映射 —— 本应用没有独立悬浮层, 条件横幅/信息行走这条。
    用 maximumHeight 动画 (布局托管下唯一不破坏布局的高度做法), 收起时
    高度与透明度由**同一个** QVariantAnimation 驱动, 不存在两动画竞态。"""
    # 幂等: 状态没变且没有进行中的动画就直接返回 —— 控制/曲线页的周期 tick
    # 会反复调 reveal_block(True), 不挡的话信息行每 3s 重放一次开场动画
    if getattr(w, '_alp_shown', None) == show and getattr(w, '_alp_reveal_anim', None) is None:
        return
    w._alp_shown = show
    old = getattr(w, '_alp_reveal_anim', None)
    if old is not None:
        try:
            old.stop()
        except RuntimeError:
            pass
    if duration(ms) == 0:
        w.setVisible(show)
        w.setMaximumHeight(_MAX_H)
        _drop_opacity(w)
        if on_done:
            on_done()
        return

    if show:
        w.setMaximumHeight(_MAX_H)           # 先复位, sizeHint 才是完整内容高
        w.setVisible(True)
        h = max(1, w.sizeHint().height())
        w.setMaximumHeight(0)
        eff = _ensure_opacity(w)
        eff.setOpacity(0.0)

        skip = [True]

        def _tick(v):
            skip[0] = not skip[0]
            if skip[0] and v < 1.0:
                return                   # 30fps: maximumHeight 每帧重排所在布局
            w.setMaximumHeight(max(0, int(h * v)))
            eff.setOpacity(v)

        def _fin():
            w.setMaximumHeight(_MAX_H)       # 复位, 后续改文案能自然重排
            _drop_opacity(w)
            w._alp_reveal_anim = None        # 清引用 (DeleteWhenStopped 后是僵尸), 幂等守卫才生效
            if on_done:
                on_done()
    else:
        h = max(w.height(), 1)
        eff = _ensure_opacity(w)

        skip = [True]

        def _tick(v):
            skip[0] = not skip[0]
            if skip[0] and v < 1.0:
                return                   # 30fps: 同上
            w.setMaximumHeight(max(0, int(h * (1.0 - v))))
            eff.setOpacity(1.0 - v)

        def _fin():
            w.setVisible(False)
            w.setMaximumHeight(_MAX_H)
            _drop_opacity(w)
            w._alp_reveal_anim = None        # 同上
            if on_done:
                on_done()

    anim = QVariantAnimation(w)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setDuration(ms)
    anim.setEasingCurve(ease(EASE_SIGNATURE))
    anim.valueChanged.connect(_tick)
    anim.finished.connect(_fin)
    anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
    w._alp_reveal_anim = anim


# ─────────────────────────── 宽度动画 (侧栏展开/收起) ───────────────────────────
def animate_width(w: QWidget, target: int, ms: int = 200, on_done=None) -> None:
    """setFixedWidth 的动画版 (200ms 签名缓动)。侧栏 76↔200 用。"""
    old = getattr(w, '_alp_width_anim', None)
    if old is not None:
        try:
            old.stop()
        except RuntimeError:
            pass
    if duration(ms) == 0 or w.width() == target:
        w.setFixedWidth(target)
        if on_done:
            on_done()
        return
    anim = QVariantAnimation(w)
    anim.setStartValue(float(w.width()))
    anim.setEndValue(float(target))
    anim.setDuration(ms)
    anim.setEasingCurve(ease(EASE_SIGNATURE))
    skip = [True]

    def _tick(v):
        skip[0] = not skip[0]
        if skip[0] and v < 1.0:
            return                       # 30fps: setFixedWidth 每帧触发全窗口布局重算
        w.setFixedWidth(int(v))

    anim.valueChanged.connect(_tick)
    if on_done:
        anim.finished.connect(on_done)
    anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
    w._alp_width_anim = anim


# ─────────────────────────── 主题扫描线 (手册 §12) ───────────────────────────
class _ScanBand(QWidget):
    """宿主卡片上的竖向柔光带, 6.2s 一轮 (glacier-scan 的 Qt 版):
    ease-in-out 扫过全卡, 峰值 alpha ~12%, 圆角裁剪, 不挡鼠标。"""

    def __init__(self, host: QWidget, color_fn):
        super().__init__(host)
        self._host = host
        self._color_fn = color_fn
        self._phase = 0.0
        self._q = -1.0
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._anim = QPropertyAnimation(self, b'phase', self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(6200)
        self._anim.setLoopCount(-1)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

    def _get_phase(self) -> float:
        return self._phase

    def _set_phase(self, v: float) -> None:
        self._phase = v
        # 流畅度: 6.2s × 24fps ≈ 150 步 —— 相位量化; **不可见时连 update 都不调**
        # (QStackedWidget 里深层子控件收不到显隐事件, 事件起停不可靠 —— 实证。
        #  常跑动画 + 可见才重绘: 隐藏页零成本, 无需起停)
        q = round(v * 150) / 150.0
        if q != self._q:
            self._q = q
            if self.isVisible():
                self.update()

    phase = Property(float, _get_phase, _set_phase)

    def paintEvent(self, ev):
        host = self._host
        if not host.isVisible() or host.width() <= 0 or host.height() <= 0:
            return
        w, h = self.width(), self.height()
        band_h = max(64.0, h * 0.42)
        y = -band_h + self._phase * (h + band_h)
        c = QColor(self._color_fn())
        c.setAlpha(30)
        grad = QLinearGradient(0.0, y, 0.0, y + band_h)
        grad.setColorAt(0.0, QColor(c.red(), c.green(), c.blue(), 0))
        grad.setColorAt(0.5, c)
        grad.setColorAt(1.0, QColor(c.red(), c.green(), c.blue(), 0))
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0.0, 0.0, float(w), float(h)), 21.0, 21.0)
        p.setClipPath(path)
        p.fillRect(QRectF(0.0, y, float(w), band_h), grad)
        p.end()


class _ScanHostFilter(QObject):
    """宿主卡片 Resize 跟随 / Show-Hide 起停扫描。"""

    def __init__(self, band: _ScanBand):
        super().__init__(band)
        self.band = band

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Resize:
            self.band.setGeometry(0, 0, obj.width(), obj.height())
        return False


def attach_scan(frame: QWidget, color_fn) -> None:
    """给卡片挂主题扫描线 (color_fn 每帧取色, 主题切换自动跟色)。减少动态效果时不挂。"""
    if reduced_motion():
        return
    if getattr(frame, '_alp_scan', None) is not None:
        return
    band = _ScanBand(frame, color_fn)
    band.setGeometry(0, 0, frame.width(), frame.height())
    band.raise_()
    band.show()
    flt = _ScanHostFilter(band)
    frame.installEventFilter(flt)
    frame._alp_scan = band
    frame._alp_scan_filter = flt
    try:
        band._anim.start()
    except RuntimeError:
        pass
