# -*- coding: utf-8 -*-
"""屏幕信息卡渲染器 (生产模块, v3.20/v3.21)。

屏上默认内容 = **长时间不需要刷新**的信息: 日期 + 星期 + CPU/GPU 型号。
上传策略 (service): 连接/恢复时一次 + 日期轮转时一次 (≤2 次/天) → 闪存磨损与
16s 独占窗口可忽略。动态仪表方案已搁置 (tools/render_monitor_canvas.py 留档)。

色彩与 brb02/gui/theme.py (LIGHT) 同源: 白卡底; 型号用 UI 身份色
(CPU=主题蓝 #2f6df6 / GPU=图表橙 #f97316)。中文星期优先雅黑/黑体, 缺失退化英文缩写。
全部渲染在内存完成 (QImage → screen_upload.qimage_to_rgb565_be 量化), 不落盘。
"""
from __future__ import annotations

import datetime
import os
import time

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter

W, H = 428, 142

PAL = {
    'bg': '#ffffff', 'fg': '#16181d', 'muted_fg': '#7a8494', 'border': '#e4e8ef',
    'primary': '#2f6df6', 'gpu_orange': '#f97316',
}
WEEKDAY_CN = '一二三四五六日'          # datetime.weekday(): 0=周一
WEEKDAY_EN = 'MON TUE WED THU FRI SAT SUN'.split()

_fonts = None


class _Fonts:
    def __init__(self):
        for f in os.listdir(os.path.join(os.path.dirname(__file__), 'gui', 'fonts')):
            if f.lower().endswith(('.ttf', '.otf')):
                QFontDatabase.addApplicationFont(os.path.join(
                    os.path.dirname(__file__), 'gui', 'fonts', f))
        fams = list(QFontDatabase.families())
        self.mono = next((f for f in fams if 'geist' in f.lower()), fams[0])
        self.sans = next((f for f in fams if 'manrope' in f.lower()), self.mono)
        self.cjk = next((f for f in fams if 'yahei' in f.lower()
                         or 'simhei' in f.lower() or 'noto sans cjk' in f.lower()), None)


def prepare():
    """预载字体 (GUI 线程调用最佳; 幂等)。"""
    global _fonts
    if _fonts is None:
        _fonts = _Fonts()
    return _fonts


def _font(family, px, bold=False):
    f = QFont(family)
    f.setPixelSize(px)
    f.setBold(bold)
    return f


def clean_model_name(name: str, kind: str = 'cpu') -> str:
    """LHM 硬件名 → 画布短名。
    'AMD Ryzen 9 7845HX with Radeon Graphics' → 'Ryzen 9 7845HX' (剥厂商前缀 +
    剥 " with …" iGPU 尾巴 + 清 (R)/(TM) + 超长截断); 'NVIDIA GeForce RTX 5060' →
    'RTX 5060'。"""
    n = ' '.join((name or '').replace('(R)', '').replace('(TM)', '').split())
    n = n.split(' with ')[0].strip()                 # AMD LHM 名的 iGPU 尾巴
    for pre in ('NVIDIA GeForce', 'GeForce', 'NVIDIA', 'AMD', 'Intel'):
        if n == pre or n.startswith(pre + ' '):
            n = n[len(pre):].strip()
            break                                    # 只剥一层厂商前缀
    if len(n) > 30:                                  # 保险: 超长截断 (防未知固件命名)
        n = n[:30].rstrip() + '...'
    return n or ('CPU' if kind == 'cpu' else 'GPU')


