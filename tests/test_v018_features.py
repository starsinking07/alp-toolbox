# -*- coding: utf-8 -*-
"""v0.1.8 新功能离线测试: 场景配置引擎 + 温度墙 (不碰设备)。
跑法: python tools/test_v018_features.py"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

FAILS = []
def check(name, cond, detail=''):
    print(f'  [{"PASS" if cond else "FAIL"}] {name} {detail}')
    if not cond:
        FAILS.append(name)

# ========== 1) match_scene_profile 纯函数 ==========
print('=== T1 场景匹配纯函数 ===')
from brb02.service import match_scene_profile, DeviceWorker
profiles = [
    {'name': 'A', 'enabled': False, 'processes': ['game']},
    {'name': 'B', 'enabled': True, 'processes': ['cyberpunk.exe', 'steam']},
    {'name': 'C', 'enabled': True, 'processes': []},
    {'name': 'D', 'enabled': True, 'processes': ['CYBERPUNK']},
]
check('首条命中优先', match_scene_profile(profiles, 'cyberpunk.exe') == (1, profiles[1]))
check('子串+大小写不敏感', match_scene_profile(profiles, 'CYBERPUNK2077.EXE') == (3, profiles[3]))
check('未命中', match_scene_profile(profiles, 'explorer.exe') == (None, None))
check('空表', match_scene_profile([], 'x') == (None, None))
check('空子串跳过', match_scene_profile([{'enabled': True, 'processes': ['']}], 'x') == (None, None))

# ========== 2) 场景引擎全流程 (假设备) ==========
print('=== T2 场景引擎全流程 ===')
import brb02.service as svc
from brb02 import protocol as pc
sent = []
class _FakeDev:
    connected = True
    conn_type = 'usb'
    def set_fixed_rpm(self, rpm, level=1, src=None):
        sent.append(('rpm', rpm, level))
    def _send(self, frame):
        sent.append(('raw', frame.hex()))
        p = pc.parse_frame(frame)
        if p and p[0] == 0x03:
            return [(0x03, bytes([0x00, 0x01]))]     # 开关向量回读
        if p and p[0] == 0xC2:
            return [(0xC2, b'')]                     # 参数页配置 ACK
        return [(0x13, bytes([0x11, 0xb8, 0x0b, 0x64, 0x0a, 0x00, 0x83, 0xff, 0x4c]))]

from brb02.config import Config
w = DeviceWorker.__new__(DeviceWorker)
w.device = _FakeDev()
w.config = Config.load()
w.config.curve_profiles = {'游戏曲线': [[40, 2000], [60, 3000], [80, 4000], [90, 4500]]}
w.config.curve = [[40, 1200], [60, 2000], [80, 3000], [90, 3500]]
w.config.curve_active = '默认'
w._scene_applied_key = None
w._scene_saved = None
w._last_sent_rpm = 1500
w._last_host_0x24_ts = 0.0
w._manual_until = 0.0
svc.foreground_process = lambda: 'cyberpunk2077.exe'
w.config.scene_profiles = [
    {'name': '配置A', 'enabled': False, 'rpm': 0, 'scheme': '', 'light_mode': -1, 'processes': []},
    {'name': '配置B', 'enabled': True, 'rpm': 3200, 'scheme': '游戏曲线', 'light_mode': 0x03,
     'processes': ['cyberpunk']},
]
now = time.time()
w._scene_tick()
check('应用序列=快照→rpm→灯效', [s[0] for s in sent] == ['raw', 'rpm', 'raw'])
check('rpm 应用 3200', sent[1][:2] == ('rpm', 3200))
rgb9 = w._scene_saved['rgb9']
check('快照 9B', rgb9 == bytes([0x11, 0xb8, 0x0b, 0x64, 0x0a, 0x00, 0x83, 0xff, 0x4c]))
expect_apply = pc.build_frame(0x12, bytes([0x03]) + rgb9[1:8])
check('灯效应用帧字节', sent[2][1] == expect_apply.hex())
check('方案切换', w.config.curve_active == '游戏曲线' and w.config.curve[0] == [40, 2000])

sent.clear()
svc.foreground_process = lambda: 'explorer.exe'
w._scene_tick()
check('离开→方案恢复', w.config.curve_active == '默认' and w.config.curve[0] == [40, 1200])
expect_restore = pc.build_frame(0x12, rgb9[0:8])
check('灯效恢复帧字节', any(s[0] == 'raw' and s[1] == expect_restore.hex() for s in sent))
check('快照清空+rpm 交还引擎', w._scene_saved is None and w._last_sent_rpm is None)
sent.clear()
svc.foreground_process = lambda: 'explorer.exe'
w._scene_tick()
check('幂等 (同 key 不重发)', not sent)

# ========== 3) 温度墙引擎 ==========
print('=== T3 温度墙引擎 ===')
sent.clear()
w2 = DeviceWorker.__new__(DeviceWorker)
w2.device = _FakeDev()
w2.config = Config.load()
w2.config.temp_wall_enabled = True
w2.config.temp_wall_temp = 92.0
w2.config.start_stop_enabled = True
w2.config.start_stop_off_below = 45.0
w2.config.start_stop_on_above = 50.0
w2.config.curve_enabled = False
w2.config.fixed_rpm = 1200
w2._temp_wall_active = False
w2._ss_stopped = True
w2._last_sent_rpm = 0
w2._last_send_ts = 0.0
w2._manual_until = 0.0
w2._last_tec_sent = None
w2._tec_level = 1
w2._tec_tick = lambda *a, **k: None
now = time.time()
w2._control_tick(93.0, 40.0, now)
check('压过智能启停触发 4800/L4', sent[-1][1] == 4800 and w2._temp_wall_active)
n0 = len(sent)
w2._control_tick(93.5, 41.0, now + 1)
check('高温维持不重复下发', len(sent) == n0)
w2._control_tick(88.9, 40.0, now + 2)
check('滞回解除+启停接管', not w2._temp_wall_active)
sent.clear()
w2.config.temp_wall_enabled = False
w2._control_tick(95.0, 40.0, now + 3)
check('关闭开关不触发', not w2._temp_wall_active and not any(s[1] == 4800 for s in sent))

print()
if FAILS:
    print(f'X {len(FAILS)} 项未通过: {FAILS}')
    sys.exit(1)
print('✓ v0.1.8 新功能测试全部通过')
