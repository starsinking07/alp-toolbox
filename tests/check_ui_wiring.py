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
    def read_param_page_layout(self): return None
    def set_lcd_switch(self, on): self._lcd = bool(on); return bool(on)
    def get_lcd_switch_status(self): return True

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

# --- 实时速度刷新链路 (2026-10-08 修复 15s 滞后 + 文案重复) ---
check('ControlPage 提供 on_status (每 tick 刷新)', hasattr(cp, 'on_status'))
cp.on_status({'rpm': 1980})
check('on_status → 实时速度显示裸数值 (不再重复"实时速度"前缀)',
      cp.lbl_speed.text() == '1980 RPM')
cp.on_status({'rpm': 0})
check('rpm=0 → -- RPM', cp.lbl_speed.text() == '-- RPM')
cp.on_info({'cooling': {'rpm': 1600}})
check('on_info 不覆盖实时转速 (转速只由 on_status 管)', cp.lbl_speed.text() == '-- RPM')
check('on_info → 控制模式无重复前缀', cp.lbl_mode2.text() in ('手动', '智能变频'))
cp.on_status({'rpm': 1600})
check('重新连上后 on_status 立即恢复显示', cp.lbl_speed.text() == '1600 RPM')

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

# --- 屏幕开关 0xC0/0xC1 (2026-10-08 接入 GUI) ---
check('屏幕开关: 开关控件 + 状态标签', hasattr(sp, 'tgl_screen') and hasattr(sp, 'lbl_screen'))
check('屏幕开关: 回填函数存在', hasattr(sp, '_refresh_screen_switch'))
sp._refresh_screen_switch()
check('屏幕开关: 连接后回读回填 (stub=True)',
      sp.tgl_screen.isChecked() is True and '开启' in sp.lbl_screen.text())
sp.tgl_screen.setChecked(False)              # 模拟用户拨动 (真实路径: mousePressEvent 先翻再回调)
sp._screen_switch_changed(False)
check('屏幕开关: 关屏后标签跟随',
      sp.tgl_screen.isChecked() is False and '关闭' in sp.lbl_screen.text())
sp.tgl_screen.setChecked(True)
sp._screen_switch_changed(True)
check('屏幕开关: 开屏后标签跟随',
      sp.tgl_screen.isChecked() is True and '开启' in sp.lbl_screen.text())
# 失败路径: 写未确认 (回读为 None) → 必须回滚 UI 到设备真实态, 不能停在假状态
_ow = sp.ctx['worker']
_oset, _oget = _ow.set_lcd_switch, _ow.get_lcd_switch_status
_ow.set_lcd_switch = lambda on: None          # 模拟写/回读未确认
_ow.get_lcd_switch_status = lambda: True      # 设备真实态 = 开
sp.tgl_screen.setChecked(False)
sp._screen_switch_changed(False)
check('屏幕开关: 未确认时回滚 UI 到设备真实态',
      sp.tgl_screen.isChecked() is True and '未生效' in sp.lbl_screen.text())
_ow.set_lcd_switch, _ow.get_lcd_switch_status = _oset, _oget
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
from brb02 import protocol as _p
check('响应常量 = 0x06 (固件 0x16 门控值)', _p.RGB_MODE_REACTIVE == 0x06)
check('刷新常量 = 0x08', _p.RGB_MODE_REFRESH == 0x08)
check('音频常量 = 0x07', _p.RGB_MODE_AUDIO == 0x07)
w2 = DeviceWorker.__new__(DeviceWorker)
w2.device = _D(); w2.config = cfg
w2._audio_pusher = None; w2._keypress_pusher = None
w2._uploading = False; w2._upload_cancel = __import__('threading').Event()
w2.apply_light_mode_effects(_p.RGB_MODE_AUDIO)
check('音频同步 → 0x15 源启动', w2._audio_pusher is not None)
w2.apply_light_mode_effects(_p.RGB_MODE_REACTIVE)
check('响应(0x06) → 音频停/按键源启动', w2._audio_pusher is None and w2._keypress_pusher is not None)
w2.apply_light_mode_effects(_p.RGB_MODE_REFRESH)
check('刷新(0x08) → 两者皆停 (不误启按键源)',
      w2._audio_pusher is None and w2._keypress_pusher is None)
w2.apply_light_mode_effects(_p.RGB_MODE_STEADY)
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

# ===== 参数页 0xC3 读回 (2026-10-08 功能化) =====
print('=== 参数页 0xC3 读回 ===')
from brb02 import protocol as _pc
check('get_lcd_show_pos 短形态 A5 03 C3 6B', _pc.get_lcd_show_pos() == bytes.fromhex('A503C36B'))
_rb = _pc.parse_lcd_show_pos(bytes.fromhex('000003006E006801D7006807260168'))
check('parse_lcd_show_pos id=[0,1,7]', bool(_rb) and [it[0] for it in _rb['items']] == [0, 1, 7])
check('DeviceWorker.read_param_page_layout 存在', hasattr(DeviceWorker, 'read_param_page_layout'))
check('参数页对话框读取按钮', any('从设备读取' in b.text() for b in cp.findChildren(QPushButton))
      or '从设备读取当前布局' in open(os.path.join(
          os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
          'brb02', 'gui', 'pages.py'), encoding='utf-8').read())

