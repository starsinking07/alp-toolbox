# -*- coding: utf-8 -*-
"""BRB02 设备服务层 (基于实机验证协议)。"""
from __future__ import annotations

import threading
import time
from typing import Optional

from . import protocol
from .transport_usb import TransportUSB, find_device


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
    def connect(self) -> bool:
        if self._t is not None:
            # 已连接: 检查底层是否仍存活
            try:
                if self.conn_type == 'usb':
                    return find_device() is not None
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
            ok = self._t.connect()
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
        return self._t is not None

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
            except Exception:
                break
            p = protocol.parse_frame(bytes(rx))
            if p and p[0] == protocol.Cmd.SYSTEM_INFO_REPORT:
                d = protocol.parse_status_report(p[1])
                if d:
                    last = d
        return last

    def get_firmware_version(self) -> str:
        rxs = self._send(protocol.get_firmware_version())
        for cmd, data in rxs:
            if cmd == 0x01:
                return protocol.parse_firmware_version(data)
        return '?'

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
    def set_fixed_rpm(self, rpm: int):
        """设置固定转速 (0..4000)。rpm>2800 时自动使用制冷档位 4 (高档曲线才支持 4000)。"""
        rpm = max(0, min(self.MAX_RPM, int(rpm)))
        level = 4 if rpm > 2800 else 1
        self._send(protocol.set_cooling_fixed(rpm, level=level), wait_s=0.3)

    def set_curve(self, anchors):
        """写入智能变频曲线 (4 锚点)。⚠️ 只允许写入来自 get_curve 的合法值。
        峰值转速 >2800 时自动使用制冷档位 4 (高档曲线才支持 4000)。"""
        level = 4 if max(r for _t, r in anchors) > 2800 else 1
        self._send(protocol.set_cooling_curve(anchors, level=level), wait_s=0.3)
