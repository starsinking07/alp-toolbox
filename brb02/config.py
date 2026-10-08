# -*- coding: utf-8 -*-
"""配置持久化: %APPDATA%\\Brb02Toolbox\\config.json"""
from __future__ import annotations

import json
import os
import threading
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
    titlebar_btn: str = 'thin'          # 标题栏窗口按钮档位: 'thin' 纤细 | 'normal' 正常
    # ---- 风扇 ----
    fixed_rpm: int = 1200
    pre_curve_rpm: int = 0              # 档位记忆: 开启智能变频前的手动转速 (关闭后恢复)
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
    # 档位自动切换 (0x24/0x25 的档位字段; 官方档名 低噪/平衡/强效/超频, 设备无制冷片)
    tec_auto_enabled: bool = False      # 风扇档位随温度自动升/降档
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
    # 温度墙 (0.1.9): 过热保护, 优先级高于一切控制 (含智能启停)
    temp_wall_enabled: bool = True
    temp_wall_temp: float = 92.0        # 触发温度°C, 降 3°C 滞回解除
    # RGB
    rgb_on: bool = True
    gear_light: bool = False            # 挡位灯联动 (依赖 RGB 写入逆向)
    # 情景
    scene_enabled: bool = False
    scene_rules: list = field(default_factory=list)   # (旧版遗留, 已由 scene_profiles 取代)
    # 场景配置 (0.1.9, 官方"情景"同款+增强): 4 槽 × [固定转速/曲线方案/灯效模式 + 进程子串]
    # 每项: {'name': str, 'enabled': bool, 'rpm': int(0=不改), 'scheme': str(''=不改),
    #        'light_mode': int(-1=不改, RGB_MODE_*), 'processes': [前台进程子串,...]}
    scene_profiles: list = field(default_factory=list)
    # 快捷键
    hotkeys_enabled: bool = True
    # 屏幕图片页
    last_image_path: str = ''           # 记住上次选择的图片
    image_fit: str = 'stretch'          # 'stretch'=拉伸铺满 | 'cover'=等比放大后居中裁边
    # 屏幕写入磨损计数 (0.1.9): 累计成功上屏次数
    screen_upload_count: int = 0
    # 设备端开关 (0.1.8, 官方"设备信息"卡同款): 智能启停 (散热器风扇随电脑开关机)
    # 与通电自启 (散热器接入电源自动开机) —— 每次连接成功后经 0x02 下发 (同官方会话开场)。
    device_smart_startstop: bool = False
    device_power_on: bool = True          # 官方默认 = 通电自启开
    # 上传稳定模式 (0.1.8): 开启后图片上传走 0xC6 流控 (每包等设备流控帧, ~34s/张,
    # 永不 0x0C); 关闭 = 节拍式 (~16.8s, 失败自动重传且重传自动回落流控)。
    upload_stable: bool = False
    # 屏幕参数页显示配置 (0.1.8): 0xC2 SetLcdShowPos 三格的参数 id (最多 3,
    # 顺序=屏幕左右)。默认 [0, 1, 7] = CPU温度/GPU温度/时间 (官方默认)。
    # 可选 id 见 protocol.LCD_PARAM_DEFS。设备槽位/几何随此命令配置。
    param_page_ids: list = field(default_factory=lambda: [0, 1, 7])
    # 屏幕参数页第三槽数据源 (留档, 未启用): 设备三槽标签固画 (GPU℃/CPU℃//%) 且
    # 槽绑定固件固定, 第三槽 (ID03) 数据源选择经真机验证未生效, 暂时搁置。
    # 可选: cpu_load gpu_load ram disk; temps.load_snapshot() 负载体已就绪。
    param_slot3: str = 'cpu_load'
    # 散热器屏幕内容 (v3.21: 信息卡默认, 自定义图片是用户的选择)
    screen_mode: str = 'card'           # 'card'=信息卡(默认) | 'custom'=自定义图片
    screen_cards: bool = False          # 情境卡片 (v3.32): 默认关, 有需要的人在设置里开
    screen_card_auto: bool = False      # 自动上屏 (连接恢复 + 日期轮转); 默认关, 用户显式开启
    # 挡位预设
    presets: dict = field(default_factory=lambda: dict(DEFAULT_PRESETS))
    autostart_minimized: bool = False
    # 灯效模式编码语义版本: 1 = 旧 (0x06=刷新 / 0x08=响应 —— 实为标反);
    # 2 = 定案 (0x06=响应 / 0x08=刷新, 见 PROTOCOL.md §11.4 D2)。
    # 载入旧配置时把场景方案里存的 light_mode 6/8 互换一次, 然后打版本号防重复迁移。
    light_mode_enc: int = 1

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
            cfg._normalize()
            return cfg
        except Exception:
            # 坏文件改名保留 (可人工找回), 下次 save 不再覆盖它; 静默重置 = 配置无痕全丢
            try:
                os.replace(cls.path(), cls.path() + '.corrupt.bak')
            except OSError:
                pass
            try:
                from brb02.logbuf import LOGBUF
                LOGBUF.write('[config] 配置加载失败, 已改用默认值 (原文件保留为 *.corrupt.bak)')
            except Exception:
                pass
            # ⚠️ 2026-10-08 修复: 旧版直接 `return cls()` —— **跳过了 _normalize()**,
            # 于是"首次运行 (还没有配置文件)"与"配置文件损坏"两种情况都会拿到未归一化的配置
            # (最直观: scene_profiles 为空, 情景编辑器显示 0 槽而不是 4 槽)。
            cfg = cls()
            cfg._normalize()
            return cfg

    def _normalize(self) -> None:
        """旧配置/手改 JSON 的容错归一: 非法值回默认, 结构修正 (防延迟爆炸)。"""
        def _clamp(v, lo, hi, dflt):
            try:
                return max(lo, min(hi, int(v)))
            except (TypeError, ValueError):
                return dflt

        self.fixed_rpm = _clamp(self.fixed_rpm, 0, 4800, 1200)
        self.pre_curve_rpm = _clamp(self.pre_curve_rpm, 0, 4800, 0)
        self.autostart_minimized = bool(self.autostart_minimized)

        def _san_curve(c):
            pts = []
            for p in (c if isinstance(c, list) else []):
                try:
                    pts.append([int(p[0]), int(p[1])])
                except (TypeError, ValueError, IndexError):
                    continue
            pts.sort(key=lambda p: p[0])
            out, last_r = [], 0
            for t, r in pts:
                t = max(0, min(110, t))
                r = max(0, min(4800, r))
                if r < last_r:
                    r = last_r                      # 转速非递减 (曲线红线)
                if out and t == out[-1][0]:
                    out[-1][1] = r
                else:
                    out.append([t, r])
                last_r = r
            return out or [list(x) for x in DEFAULT_CURVE]

        self.curve = _san_curve(self.curve)
        if not isinstance(self.curve_profiles, dict):
            self.curve_profiles = {}
        self.curve_profiles = {str(k): _san_curve(v)
                               for k, v in self.curve_profiles.items()}
        if not isinstance(self.curve_active, str) or not self.curve_active:
            self.curve_active = '默认'

        raw_thr = self.tec_thresholds if isinstance(self.tec_thresholds, list) else []
        vals = [_clamp(v, 30, 100, d) for v, d in zip(raw_thr, (55, 65, 75))]
        while len(vals) < 3:
            vals.append(55 + 10 * len(vals))
        self.tec_thresholds = sorted(vals[:3])
        try:
            self.tec_hysteresis = max(0.5, min(10.0, float(self.tec_hysteresis)))
        except (TypeError, ValueError):
            self.tec_hysteresis = 2.0

        if self.start_stop_on_above < self.start_stop_off_below + 2:
            self.start_stop_on_above = self.start_stop_off_below + 2
        if self.temp_smoothing not in (1, 2, 3, 5, 10):
            self.temp_smoothing = 3
        if self.learning_bias not in ('balanced', 'cooling', 'quiet'):
            self.learning_bias = 'balanced'
        if not isinstance(self.learning_offsets, list):
            self.learning_offsets = []
        if not isinstance(self.scene_rules, list):
            self.scene_rules = []
        if not isinstance(self.scene_profiles, list):
            self.scene_profiles = []
        while len(self.scene_profiles) < 4:          # 固定 4 槽 (官方同款 配置A-D)
            self.scene_profiles.append({
                'name': f'配置{chr(65 + len(self.scene_profiles))}',
                'enabled': False, 'rpm': 0, 'scheme': '', 'light_mode': -1,
                'processes': []})
        del self.scene_profiles[4:]
        if not isinstance(self.scene_profiles, list):
            self.scene_profiles = []
        while len(self.scene_profiles) < 4:          # 固定 4 槽 (官方同款 配置A-D)
            self.scene_profiles.append({
                'name': f'配置{chr(65 + len(self.scene_profiles))}',
                'enabled': False, 'rpm': 0, 'scheme': '', 'light_mode': -1,
                'processes': []})
        del self.scene_profiles[4:]
        # 灯效模式语义迁移 (v0.1.9): 旧配置里 0x06/0x08 的"响应/刷新"标反了,
        # 场景方案存的 light_mode 若是 6 或 8 需互换一次 (仅此一次, 用版本号兜住)。
        try:
            if int(getattr(self, 'light_mode_enc', 1) or 1) < 2:
                _swap = {6: 8, 8: 6}
                for _p in self.scene_profiles:
                    if not isinstance(_p, dict):
                        continue
                    try:
                        _lm = int(_p.get('light_mode', -1))
                    except (TypeError, ValueError):
                        continue
                    if _lm in _swap:
                        _p['light_mode'] = _swap[_lm]
                self.light_mode_enc = 2
        except Exception:
            pass
        if not isinstance(self.presets, dict):
            self.presets = {}
        if self.conn_type not in ('usb', 'ble'):
            self.conn_type = 'usb'
        if self.image_fit not in ('stretch', 'cover'):
            self.image_fit = 'stretch'
        if self.screen_mode not in ('card', 'custom'):
            self.screen_mode = 'card'
        if self.titlebar_btn not in ('thin', 'normal'):
            self.titlebar_btn = 'thin'

    _save_lock = threading.Lock()

    def save(self):
        """原子写 (tmp + os.replace) + 线程锁: GUI 与 worker 并发保存不再交错损坏。"""
        try:
            data = json.dumps(asdict(self), ensure_ascii=False, indent=1)
            with self._save_lock:
                tmp = self.path() + '.tmp'
                with open(tmp, 'w', encoding='utf-8') as f:
                    f.write(data)
                os.replace(tmp, self.path())
        except Exception as e:
            try:
                from brb02.logbuf import LOGBUF
                LOGBUF.write(f'[config] 保存失败: {e!r}')
            except Exception:
                pass
