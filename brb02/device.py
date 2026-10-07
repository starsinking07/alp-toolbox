# -*- coding: utf-8 -*-
"""BRB02 设备服务层 (基于实机验证协议)。"""
from __future__ import annotations

import threading
import time
from typing import Optional

from . import protocol
from .transport_usb import TransportUSB, find_device


def _flip_canvas(data: bytes, w: int, h: int, flip_h: bool, flip_v: bool) -> bytes:
    """RGB565 大端画布的 水平/垂直 翻转 (设备方向标定用)。

    flip_h = 左右镜像 (逐行像素反序); flip_v = 上下镜像 (行序反序)。
    """
    if not (flip_h or flip_v):
        return data
    row = w * 2
    rows = [data[y * row:(y + 1) * row] for y in range(h)]
    if flip_h:
        rows = [b''.join(r[i:i + 2] for i in range(row - 2, -1, -2)) for r in rows]
    if flip_v:
        rows = rows[::-1]
    return b''.join(rows)


class Brb02Device:
    """散热器设备对象。所有帧均按已验证协议构建。支持 USB / BLE 双通道。"""

    MAX_RPM = 4800

    def __init__(self, conn_type: str = 'usb'):
        self.conn_type = conn_type          # 'usb' | 'ble'
        self.ble_address = None             # 上次 BLE 连接地址 (自动重连用)
        self._t = None
        self._lock = threading.Lock()
        self.firmware = ''
        self._rgb_cfg_head = None           # 0x12 写颜色所需的配置头 (GET 0x13 前 5 字节)
        self._rgb_cfg_ts = 0.0

    # ---- 连接 ----
    def connect(self, timeout: float | None = None) -> bool:
        """timeout 仅蓝牙通道有意义: 扫描/连接最长等待秒数 (None=默认 45s)。"""
        if self._t is not None:
            # 已连接: 检查底层是否仍存活
            try:
                if self.conn_type == 'usb':
                    dev = find_device()
                    if dev is None:
                        return False
                    # 重枚举检测 (审计 G15): bus/address 变化 = 旧句柄已失效的假连接,
                    # 返回 False 走下方 disconnect → 重建, 防假连接死循环
                    return (dev.bus, dev.address) == self._t.usb_addr
                return self._t.is_connected
            except Exception:
                pass
        try:
            self.disconnect()
        except Exception:
            pass
        if self.conn_type == 'ble':
            from .transport_ble import TransportBLE
            self._t = TransportBLE(address=self.ble_address)
            ok = self._t.connect(timeout=timeout or 45.0)
            if ok:
                self.ble_address = self._t.address
            return ok
        dev = find_device()
        if dev is None:
            return False
        self._t = TransportUSB(dev)
        return True

    def disconnect(self):
        if self._t:
            try:
                self._t.close()
            except Exception:
                pass
            self._t = None

    @property
    def connected(self):
        """底层存活判定 (不能只看 transport 对象是否存在 —— BLE 连接失败后
        TransportBLE 仍会驻留, 僵尸对象曾让上层永远跳过重连分支, 卡死在
        "蓝牙连接丢失"循环)。USB = 设备仍在总线; BLE = bleak 客户端仍连接。"""
        if self._t is None:
            return False
        try:
            return self._t.is_connected
        except Exception:
            return False

    # ---- 底层 ----
    def _send(self, frame: bytes, wait_s: float = 0.45, max_rx: int = 6) -> list:
        """发送并收集应答。USB 通道会补零到 65 字节; BLE 直接发帧本身。"""
        if self._t is None:      # 未连接 (启动初期/断线) 时静默忽略, 防止 None.write 崩溃
            return []
        with self._lock:
            self._t.write(frame)
            time.sleep(wait_s)
            rxs = self._t.read_all(int(wait_s * 400), max_rx)
        out = []
        for rx in rxs:
            p = protocol.parse_frame(rx)
            if p:
                out.append(p)
        return out

    # ---- 状态 ----
    def poll_report(self, seconds: float = 0.6) -> Optional[dict]:
        """收自动上报 (设备每 500ms 推送), 返回 {rpm, flag}"""
        t0 = time.time()
        last = None
        while time.time() - t0 < seconds:
            try:
                with self._lock:
                    rx = self._t.read(300)
            except Exception as e:
                # 读超时属常态 (设备 500ms 才推一帧): 吃满窗口继续等, 其他异常才中断 (审计 G16)
                if isinstance(e, TimeoutError) or 'timeout' in str(e).lower() \
                        or getattr(e, 'errno', None) == 110:
                    continue
                break
            p = protocol.parse_frame(bytes(rx))
            if p and p[0] == protocol.Cmd.SYSTEM_INFO_REPORT:
                d = protocol.parse_status_report(p[1])
                if d:
                    last = d
        return last

    def set_on_off_vector(self, smart_startstop: bool, power_on: bool) -> dict | None:
        """写 0x02 开关向量 (智能启停 + 通电自启) 并 0x03 回读确认。
        返回回读确认后的向量 dict; None = 写入或回读失败 (状态未知)。"""
        self._send(protocol.set_on_off_vector(smart_startstop, power_on), wait_s=0.3)
        cur = self.get_on_off_vector()
        if cur and cur.get('smart_startstop') == bool(smart_startstop)                 and cur.get('power_on') == bool(power_on):
            return cur
        return None

    def get_on_off_vector(self) -> dict | None:
        """读 0x03 开关向量 (智能启停 + 通电自启)。失败/超时返回 None。"""
        rxs = self._send(protocol.get_on_off_vector(), wait_s=0.4)
        for cmd, data in rxs:
            if cmd == 0x03:
                return protocol.parse_on_off_vector(data)
        return None

    def get_firmware_version(self) -> str:
        rxs = self._send(protocol.get_firmware_version())
        for cmd, data in rxs:
            if cmd == 0x01:
                return protocol.parse_firmware_version(data)
        return '?'

    def push_host_info(self, entries) -> None:
        """0x07 主机参数推送 (屏幕参数页数据源, service 1Hz 调度): 只写不等应答,
        应答 (若有) 由 poll_report 顺带清掉。失败静默 —— 单次推送中断不可操作,
        真断线由 poll_report 的正常路径检测并走重连, 不在此重复上报。"""
        if self._t is None:
            return
        try:
            with self._lock:
                self._t.write(protocol.build_host_info(entries))
        except Exception:
            pass

    def mark_host_control(self) -> None:
        """0x22 控制源=01 (PC 主机): 官方会话开场帧 (replay_07 前置), 失败无碍。"""
        try:
            self._send(protocol.set_control_source(0x01), wait_s=0.2)
        except Exception:
            pass

    def get_cur_cooling(self) -> Optional[dict]:
        rxs = self._send(protocol.get_cur_cooling())
        for cmd, data in rxs:
            if cmd == protocol.Cmd.GET_CUR_COOLING_CONFIG:
                return protocol.parse_cur_cooling(data)
        return None

    def get_curve(self, slot: int = 1) -> Optional[list]:
        rxs = self._send(protocol.get_any_cooling(slot))
        for cmd, data in rxs:
            if cmd == protocol.Cmd.GET_ANY_COOLING_CONFIG:
                return protocol.parse_curve(data)
        return None

    def get_rgb_switch(self) -> Optional[bool]:
        rxs = self._send(protocol.get_rgb_switch())
        for cmd, data in rxs:
            if cmd == protocol.Cmd.GET_RGB_SWITCH:
                return data[0] == 1 if data else None
        return None

    def get_rgb_effect(self) -> Optional[dict]:
        """读当前灯效: {'cfg': 配置头5B, 'rgb': (r,g,b), 'extra': 动态字节}"""
        rxs = self._send(protocol.get_cur_rgb())
        for cmd, data in rxs:
            if cmd == protocol.Cmd.GET_CUR_RGB_EFFECTS:
                return protocol.parse_rgb_effect(data)
        return None

    def set_rgb_color(self, r: int, g: int, b: int) -> bool:
        """挡位灯: 把当前灯效颜色换成 (r, g, b)。配置头缓存 60 秒。"""
        now = time.time()
        if self._rgb_cfg_head is None or now - self._rgb_cfg_ts > 60:
            eff = self.get_rgb_effect()
            if not eff:
                return False
            self._rgb_cfg_head = eff['cfg']
            self._rgb_cfg_ts = now
        self._send(protocol.set_rgb_color(self._rgb_cfg_head, (r, g, b)), wait_s=0.3)
        return True

    def set_rgb_effect(self, mode: int, speed: int, brightness: int,
                       color_mode: int, rgb) -> bool:
        """完整灯效写入 (模式/速度/亮度/单彩/颜色), 字段语义见 protocol.set_rgb_effect。"""
        self._send(protocol.set_rgb_effect(mode, speed, brightness, color_mode, rgb),
                   wait_s=0.3)
        return True

    # ---- 控制 ----
    def set_fixed_rpm(self, rpm: int, level: int | None = None):
        """设置固定转速 (0..4800)。
        level=None 时按转速自动选档 (>2800 → 档位 4);
        也可显式指定 1-4 (档位自动切换模式下由引擎传入)。"""
        rpm = max(0, min(self.MAX_RPM, int(rpm)))
        if level is None:
            level = 4 if rpm > 2800 else 1
        level = max(1, min(4, int(level)))
        self._send(protocol.set_cooling_fixed(rpm, level=level), wait_s=0.3)

    def set_curve(self, anchors):
        """写入智能变频曲线 (4 锚点)。⚠️ 只允许写入来自 get_curve 的合法值。
        峰值转速 >2800 时自动使用档位 4 (高档曲线才支持 4000)。
        校验 (审计 G23, flash 红线防线): 温度 0-110 且严格递增, 转速 0-4800 非递减;
        违例直接 raise ValueError, 不向设备下发任何帧。"""
        pts = [(int(t), int(r)) for t, r in anchors]
        if len(pts) != 4:
            raise ValueError(f'曲线需要 4 个锚点, 实际 {len(pts)}')
        prev_t = prev_r = None
        for t, r in pts:
            if not 0 <= t <= 110:
                raise ValueError(f'曲线温度越界: {t}°C (允许 0-110)')
            if not 0 <= r <= self.MAX_RPM:
                raise ValueError(f'曲线转速越界: {r} RPM (允许 0-{self.MAX_RPM})')
            if prev_t is not None:
                if t <= prev_t:
                    raise ValueError(f'曲线温度必须严格递增: {prev_t}°C → {t}°C')
                if r < prev_r:
                    raise ValueError(f'曲线转速必须非递减: {prev_r} → {r} RPM')
            prev_t, prev_r = t, r
        level = 4 if max(r for _t, r in pts) > 2800 else 1
        self._send(protocol.set_cooling_curve(pts, level=level), wait_s=0.3)

    # ---- 屏幕上传 (整程 ~10.5~16.3s, 必须在工作线程调用, 不阻塞调用方) ----
    @staticmethod
    def _upload_fail(reason, **kw) -> dict:
        base = {'ok': False, 'reason': reason, 'c6_total': 0, 'c6_bad': 0,
                'first_bad': None, 'status_hist': {}, 'c4_ack_ms': 0.0,
                'elapsed_ms': 0.0, 'canceled': False, 'sent': 0}
        base.update(kw)
        return base

    def read_screen_image(self, progress_cb=None, cancel_event=None) -> dict:
        """读回散热器当前屏图 (0xC7 起读 + 0xC8 逐帧拉, ~2096 帧, 约几十秒)。

        协议 (FanControlPortable/PIut02 交叉验证): 0xC7 空载荷 → 设备推首帧 0xA4;
        之后每发一条 0xC8 设备回一条 0xA4 (1:1)。0xA4 帧: [A4][LEN][seq u16le]
        [tag][载荷 LEN-6][CK], 载荷按到达顺序拼接 = 121,552B 画布 (RGB565BE);
        结束条件 = seq 低 15 位 <= 1 (尾帧 LEN=48)。
        返回 {'ok','reason','canvas':bytes|None,'frames','elapsed_ms','canceled'}。
        ⚠️ 独占操作: 调用方须在 worker 线程暂停轮询/下发。"""
        from . import screen_upload as su
        if self.conn_type != 'usb':
            return self._upload_fail('屏图读回仅支持 USB')
        if self._t is None:
            return self._upload_fail('设备未连接')
        t = self._t
        t0 = time.time()
        total_expect = su.CANVAS_BYTES
        frames_max = 2104                          # 2096 + 余量
        out = bytearray()
        n_frames = 0
        canceled = False

        C8_CMD = bytes([0xA5, 0x04, 0xC8, 0x71])   # 拉取下一条 (CK=sum)

        def _pull(send_cmd):
            """可选发一条命令并收一帧 0xA4 (超时返回 (None, False))。"""
            if send_cmd is not None:
                t.write_exact(send_cmd)
            end = time.time() + 2.0
            while time.time() < end:
                if cancel_event is not None and cancel_event.is_set():
                    return None, True
                try:
                    rx = bytes(t.read_exact(200, 64))
                except Exception:
                    continue
                if len(rx) >= 6 and rx[0] == 0xA4 and 6 <= rx[1] <= 64:
                    return rx[5:rx[1] - 1], False   # 载荷 (去 CK)
            return None, False

        try:
            import ctypes
            ctypes.windll.winmm.timeBeginPeriod(1)
        except Exception:
            pass
        try:
            t.write_exact(bytes([0xA5, 0x04, 0xC7, 0x70]))   # 0xC7 起读 → 设备直接推首帧
            for i in range(frames_max):
                # 首帧由 0xC7 直接推来 (只收); 之后每帧先发 0xC8 再收 (1:1)
                payload, canceled = _pull(None if i == 0 else C8_CMD)
                if canceled:
                    canceled = True
                    break
                if payload is None:
                    return self._upload_fail(
                        f'屏图读回中断 (第 {i + 1} 帧 2s 未到达, 已收 {len(out)}B)',
                        elapsed_ms=(time.time() - t0) * 1000.0, sent=n_frames)
                out += payload
                n_frames += 1
                if progress_cb is not None and i % 16 == 0:
                    try:
                        progress_cb(min(len(out), total_expect), total_expect)
                    except Exception:
                        pass
                if len(out) >= total_expect:        # 尾帧 42B 凑满即停 (seq≤1 同效)
                    break
            if len(out) < total_expect:
                return self._upload_fail(
                    f'屏图读回不完整 (已收 {len(out)}/{total_expect}B, {n_frames} 帧)',
                    elapsed_ms=(time.time() - t0) * 1000.0, sent=n_frames)
            if progress_cb is not None:
                try:
                    progress_cb(total_expect, total_expect)
                except Exception:
                    pass
            return {'ok': True, 'reason': f'读回成功 ({n_frames} 帧)',
                    'canvas': bytes(out[:total_expect]), 'frames': n_frames,
                    'elapsed_ms': (time.time() - t0) * 1000.0, 'canceled': False}
        except Exception as e:
            import traceback
            traceback.print_exc()
            return self._upload_fail(f'屏图读回异常: {e}',
                                     elapsed_ms=(time.time() - t0) * 1000.0, sent=n_frames)
        finally:
            try:
                ctypes.windll.winmm.timeEndPeriod(1)
            except Exception:
                pass

    def upload_image(self, path: str, heartbeats=None, progress_cb=None,
                     cancel_event=None, flip_h: bool = False,
                     flip_v: bool = False, fit: str = 'stretch',
                     base_ms: float | None = None) -> dict:
        """上传**图片文件**到散热器屏幕 (解码为画布后委托 upload_canvas)。

        path         : PNG/JPG 图片路径
        heartbeats   : 16 条实时 0x07 帧 (生产路径)。None = cap8 实录默认值, 仅供复刻验证。
        progress_cb  : f(done:int, total:int) 进度回调 (每 ~16 帧一次, 在调用线程执行)
        cancel_event : threading.Event; set() 后尽快中止
        flip_h/flip_v: 画布水平/垂直翻转 (设备方向标定)
        fit          : 'stretch' (默认, 拉伸铺满) | 'cover' (等比放大后居中裁边)
        base_ms      : 块流基速 (ms)。None = 7.0 (官方节奏, 封版); 监控上屏用 4.0。

        返回 dict: ok / reason / c6_total / c6_bad / first_bad / status_hist /
                  c4_ack_ms / elapsed_ms / canceled / sent
        """
        from . import screen_upload as su
        if self._t is None:
            return self._upload_fail('设备未连接')
        if path.lower().endswith('.bin'):
            # 官方缓存画布直传 (0.1.9 历史图片): bin 即 121,552B RGB565BE 画布, 免解码
            try:
                data = open(path, 'rb').read()
            except Exception as e:
                return self._upload_fail(f'画布文件读取失败: {e}')
            if len(data) != su.CANVAS_BYTES:
                return self._upload_fail(f'画布长度必须 {su.CANVAS_BYTES}, 实际 {len(data)}')
            return self.upload_canvas(data, heartbeats=heartbeats, progress_cb=progress_cb,
                                      cancel_event=cancel_event, flip_h=flip_h,
                                      flip_v=flip_v, base_ms=base_ms)
        try:
            data = su.image_to_rgb565_be(path, fit=fit)
        except Exception as e:
            return self._upload_fail(f'图片解码失败: {e}')
        return self.upload_canvas(data, heartbeats=heartbeats, progress_cb=progress_cb,
                                  cancel_event=cancel_event, flip_h=flip_h,
                                  flip_v=flip_v, base_ms=base_ms)

    def upload_canvas(self, canvas_bytes: bytes, heartbeats=None, progress_cb=None,
                      cancel_event=None, flip_h: bool = False, flip_v: bool = False,
                      base_ms: float | None = None) -> dict:
        """上传一张 **428×142 RGB565 大端画布** (121,552B) 到屏幕。

        移植自 tools/replay_upload24_builder.py (v24 设备全绿): C4 → 等真实 ACK →
        首块锚定 ACK+4.2ms → 按 at_ms 推进 (含 29 处 4096B flash 页停顿) → IN 排水线程
        → 汇总 C6 状态。**上传期间设备独占**: 调用方 (service) 须暂停其他下发与轮询。

        canvas_bytes : 121,552B 画布 (来自 screen_upload.image_to_rgb565_be /
                       qimage_to_rgb565_be, 或经 _flip_canvas 处理后的结果)
        其余参数同 upload_image (base_ms=None → 7.0; 监控上屏传 4.0)。
        """
        from . import screen_upload as su
        if self.conn_type != 'usb':
            # 红线 (审计 G13): 图片上传仅 USB —— 7ms 节拍 + ≥5ms 写间隔是 USB 时序
            return self._upload_fail('图片上传仅支持 USB')
        if self._t is None:
            return self._upload_fail('设备未连接')
        data = canvas_bytes
        if len(data) != su.CANVAS_BYTES:
            return self._upload_fail(f'画布长度必须 {su.CANVAS_BYTES}, 实际 {len(data)}')
        if flip_h or flip_v:
            data = _flip_canvas(data, su.CANVAS_W, su.CANVAS_H, flip_h, flip_v)
        try:
            frames = su.build_upload(data, heartbeats=heartbeats,
                                     base_ms=(su.BASE_MS if base_ms is None else base_ms))
        except Exception as e:
            return self._upload_fail(f'构建上传帧失败: {e}')

        total_all = len(frames) - 1                 # C4 之后的总帧数
        data_total = su.BLOCK_COUNT + 1             # 数据帧 (2095 块 + 尾帧) = 2096

        t = self._t
        acks = []                                   # (t, cmd, status)
        c6n = [0]                                   # C6 计数 (页首块门控用; reader 独写, GIL 安全)
        stop = threading.Event()

        def reader():
            while not stop.is_set():
                try:
                    rx = bytes(t.read_exact(20, 64))
                except Exception:
                    time.sleep(0.002)               # 断开时避免空转热循环
                    continue
                if rx[:1] == b'\xa5' and len(rx) >= 5 and rx[1] == 0x05:
                    acks.append((time.time(), rx[2], rx[3]))
                    if rx[2] == 0xC6:
                        c6n[0] += 1

        th = threading.Thread(target=reader, daemon=True)
        th.start()

        try:                                        # 1ms 定时精度 (与设备侧脚本一致)
            import ctypes
            ctypes.windll.winmm.timeBeginPeriod(1)
        except Exception:
            pass

        t0 = time.time()
        c4_ack_ms = 0.0
        sent = 0
        canceled = False
        try:
            mark = time.time()
            t.write_exact(frames[0].payload)        # C4 握手
            st = None
            deadline = time.time() + 3.0
            while time.time() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    return self._upload_fail('已取消', canceled=True, c4_ack_ms=c4_ack_ms)
                for k in range(len(acks) - 1, -1, -1):
                    at, c, s = acks[k]
                    if c == 0xC4 and at > mark:
                        mark = at
                        st = s
                        break
                if st is not None:
                    break
                time.sleep(0.002)
            ack_t = time.time()
            c4_ack_ms = (ack_t - t0) * 1000.0
            if st is None or st != 0:
                # 红线 (v3.47 审计): 无 ACK/异常 ACK 时盲发 2096 块 = 0x0C 级联教科书场景
                why = '无应答' if st is None else f'状态 0x{st:02X}'
                return self._upload_fail(f'C4 握手失败 ({why}) —— 设备忙或未就绪',
                                         c4_ack_ms=c4_ack_ms, sent=1)
            sent = 1

            base = su.ACK_WAIT_MS
            last_w = 0.0
            n_data = 0                        # 进度只数 A4 数据帧 (审计 G22)
            pauses = set(su.page_pause_after())   # 块 k 之后有 40ms 页停顿 (29 处)
            for n, fr in enumerate(frames[1:], 1):
                if cancel_event is not None and cancel_event.is_set():
                    canceled = True
                    break
                delta = ack_t + (fr.at_ms - base) / 1000.0 - time.time()
                if delta > 0:
                    time.sleep(delta)
                # 防突发 (v3.34): 主机调度卡顿后会追赶式连发, 设备每块消化下限 ~4ms,
                # 0 间隔突发会 0x0C 级联 (15:48 案例任意位置首拒) —— 两次写强制 ≥5ms
                since = time.time() - last_w
                if since < 0.005:
                    time.sleep(0.005 - since)
                if (n - 1) in pauses:
                    # 页首块门控 (2026-10-06 诊断包案): 级联失败的首拒块全部精确落在
                    # 页边界首块 (#1201×3 / #354×1, 与 v3.23 案签名相同 —— 当年判
                    # "非页边界"系 71 块网格误算): 设备页刷写偶发 >40ms 固定停顿,
                    # 盲发新页首块即 0x0C 级联到流尾。改为等页末块 C6 到达再发
                    # (正常早已到达, 零等待); 最多多等 250ms, 超时照发
                    # (设备深度忙, 由 service 层整流重传兜底)。
                    gate_until = time.time() + 0.25
                    while c6n[0] < n - 1 and time.time() < gate_until:
                        if cancel_event is not None and cancel_event.is_set():
                            break
                        time.sleep(0.002)
                t.write_exact(fr.payload)
                last_w = time.time()
                sent = n + 1
                if fr.payload[:1] == b'\xa4':    # A4 数据帧 (块+尾帧) 才计入进度
                    n_data += 1
                    if progress_cb is not None and (n_data % 16 == 0 or n_data == data_total):
                        try:
                            progress_cb(min(n_data, data_total), data_total)
                        except Exception:
                            pass
            time.sleep(0.5)                          # 收尾: 让最后一批 C6 回齐
        except Exception as e:
            import traceback
            traceback.print_exc()
            return self._upload_fail(f'发送异常: {e}', c4_ack_ms=c4_ack_ms,
                                     elapsed_ms=(time.time() - t0) * 1000.0,
                                     canceled=canceled, sent=sent)
        finally:
            stop.set()
            th.join(timeout=1)
            try:
                import ctypes
                ctypes.windll.winmm.timeEndPeriod(1)
            except Exception:
                pass

        c6 = [e for e in acks if e[1] == 0xC6]
        sts = [e[2] for e in c6]
        hist: dict = {}
        for s in sts:
            hist[s] = hist.get(s, 0) + 1
        bad = [k for k, s in enumerate(sts) if s != 0]
        first_bad = (bad[0], sts[bad[0]]) if bad else None
        if canceled:
            ok, reason = False, f'已取消 (已发 {sent}/{total_all})'
        elif not sts:
            ok, reason = False, '未收到任何 C6 应答 (设备无响应?)'
        elif bad:
            ok = False
            reason = f'完成但有 {len(bad)} 个非 00 状态 (首个 C6#{bad[0]}={sts[bad[0]]:#04x})'
        else:
            ok, reason = True, f'成功 (C6 {len(sts)}×00)'
        return {'ok': ok, 'reason': reason, 'c6_total': len(sts), 'c6_bad': len(bad),
                'first_bad': first_bad, 'status_hist': hist, 'c4_ack_ms': c4_ack_ms,
                'elapsed_ms': (time.time() - t0) * 1000.0, 'canceled': canceled,
                'sent': sent}

