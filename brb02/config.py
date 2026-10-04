# -*- coding: utf-8 -*-
"""配置持久化: %APPDATA%\\Brb02Toolbox\\config.json"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict

DEFAULT_PRESETS = {
    '低噪': 1100,
    '平衡': 1600,
    '强效': 2100,
    '超频': 4000,
    '极限': 4800,
}

DEFAULT_CURVE = [(40, 1000), (55, 1500), (70, 2100), (85, 2800)]


@dataclass
class Config:
    # ---- 连接 ----
    conn_type: str = 'usb'              # 'usb' | 'ble'
    auto_switch: bool = True            # 拔线自动切蓝牙, 插线自动切回 USB
    ble_address: str = ''               # 最近一次成功连接的散热器蓝牙地址 (直连免广播)
    # ---- 外观 ----
    dark: bool = False
    # ---- 风扇 ----
    fixed_rpm: int = 1200
    restore_on_start: bool = False
    curve_enabled: bool = False
    curve: list = field(default_factory=lambda: [list(x) for x in DEFAULT_CURVE])
    curve_source: str = 'auto'          # auto/cpu/gpu
    curve_hysteresis: int = 80
    # 曲线方案: {方案名: [[temp,rpm],...]}
    curve_profiles: dict = field(default_factory=dict)
    curve_active: str = '默认'
    # 温度平滑: 采样数(EMA); 尖峰过滤
    temp_smoothing: int = 3             # 1=即时 2/3/5/10=EMA 强度
    spike_filter: bool = True
    # TEC 自动档位
    tec_auto_enabled: bool = False      # 半导体制冷随温度自动升/降档
    tec_thresholds: list = field(default_factory=lambda: [55, 65, 75])   # L2/L3/L4 进入温度°C
    tec_hysteresis: float = 2.0         # 降档回落滞回 °C
    # 智能启停
    start_stop_enabled: bool = False
    start_stop_off_below: float = 45.0  # 低于此温度关风扇
    start_stop_on_above: float = 50.0   # 高于此温度恢复
    # 自适应学习
    learning_enabled: bool = False
    learning_target: float = 65.0       # 目标稳态温度
    learning_bias: str = 'balanced'     # balanced/cooling/quiet
    learning_offsets: list = field(default_factory=list)   # 每锚点偏移(%), 与19锚点对齐
    # 温升预判
    prediction_enabled: bool = False
    # RGB
    rgb_on: bool = True
    gear_light: bool = False            # 挡位灯联动 (依赖 RGB 写入逆向)
    # 情景
    scene_enabled: bool = False
    scene_rules: list = field(default_factory=list)
    # 快捷键
    hotkeys_enabled: bool = True
    # 屏幕图片页
    last_image_path: str = ''           # 记住上次选择的图片
    image_fit: str = 'stretch'          # 'stretch'=拉伸铺满 | 'cover'=等比放大后居中裁边
    # 散热器屏幕内容 (v3.21: 信息卡默认, 自定义图片是用户的选择)
    screen_mode: str = 'card'           # 'card'=信息卡(默认) | 'custom'=自定义图片
    screen_cards: bool = False          # 情境卡片 (v3.32): 默认关, 有需要的人在设置里开
    screen_card_auto: bool = True       # 信息卡自动上屏 (连接恢复 + 日期轮转)
    # 挡位预设
    presets: dict = field(default_factory=lambda: dict(DEFAULT_PRESETS))
    autostart_minimized: bool = False

    # ---- IO ----
    @classmethod
    def path(cls) -> str:
        base = os.environ.get('APPDATA', os.path.expanduser('~'))
        d = os.path.join(base, 'Brb02Toolbox')
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, 'config.json')

    @classmethod
    def load(cls) -> 'Config':
        try:
            with open(cls.path(), encoding='utf-8') as f:
                data = json.load(f)
            cfg = cls()
            for k, v in data.items():
                if hasattr(cfg, k):
                    setattr(cfg, k, v)
            return cfg
        except Exception:
            return cls()

    def save(self):
        try:
            with open(self.path(), 'w', encoding='utf-8') as f:
                json.dump(asdict(self), f, ensure_ascii=False, indent=1)
        except Exception as e:
            print('[config] 保存失败:', e)