def render_card(date_str: str | None = None, weekday_idx: int | None = None,
                cpu_model: str | None = None, gpu_model: str | None = None) -> QImage:
    """渲染 428×142 信息卡 (RGB888 QImage)。

    date_str 缺省=今天 (%Y-%m-%d); weekday_idx 缺省=今天 (0=周一);
    型号缺省 'CPU'/'GPU' (service 传 clean_model_name 结果)。
    """
    F = prepare()
    now = datetime.datetime.now()
    date_str = date_str or now.strftime('%Y-%m-%d')
    wd = now.weekday() if weekday_idx is None else int(weekday_idx) % 7
    cpu_model = cpu_model or 'CPU'
    gpu_model = gpu_model or 'GPU'

    img = QImage(W, H, QImage.Format.Format_RGB888)
    img.fill(QColor(PAL['bg']))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.TextAntialiasing, True)
    LM = 22

    # 日期行 (天级变更)
    p.setPen(QColor(PAL['muted_fg']))
    p.setFont(_font(F.mono, 18))
    w_date = p.fontMetrics().horizontalAdvance(date_str)
    p.drawText(QRectF(LM, 10, w_date + 4, 22), Qt.AlignLeft | Qt.AlignVCenter, date_str)
    wd_x = LM + w_date + 12
    if F.cjk:
        p.setFont(_font(F.cjk, 16))
        p.drawText(QRectF(wd_x, 10, 80, 22), Qt.AlignLeft | Qt.AlignVCenter,
                   f'周{WEEKDAY_CN[wd]}')
    else:
        p.setFont(_font(F.mono, 16))
        p.drawText(QRectF(wd_x, 10, 80, 22), Qt.AlignLeft | Qt.AlignVCenter,
                   WEEKDAY_EN[wd])

    # 型号两行 (UI 身份色), **每行独立** shrink-to-fit (一行过长不拖累另一行)
    rows = [(cpu_model, QColor(PAL['primary']), 40),
            (gpu_model, QColor(PAL['gpu_orange']), 94)]
    for text, color, y in rows:
        px = 52
        while px > 16:
            p.setFont(_font(F.mono, px, True))
            if p.fontMetrics().horizontalAdvance(text) <= W - 44:
                break
            px -= 2
        p.setFont(_font(F.mono, px, True))
        w = p.fontMetrics().horizontalAdvance(text)
        p.setPen(color)
        p.drawText(QRectF(LM, y, w + 4, px + 8), Qt.AlignLeft | Qt.AlignVCenter, text)
    p.end()
    return img


# ============ 情境卡片 (v3.32): 游戏模式 / 温度警报 ============

def render_game(proc: str = 'GAME', rpm: int = 0, level: int = 0) -> QImage:
    """游戏模式卡 (深底): GAME MODE + 前台进程名 + 转速/档位。"""
    F = prepare()
    img = QImage(W, H, QImage.Format.Format_RGB888)
    img.fill(QColor('#101418'))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.TextAntialiasing, True)
    p.setPen(QColor('#58a6ff'))
    p.setFont(_font(F.mono, 15, True))
    p.drawText(QRectF(20, 10, 220, 20), Qt.AlignLeft | Qt.AlignVCenter, 'GAME MODE')
    p.setPen(QColor('#8b98a5'))
    p.setFont(_font(F.mono, 13))
    p.drawText(QRectF(W - 164, 10, 144, 20), Qt.AlignRight | Qt.AlignVCenter,
               f'FAN {rpm} RPM')
    name = (proc or 'GAME').upper()
    px = 46
    while px > 18:
        p.setFont(_font(F.mono, px, True))
        if p.fontMetrics().horizontalAdvance(name) <= W - 44:
            break
        px -= 2
    p.setFont(_font(F.mono, px, True))
    p.setPen(QColor('#eef2f7'))
    p.drawText(QRectF(22, 44, W - 40, px + 12), Qt.AlignLeft | Qt.AlignVCenter, name)
    p.setPen(QColor('#f97316'))
    p.setFont(_font(F.sans, 15, True))
    p.drawText(QRectF(22, H - 32, 220, 22), Qt.AlignLeft | Qt.AlignVCenter,
               f'COOLING L{level}' if level else 'COOLING ON')
    p.end()
    return img


