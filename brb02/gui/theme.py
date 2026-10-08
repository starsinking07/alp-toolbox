# -*- coding: utf-8 -*-
"""主题系统: 对齐 FanControl 真机截图的视觉体系 (悬浮圆角侧栏/大圆角卡片/柔和阴影)。"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPalette, QPen, QPixmap


def _pen(color: str, width: float) -> QPen:
    pen = QPen(QColor(color), width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


LIGHT = {
    'bg': '#eceff5',            # 窗口底 (比卡片深一档的灰)
    'fg': '#16181d',
    'card': '#ffffff',
    'muted': '#f1f3f8', 'muted_fg': '#7a8494',
    'border': '#e4e8ef',
    'primary': '#2f6df6', 'primary_fg': '#ffffff',
    'danger': '#dc2626',
    'sidebar': '#ffffff',
    'hot': '#ef4444', 'warm': '#f97316', 'ok': '#10b981',
    'chip_bg': '#e8eefc', 'chip_border': '#d5e0fa',
    'shadow': '0 10px 30px -12px rgba(23,31,54,0.10), 0 2px 6px -2px rgba(23,31,54,0.05)',
    'shadow_soft': '0 4px 14px -6px rgba(23,31,54,0.08)',
    'tile': '#f5f7fb',
    'hover_line': '#c9d8fb',
}

DARK = {
    'bg': '#07090d', 'fg': '#eef2f7',
    'card': '#11161d', 'muted': '#1a2029', 'muted_fg': '#98a2b3',
    'border': '#232a35',
    'primary': '#ff4b26', 'primary_fg': '#fff7f3',
    'danger': '#ef4444',
    'sidebar': '#0a0d12',
    'hot': '#ef4444', 'warm': '#fb923c', 'ok': '#34d399',
    'chip_bg': 'rgba(255,75,38,0.12)', 'chip_border': 'rgba(255,75,38,0.30)',
    'shadow': '0 12px 34px -14px rgba(0,0,0,0.65)',
    'shadow_soft': '0 4px 14px -6px rgba(0,0,0,0.5)',
    'tile': '#161c24',
    'hover_line': 'rgba(255,75,38,0.4)',
}

CHART = {
    'cpu': ('#2f6df6', '#6aa6ff'),
    'gpu': ('#f97316', '#ff9a62'),
    'fan': ('#10b981', '#34d399'),
    'grid': ('#e8edf5', '#252b35'),
    'tick': ('#6b7280', '#98a2b3'),
}


def chart_color(key: str, dark: bool) -> str:
    return CHART[key][1] if dark else CHART[key][0]


def temp_color(temp, dark=False) -> str:
    if not temp:
        return '#98a2b3'
    if temp > 85:
        return '#ef4444'
    if temp > 75:
        return '#fb923c' if dark else '#f97316'
    return DARK['primary'] if dark else LIGHT['primary']


def _chevron_path(dark: bool) -> str:
    """生成下拉箭头小图 (QSS image 引用; QSS 三角形边框技巧不生效)"""
    import os
    base = os.environ.get('APPDATA', '.')
    d = os.path.join(base, 'Brb02Toolbox')
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f'chevron_{"dark" if dark else "light"}.png').replace('\\', '/')
    pm = QPixmap(24, 24)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(_pen('#98a2b3', 2.4))
    p.drawLine(QPointF(7, 9), QPointF(12, 14))
    p.drawLine(QPointF(12, 14), QPointF(17, 9))
    p.end()
    pm.save(path)
    return path


def build_qss(dark: bool) -> str:
    t = DARK if dark else LIGHT
    chevron = _chevron_path(dark).replace('\\', '/')
    qss = f"""
* {{
    font-family: "Manrope", "Microsoft YaHei UI", "Segoe UI", sans-serif;
    outline: none;
}}
QMainWindow, QWidget#AppShell {{
    background: {t['bg']}; color: {t['fg']};
    border-radius: 14px;
}}
QWidget#ContentArea {{ background: transparent; }}
QToolTip {{
    background: {t['card']}; color: {t['fg']}; border: 1px solid {t['border']};
    border-radius: 8px; padding: 6px 10px; font-size: 12px;
}}

/* ---- 数字读数: Manrope + 等宽数字特性 (代码里 setFeature tnum) ---- */
QLabel#StatVal, QWidget#TitleBadge, QWidget#TitleBadgeActive, QLabel#HeroRpm {{
    letter-spacing: 0.01em;
}}

/* ---- SpinBox: 隐藏原生上下箭头, 干净输入框 (对齐原版 NumberInput) ---- */


/* ---- 卡片: 大圆角 + 柔和阴影 ---- */
QFrame[card="true"] {{
    background: {t['card']}; border: none;
    border-radius: 22px;
}}
QLabel#CardTitle {{ font-size: 15px; font-weight: 700; color: {t['fg']}; background: transparent; }}
QLabel#CardHint  {{ font-size: 12px; color: {t['muted_fg']}; background: transparent; }}
QLabel#PageTitle {{ font-size: 24px; font-weight: 800; color: {t['fg']}; background: transparent; }}