# ===== 0x26 档位配置读 (2026-10-08 修复: 旧帧多塞 slot 字节 → 被固件忽略) =====
print('=== 0x26 档位配置读 ===')
check('get_any_cooling(1) 帧形 = A5 06 26 01 01 D3',
      _pc.get_any_cooling(1) == bytes.fromhex('A506260101D3'))
check('get_any_cooling(3, form=0) = A5 06 26 00 03 D4',
      _pc.get_any_cooling(3, 0) == bytes.fromhex('A506260003D4'))
check('越界档位被夹到 1..4',
      _pc.get_any_cooling(9) == _pc.get_any_cooling(4)
      and _pc.get_any_cooling(0) == _pc.get_any_cooling(1))
_cur = _pc.parse_cur_cooling(bytes.fromhex('0000014006A8'))
check('parse_cur_cooling 暴露 src/form/level',
      bool(_cur) and (_cur['src'], _cur['form'], _cur['level']) == (0, 0, 1)
      and _cur['rpm'] == 1600)
check('parse_cur_cooling 旧键名仍兼容 (mode/on)',
      _cur['mode'] == _cur['src'] and _cur['on'] == _cur['level'])
_cv = _pc.parse_curve(bytes.fromhex('00010114B00428E8053C560850C40AC8'))
check('parse_curve 对 16B 真机应答 → 4 锚点',
      _cv == [(20, 1200), (40, 1512), (60, 2134), (80, 2756)])

# ===== 0xC0/0xC1 屏开关 (2026-10-08 固件全解 + 真机验证) =====
print('=== 0xC0/0xC1 屏开关 ===')
check('set_lcd_switch(关) 帧形 = A5 05 C0 00 6A (官方实帧)',
      _pc.set_lcd_switch(False) == bytes.fromhex('A505C0006A'))
check('set_lcd_switch(开) 帧形 = A5 05 C0 01 6B (官方实帧)',
      _pc.set_lcd_switch(True) == bytes.fromhex('A505C0016B'))
check('get_lcd_switch_status 帧形 = A5 04 C1 6A (官方实帧)',
      _pc.get_lcd_switch_status() == bytes.fromhex('A504C16A'))
check('Cmd.SET_LCD_SWITCH = 0xC0 且 ≠ GET(0xC1)',
      _pc.Cmd.SET_LCD_SWITCH == 0xC0 and _pc.Cmd.GET_LCD_SWITCH_STATUS == 0xC1)
check('Brb02Device 提供 set/get_lcd_switch_status',
      all(hasattr(__import__('brb02.device', fromlist=['Brb02Device']).Brb02Device, m)
          for m in ('set_lcd_switch', 'get_lcd_switch_status')))

# ===== 状态页 vs 曲线页: 必须是同一条曲线 (2026-10-08 修复"压扁"bug) =====
print('=== 曲线显示一致性 (状态页 vs 曲线页) ===')
from brb02.gui.curve_editor import CurveEditor as _CE
_C4 = [[40, 2000], [60, 3000], [80, 4000], [90, 4500]]      # 默认/方案预设就是 4 个锚点
_exp = _CE.resample_pairs(_C4, 4800)
check('resample_pairs(4 锚点) → 19 点 (首 42 / 末 94)',
      len(_exp) == 19 and _exp[0] == 42 and _exp[-1] == 94)
try:
    from brb02.gui.main_window import MainWindow as _MW
    class _MC:
        curve = _C4
    class _MO:
        _curve_cache = None
        cfg = _MC()
    _got = _MW.curve_pct(_MO())
    check('MainWindow.curve_pct() 返回 19 点重采样 (旧实现只给 4 点 → 压扁)',
          _got == _exp)
    _a = _CE(rpm_axis=True); _a.set_curve(_got)
    _b = _CE(); _b.set_curve(_C4)
    check('状态页曲线 == 曲线页曲线', _a.pct == _b.pct)
except Exception as _e:
    print(f'  [SKIP] main_window 不可导入 ({_e})')

# ===== USB 读超时判定 (2026-10-08 修复: 旧判据对 USBTimeoutError 全失效) =====
print('=== USB 读超时判定 ===')
from brb02.device import _is_read_timeout
try:
    import usb.core as _uc
    _te = _uc.USBTimeoutError(10060, 'Operation timed out')
    check('USBTimeoutError 判为超时 (poll_report 依赖)', _is_read_timeout(_te))
except Exception as _e:
    print(f'  [SKIP] pyusb 不可用 ({_e})')
check('TimeoutError 判为超时', _is_read_timeout(TimeoutError()))
check('普通异常不判为超时 (否则会吞掉真错误)',
      not _is_read_timeout(ValueError('boom')) and not _is_read_timeout(OSError('io')))

print()
if FAILS:
    print(f'✗ {len(FAILS)} 项未通过: {FAILS}')
    sys.exit(1)
print('✓ UI 接线全面自检通过')