def render_alarm(cpu: float = 0.0, gpu: float = 0.0) -> QImage:
    """温度警报卡 (红底白字): CPU/GPU 大数字。"""
    F = prepare()
    img = QImage(W, H, QImage.Format.Format_RGB888)
    img.fill(QColor('#b91c1c'))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.TextAntialiasing, True)
    p.setPen(QColor('#ffe4e4'))
    p.setFont(_font(F.mono, 15, True))
    p.drawText(QRectF(20, 10, 240, 20), Qt.AlignLeft | Qt.AlignVCenter, 'TEMP ALERT')
    p.setPen(QColor('#ffd6d6'))
    p.setFont(_font(F.mono, 13))
    p.drawText(QRectF(W - 174, 10, 154, 20), Qt.AlignRight | Qt.AlignVCenter,
               time.strftime('%H:%M'))
    line = f'CPU {cpu:.0f}   GPU {gpu:.0f}'
    px = 44
    while px > 18:
        p.setFont(_font(F.mono, px, True))
        if p.fontMetrics().horizontalAdvance(line) <= W - 44:
            break
        px -= 2
    p.setFont(_font(F.mono, px, True))
    p.setPen(QColor('#ffffff'))
    p.drawText(QRectF(22, 40, W - 40, px + 12), Qt.AlignLeft | Qt.AlignVCenter, line)
    p.setPen(QColor('#ffd6d6'))
    p.setFont(_font(F.sans, 14, True))
    p.drawText(QRectF(22, H - 34, W - 44, 22), Qt.AlignLeft | Qt.AlignVCenter,
               'OVERHEAT - CHECK COOLING')
    p.end()
    return img


def decide_card(st: dict):
    """情境卡片纯函数决策 (可离线单测)。

    st 字段:
      mode ('card'|'custom'), usb, busy, pending, cards_enabled,
      cpu, gpu (°C, 可为 0), fullscreen (bool), proc (str, 已清洗),
      today ('YYYY-MM-DD'), custom_ok (自定义图文件存在),
      restore_done (本次进程已恢复过内容), names_ready (LHM 型号名就绪),
      since_connect, since_tec, since_upload (秒), fail_active (bool),
      last_key (上次成功上屏的内容 key, None = 尚未恢复)
    返回 (key, want) 或 None:
      want = ('info', today) | ('game', proc) | ('alarm', cpu, gpu) | ('custom', path)
    """
    if st.get('mode') not in ('card', 'custom'):
        return None
    if not st.get('usb') or st.get('busy') or st.get('pending'):
        return None
    if st.get('since_tec', 1e9) < 30 or st.get('fail_active'):
        return None                                  # TEC 平静窗 / 失败冷却
    if not st.get('restore_done'):
        # 首次恢复: 等 LHM 型号名就绪 (≤15s), 防卡上写死占位名
        if not st.get('names_ready') and st.get('since_connect', 1e9) < 15:
            return None
    elif st.get('since_upload', 1e9) < 120:
        return None                                  # 磨损预算: 任意两次上屏 ≥120s
    mode = st['mode']
    if mode == 'custom':
        if st.get('restore_done'):
            return None                              # 用户的图保持显示
        if st.get('custom_ok'):
            return 'custom', ('custom', st.get('custom_path', ''))
        return 'info', ('info', st['today'])
    if mode == 'card' and not st.get('card_auto', True):
        return None                                  # 信息卡自动上屏已关 (手动恢复按钮仍可用)
    # card 模式: 情境决策 (警报 > 游戏 > 信息)
    temp = max(st.get('cpu') or 0, st.get('gpu') or 0)
    if st.get('cards_enabled', True) and temp >= 85:
        return 'alarm', ('alarm', st.get('cpu') or 0, st.get('gpu') or 0)
    if st.get('cards_enabled', True) and st.get('fullscreen'):
        key = f"game:{st.get('proc') or 'GAME'}"
        return key, ('game', st.get('proc') or 'GAME')
    key = f"info:{st.get('today')}"
    if st.get('restore_done') and key == st.get('last_key'):
        return None                                  # 同日信息卡已上过
    return key, ('info', st['today'])
