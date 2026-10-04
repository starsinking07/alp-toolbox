# -*- coding: utf-8 -*-
"""BRB02 风神Pro 散热器 USB 传输层 (libusb 中断传输, 65 字节包)。

官方软件走 libusb(WinUSB 驱动), 非 hidapi 路径。
VID 0xE2B7 PID 0x7001, EP OUT=0x01 / IN=0x81, 包长固定 65。
"""
from __future__ import annotations

import time
import threading

import usb.core
import usb.util
import usb.backend.libusb1

VID = 0xE2B7          # BRB02 风神Pro (杰理芯片, Brb02Service 调用参数确认)
PID = 0x7001
PIDS = (PID,)         # 0x369B 是黑鲨其他外设 (鼠标等), 非散热器
EP_OUT = 0x01
EP_IN = 0x81
PKT_SIZE = 65          # report ID(1) + payload(64)
TIMEOUT_MS = 400

# libusb_package 缺失时的回退候选: PATH 上的同名库。
# 如需指定 libusb-1.0.dll 的位置, 在此追加绝对路径即可。
_LIBUSB_CANDIDATES = (
    'libusb-1.0.dll',
)


def _backend():
    # 1) libusb-package 自带的 64 位 libusb (pip install libusb-package)
    try:
        import libusb_package
        be = usb.backend.libusb1.get_backend(find_library=libusb_package.find_library)
        if be is not None:
            return be
    except ImportError:
        pass
    # 2) 官方目录自带的 (32 位, 仅 32 位 Python 可用)
    for cand in _LIBUSB_CANDIDATES:
        try:
            be = usb.backend.libusb1.get_backend(find_library=lambda p, c=cand: c)
            if be is not None:
                return be
        except Exception:
            continue
    return None


def find_device(**kwargs):
    """枚举散热器, 返回 pyusb Device 或 None"""
    be = _backend()
    dev = usb.core.find(idVendor=VID, idProduct=PID, backend=be, **kwargs)
    return dev


class TransportUSB:
    """65 字节中断传输。write() 补零至 65 字节; read() 返回实际收到的原始包。"""

    def __init__(self, dev=None, verbose=False):
        self.dev = dev or find_device()
        if self.dev is None:
            raise ConnectionError('未找到 BRB02 散热器 (VID 0xE2B7)。请用 USB 线连接后重试。')
        self.verbose = verbose
        self._lock = threading.Lock()
        try:
            if self.dev.is_kernel_driver_active(0):
                try:
                    self.dev.detach_kernel_driver(0)
                except Exception:
                    pass
        except NotImplementedError:
            pass  # Windows 上无此概念
        try:
            self.dev.set_configuration()
        except Exception:
            pass  # 已配置

    @property
    def serial(self) -> str:
        try:
            return usb.util.get_string(self.dev, self.dev.iSerialNumber) or ''
        except Exception:
            return ''

    @property
    def is_connected(self) -> bool:
        """存活判定 = 设备仍在总线上 (拔线/休眠后为 False, 供上层重连判定)。"""
        try:
            return find_device() is not None
        except Exception:
            return False

    def write(self, data: bytes) -> int:
        """发送任意长度 payload, 自动补零到 65 字节"""
        pkt = data.ljust(PKT_SIZE, b'\x00')[:PKT_SIZE]
        with self._lock:
            n = self.dev.write(EP_OUT, pkt, TIMEOUT_MS)
        if self.verbose:
            print(f'TX {len(data):2d}: {data.hex(" ")}')
        return n

    def read(self, timeout_ms=TIMEOUT_MS, size=PKT_SIZE):
        """读一个 65 字节中断包; 超时抛 usb.USBError"""
        with self._lock:
            data = self.dev.read(EP_IN, size, timeout_ms)
        raw = bytes(data)
        if self.verbose:
            print(f'RX {len(raw):2d}: {raw.hex(" ")}')
        return raw

    def read_all(self, timeout_ms=60, max_pkts=16):
        """非阻塞式读空 IN 端点"""
        out = []
        for _ in range(max_pkts):
            try:
                out.append(self.read(timeout_ms))
            except usb.USBError:
                break
        return out

    # ---- 上传等时序敏感路径专用: 原样收发, 不补零、不加锁 ----
    # (上传需 ~7ms/帧的精确节拍; 走 _lock 会被对端 read 的超时窗口卡住。调用方须保证
    #  设备独占 —— service 上传期间已暂停轮询/下发。)
    def write_exact(self, data: bytes) -> int:
        """原样发送 len(data) 字节 (不补零到 65, 不加锁)。"""
        return self.dev.write(EP_OUT, bytes(data), TIMEOUT_MS)

    def read_exact(self, timeout_ms: int = 20, size: int = 64) -> bytes:
        """读一个原始中断包 (不加锁)。超时抛 usb.USBError。"""
        return bytes(self.dev.read(EP_IN, size, timeout_ms))

    def request(self, data: bytes, wait_ms=200):
        """发送并收集应答 (简化交互: 写后读空)"""
        self.write(data)
        time.sleep(wait_ms / 1000)
        return self.read_all()

    def close(self):
        try:
            usb.util.dispose_resources(self.dev)
        except Exception:
            pass


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    d = find_device()
    if d is None:
        print('未找到设备 (VID_E2B7)。请连接散热器 USB。')
    else:
        print(f'找到设备: PID=0x{d.idProduct:04X}')
        print('  manufacturer:', usb.util.get_string(d, d.iManufacturer))
        print('  product     :', usb.util.get_string(d, d.iProduct))
        print('  serial      :', usb.util.get_string(d, d.iSerialNumber))
        for cfg in d:
            print(f'  config {cfg.bConfigurationValue}')
            for itf in cfg:
                print(f'    interface {itf.bInterfaceNumber} alt {itf.bAlternateSetting} class 0x{itf.bInterfaceClass:02X}')
                for ep in itf:
                    print(f'      ep 0x{ep.bEndpointAddress:02X} attr 0x{ep.bmAttributes:02X} maxpkt {ep.wMaxPacketSize}')