/* ---- 标题栏徽章: 白底胶囊 ---- */
QWidget#TitleBar {{ background: transparent; }}
QWidget#TitleBadge {{
    background: {t['card']}; border: 1px solid {t['border']};
    border-radius: 18px; padding: 6px 16px; font-size: 13px; font-weight: 600;
    color: {t['muted_fg']};
}}
QWidget#TitleBadgeActive {{
    background: {t['card']}; border: 1px solid {t['chip_border']};
    border-radius: 18px; padding: 6px 16px; font-size: 13px; font-weight: 700;
    color: {t['primary']};
}}
QPushButton#WinBtn {{
    border: none; background: transparent; border-radius: 8px;
    color: {t['muted_fg']}; font-size: 13px;
}}
QPushButton#WinBtn:hover {{ background: {t['muted']}; color: {t['fg']}; }}
QPushButton#WinBtnClose:hover {{ background: #ef4444; color: white; }}

/* ---- 悬浮侧栏面板 (圆角 + 细边) ---- */
QWidget#Sidebar {{
    background: {t['sidebar']}; border: 1px solid {t['border']}; border-radius: 20px;
}}
QLabel#Brand {{
    color: {t['primary']}; font-size: 21px; font-weight: 800;
    background: transparent;
}}
QPushButton#DockBtn {{
    border: none; background: transparent; border-radius: 14px;
    color: {t['muted_fg']}; font-size: 13px; font-weight: 600; text-align: left;
    padding: 0 14px; min-height: 48px;
}}
QPushButton#DockBtn:hover {{ background: {t['muted']}; color: {t['fg']}; }}
QPushButton#DockBtn:checked {{
    background: {t['chip_bg']}; color: {t['primary']}; font-weight: 700;
}}

/* ---- 按钮 ---- */
QPushButton {{
    background: {t['card']}; color: {t['fg']}; border: 1px solid {t['border']};
    border-radius: 12px; padding: 9px 18px; font-size: 13px; font-weight: 600;
}}
QPushButton:hover {{ border-color: {t['hover_line']}; }}
QPushButton:disabled {{ color: {t['muted_fg']}; background: {t['muted']}; }}
QPushButton#Primary {{
    background: {t['primary']}; color: {t['primary_fg']}; border: none; font-weight: 700;
    padding: 10px 22px;
}}
QPushButton#Primary:hover {{ background: {t['primary']}; }}
QPushButton#Primary:disabled {{ background: {t['muted']}; color: {t['muted_fg']}; }}
QPushButton#GearBtn {{
    background: {t['tile']}; border: 1px solid {t['border']};
    border-radius: 16px; padding: 10px 14px; text-align: left; font-size: 13px;
}}
QPushButton#GearBtn:checked {{ border: 2px solid {t['primary']}; }}

/* ---- 瓦片 / 设置行 ---- */
QFrame#StatTile {{ background: {t['tile']}; border: none; border-radius: 16px; }}
QFrame#StatTile QLabel {{ background: transparent; border: none; }}
QLabel#StatCap {{ font-size: 12px; color: {t['muted_fg']}; }}
QLabel#StatVal {{ font-size: 16px; font-weight: 700; color: {t['fg']}; }}
QFrame#SettingRow {{
    background: {t['tile']}; border: none; border-radius: 16px;
}}
QFrame#SettingRow:hover {{ background: {t['muted']}; }}
QFrame#SettingRow QLabel {{ background: transparent; }}
QLabel#RowTitle {{ font-size: 14px; font-weight: 600; color: {t['fg']}; background: transparent; }}
QLabel#RowDesc {{ font-size: 12px; color: {t['muted_fg']}; background: transparent; }}
QFrame#IconBox {{
    background: {t['muted']}; border: none; border-radius: 12px;
}}
QLabel#IconChar {{ font-size: 15px; color: {t['muted_fg']}; background: transparent; }}

/* ---- 分段页签 ---- */
QWidget#SegmentBar {{ background: {t['tile']}; border-radius: 16px; }}
QPushButton#Segment {{
    background: transparent; border: none; border-radius: 13px;
    color: {t['muted_fg']}; font-size: 14px; font-weight: 600; min-height: 44px;
}}
QPushButton#Segment:checked {{
    background: {t['chip_bg']}; color: {t['primary']}; font-weight: 700;
}}

QSlider::groove:horizontal {{ height: 8px; border-radius: 4px; background: {t['muted']}; }}
QSlider::sub-page:horizontal {{ background: {t['primary']}; border-radius: 4px; }}
QSlider::handle:horizontal {{
    width: 18px; height: 18px; margin: -6px 0; border-radius: 9px;
    background: {t['primary']}; border: 2px solid {t['card']};
}}
/* 禁用态 (2026-10-08): 此前无此规则 ⇒ 被禁用的滑条仍是主色蓝, 看起来像可用 ——
   屏幕图片页"缩放与位置"在「拉伸铺满」下禁用时尤其误导 (用户以为滑条坏了)。 */
