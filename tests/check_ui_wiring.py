# -*- coding: utf-8 -*-
"""UI 接线全面自检 (0.1.8): 每个功能的 UI 入口 → 控件 → 信号 → 数据链。
背景: 本周两次"构造了但没加进容器/没接线"事故 —— 本脚本用控件树断言兜住。
跑法: python tools/check_ui_wiring.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication, QPushButton, QComboBox, QSlider, QSpinBox, QCheckBox
from PySide6.QtCore import Signal, QObject

FAILS = []
def check(name, cond, detail=''):
    print(f'  [{"PASS" if cond else "FAIL"}] {name} {detail}')
    if not cond:
        FAILS.append(name)

app = QApplication([])
from brb02.config import Config
from brb02.gui import theme as th

class _Tok:
    def __init__(self, dark):
        self._dark = dark; self.t = th.DARK if dark else th.LIGHT
    def __getattr__(self, k): return self.t[k]
    def __getitem__(self, k): return self.t[k]
    def __contains__(self, k): return k in self.t
class _D(QObject):
    def __getattr__(self, k): return False if k == 'connected' else None
class _T:
    cpu_name = 'C'; gpu_name = 'G'
    cpu_load = 1.0; gpu_load = 2.0; disk_active = 3.0
    _ram_percent = staticmethod(lambda: 4)
class _W(QObject):
    uploadProgress = Signal(int, int); uploadFinished = Signal(dict)
    connectionChanged = Signal(bool, str); uploadCountChanged = Signal(int)
    deviceSwitchesChanged = Signal(dict); screenReadFinished = Signal(dict)
    def __init__(self):
        super().__init__(); self.device = _D(); self.temps = _T()
    def set_device_switches(self, s, p): pass
    def set_lighting(self, *a, **k): pass
    def start_image_upload(self, *a, **k): pass
    def start_screen_read(self): return True
    def send_param_page_config(self): pass

cfg = Config.load(); cfg.dark = False
ctx = {'cfg': cfg, 'worker': _W(), 'dark': False,
       'main': type('M', (), {'is_connected': False, 'refresh_curve_cache': lambda s: None})(),
       'theme': _Tok(False)}

# ===== ControlPage =====
print('=== ControlPage ===')
from brb02.gui.pages import ControlPage
cp = ControlPage(dict(ctx))
check('温度墙: 开关+阈值', hasattr(cp, 'tgl_wall') and hasattr(cp, 'spin_wall'))
check('温度墙: 开关与配置一致', cp.tgl_wall.isChecked() == bool(cfg.temp_wall_enabled)
      and cp.spin_wall.value() == int(cfg.temp_wall_temp))
check('磨损计数标签', hasattr(cp, 'lbl_wear'))
check('情景编辑器 4 行', len(getattr(cp, 'scene_edits', [])) == 4)
check('设备信息卡: 两开关', hasattr(cp, 'tgl_dev_smart') and hasattr(cp, 'tgl_dev_power'))
check('设备信息卡: 开关与配置一致', cp.tgl_dev_smart.isChecked() == bool(cfg.device_smart_startstop)
      and cp.tgl_dev_power.isChecked() == bool(cfg.device_power_on))
check('固件版本标签', hasattr(cp, 'lbl_fw'))
btns = [b.text() for b in cp.findChildren(QPushButton)]
check('参数页显示按钮', any('参数页显示' in t for t in btns))

# ===== ScreenPage =====
print('=== ScreenPage ===')
from brb02.gui.pages import ScreenPage
sp = ScreenPage(dict(ctx))
check('缩放/平移滑条', all(hasattr(sp, a) for a in ('sld_zoom', 'sld_panx', 'sld_pany')))
check(f"初始 fit={sp._fit} 滑条态与之一致",
      sp.sld_zoom.isEnabled() == (sp._fit == 'cover'))
sp.fit_combo.setCurrentIndex(max(0, sp.fit_combo.findData('stretch')))
check('切 stretch → 禁用', not sp.sld_zoom.isEnabled())
sp.fit_combo.setCurrentIndex(max(0, sp.fit_combo.findData('cover')))
check('切 cover → 启用', sp.sld_zoom.isEnabled())
check('备份按钮', hasattr(sp, 'btn_read') or any('备份当前屏图' in b.text() for b in sp.findChildren(QPushButton)))
if sp.hist_list is None:
    # 官方装备箱画布缓存目录 (C:\ProgramData\BlackSharkEquipmentBox\Brb02Image) 不存在
    # 时历史图卡整体不建 — 条件功能, 跳过而非失败 (公共仓库/未装官方软件的机器可移植)
    print('  [SKIP] 历史图列表 (本机无官方装备箱画布缓存)')
else:
    check('历史图列表', hasattr(sp, 'hist_list'))
    n_hist = sp.hist_list.count()
    check(f'历史图加载 ({n_hist} 张)', n_hist > 0)

# ===== 模式互斥联动 =====
print('=== 灯效联动流 ===')
from brb02.service import DeviceWorker
w2 = DeviceWorker.__new__(DeviceWorker)
w2.device = _D(); w2.config = cfg
w2._audio_pusher = None; w2._keypress_pusher = None
w2._uploading = False; w2._upload_cancel = __import__('threading').Event()
w2.apply_light_mode_effects(0x07)
check('音频同步 → 0x15 源启动', w2._audio_pusher is not None)
w2.apply_light_mode_effects(0x08)
check('响应 → 音频停/按键源启动', w2._audio_pusher is None and w2._keypress_pusher is not None)
w2.apply_light_mode_effects(0x04)
check('其他模式全停', w2._audio_pusher is None and w2._keypress_pusher is None)

# ===== 温度墙引擎 =====
print('=== 温度墙引擎 ===')
sent = []
class _D2:
    connected = True
    def set_fixed_rpm(self, rpm, level=1, src=None): sent.append((rpm, level, src))
w3 = DeviceWorker.__new__(DeviceWorker)
w3.device = _D2(); w3.config = cfg
w3.config.temp_wall_enabled = True; w3.config.temp_wall_temp = 92.0
w3.config.start_stop_enabled = False; w3.config.curve_enabled = False
w3.config.fixed_rpm = 1200
_tec_auto_orig = w3.config.tec_auto_enabled
w3.config.tec_auto_enabled = False   # 钉死输入: cfg 是真实配置, TEC 自动档开着时档位走 _tec_level 而非转速映射
w3._temp_wall_active = False; w3._ss_stopped = False
w3._last_sent_rpm = 1500; w3._last_send_ts = 0.0
w3._manual_until = 0.0; w3._last_tec_sent = None; w3._tec_level = 1
w3._tec_tick = lambda *a, **k: None
now = __import__('time').time()
w3._control_tick(93.0, 40.0, now)
check('93°C 触发 4800/L4', sent[-1][0] == 4800 and sent[-1][1] == 4 and w3._temp_wall_active)
check('src 原样保留 (0x24 byte0)', sent[-1][2] == getattr(w3, '_cooling_src', None)
      or sent[-1][2] is None)
# TEC 自动档开启: 墙触发档位跟随 _tec_level (v3.35 档位合并帧设计), RPM 仍拉满
sent.clear()
w3b = DeviceWorker.__new__(DeviceWorker)
w3b.device = _D2(); w3b.config = cfg
w3b.config.tec_auto_enabled = True
w3b._temp_wall_active = False; w3b._ss_stopped = False
w3b._last_sent_rpm = None; w3b._last_send_ts = 0.0
w3b._manual_until = 0.0; w3b._last_tec_sent = None; w3b._tec_level = 3
w3b._tec_tick = lambda *a, **k: None
w3b._control_tick(93.0, 40.0, now)
check('墙+TEC自动档: 4800 且档位跟随 _tec_level', sent[-1][0] == 4800 and sent[-1][1] == 3)
w3.config.tec_auto_enabled = _tec_auto_orig   # 还原, 后续段共用 cfg

# ===== 0x07 ID 表 =====
print('=== 0x07 ID 表 ===')
from brb02.service import DeviceWorker as DW
w4 = DW.__new__(DW)
class _D4:
    def push_host_info(self, e): w4._e = e
class _T4:
    cpu_load = 1.0; gpu_load = 2.0
    _disk_percent = staticmethod(lambda: 3)
    _ram_percent = staticmethod(lambda: 6)
    gpu_power = None
w4.device = _D4(); w4.temps = _T4(); w4.config = cfg
e = dict(w4._host_info_entries(65.0, 42.0))
check('ID02=CPU 负载', e.get(0x02) == 1)
check('ID03=GPU 负载', e.get(0x03) == 2)
check('ID05=磁盘占用', e.get(0x05) == 3)
check('ID06=内存占用', e.get(0x06) == 6)

print()
if FAILS:
    print(f'✗ {len(FAILS)} 项未通过: {FAILS}')
    sys.exit(1)
print('✓ UI 接线全面自检通过')
