# -*- coding: utf-8 -*-
"""UI 接线全面自检 (0.1.8): 每个功能的 UI 入口 → 控件 → 信号 → 数据链。
背景: 本周两次"构造了但没加进容器/没接线"事故 —— 本脚本用控件树断言兜住。
跑法: python tools/check_ui_wiring.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# ⚠️ 配置沙箱 (2026-10-08 事故): 自检**不得改写用户真实配置** —— 把 APPDATA 指向临时目录。
#    本脚本会切换屏幕页"缩放方式"下拉, 而 `_on_fit_changed` 里 `cfg.save()` ⇒ 旧版每次跑自检
#    都把用户的 `image_fit` 静默改成测试最后停留的值。Config.path() 调用时读 APPDATA, 故此处重定向即可。
import tempfile as _tf
_SBX = os.path.join(_tf.gettempdir(), 'brb02_selftest')
os.makedirs(os.path.join(_SBX, 'Brb02Toolbox'), exist_ok=True)
os.environ['APPDATA'] = _SBX                      # 必须在 brb02.config 导入之前
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
        self._last_dev_level = 1        # 物理按键控制: 设备当前档位 (stub)
    def set_device_switches(self, s, p): pass
    def set_lighting(self, *a, **k): pass
    def start_image_upload(self, *a, **k): pass
    def start_screen_read(self): return True
    def send_param_page_config(self): pass
    def read_param_page_layout(self): return None
    def set_lcd_switch(self, on): self._lcd = bool(on); return bool(on)
    def get_lcd_switch_status(self): return True
    def push_button_profiles(self, only=0): return 4  # 物理按键控制: 写设备档位槽 (stub)
    def request_button_push(self, level=0): self._btn_push = level   # 异步请求 (stub)
    def read_device_button_profiles(self): return 4   # 物理按键控制: 从设备读回 4 档 (stub)

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
check(f"初始 fit={sp._fit} 三个滑条态与之一致",
      all(s.isEnabled() == (sp._fit == 'cover')
          for s in (sp.sld_zoom, sp.sld_panx, sp.sld_pany)))
sp.fit_combo.setCurrentIndex(max(0, sp.fit_combo.findData('stretch')))
check('切 stretch → 三个滑条全部禁用 (仅 cover 模式可用)',
      not any(s.isEnabled() for s in (sp.sld_zoom, sp.sld_panx, sp.sld_pany)))
check('stretch 下给出原因提示 (不再"滑不动"却无说明)',
      '等比裁边' in sp.lbl_zoom_hint.text())
sp.fit_combo.setCurrentIndex(max(0, sp.fit_combo.findData('cover')))
check('切 cover → 三个滑条全部启用',
      all(s.isEnabled() for s in (sp.sld_zoom, sp.sld_panx, sp.sld_pany)))
check('cover 下提示切换为可用态', '等比裁边' not in sp.lbl_zoom_hint.text())
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
w3.config.button_follow_enabled = False   # 同上: 沙箱配置会继承真实/上次运行的状态, 必须钉死
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
w3b.config.button_follow_enabled = False   # 钉死: 否则档位会走 _dev_level 而不是 _tec_level
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

# ===== 设备上报窗口 (2026-10-11: 实测周期 1000ms, 旧值 0.45 实测漏帧 40%) =====
print('=== 设备上报窗口 vs 上报周期 ===')
import inspect as _ins
from brb02 import device as _dev, service as _svc
_dflt = _dev.Brb02Device.poll_report.__defaults__[0]
check(f'poll_report 默认窗口 {_dflt}s > 上报周期 1.0s', _dflt > 1.0)
_tsrc = _ins.getsource(_svc.DeviceWorker._tick)
check('service._tick 传入窗口 > 1.0s (旧值 0.45 → 40% 漏帧 → 假瞬断)', 'poll_report(1.1)' in _tsrc)
check('单次漏帧不判瞬断 (连续 2 次才判)', '_miss_streak' in _tsrc)
check('poll_report docstring 已纠正周期 (不再写 500ms)',
      '1000ms' in (_dev.Brb02Device.poll_report.__doc__ or ''))

# ===== 上传稳定模式 (2026-10-11: 修"配置项无人读 / flow_control 无人传"的死功能) =====
print('=== 上传稳定模式 (0xC6 流控) ===')
check('屏幕页有「上传稳定模式」开关', hasattr(sp, 'chk_stable'))
check('开关初值与配置一致', sp.chk_stable.isChecked() == bool(getattr(cfg, 'upload_stable', False)))
sp.chk_stable.setChecked(True)
sp._stable_changed(True)
check('开启 → 写入 cfg.upload_stable', bool(getattr(cfg, 'upload_stable', False)) is True)
sp.chk_stable.setChecked(False)
sp._stable_changed(False)
check('关闭 → 写回 cfg.upload_stable', bool(getattr(cfg, 'upload_stable', False)) is False)
_svc_src = _ins.getsource(_svc.DeviceWorker)
check('upload_stable 不再是死字段 (生产代码真的读它)', 'upload_stable' in _svc_src)
check('两条上传路径都把 flow_control 传下去了 (旧版无人传 ⇒ 路径不可达)',
      _svc_src.count('flow_control=use_flow') >= 2)
check('失败重传切流控 (兑现发布说明承诺的"自动回落")',
      _svc_src.count('_once(flow=True)') >= 2)

# ===== ButtonPage: 物理按键控制 (2026-10-11, 独立分区) =====
print('=== ButtonPage 物理按键控制 ===')
from brb02.gui.pages import ButtonPage, CurvePage as _CP2
bp = ButtonPage(dict(ctx))
check('独立页面存在 + 有总开关', hasattr(bp, 'tgl_follow'))
check('有 4 档位行 (模式 / 转速 / 预设 / 编辑曲线)',
      len(bp.cmb_mode) == 4 and len(bp.spin_rpm) == 4
      and len(bp.cmb_preset) == 4 and len(bp.btn_edit) == 4)
check('有该档曲线编辑器 + 档位标签',
      hasattr(bp, 'curve') and hasattr(bp, 'lbl_level'))
check('有「从设备读取 4 档曲线」按钮', hasattr(bp, 'btn_read'))
check('有预设曲线下拉 (设备四档 + 静音)',
      hasattr(bp, 'cmb_curve_preset') and bp.cmb_curve_preset.count() == 6)
check('转速预设齐全 (静音/标准/强劲/超频/极限)', bp.cmb_preset[0].count() == 6)
bp.tgl_follow.setChecked(False)
bp._sync_enabled()
check('未开启跟随时整卡禁用 (防误改)',
      not bp.cmb_mode[0].isEnabled() and not bp.curve.isEnabled())
bp.tgl_follow.setChecked(True)
bp._sync_enabled()
check('开启后整卡可用', bp.cmb_mode[0].isEnabled() and bp.curve.isEnabled())
bp.cmb_mode[1].setCurrentIndex(max(0, bp.cmb_mode[1].findData('curve')))
bp.spin_rpm[1].setValue(2500)
bp._row_changed(1)
check('输入不即时落盘 (走 debounce 计时器)', bp._save_timer.isActive())
bp._commit()
check('debounce 到期后才写进 cfg.button_profiles',
      cfg.button_profiles[1]['mode'] == 'curve' and cfg.button_profiles[1]['rpm'] == 2500)
check('曲线档禁用转速框、启用曲线编辑',
      not bp.spin_rpm[1].isEnabled() and bp.btn_edit[1].isEnabled())
bp.cmb_mode[0].setCurrentIndex(max(0, bp.cmb_mode[0].findData('fixed')))
bp._row_changed(0)
bp._commit()
check('固定转速档: 转速框可用、曲线按钮禁用',
      bp.spin_rpm[0].isEnabled() and not bp.btn_edit[0].isEnabled())
check('转速框关掉键盘跟踪 (打字中途不触发落盘)',
      not bp.spin_rpm[0].keyboardTracking())
bp.cmb_preset[2].setCurrentIndex(max(0, bp.cmb_preset[2].findData(2100)))
bp._preset_picked(2)
bp._commit()
check('常用转速预设一键填入', cfg.button_profiles[2]['rpm'] == 2100)
bp._edit_idx = 3
bp.cmb_curve_preset.setCurrentIndex(1)          # 设备 LV1 低噪
bp._curve_preset_picked(1)
check('预设曲线一键套用 + 模式同步为智能变频',
      cfg.button_profiles[3]['mode'] == 'curve'
      and cfg.button_profiles[3]['curve'][0] == [20, 1200])
check('曲线页已不再包含按键卡片 (独立成区)',
      not hasattr(_CP2, 'tgl_btn_follow'))
check('引擎: 档位取设备值而非按转速反推', '_dev_level()' in _svc_src)
check('引擎: TEC 自动档位在按键模式下跳过', 'cfg.button_follow_enabled' in _svc_src)
check('引擎: 目标按档位配置取 (固定/该档曲线)', '_button_profile' in _svc_src)
check('设备写入走异步请求 (不在 GUI 线程做 USB 写)',
      hasattr(_svc.DeviceWorker, 'request_button_push')
      and hasattr(_svc.DeviceWorker, 'push_button_profiles')
      and 'request_button_push' in _ins.getsource(ButtonPage._commit))
check('开关切换不写设备 (只有改配置才写)',
      'request_button_push' not in _ins.getsource(ButtonPage._follow_toggled))

# --- 跟随模式: 必须"先读档再下发", 否则会用旧档位把按键覆盖回去 (2026-10-11 用户反馈) ---
_tick_src = _ins.getsource(_svc.DeviceWorker._tick)
_i_read = _tick_src.find('_gear_sync_tick(now, force=True)')
_i_ctrl = _tick_src.find('self._control_tick(cpu, gpu, now)')
check('跟随模式先读档再下发 (顺序反了就会覆盖按键)',
      0 <= _i_read < _i_ctrl)
_gsrc = _ins.getsource(_svc.DeviceWorker._gear_sync_tick)
check('档位轮询支持 force 绕过 4s 节流', 'force: bool = False' in _gsrc)
check('force 时不受 4s 节流限制',
      'if not force and now - self._last_0x25_poll < 4.0' in _gsrc)

# --- 主题: 输入控件必须有禁用样式 (否则禁用了也看不出灰) ---
_qss = th.build_qss(False)
check('主题含 QComboBox:disabled (禁用要看得出来)',
      'QComboBox:disabled' in _qss and 'QSpinBox:disabled' in _qss
      and 'QLineEdit:disabled' in _qss)

# --- 换档后界面要跟着变 (2026-10-11 用户反馈"档位都读取到了 为什么图片中不变") ---
check('ButtonPage 接了 deviceGearChanged 信号 (换档即刷新显示)',
      'deviceGearChanged.connect' in _ins.getsource(ButtonPage.__init__))
check('ButtonPage 有 tick_ui 心跳兜底', hasattr(ButtonPage, 'tick_ui'))
try:
    from brb02.gui.main_window import MainWindow as _MW2
except Exception:
    _MW2 = None
if _MW2 is not None:
    _mw_src = _ins.getsource(_MW2)
    check('主窗口心跳会刷新按键页', "pages['buttons'].tick_ui()" in _mw_src)
    _odg = _ins.getsource(_MW2._on_device_gear)
    check('换档提示区分"跟随/接管" (不再一律说"已接管")',
          'button_follow_enabled' in _odg and '跟随' in _odg)

    # --- 控制方式互斥 (2026-10-11 用户要求: 开启一个, 其余置灰 + 说明原因) ---
    print('=== 控制方式互斥 ===')
    check('主窗口有 set_exclusive_mode / sync_mode_exclusivity',
          hasattr(_MW2, 'set_exclusive_mode') and hasattr(_MW2, 'sync_mode_exclusivity'))
    _em = _ins.getsource(_MW2.set_exclusive_mode)
    check('互斥: 三种模式互相排斥', all(k in _em for k in ("'follow'", "'curve'", "'tec'")))
    from brb02.gui.pages import StatusPage as _SP2
    check('各页都有 sync_mode_state (统一置灰入口)',
          all(hasattr(c, 'sync_mode_state')
              for c in (_SP2, _CP2, ControlPage, ButtonPage)))
    check('曲线页: 被顶掉时置灰智能变频开关 + 给原因',
          'tgl_smart2.setEnabled' in _ins.getsource(_CP2.sync_mode_state)
          and 'lbl_mode_hint' in _ins.getsource(_CP2.sync_mode_state))
    check('控制页: 被顶掉时置灰两个自动开关 + 给原因',
          'row_curve' in _ins.getsource(ControlPage.sync_mode_state)
          and 'row_tec' in _ins.getsource(ControlPage.sync_mode_state))

    class _FakeMain:
        cfg = cfg
        pages = {}

        def set_exclusive_mode(self, mode):
            _MW2.set_exclusive_mode(self, mode)

        def sync_mode_exclusivity(self):
            pass

    _fm = _FakeMain()
    cfg.curve_enabled = True
    cfg.tec_auto_enabled = True
    cfg.button_follow_enabled = False
    _fm.set_exclusive_mode('follow')
    check('开「物理按键控制」→ 自动关掉智能变频 + 档位自动切换',
          cfg.button_follow_enabled and not cfg.curve_enabled and not cfg.tec_auto_enabled)
    _fm.set_exclusive_mode('curve')
    check('开「智能变频」→ 自动关掉按键控制 + 档位自动切换',
          cfg.curve_enabled and not cfg.button_follow_enabled and not cfg.tec_auto_enabled)
    _fm.set_exclusive_mode('tec')
    check('开「档位自动切换」→ 自动关掉按键控制 + 智能变频',
          cfg.tec_auto_enabled and not cfg.button_follow_enabled and not cfg.curve_enabled)
    _fm.set_exclusive_mode('manual')
    check('切回「手动」→ 三者全关',
          not cfg.tec_auto_enabled and not cfg.button_follow_enabled and not cfg.curve_enabled)

    # --- 托盘菜单三项控制方式 (2026-10-11 用户要求) ---
    _init_src = _ins.getsource(_MW2.__init__)
    check('托盘有三项控制方式 (智能变频/档位自动切换/物理按键控制)',
          all(k in _init_src for k in ("'智能变频'", "'档位自动切换'", "'物理按键控制'")))
    check('托盘切换走互斥入口 (不再直接改配置)',
          'set_exclusive_mode' in _ins.getsource(_MW2._tray_mode)
          and '_tray_mode' in _ins.getsource(_MW2.__init__))
    check('托盘三项与配置同步 (含已激活外置灰)',
          '_sync_tray_mode_actions' in _ins.getsource(_MW2.sync_mode_exclusivity)
          and 'setEnabled' in _ins.getsource(_MW2._sync_tray_mode_actions))
    check('快捷键切智能变频也走互斥入口',
          'set_exclusive_mode' in _ins.getsource(_MW2._on_hotkey)
          if hasattr(_MW2, '_on_hotkey') else True)
    check('手动挡位提示随原因变化 (不再固定写"智能变频运行中")',
          'why or' in _ins.getsource(_CP2._sync_manual_enabled))

    # --- GitHub issue #1 三连修复 (2026-10-11) ---
    print('=== GitHub issue #1 修复 ===')
    _sig = list(_ins.signature(_svc.DeviceWorker.start_image_upload).parameters)[1:]
    check('上传请求签名含 zoom/pan_x/pan_y (修 NameError 卡死)',
          _sig == ['path', 'flip_h', 'flip_v', 'fit', 'zoom', 'pan_x', 'pan_y'])
    _siu = _ins.getsource(_svc.DeviceWorker.start_image_upload)
    check('请求元组 7 元素且消费端解 7 元 (旧代码只解 4 元)',
          'int(pan_y or 0))' in _siu)
    _riu = _ins.getsource(_svc.DeviceWorker._run_image_upload)
    check('_run_image_upload 接收并下发 zoom/pan (预览与上传同源)',
          'zoom: int = 100' in _riu and 'zoom=zoom' in _riu)
    from brb02.device import Brb02Device as _BD
    _ui_src = _ins.getsource(_BD.upload_image)
    check('device.upload_image 透传 zoom/pan 到图像变换',
          'zoom: int = 100' in _ui_src and 'pan_y=pan_y' in _ui_src)
    from brb02.gui.pages import ScreenPage as _ScreenPage2
    check('GUI 上传携带滑条当前值 + 异常不再卡死 _busy',
          'sld_zoom.value()' in _ins.getsource(_ScreenPage2._upload)
          and 'self._busy = False' in _ins.getsource(_ScreenPage2._upload))
    _fin = _ins.getsource(_svc.DeviceWorker._run_image_upload)
    check('上传成功抑制 0x07 补推 (修"图片与参数卡反复争抢")',
          '_last_07_ts = time.time()' in _fin and 'else:' in _fin)
    import re as _re
    _nsi = open('installer/Alp-setup.nsi', encoding='utf-8', errors='replace').read()
    check('NSIS: PawnIO 静默参数改为 -install -silent (修 /SILENT 弹错)',
          '-install -silent' in _nsi and '/SILENT /NORESTART' not in _nsi)

    # --- 互斥必须**双向**同步: 配置 → 控件 (2026-10-11 用户反馈"状态栏开了工具箱没同步") ---
    print('=== 互斥双向同步 (配置→控件) ===')
    _sp3 = _SP2(dict(ctx))
    _cp3 = ControlPage(dict(ctx))
    _bp3 = ButtonPage(dict(ctx))
    _all3 = (_sp3, _cp3, _bp3)

    def _apply():
        for _pg in _all3:
            _pg.sync_mode_state()

    cfg.curve_enabled = False
    cfg.tec_auto_enabled = False
    cfg.button_follow_enabled = True
    _apply()
    check('配置 follow=开 → 按键页开关回填为开', _bp3.tgl_follow.isChecked() is True)
    check('配置 follow=开 → 控制页两开关回填为关',
          _cp3.tgl_curve.isChecked() is False and _cp3.tgl_tec.isChecked() is False)
    check('配置 follow=开 → 状态页智能变频开关回填为关', _sp3.tgl_smart.isChecked() is False)

    cfg.button_follow_enabled = False
    cfg.tec_auto_enabled = True
    _apply()
    check('配置 tec=开 → 控制页档位自动开关回填为开', _cp3.tgl_tec.isChecked() is True)
    check('配置 tec=开 → 按键页开关回填为关', _bp3.tgl_follow.isChecked() is False)

    cfg.tec_auto_enabled = False
    cfg.curve_enabled = True
    _apply()
    check('配置 curve=开 → 状态页/控制页智能变频开关回填为开',
          _sp3.tgl_smart.isChecked() is True and _cp3.tgl_curve.isChecked() is True)
    check('配置 curve=开 → 按键页开关回填为关', _bp3.tgl_follow.isChecked() is False)
    cfg.curve_enabled = False
    _apply()

    # --- 端到端: 主窗口 sync_mode_exclusivity → 各页回填 (托盘走的就是这条链) ---
    print('=== 端到端: 托盘 → 主窗口 → 各页 ===')
    _cvp3 = _CP2(dict(ctx))
    _mw3 = _MW2.__new__(_MW2)          # 只搭最小属性, 走真实 sync_mode_exclusivity
    _mw3.cfg = cfg
    _mw3.pages = {'status': _sp3, 'curve': _cvp3, 'control': _cp3, 'buttons': _bp3}
    cfg.button_follow_enabled = True
    cfg.curve_enabled = False
    cfg.tec_auto_enabled = False
    _MW2.sync_mode_exclusivity(_mw3)
    check('主窗口互斥同步 → 按键页开关回填为开', _bp3.tgl_follow.isChecked() is True)
    check('主窗口互斥同步 → 控制页/状态页/曲线页开关全部回填为关',
          not _cp3.tgl_curve.isChecked() and not _cp3.tgl_tec.isChecked()
          and not _sp3.tgl_smart.isChecked() and not _cvp3.tgl_smart2.isChecked())
    cfg.button_follow_enabled = False
    cfg.tec_auto_enabled = True
    _MW2.sync_mode_exclusivity(_mw3)
    check('主窗口互斥同步 → 控制页档位自动开关回填为开', _cp3.tgl_tec.isChecked() is True)
    cfg.tec_auto_enabled = False

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