QSlider::sub-page:horizontal:disabled {{ background: {t['muted']}; border-radius: 4px; }}
QSlider::handle:horizontal:disabled {{
    width: 18px; height: 18px; margin: -6px 0; border-radius: 9px;
    background: {t['muted_fg']}; border: 2px solid {t['card']};
}}

/* ---- 输入控件: 无边框灰底 (分数 DPI 下无边框=无伪影), 聚焦主色描边 ---- */
QComboBox, QSpinBox, QLineEdit {{
    background: {t['tile']}; color: {t['fg']};
    border: 2px solid transparent; border-radius: 12px;
    padding: 6px 12px; font-size: 13px; font-weight: 600;
}}
QComboBox:hover, QSpinBox:hover, QLineEdit:hover {{ background: {t['muted']}; }}
QComboBox:focus, QSpinBox:focus, QLineEdit:focus {{
    background: {t['card']}; border-color: {t['primary']};
}}
QComboBox::drop-down {{ border: none; width: 22px; }}
QSpinBox::up-button, QSpinBox::down-button {{
    width: 0; height: 0; border: none; background: transparent;
}}
QSpinBox::up-arrow, QSpinBox::down-arrow {{ image: none; width: 0; height: 0; }}
QComboBox::down-arrow {{
    image: url({chevron}); width: 14px; height: 14px;
}}
QComboBox QAbstractItemView {{
    background: {t['card']}; color: {t['fg']};
    border: 1px solid {t['border']}; border-radius: 14px;
    padding: 6px; outline: none;
    selection-background-color: {t['chip_bg']}; selection-color: {t['primary']};
}}
QComboBoxPrivateContainer {{
    background: {t['card']}; border-radius: 14px;
}}
QComboBox QAbstractItemView::item {{
    min-height: 34px; border-radius: 9px; padding: 4px 10px; margin: 1px 2px;
    color: {t['fg']}; background: transparent;
}}
QComboBox QAbstractItemView::item:selected, QComboBox QAbstractItemView::item:hover {{
    background: {t['chip_bg']}; color: {t['primary']};
}}

QCheckBox {{ font-size: 13px; color: {t['fg']}; spacing: 8px; background: transparent; }}
QCheckBox::indicator {{
    width: 18px; height: 18px; border-radius: 5px;
    border: 1px solid {t['border']}; background: {t['card']};
}}
QCheckBox::indicator:checked {{ background: {t['primary']}; border-color: {t['primary']}; }}

QProgressBar {{ background: {t['muted']}; border: none; border-radius: 4px; }}
QProgressBar::chunk {{ background: {t['primary']}; border-radius: 4px; }}

QLabel#HeroRpm {{ font-size: 30px; font-weight: 800; }}
QLabel#Muted {{ color: {t['muted_fg']}; font-size: 12px; background: transparent; }}
QLabel#WarnBanner {{
    background: {t['tile']}; color: {t['muted_fg']};
    border: 1px solid {t['border']}; border-radius: 14px; padding: 10px 16px;
    font-size: 13px;
}}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {t['border']}; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {t['muted_fg']}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget {{ background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
"""
    return qss


def build_palette(dark: bool) -> QPalette:
    """应用级调色板 —— 深色模式的最后一块拼图。

    QSS 只能罩住挂了规则/objectName 的控件; 裸 QLabel (无显式颜色、无 objectName)
    的文字走 Qt 调色板 = 浅色系统的黑字, 深色下全部不可见 (hero 标题/FAQ 问题行/
    日志文本/开源仓库行等)。Fusion + 深色调色板是标准兜底, 顺带把原生弹窗
    (QMessageBox/QMenu/提示框) 一并染深。"""
    t = DARK if dark else LIGHT
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(t['card']))
    pal.setColor(QPalette.WindowText, QColor(t['fg']))
    pal.setColor(QPalette.Base, QColor(t['card']))
    pal.setColor(QPalette.AlternateBase, QColor(t['muted']))
    pal.setColor(QPalette.Text, QColor(t['fg']))
    pal.setColor(QPalette.Button, QColor(t['muted']))
    pal.setColor(QPalette.ButtonText, QColor(t['fg']))
    pal.setColor(QPalette.ToolTipBase, QColor(t['card']))
    pal.setColor(QPalette.ToolTipText, QColor(t['fg']))
    pal.setColor(QPalette.Highlight, QColor(t['primary']))
    pal.setColor(QPalette.HighlightedText, QColor('#ffffff'))
    try:
        pal.setColor(QPalette.PlaceholderText, QColor(t['muted_fg']))
    except Exception:
        pass
    pal.setColor(QPalette.Disabled, QPalette.WindowText, QColor(t['muted_fg']))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor(t['muted_fg']))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(t['muted_fg']))
    return pal


