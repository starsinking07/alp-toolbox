# -*- coding: utf-8 -*-
"""设备服务线程: 状态轮询 + 温度处理链 + 智能启停 + 曲线/学习/温升预判引擎 + 历史记录 + 情景联动。"""
from __future__ import annotations

import asyncio
import collections
import ctypes
import os
import threading
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


def match_scene_profile(profiles: list, proc: str):
    """场景匹配纯函数: 返回 (index, profile) 或 (None, None)。
    只看 enabled 且 processes 非空的槽; 子串大小写不敏感; 空子串跳过 (防命中一切)。"""
    pl = (proc or '').lower()
    if not pl:
        return None, None
    for i, p in enumerate(profiles or []):
        if not isinstance(p, dict) or not p.get('enabled'):
            continue
        for sub in (p.get('processes') or []):
            s = (sub or '').strip().lower()
            if s and s in pl:
                return i, p
    return None, None


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
    uploadProgress = Signal(int, int)      # 屏幕上传进度 (已发数据帧, 总 2096)
    uploadFinished = Signal(dict)          # 上传结束 (结果 dict, 见 device.upload_canvas)
    uploadCountChanged = Signal(int)       # 磨损计数变化 (累计成功上屏次数, 0.1.9)
    deviceGearChanged = Signal(int, int)   # 散热器实体按钮换档 (level, rpm) — 0x25 轮询检测

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        # 档位记忆 (v3.52): 开启智能变频前的手动转速, 关闭后自动恢复
        self._curve_was_on = bool(config.curve_enabled)
        self._pre_curve_rpm = config.pre_curve_rpm
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
        self._tec_level = 1                 # 当前档位 (1-4)
        self._last_status = {}              # 最近一次 0x06/0x25 状态
        self._last_tec_sent = 0             # 最后下发的档位 (0=未知)
        self._last_sent_rpm: int | None = None
        self._last_send_ts = 0.0
        self._last_scene_key = None
        self._temp_wall_active = False        # 温度墙激活态 (0.1.9)
        self._scene_applied_key = None        # 场景应用态 (('p',idx)/('none',proc))
        self._scene_saved = None              # 进入场景前快照 (0.1.9)
        self._scene_rpm_applied = False     # 情景转速已下发 (规则清空后需恢复, 审计 G11)
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
        # 0x07 主机参数推送节流
        self._last_07_ts = 0.0
        # 屏幕图片上传 (整程 ~16.3s 在 worker 线程内独占设备)
        self._upload_req = None              # (path, flip_h, flip_v, fit) | None
        self._upload_cancel = threading.Event()
        self._uploading = False
        # 屏幕内容 (v3.35: 队列化 — 未完成任务完成后再执行下一个, 不丢弃)
        self._screen_queue = collections.deque(maxlen=4)   # [{'key','want'}] 串行上屏队列
        self._screen_qlock = threading.Lock()              # 队列跨线程访问锁 (审计 G6)
        self._screen_last_key = None         # 上次成功上屏的内容 key
        self._screen_last_upload_ts = 0.0    # 上次上屏时刻 (磨损预算)
        self._screen_cancel = threading.Event()
        self._connect_ts = 0.0
        self._screen_fail_until = 0.0
        self._last_tec_change_ts = 0.0
        self._screen_tec_block_since = 0.0   # 换档平静窗开始阻塞的时刻 (超 120s 强制放行)
        self._last_0x25_poll = 0.0           # 0x25 档位轮询节流
        self._last_dev_level = None          # 设备侧最近档位 (0x25 'on')
        self._last_dev_rpm = None            # 设备侧最近目标转速 (0x25 'rpm')
        self._last_host_0x24_ts = 0.0        # 主机最近一次发 0x24 的时刻 (区分按钮/主机变更)
        # GUI 主动连接 (点"连接"/换通道): 蓝牙允许等满 45s; 自动重连走短扫描
        self._user_connect = False

    # ============ 主循环 ============
    def stop(self):
        self._stop = True
        self._upload_cancel.set()            # 退出时终止在跑的上传/等待 (审计 G8)
        self._screen_cancel.set()
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
        # 手动图片上传: 在 worker 线程内独占执行 (~16.3s), 期间不轮询/不下发/不切换
        if self._upload_req is not None:
            path, fh, fv, fit = self._upload_req
            self._upload_req = None
            self._run_image_upload(path, fh, fv, fit)
            return
        # 屏幕上屏队列: 串行执行 —— 未完成任务完成后再取下一个 (用户原则)
        if self._screen_queue and not self._uploading and self._upload_req is None:
            with self._screen_qlock:         # 消费与 GUI 侧 clear 竞争 (审计 G6)
                req = self._screen_queue.popleft() if self._screen_queue else None
            if req is not None:
                self._run_screen_upload(req)
                return
        st = self.device.poll_report(0.45)
        if st is None:
            # 瞬断重连
            if self.device.connect():
                self._last_sent_rpm = None   # 重连后强制重发当前目标 (防假成功残留, v3.61)
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
            self._last_status = st
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

        # 0x07 主机参数推送 (1Hz): 屏幕参数页的唯一数据源, 不推则参数页纯白
        if now - self._last_07_ts >= 1.0:
            self._last_07_ts = now
            self._push_host_info07(cpu, gpu)

        # 控制引擎
        self._control_tick(cpu, gpu, now)

        # 屏幕信息卡维持 (连接恢复 + 情境卡片决策)
        self._screen_tick(now, cpu, gpu)

        # 档位双向同步 (0x25 轮询: 散热器实体按钮 → 工具箱)
        self._gear_sync_tick(now)

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
        # 尖峰过滤: 与上次原始值偏差 > 12° 视为单次跳温丢弃。
        # 连续 3 次拒收后强制接受 —— 否则真实温升阶跃 (烤机/游戏启动) 会把基准永久
        # 冻结在旧值: 温度读数锁死、风扇永不升速, 直到温度回落旧值 ±12° (v3.47 审计 P0)。
        if self.config.spike_filter and st['last_raw'] is not None:
            if abs(raw - st['last_raw']) > 12.0:
                st['rejects'] = st.get('rejects', 0) + 1
                if st['rejects'] < 3:
                    return st['ema'] if st['ema'] is not None else raw
            else:
                st['rejects'] = 0
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
        self._tec_tick(temp, now)   # 档位自动切换 (功能 #10)

        # --- 温度墙 (0.1.9): 过热保护, 优先级高于一切 (含智能启停) ---
        if cfg.temp_wall_enabled:
            wall = float(cfg.temp_wall_temp)
            if self._temp_wall_active:
                if temp < wall - 3.0:         # 滞回解除
                    self._temp_wall_active = False
                    self._last_sent_rpm = None  # 强制下一 tick 恢复正常控制
                    from .logbuf import LOGBUF
                    LOGBUF.write(f'[温度墙] 已解除 ({temp:.0f}°C), 恢复正常控制')
                else:
                    self._apply_target(4800, now, 'tempwall')
                    return
            elif temp >= wall:
                self._temp_wall_active = True
                from .logbuf import LOGBUF
                LOGBUF.write(f'[温度墙] 触发 ({temp:.0f}°C ≥ {wall:.0f}°C), 拉满 4800 RPM')
                self._apply_target(4800, now, 'tempwall')
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
            if getattr(self, '_curve_was_on', False):
                self._curve_was_on = False
                self._restore_pre_curve(now)   # 档位记忆: 恢复开启智能变频前的档位
                return
            if now < self._manual_until:
                return
            # 恢复配置的固定转速 (仅在未下发过或掉线重连后)
            if self._last_sent_rpm is None:
                self._apply_target(cfg.fixed_rpm, now, 'manual')
            return

        # --- 智能变频曲线 ---
        if not getattr(self, '_curve_was_on', False):
            # 开启曲线瞬间记住当前手动转速 (档位记忆), 关闭后恢复
            self._curve_was_on = True
            self._pre_curve_rpm = self._last_sent_rpm or cfg.fixed_rpm or 0
            cfg.pre_curve_rpm = self._pre_curve_rpm
            cfg.save()
        if now < self._manual_until:         # 手动保持窗在曲线模式同样生效 (审计 G2)
            return
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
        if self.config.tec_auto_enabled:
            level = self._tec_level
        elif rpm >= 3800:
            level = 4
        elif rpm >= 3000:
            level = 3
        elif rpm >= 2400:
            level = 2
        else:
            level = 1
        # 档位映射 (v3.35): 手动模式按转速渐进 L1→L4 (散热器屏 FAN LV 平滑跟随)
        due = now - self._last_send_ts > 5.0
        # 停转 (rpm=0) 只在切换瞬间立即下发, 之后靠 due 5s 保活一次, 不再每 tick 重发 (审计 G1)
        stop_now = rpm == 0 and self._last_sent_rpm != 0
        if self._last_sent_rpm is None or stop_now or \
           abs(rpm - self._last_sent_rpm) >= hyst or due or \
           (self.config.tec_auto_enabled and level != self._last_tec_sent):
            self.device.set_fixed_rpm(rpm, level=level)
            self._last_host_0x24_ts = time.time()   # 主机 0x24 (区分设备按钮变更)
            self._last_sent_rpm = rpm
            self._last_send_ts = now
            self._last_tec_sent = level

    def _restore_pre_curve(self, now: float):
        """档位记忆 (v3.52): 关闭智能变频 → 恢复开启前记住的手动转速/档位。"""
        from .logbuf import LOGBUF
        mem = max(0, min(4800, int(getattr(self, '_pre_curve_rpm', 0)
                                or self.config.fixed_rpm or 0)))
        level = 4 if mem >= 3800 else 3 if mem >= 3000 else 2 if mem >= 2400 else 1
        self.config.fixed_rpm = mem
        self.config.save()
        if not self.device.connected:
            # 断线/未连接: _send 会静默吞掉下发, 记"已恢复"是假成功 ——
            # 置 None 让引擎重连后按 fixed_rpm 自动重发 (v3.61 修复)
            self._last_sent_rpm = None
            LOGBUF.write(f'[档位] 设备未连接, 已记忆目标 {mem} RPM (L{level}) —— 重连后自动恢复')
            return
        try:
            self.device.set_fixed_rpm(mem, level=level)
            self._last_sent_rpm = mem
            self._last_tec_sent = level
            self._last_host_0x24_ts = now
            self._last_send_ts = now
            self.deviceGearChanged.emit(level, mem)   # GUI 同步 (日志/托盘/标签)
            LOGBUF.write(f'[档位] 已退出智能变频, 恢复之前的档位: {mem} RPM (L{level})')
        except Exception as e:
            LOGBUF.write(f'[档位] 恢复失败: {e!r} —— 引擎将按 fixed_rpm 重发')
            self._last_sent_rpm = None

    # ============ 档位自动切换 (功能 #10) ============
    def _tec_tick(self, temp: float, now: float):
        """风扇档位随温度自动升/降档 (进入阈值 + 回落滞回)。
        档位与风扇目标转速合并到同一帧下发, 三种控制模式通用。"""
        cfg = self.config
        if not cfg.tec_auto_enabled:
            self._tec_level = 1
            return
        thr = sorted(cfg.tec_thresholds or [55, 65, 75]) + [999]
        entry = {1: 0, 2: thr[0], 3: thr[1], 4: thr[2]}
        lvl = self._tec_level
        while lvl < 4 and temp >= entry[lvl + 1]:          # 升档: 立即
            lvl += 1
        while lvl > 1 and temp < entry[lvl] - cfg.tec_hysteresis:   # 降档: 滞回
            lvl -= 1
        if lvl != self._tec_level:
            self._tec_level = lvl
            self._last_tec_change_ts = now   # 换档 = 设备写曲线 flash; 屏幕上屏需避让
            rpm_now = self._last_sent_rpm or cfg.fixed_rpm or 0
            try:
                self.device.set_fixed_rpm(rpm_now, level=lvl)
                self._last_tec_sent = lvl
                self._last_host_0x24_ts = now   # 主机直发换档, 防 0x25 轮询误判成设备按钮 (审计 G5)
            except Exception as e:
                from .logbuf import LOGBUF
                LOGBUF.write(f'[档位] 下发失败: {e}')
                return
            from .logbuf import LOGBUF
            LOGBUF.write(f'[档位] → L{lvl} ({temp:.0f}°C)')

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

    # ============ 0x07 主机参数推送 (屏幕参数页数据源) ============
    # 参数页 ID 映射 (0.1.8, 官方"参数选项"逆向定案):
    # 官方 8 项中"风扇转速"是设备本地数据 (0x06), 其余 7 项 = 0x07 的 7 个 ID:
    # ID00=CPU温 ID01=GPU温 ID02=GPU负载 ID03=磁盘占用率 ID05=运行使用率(内存)
    # ID06=CPU负载 (2026-10-06 修正: 旧标注"GPU 热点"系误判) ID07=时间
    PARAM_DEFS = [
        ('cpu_temp', 0x00), ('gpu_temp', 0x01), ('gpu_load', 0x02),
        ('disk', 0x03), ('ram', 0x05), ('cpu_load', 0x06), ('time', 0x07),
    ]

    def _param_value(self, key: str, cpu: float, gpu: float) -> int:
        """参数选项某项的实时数值 (0~999 整数; 无读数回 0)。"""
        tp = self.temps
        if key == 'cpu_temp':
            return int(round(cpu)) if cpu and cpu > 1 else 0
        if key == 'gpu_temp':
            return int(round(gpu)) if gpu and gpu > 1 else 0
        if key == 'gpu_load':
            return int(round(tp.gpu_load)) if tp.gpu_load is not None else 0
        if key == 'cpu_load':
            return int(round(tp.cpu_load)) if tp.cpu_load is not None else 0
        if key == 'disk':
            return int(round(tp.disk_active)) if tp.disk_active is not None else 0
        if key == 'ram':
            return int(round(tp._ram_percent() or 0))
        if key == 'time':
            lt = time.localtime()
            return lt.tm_hour * 60 + lt.tm_min
        return 0

    def _host_info_entries(self, cpu: float, gpu: float):
        """构造 0x07 的 (ID, 值) 列表 —— 参数页推送与图片上传尾部心跳共用。

        0.1.8 起按 config.param_slots (官方"参数选项") 动态组装: 选中项按顺序推对应
        ID (官方预览语义"选择先后顺序为屏幕左右顺序"); **slots 为空 = 兼容模式**,
        推全部 7 项 (与旧版行为一致)。ID 语义 2026-10-06 定案见 PARAM_DEFS
        (旧版 ID02=GPU 功耗 / ID06=GPU 热点 / ID05 恒 74 均系误判修正)。"""
        slots = list(getattr(self.config, 'param_slots', []) or [])
        if not slots:                       # 兼容模式: 官方全 7 项顺序
            slots = [k for k, _ in self.PARAM_DEFS]
        id_by_key = dict(self.PARAM_DEFS)
        out = []
        for key in slots:
            sid = id_by_key.get(key)
            if sid is None:
                continue
            out.append((sid, self._param_value(key, cpu, gpu)))
        if not out:                         # 兜底: 至少推时间
            out.append((0x07, self._param_value('time', cpu, gpu)))
        return out

    def _push_host_info07(self, cpu: float, gpu: float):
        """屏幕参数页推送 (1Hz), 格式/ID 语义见 protocol.build_host_info。"""
        self.device.push_host_info(self._host_info_entries(cpu, gpu))

    def _live_heartbeats(self):
        """图片上传尾部 16 条 0x07 心跳 (生产路径: 用实时主机参数, 非 cap8 实录)。"""
        cpu_raw, gpu_raw = self.temps.snapshot()
        frame = protocol.build_host_info(self._host_info_entries(cpu_raw, gpu_raw))
        return [frame] * 16

    # ============ 屏幕图片上传 ============
    def start_image_upload(self, path: str, flip_h: bool = False, flip_v: bool = False,
                           fit: str = 'stretch'):
        """请求上传图片到屏幕 (GUI 调用, 立即返回; 实际在 worker 线程内执行)。

        手动上传优先于自动信息卡: 清掉待执行的自动上屏请求; 成功后模式转 custom。
        """
        self._upload_cancel.clear()
        with self._screen_qlock:             # 队列操作持锁 (审计 G6)
            self._screen_queue.clear()       # 手动图片优先: 清掉待执行的自动上屏
        self._upload_req = (path, flip_h, flip_v, fit)
        self._screen_cancel.clear()   # 新上传不应继承上一次取消的残留状态

    def cancel_image_upload(self):
        """请求取消正在进行的上传 (手动/自动上屏一律生效, 尽力而为)。"""
        self._upload_cancel.set()
        self._screen_cancel.set()

    @property
    def uploading(self) -> bool:
        return self._uploading

    @property
    def screen_active(self) -> bool:
        """手动/自动上屏任一在执行或排队 (GUI 忙态判定, 防误复位/重复排队)。"""
        return (self._uploading or self._upload_req is not None
                or bool(self._screen_queue))

    def _run_image_upload(self, path, flip_h, flip_v, fit):
        """在 worker 线程内独占执行上传; 结束/失败/取消后一律恢复引擎。"""
        from .logbuf import LOGBUF
        if self.device.conn_type != 'usb':   # 红线 (审计 G13): 图片上传仅支持 USB
            LOGBUF.write('[屏幕] 上传拒绝: 图片上传仅支持 USB 通道')
            self.uploadFinished.emit(self.device._upload_fail('图片上传仅支持 USB'))
            return
        LOGBUF.write(f'[屏幕] 开始上传图片: {path} (翻转 H={flip_h} V={flip_v} fit={fit})')
        self._uploading = True

        def _once():
            try:
                return self.device.upload_image(
                    path, heartbeats=self._live_heartbeats(),
                    progress_cb=lambda s, t: self.uploadProgress.emit(s, t),
                    cancel_event=self._upload_cancel, flip_h=flip_h, flip_v=flip_v, fit=fit)
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                traceback.print_exc()
                LOGBUF.write(f'[屏幕] 上传异常: {e}\n{tb}')
                return {'ok': False, 'reason': f'异常: {e}', 'c6_total': 0, 'c6_bad': 0,
                        'first_bad': None, 'status_hist': {}, 'c4_ack_ms': 0.0,
                        'elapsed_ms': 0.0, 'canceled': False, 'sent': 0}

        try:
            res = _once()
            # 整流重传兜底 (2026-10-06 诊断包案): 页边界 0x0C 级联失败后重传即愈
            # (8 次上传 4 败, 手动重传全部成功)。只重传 1 次防循环; 取消/握手失败不重传。
            if not res.get('ok') and not res.get('canceled') and res.get('c6_bad', 0) > 0:
                LOGBUF.write(f'[屏幕] 首次上传失败 ({res.get("c6_bad", 0)} 非00) —— 自动重传一次')
                res = _once()
        finally:
            self._uploading = False
            self._upload_cancel.clear()
            self._screen_cancel.clear()   # 取消按钮双事件置位, 两个都要清 (否则自动上屏永久秒取消)
            # 恢复引擎: 强制重发当前转速 + 立即补推 0x07 参数页
            self._last_sent_rpm = None
            self._last_07_ts = 0.0
        self.last_upload_kind = 'manual'
        LOGBUF.write(f'[屏幕] 上传结束: {res.get("reason", "")} '
                     f'(C6={res.get("c6_total", 0)} 非00={res.get("c6_bad", 0)})')
        if res.get('ok') and not res.get('canceled'):
            # 用户手动上传成功 → 切到自定义图片模式并持久化 (信息卡不再自动覆盖)
            self.config.screen_mode = 'custom'
            try:                                    # 磨损计数 (0.1.9)
                self.config.screen_upload_count = int(getattr(self.config, 'screen_upload_count', 0)) + 1
                self.uploadCountChanged.emit(self.config.screen_upload_count)
            except Exception:
                pass
            self.config.save()
        self.uploadFinished.emit(res)

    # ============ 屏幕信息卡 (v3.21: 默认卡 / 自定义图片) ============
    def restore_info_card(self):
        """GUI「恢复信息卡」: 切回 card 模式; 已连接 (USB) 时立即上当日卡。"""
        self.config.screen_mode = 'card'
        self.config.save()
        self._screen_cancel.clear()
        if (self.device.connected and self.device.conn_type == 'usb'
                and not self._uploading and self._upload_req is None):
            today = time.strftime('%Y-%m-%d')
            req = {'key': f'info:{today}', 'want': ('info', today)}
            with self._screen_qlock:         # 队列迭代/追加持锁 (审计 G6)
                if not self._screen_queue and \
                        not any(q['key'] == req['key'] for q in self._screen_queue):
                    self._screen_queue.append(req)

    def _fullscreen_now(self) -> bool:
        """前台窗口是否全屏覆盖其所在显示器 (排除自身进程) —— 情境卡片的游戏判定。
        按窗口最近显示器矩形判定 (而非固定主屏): 修复多屏/副屏游戏误判, 以及
        自动隐藏任务栏时"工作区≠显示器矩形"导致的漏判 (审计 G10)。"""
        try:
            u = ctypes.windll.user32
            hwnd = u.GetForegroundWindow()
            if not hwnd:
                return False
            pid = ctypes.wintypes.DWORD()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == os.getpid():
                return False                    # 自己的前台窗口不算游戏
            class RECT(ctypes.Structure):
                _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                            ('right', ctypes.c_long), ('bottom', ctypes.c_long)]
            class MONITORINFO(ctypes.Structure):
                _fields_ = [('cbSize', ctypes.c_ulong), ('rcMonitor', RECT),
                            ('rcWork', RECT), ('dwFlags', ctypes.c_ulong)]
            wr = RECT()
            if not u.GetWindowRect(hwnd, ctypes.byref(wr)):
                return False
            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            mon = u.MonitorFromWindow(hwnd, 2)   # 2 = MONITOR_DEFAULTTONEAREST
            if not mon or not u.GetMonitorInfoW(mon, ctypes.byref(mi)):
                return False
            m = mi.rcMonitor
            mw, mh = m.right - m.left, m.bottom - m.top
            if mw <= 0 or mh <= 0:
                return False
            # 窗口矩形覆盖所在显示器 ≥98% (宽高双维) 判为全屏
            return ((wr.right - wr.left) >= 0.98 * mw
                    and (wr.bottom - wr.top) >= 0.98 * mh)
        except Exception:
            return False

    def _screen_tick(self, now, cpu=None, gpu=None):
        """屏幕内容维持 (每 tick 调用): 连接恢复 + 情境卡片决策 (v3.32)。

        触发只看"内容 key"变化 (警报 > 游戏 > 信息卡), 三重防护照旧:
        换档 30s 平静窗 / 失败 10min 冷却 / 任意两次上屏 ≥120s (flash 磨损预算)。
        """
        from . import screen_card as sc
        with self._screen_qlock:             # 队列可被 GUI 线程 clear (审计 G6)
            q_pending = bool(self._screen_queue)
        st = {
            'mode': getattr(self.config, 'screen_mode', 'card'),
            'usb': self.device.conn_type == 'usb',
            'busy': self._uploading or self._upload_req is not None,
            'pending': q_pending,
            'since_tec': now - self._last_tec_change_ts,
            'fail_active': now < self._screen_fail_until,
            'restore_done': self._screen_last_key is not None,
            'names_ready': bool(self.temps.cpu_name or self.temps.gpu_name),
            'since_connect': now - self._connect_ts,
            'custom_path': self.config.last_image_path,
            'custom_ok': bool(self.config.last_image_path)
                         and os.path.exists(self.config.last_image_path),
            'cards_enabled': getattr(self.config, 'screen_cards', True),
            'card_auto': getattr(self.config, 'screen_card_auto', True),
            'cpu': cpu or 0, 'gpu': gpu or 0,
            'fullscreen': self._fullscreen_now(),
            'proc': '',
            'today': time.strftime('%Y-%m-%d'),
            'last_key': self._screen_last_key,
            'since_upload': now - self._screen_last_upload_ts,
        }
        if st['fullscreen']:
            fg = (foreground_process() or '').lower()
            if fg:
                st['proc'] = os.path.splitext(fg)[0]
        # 换档平静窗死锁保险: 阻塞超过 120s 强制放行 (失败仍有 10min 重试兜底)
        if st['since_tec'] < 30:
            if self._screen_tec_block_since == 0.0:
                self._screen_tec_block_since = now
            elif now - self._screen_tec_block_since > 120:
                st['since_tec'] = 999                # 平静窗等太久, 强制放行
        else:
            self._screen_tec_block_since = 0.0
        try:
            decided = sc.decide_card(st)
        except Exception as e:
            from .logbuf import LOGBUF
            LOGBUF.write(f'[屏幕] 情境决策异常: {e}')
            return
        if decided is None:
            return
        key, want = decided
        with self._screen_qlock:             # 队列迭代/追加持锁 (审计 G6)
            if any(q['key'] == key for q in self._screen_queue):
                return                           # 同 key 已在队列, 去重
            self._screen_queue.append({'key': key, 'want': want})
        # 磨损预算时间戳改由 _run_screen_upload 成功后记账 (审计 G19)

    def _gear_sync_tick(self, now):
        """0x25 轮询 (每 ~4s): 检测散热器物理按钮换档 → 工具箱按当前模式立即接管。

        v3.69 简化模型 (用户定案): 工具箱是唯一权威, 物理按钮的变更一律被当前模式
        的输出覆盖回去 (智能变频=曲线目标, 手动=fixed_rpm) —— 不再做双向同步/
        档位记忆/采纳持久化, 一劳永逸。"""
        if self._uploading or self._upload_req is not None or self._screen_queue:
            return
        if now - self._last_0x25_poll < 4.0:
            return
        self._last_0x25_poll = now
        try:
            cur = self.device.get_cur_cooling()
        except Exception:
            return
        if not cur:
            return
        lvl, rpm = cur.get('on'), cur.get('rpm')
        if lvl is None or rpm is None:
            return
        # 回读与工具箱意图一致 (= 刚下发的回声) → 不是按钮变更
        same_as_sent = (self._last_sent_rpm is not None
                        and abs((rpm or 0) - self._last_sent_rpm) <= 60)
        changed = (not same_as_sent
                   and (self._last_dev_level is None
                        or lvl != self._last_dev_level
                        or abs((rpm or 0) - (self._last_dev_rpm or 0)) > 60))
        self._last_dev_level, self._last_dev_rpm = lvl, rpm
        if not changed:
            return
        if now - self._last_host_0x24_ts < 6:
            return                               # 主机刚发过 0x24 → 自己的回声
        # 物理按钮换档 → 清空下发记忆, 引擎下一 tick 按当前模式立即接管
        from .logbuf import LOGBUF
        self._last_sent_rpm = None
        self._last_host_0x24_ts = 0
        LOGBUF.write(f'[档位] 物理按钮换档 (L{lvl} · {rpm} RPM) —— 已按当前模式接管')

    def _run_screen_upload(self, req):
        """在 worker 线程内独占执行屏幕内容上屏; 结束/失败后一律恢复引擎。

        req = {'key': str, 'want': tuple}; want 形态:
          ('info', today) | ('game', proc) | ('alarm', cpu, gpu) | ('custom', path)。
        走 device.upload_canvas 同一管道, **官方 7ms 档** —— 自动上屏低频且要稳
        (4ms 在设备忙/档位换档背景下会 0x0C 级联, 见 01:40 案例), 不省这 5 秒。
        """
        import datetime
        from .logbuf import LOGBUF
        from . import screen_upload as su
        from . import screen_card as sc
        if self.device.conn_type != 'usb':   # 红线 (审计 G13): 图片上传仅支持 USB
            LOGBUF.write('[屏幕] 自动上屏跳过: 图片上传仅支持 USB 通道')
            self.uploadFinished.emit(self.device._upload_fail('图片上传仅支持 USB'))
            return
        try:
            key, want = req['key'], req['want']
        except Exception as e:
            # req 格式防御 (v3.21 旧元组误入也不允许炸掉 worker 线程)
            LOGBUF.write(f'[屏幕] 自动上屏 req 格式错误: {req!r} ({e})')
            res = self.device._upload_fail(f'内部错误: req 格式 {type(req).__name__}')
            self.uploadFinished.emit(res)
            return
        kind = want[0]
        LOGBUF.write(f'[屏幕] 自动上屏: {key}')
        self._uploading = True

        def _once():
            try:
                if kind == 'info':
                    img = sc.render_card(
                        date_str=want[1],
                        weekday_idx=datetime.datetime.strptime(want[1], '%Y-%m-%d').weekday(),
                        cpu_model=sc.clean_model_name(self.temps.cpu_name, 'cpu'),
                        gpu_model=sc.clean_model_name(self.temps.gpu_name, 'gpu'))
                    data = su.qimage_to_rgb565_be(img)
                elif kind == 'game':
                    rpm = int((self._last_status or {}).get('rpm', 0) or 0)
                    img = sc.render_game(proc=want[1], rpm=rpm, level=self._tec_level)
                    data = su.qimage_to_rgb565_be(img)
                elif kind == 'alarm':
                    img = sc.render_alarm(cpu=want[1], gpu=want[2])
                    data = su.qimage_to_rgb565_be(img)
                elif kind == 'custom':
                    p = want[1]
                    if not p or not os.path.exists(p):
                        raise ValueError(f'自定义图片路径无效: {p!r}')
                    data = su.image_to_rgb565_be(p, fit=getattr(self.config, 'image_fit', 'stretch'))
                else:                            # want 只可能是以上四种 (防御)
                    raise ValueError(f'未知上屏类型: {kind!r}')
                return self.device.upload_canvas(
                    data, heartbeats=self._live_heartbeats(),
                    progress_cb=lambda s, t: self.uploadProgress.emit(s, t),
                    cancel_event=self._screen_cancel, base_ms=7.0)
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                traceback.print_exc()
                LOGBUF.write(f'[屏幕] 自动上屏异常: {e}\n{tb}')
                return self.device._upload_fail(f'异常: {e}')

        try:
            res = _once()
            # 整流重传兜底 (同 _run_image_upload, 2026-10-06 案): 级联非00 重传即愈
            if not res.get('ok') and not res.get('canceled') and res.get('c6_bad', 0) > 0:
                LOGBUF.write(f'[屏幕] 首次上屏失败 ({res.get("c6_bad", 0)} 非00) —— 自动重传一次')
                res = _once()
        finally:
            self._uploading = False
            self._screen_cancel.clear()   # 取消意图已消化, 不残留给下次自动上屏 (否则恢复卡永久秒取消)
            # 恢复引擎: 强制重发当前转速 + 立即补推 0x07 参数页
            self._last_sent_rpm = None
            self._last_07_ts = 0.0
        LOGBUF.write(f'[屏幕] 自动上屏结束: {res.get("reason", "")} '
                     f'(C6={res.get("c6_total", 0)} 非00={res.get("c6_bad", 0)})')
        if res.get('ok'):
            self._screen_last_key = key          # 成功才记账 (失败冷却后重试)
            self._screen_last_upload_ts = time.time()   # 成功后记账磨损预算 (审计 G19)
            try:                                        # 磨损计数 (0.1.9): 累计成功上屏次数
                cfg = self.config
                cfg.screen_upload_count = int(getattr(cfg, 'screen_upload_count', 0)) + 1
                cfg.save()
                self.uploadCountChanged.emit(cfg.screen_upload_count)
            except Exception:
                pass
        elif not res.get('canceled'):
            self._screen_fail_until = time.time() + 600   # 10min 冷却后自动重传治愈残图
        self.last_upload_kind = 'screen'
        self.uploadFinished.emit(res)

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
                    if self.device.connect(timeout=6):   # 短超时直连, 不阻塞自动探测 (审计 G3)
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
        """场景配置 (0.1.9): 前台进程命中 profile → 应用 [固定转速/曲线方案/灯效模式];
        离开场景 → 恢复进入前快照。修复旧版"离开有规则进程后 RPM 残留" (P2)。"""
        proc = foreground_process()
        if not proc:
            return
        idx, prof = match_scene_profile(getattr(self.config, 'scene_profiles', []) or [], proc)
        key = ('p', idx) if prof else ('none', proc)
        if key == self._scene_applied_key:
            return
        prev = self._scene_applied_key
        self._scene_applied_key = key
        if prof:
            if prev is None or prev[0] != 'p':
                self._scene_snapshot()       # 进入第一个场景前快照 (A→B 不重复快照)
            self._apply_scene_profile(prof)
        elif prev is not None and prev[0] == 'p':
            self._restore_scene()            # 离开场景 → 恢复快照 (P2 修复)

    def _scene_snapshot(self):
        """记录进入场景前的状态 (曲线方案 + 灯效参数), 供离开时恢复。"""
        cfg = self.config
        self._scene_saved = {
            'curve_active': cfg.curve_active,
            'curve': [list(x) for x in (cfg.curve or [])],
        }
        try:
            r = self.device._send(protocol.get_cur_rgb())
            for cmd, data in r:
                if cmd == 0x13 and len(data) >= 9:
                    self._scene_saved['rgb9'] = bytes(data[:9])   # 完整 0x13 应答 (9B)
        except Exception:
            pass                              # 读不到灯效就不恢复灯效 (rpm/曲线照常)

    def _apply_scene_profile(self, prof: dict):
        from .logbuf import LOGBUF
        try:
            rpm = int(prof.get('rpm', 0) or 0)
            if rpm > 0:
                self.device.set_fixed_rpm(rpm)
                self._last_sent_rpm = rpm
                self._last_host_0x24_ts = time.time()
                self._manual_until = time.time() + 10
            scheme = prof.get('scheme') or ''
            if scheme and scheme in (self.config.curve_profiles or {}):
                self.config.curve_active = scheme
                self.config.curve = [list(x) for x in self.config.curve_profiles[scheme]]
                self.config.save()
                self._last_sent_rpm = None    # 引擎下 tick 按新曲线重发
            lm = prof.get('light_mode', -1)
            if lm is not None and int(lm) >= 0:
                saved = self._scene_saved or {}
                rgb9 = saved.get('rgb9')
                if rgb9 and len(rgb9) >= 8:
                    # 0x12 params = [mode][speed16][亮][彩][R][G][B] —— 原参数只换模式位
                    params = bytes([int(lm) & 0xFF]) + rgb9[1:8]
                    self.device._send(protocol.build_frame(0x12, params))
            LOGBUF.write(f"[场景] 应用 {prof.get('name', '?')} "
                         f"(rpm={rpm or '-'} 方案={scheme or '-'} 灯效={lm})")
        except Exception as e:
            LOGBUF.write(f'[场景] 应用失败: {e}')

    def _restore_scene(self):
        from .logbuf import LOGBUF
        saved = self._scene_saved or {}
        try:
            if 'curve_active' in saved:
                self.config.curve_active = saved['curve_active']
                self.config.curve = [list(x) for x in saved['curve']]
                self.config.save()
            if 'rgb9' in saved:
                self.device._send(protocol.build_frame(0x12, bytes(saved['rgb9'][:8])))
            self._last_sent_rpm = None        # 引擎下 tick 恢复固定转速/曲线
            self._scene_saved = None
            LOGBUF.write('[场景] 已恢复进入前状态')
        except Exception as e:
            LOGBUF.write(f'[场景] 恢复失败: {e}')

    # ============ 连接 ============
    def _connect(self) -> bool:
        user = self._user_connect
        self._user_connect = False
        ble_timeout = None
        if self.device.conn_type == 'ble':
            # GUI 主动连接: 等 45s 给用户长按配对键; 自动重连: 6s 短扫描快速失败,
            # 让双通道探测尽早接管 (否则配置为蓝牙而设备在 USB 时启动卡死 45s)。
            ble_timeout = 45.0 if user else 6.0
            hint = ', 请长按散热器按键 3-5 秒触发广播' if user else ''
            self.connectionChanged.emit(False, f'蓝牙扫描/连接中 (最长{ble_timeout:.0f}秒{hint})...')
        try:
            ok = self.device.connect(timeout=ble_timeout)
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
            self.device.mark_host_control()     # 0x22 控制源=01, 官方会话开场前置
            self._last_07_ts = 0.0              # 连上即推首帧 0x07, 参数页尽快出数据
            self._screen_last_key = None        # 新连接: 屏幕内容重新恢复一次 (v3.21)
            self._last_dev_level = None         # 重连后 0x25 基线重建, 防误报按钮换档 (审计 G5)
            self._last_dev_rpm = None
            self._connect_ts = time.time()
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
        self._user_connect = True

    def pause_connection(self) -> bool:
        """状态页'断开'按钮: 停止轮询并断开, 直到'连接'按下"""
        if self._uploading:                  # 上传中断开会 dispose 设备句柄, 上传必失败 (审计 G20)
            from .logbuf import LOGBUF
            LOGBUF.write('[连接] 上传进行中, 已暂缓断开 (请等待上传完成)')
            return False
        self._paused = True
        self._last_sent_rpm = None
        try:
            self.device.disconnect()
        except Exception:
            pass
        self.connectionChanged.emit(False, '已断开 (点击"连接"重新接入)')
        return True

    def resume_connection(self):
        self._paused = False
        self._last_sent_rpm = None
        self._user_connect = True

    def emit_info(self):
        info = {
            'firmware': self.device.get_firmware_version(),
            'cooling': self.device.get_cur_cooling(),
            'curve': self.device.get_curve(1),
            'rgb_on': self.device.get_rgb_switch(),
        }
        self.infoChanged.emit(info)

    # ============ GUI 控制接口 ============
    def apply_fixed_rpm(self, rpm: int, manual: bool = True, quiet: bool = False):
        """quiet=True 用于启动期自动还原 (未连接时静默跳过, 不刷错误);
        GUI 手动操作保持原报错路径。未连接时引擎连上后也会自行恢复固定转速。"""
        if self._uploading:      # 上传期间设备独占, 忽略一切下发 (结束后引擎自会恢复)
            return
        # 档位与引擎平滑映射一致 (2400/3000/3800): 滑条/挡位卡才能逐级到达 L1-L4
        # (此前 level=None 走设备层二值映射 >2800→L4, L2/L3 永远发不出来, v3.65)
        level = 4 if rpm >= 3800 else 3 if rpm >= 3000 else 2 if rpm >= 2400 else 1
        try:
            self.device.set_fixed_rpm(rpm, level=level)
        except Exception as e:
            if quiet:
                return
            from .logbuf import LOGBUF
            LOGBUF.write(f'[下发失败] {rpm} RPM: {e}')
            self.connectionChanged.emit(False, f'转速下发失败: {e} (等待重连...)')
            return
        self._last_sent_rpm = rpm
        self._last_send_ts = time.time()
        if manual:
            self._manual_until = time.time() + 5

    def set_rgb(self, on: bool):
        if self._uploading:
            return
        body = bytes([0x04, 0x10, 0x01 if on else 0x00])
        f = bytes([protocol.HEADER]) + body + bytes([protocol.checksum(bytes([protocol.HEADER]) + body)])
        self.device._send(f, wait_s=0.3)

    def get_lighting(self):
        """读当前灯效 (GUI 灯效面板初始化用), 未连接/失败返回 None 不刷错误"""
        if self._uploading:                  # 上传期防插帧 (审计 G7)
            return None
        t = self.device._t
        if t is None or not getattr(t, 'is_connected', True):
            return None
        try:
            return self.device.get_rgb_effect()
        except Exception as e:
            from .logbuf import LOGBUF
            LOGBUF.write(f'[灯效] 读取失败: {e}')
            return None

    def get_rgb_switch(self) -> Optional[bool]:
        if self._uploading:                  # 上传期防插帧 (审计 G7)
            return None
        try:
            return self.device.get_rgb_switch()
        except Exception:
            return None

    def set_lighting(self, mode: int, speed: int, brightness: int,
                     color_mode: int, rgb):
        """写完整灯效 (GUI 灯效面板)"""
        if self._uploading:
            return
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
        if self._uploading:
            return
        try:
            self.device.set_rgb_color(*self.GEAR_RGB[gear % len(self.GEAR_RGB)])
        except Exception as e:
            from .logbuf import LOGBUF
            LOGBUF.write(f'[挡位灯] 写入失败: {e}')

    # ============ 调试面板 ============
    def debug_send(self, hex_str: str) -> str:
        """发送原始 hex 命令并返回应答文本 (调试面板用, 慎重!)"""
        if self._uploading:
            return '上传进行中, 调试发送已暂停'
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
