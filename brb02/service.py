# -*- coding: utf-8 -*-
"""设备服务线程: 状态轮询 + 温度处理链 + 智能启停 + 曲线/学习/温升预判引擎 + 历史记录 + 情景联动。"""
from __future__ import annotations

import asyncio
import collections
import ctypes
import time
from ctypes import wintypes
from typing import Optional

from PySide6.QtCore import QThread, Signal

from . import protocol
from .config import Config
from .device import Brb02Device
from .temps import TempReader

TEMPS_ANCHORS = list(range(20, 111, 5))     # 19 锚点, 与曲线编辑器对齐
MAX_RPM = 4800


# ---- 前台进程名 (ctypes, 免 psutil 依赖) ----
_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
_psapi = ctypes.windll.psapi


def foreground_process() -> str:
    try:
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return ''
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return ''
        h = _kernel32.OpenProcess(0x1000, False, pid.value)
        if not h:
            return ''
        try:
            buf = ctypes.create_unicode_buffer(512)
            size = wintypes.DWORD(512)
            if not _psapi.GetModuleFileNameExW(h, None, buf, size):
                return ''
            return buf.value.replace('/', '\\').split('\\')[-1].lower()
        finally:
            _kernel32.CloseHandle(h)
    except Exception:
        return ''


class DeviceWorker(QThread):
    statusChanged = Signal(dict)
    tempsChanged = Signal(float, float)
    connectionChanged = Signal(bool, str)
    infoChanged = Signal(dict)
    historyChanged = Signal()

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self.device = Brb02Device(config.conn_type)
        if config.ble_address:
            self.device.ble_address = config.ble_address   # 记忆地址, 启动可蓝牙直连
        self.temps = TempReader()
        self._stop = False
        self._paused = False
        self._reconnect_req: str | None = None
        self._auto_probe_ts = 0.0
        self._switch_cooldown_until = 0.0
        self._direct_try_ts = 0.0
        self._last_sent_rpm: int | None = None
        self._last_send_ts = 0.0
        self._last_scene_key = None
        # 温度处理链状态
        self._temp_state = {}    # 每通道 (cpu/gpu) 独立的 EMA/尖峰状态
        # 智能启停状态
        self._ss_stopped = False
        # 学习状态
        self._last_learn_ts = 0.0
        self._learn_prev_temp = None
        # 温升预判
        self._temp_hist = collections.deque(maxlen=90)
        # 历史记录 (t, cpu, gpu, rpm)
        self.history = collections.deque(maxlen=7200)
        self._last_hist_ts = 0.0
        # 手动目标 (GUI 手动设定后 5s 内引擎不覆盖)
        self._manual_until = 0.0

    # ============ 主循环 ============
    def stop(self):
        self._stop = True
        self.temps.stop()

    def run(self):
        self.temps.start()
        info_timer = 0.0
        while not self._stop:
            try:
                self._tick(info_timer)
                info_timer = getattr(self, '_info_timer', info_timer)
            except Exception as e:
                try:
                    self.connectionChanged.emit(False, f'内部错误: {e} (已重试)')
                except Exception:
                    pass
                self.msleep(1000)
        try:
            self.device.disconnect()
        except Exception:
            pass

    def _dbg(self, msg):
        try:
            import os
            base = os.environ.get('APPDATA', '.')
            with open(os.path.join(base, 'Brb02Toolbox', 'tick.log'), 'a', encoding='utf-8') as f:
                f.write(f'{time.time():.1f} {msg}\n')
        except Exception:
            pass

    def _tick(self, info_timer):
        # 手动断开: 暂停轮询与重连
        if self._paused:
            self.msleep(300)
            return
        # GUI 的重连/换通道请求
        if self._reconnect_req is not None:
            self.device.conn_type = self._reconnect_req
            self.config.conn_type = self._reconnect_req    # 通道切换持久化
            self.config.save()
            self._reconnect_req = None
            self.device.disconnect()
            self._last_sent_rpm = None
        if not self.device.connected:
            if not self._connect():
                # 配置通道上没有设备: 启动/重连失败同样做双向探测 (双通道识别)
                if self.config.auto_switch:
                    self._auto_switch_tick(time.time(), False)
                self.msleep(1500)
                return
        st = self.device.poll_report(0.45)
        if st is None:
            # 瞬断重连
            if self.device.connect():
                return
            # 确认断线: 双向自动切换探测
            now2 = time.time()
            if self.config.auto_switch and now2 >= self._switch_cooldown_until \
                    and now2 - self._auto_probe_ts >= 3.0:
                self._auto_probe_ts = now2
                if self.device.conn_type == 'usb':
                    # 拔线: 探测蓝牙广播, 在则切蓝牙
                    from .transport_ble import scan_for_cooler
                    addr = asyncio.run(scan_for_cooler(4.0))
                    if addr:
                        self.device.ble_address = addr
                        self._switch_cooldown_until = now2 + 12
                        self.connectionChanged.emit(
                            False, 'USB 已断开, 发现蓝牙广播 —— 自动切换到蓝牙模式...')
                        self._reconnect_req = 'ble'
                        return
                else:
                    # 蓝牙断线: 探测 USB, 插线即切回有线
                    from .transport_usb import find_device
                    if find_device() is not None:
                        self._switch_cooldown_until = now2 + 12
                        self.connectionChanged.emit(
                            False, '检测到 USB 接入, 自动切换到有线模式...')
                        self._reconnect_req = 'usb'
                        return
            self.connectionChanged.emit(
                False, '蓝牙连接丢失 (重连中, 失败请长按配对键)' if self.device.conn_type == 'ble'
                else '设备连接丢失, 重连中...')
            return
        else:
            self.statusChanged.emit(st)

        cpu_raw, gpu_raw = self.temps.snapshot()
        cpu, gpu = self._process_temp(cpu_raw, 'cpu'), self._process_temp(gpu_raw, 'gpu')
        self.tempsChanged.emit(cpu, gpu)
        self._temp_hist.append((time.time(), cpu or gpu or 0))

        rpm_now = st.get('rpm', self._last_sent_rpm or 0) if st else (self._last_sent_rpm or 0)

        # 历史记录 (1Hz): (t, cpu温, gpu温, rpm, cpu功耗, gpu功耗)
        now = time.time()
        if now - self._last_hist_ts >= 1.0:
            self._last_hist_ts = now
            self.history.append((now, cpu, gpu, rpm_now,
                                 self.temps.cpu_power, self.temps.gpu_power))
            if len(self.history) % 5 == 0:
                self.historyChanged.emit()

        # 控制引擎
        self._control_tick(cpu, gpu, now)

        # USB / 蓝牙自动切换 (拔线切蓝牙, 插线切回 USB)
        if self.config.auto_switch:
            self._auto_switch_tick(now, st is not None)

        # 情景联动
        if self.config.scene_enabled:
            self._scene_tick()

        # 信息刷新 (15s)
        if now - info_timer > 15:
            self._info_timer = now
            try:
                self.emit_info()
            except Exception:
                pass
        self.msleep(60)

    # ============ 温度处理链 ============
    def _process_temp(self, raw: float, channel: str) -> float:
        """尖峰过滤 + EMA 平滑 (功能 #4)。每个通道独立状态, 无数据时保持自身上次值。"""
        st = self._temp_state.setdefault(channel, {'ema': None, 'last_raw': None})
        if not raw or raw <= 1:
            return st['ema'] if st['ema'] is not None else 0.0
        # 尖峰过滤: 与上次原始值偏差 > 12° 视为单次跳温丢弃
        if self.config.spike_filter and st['last_raw'] is not None:
            if abs(raw - st['last_raw']) > 12.0:
                return st['ema'] if st['ema'] is not None else raw
        st['last_raw'] = raw
        level = max(1, int(self.config.temp_smoothing or 1))
        alpha = 1.0 / level
        if st['ema'] is None:
            st['ema'] = raw
        else:
            st['ema'] += alpha * (raw - st['ema'])
        return st['ema']

    # ============ 控制引擎 ============
    def _control_tick(self, cpu: float, gpu: float, now: float):
        cfg = self.config
        temp = self._pick_temp(cpu, gpu)
        if temp is None or temp <= 1:
            return

        # --- 智能启停 (功能 #1): 迟滞门, 优先级最高 ---
        if cfg.start_stop_enabled:
            if self._ss_stopped:
                if temp > cfg.start_stop_on_above:
                    self._ss_stopped = False
                else:
                    self._apply_target(0, now, 'startstop')
                    return
            elif temp < cfg.start_stop_off_below:
                self._ss_stopped = True
                self._apply_target(0, now, 'startstop')
                return

        # --- 手动模式: GUI 手动设定后的保持期内不动 ---
        if not cfg.curve_enabled:
            if now < self._manual_until:
                return
            # 恢复配置的固定转速 (仅在未下发过或掉线重连后)
            if self._last_sent_rpm is None:
                self._apply_target(cfg.fixed_rpm, now, 'manual')
            return

        # --- 智能变频曲线 ---
        target = self._curve_target(temp)
        # --- 自适应学习 (功能 #2) ---
        if cfg.learning_enabled:
            self._learning_tick(temp, now)
        # --- 温升预判 (功能 #9) ---
        if cfg.prediction_enabled:
            target = self._prediction_boost(target, now)
        self._apply_target(target, now, 'curve')

    def _pick_temp(self, cpu, gpu) -> float | None:
        src = self.config.curve_source
        if src == 'cpu':
            return cpu if cpu > 1 else None
        if src == 'gpu':
            return gpu if gpu > 1 else None
        cands = [t for t in (cpu, gpu) if t and t > 1]
        return max(cands) if cands else None

    def _curve_target(self, temp: float) -> int:
        """按 19 锚点曲线插值出目标 RPM, 含学习偏移。"""
        pts = sorted((float(t), float(r)) for t, r in self.config.curve) or [(40, 1000)]
        # 学习偏移 (按温度换算到锚点区间)
        offsets = self.config.learning_offsets or []
        lo, hi = pts[0][0], pts[-1][0]
        t = max(lo, min(hi, temp))
        target = pts[-1][1]
        for (t0, r0), (t1, r1) in zip(pts, pts[1:]):
            if t0 <= t <= t1:
                target = r0 + (r1 - r0) * (t - t0) / max(t1 - t0, 0.1)
                break
        if offsets:
            # 找当前温度两侧锚点的偏移做线性插值
            idx = min(range(len(TEMPS_ANCHORS)),
                      key=lambda i: abs(TEMPS_ANCHORS[i] - t))
            off = 0.0
            if 0 <= idx < len(offsets):
                off = offsets[idx]
            target = target * (1 + off / 100.0)
        return int(max(0, min(MAX_RPM, target)))

    def _apply_target(self, rpm: int, now: float, source: str):
        hyst = max(20, int(self.config.curve_hysteresis or 80))
        due = now - self._last_send_ts > 5.0
        if self._last_sent_rpm is None or rpm == 0 or \
           abs(rpm - self._last_sent_rpm) >= hyst or due:
            self.device.set_fixed_rpm(rpm)
            self._last_sent_rpm = rpm
            self._last_send_ts = now

    # ============ 自适应学习 (功能 #2) ============
    def _learning_tick(self, temp: float, now: float):
        cfg = self.config
        if now - self._last_learn_ts < 60:
            return
        steady = (self._learn_prev_temp is not None and
                  abs(temp - self._learn_prev_temp) < 2.0)
        self._learn_prev_temp = temp
        self._last_learn_ts = now
        if not steady:
            return
        err = temp - cfg.learning_target
        if abs(err) < 1.5:
            return
        offsets = cfg.learning_offsets or [0.0] * len(TEMPS_ANCHORS)
        while len(offsets) < len(TEMPS_ANCHORS):
            offsets.append(0.0)
        idx = min(range(len(TEMPS_ANCHORS)), key=lambda i: abs(TEMPS_ANCHORS[i] - temp))
        step = 1.0 if err > 0 else -1.0
        # 倾向约束
        if cfg.learning_bias == 'cooling' and step < 0:
            return
        if cfg.learning_bias == 'quiet' and step > 0:
            return
        # 误差越大步长越大
        step *= 1 if abs(err) < 4 else 2
        for d_i, w in ((0, 1.0), (-1, 0.5), (1, 0.5)):
            i = idx + d_i
            if 0 <= i < len(offsets):
                offsets[i] = max(-20.0, min(20.0, offsets[i] + step * w))
        cfg.learning_offsets = offsets
        cfg.save()

    def reset_learning(self):
        self.config.learning_offsets = []
        self.config.save()

    # ============ 温升预判 (功能 #9) ============
    def _prediction_boost(self, target: int, now: float) -> int:
        h = [x for x in self._temp_hist if now - x[0] <= 45]
        if len(h) < 20:
            return target
        dt = h[-1][1] - h[0][1]
        span = h[-1][0] - h[0][0]
        if span <= 0:
            return target
        slope = dt / span                     # °C/s
        if slope > 0.04:                      # ≈2.4°C/分钟以上
            boost_pct = min(15.0, slope * 260)
            target = int(min(MAX_RPM, target * (1 + boost_pct / 100.0)))
        return target

    # ============ USB / 蓝牙自动切换 ============
    def _auto_switch_tick(self, now: float, usb_ok: bool):
        if now < self._switch_cooldown_until:
            return
        from .transport_usb import find_device

        if self.device.conn_type == 'ble':
            # 蓝牙已连接: 每 3 秒探测 USB, 插上即切回有线
            if now - self._auto_probe_ts >= 3.0:
                self._auto_probe_ts = now
                if find_device() is not None:
                    self._switch_cooldown_until = now + 10
                    self.connectionChanged.emit(False, '检测到 USB 接入, 切换到有线模式...')
                    self._reconnect_req = 'usb'
        else:
            # USB 模式: 已连接则不动; 断线时先按记忆地址蓝牙直连, 再每 5 秒扫描广播
            if self.device.connected:
                return
            # ① 记忆地址直连 (设备断链后短时间内仍可连, 无需广播; 15 秒最多试一次)
            if self.config.ble_address and now - self._direct_try_ts >= 15.0:
                self._direct_try_ts = now
                self.device.conn_type = 'ble'
                self.device.ble_address = self.config.ble_address
                self.connectionChanged.emit(False, 'USB 未发现散热器, 尝试蓝牙直连 (记忆地址)...')
                try:
                    if self.device.connect():
                        self._switch_cooldown_until = now + 12
                        self.config.conn_type = 'ble'
                        self.config.save()
                        self.connectionChanged.emit(True, 'USB 不在线, 已通过蓝牙直连散热器')
                        return
                except Exception:
                    pass
                self.device.conn_type = 'usb'
                self.connectionChanged.emit(False, '蓝牙直连未成功, 继续 USB 探测...')
            # ② 每 5 秒扫描一次蓝牙广播
            if now - self._auto_probe_ts >= 5.0:
                self._auto_probe_ts = now
                from .transport_ble import scan_for_cooler
                addr = asyncio.run(scan_for_cooler(4.0))
                if addr:
                    self._switch_cooldown_until = now + 12
                    self.device.ble_address = addr
                    self.config.ble_address = addr
                    self.config.conn_type = 'ble'
                    self.config.save()
                    self.connectionChanged.emit(
                        False, 'USB 已断开, 发现蓝牙广播 —— 自动切换到蓝牙模式...')
                    self._reconnect_req = 'ble'

    # ============ 情景联动 ============
    def _scene_tick(self):
        proc = foreground_process()
        if not proc:
            return
        rule = None
        for r in self.config.scene_rules:
            if r.get('match', '').lower() in proc:
                rule = r
                break
        key = (proc, rule.get('rpm') if rule else None)
        if key == self._last_scene_key:
            return
        self._last_scene_key = key
        if rule:
            rpm = int(rule.get('rpm', 0))
            if rpm > 0:
                self.device.set_fixed_rpm(rpm)
                self._last_sent_rpm = rpm
                self._manual_until = time.time() + 10

    # ============ 连接 ============
    def _connect(self) -> bool:
        if self.device.conn_type == 'ble':
            self.connectionChanged.emit(False, '蓝牙扫描/连接中 (最长45秒, 请长按散热器按键 3-5 秒触发广播)...')
        try:
            ok = self.device.connect()
        except Exception as e:
            import traceback
            from .logbuf import LOGBUF
            tb = traceback.format_exc()
            traceback.print_exc()
            LOGBUF.write(f'[连接异常] {e}\n{tb}')
            self.connectionChanged.emit(False, f'连接异常: {e}')
            return False
        if ok:
            ch = '蓝牙 BLE' if self.device.conn_type == 'ble' else 'USB'
            self.connectionChanged.emit(True, f'已连接 ({ch})')
            try:
                self.emit_info()
            except Exception:
                pass
        else:
            self.connectionChanged.emit(
                False, '未找到散热器 (USB / 蓝牙均未在线; 蓝牙请长按散热器按键 3-5 秒后重试)')
        return ok

    def request_reconnect(self, conn_type: str):
        self._reconnect_req = conn_type
        self._paused = False

    def pause_connection(self):
        """状态页'断开'按钮: 停止轮询并断开, 直到'连接'按下"""
        self._paused = True
        self._last_sent_rpm = None
        try:
            self.device.disconnect()
        except Exception:
            pass
        self.connectionChanged.emit(False, '已断开 (点击"连接"重新接入)')

    def resume_connection(self):
        self._paused = False
        self._last_sent_rpm = None

    def emit_info(self):
        info = {
            'firmware': self.device.get_firmware_version(),
            'cooling': self.device.get_cur_cooling(),
            'curve': self.device.get_curve(1),
            'rgb_on': self.device.get_rgb_switch(),
        }
        self.infoChanged.emit(info)

    # ============ GUI 控制接口 ============
    def apply_fixed_rpm(self, rpm: int, manual: bool = True):
        try:
            self.device.set_fixed_rpm(rpm)
        except Exception as e:
            from .logbuf import LOGBUF
            LOGBUF.write(f'[下发失败] {rpm} RPM: {e}')
            self.connectionChanged.emit(False, f'转速下发失败: {e} (等待重连...)')
            return
        self._last_sent_rpm = rpm
        self._last_send_ts = time.time()
        if manual:
            self._manual_until = time.time() + 5

    def set_rgb(self, on: bool):
        body = bytes([0x04, 0x10, 0x01 if on else 0x00])
        f = bytes([protocol.HEADER]) + body + bytes([protocol.checksum(bytes([protocol.HEADER]) + body)])
        self.device._send(f, wait_s=0.3)

    def get_lighting(self):
        """读当前灯效 (GUI 灯效面板初始化用), 失败返回 None"""
        try:
            return self.device.get_rgb_effect()
        except Exception as e:
            from .logbuf import LOGBUF
            LOGBUF.write(f'[灯效] 读取失败: {e}')
            return None

    def get_rgb_switch(self) -> Optional[bool]:
        try:
            return self.device.get_rgb_switch()
        except Exception:
            return None

    def set_lighting(self, mode: int, speed: int, brightness: int,
                     color_mode: int, rgb):
        """写完整灯效 (GUI 灯效面板)"""
        try:
            self.device.set_rgb_effect(mode, speed, brightness, color_mode, rgb)
        except Exception as e:
            from .logbuf import LOGBUF
            LOGBUF.write(f'[灯效] 写入失败: {e}')

    GEAR_RGB = [(16, 185, 129), (59, 130, 246), (168, 85, 247), (249, 115, 22), (239, 68, 68)]

    def gear_light_hook(self, gear: int):
        """挡位灯联动 (功能 #8): 换挡时把灯色调成对应挡位色
        (静音绿 / 标准蓝 / 强劲紫 / 超频橙 / 极限红, 与曲线页 GEARS 配色一致)。
        写入格式 2026-10-02 USBPcap 实测, 见 protocol.set_rgb_color。"""
        if not self.config.gear_light:
            return
        try:
            self.device.set_rgb_color(*self.GEAR_RGB[gear % len(self.GEAR_RGB)])
        except Exception as e:
            from .logbuf import LOGBUF
            LOGBUF.write(f'[挡位灯] 写入失败: {e}')

    # ============ 调试面板 ============
    def debug_send(self, hex_str: str) -> str:
        """发送原始 hex 命令并返回应答文本 (调试面板用, 慎重!)"""
        clean = hex_str.replace(' ', '').replace(',', '').replace('0x', '').replace('0X', '')
        try:
            data = bytes.fromhex(clean)
        except ValueError:
            return 'hex 格式错误'
        if not data:
            return '空命令'
        try:
            with self.device._lock:
                self.device._t.write(data)
                time.sleep(0.4)
                rxs = self.device._t.read_all(350, 8)
            return '\n'.join(rx.rstrip(b'\x00').hex(' ') for rx in rxs) or '(无应答)'
        except Exception as e:
            return f'发送失败: {e}'
