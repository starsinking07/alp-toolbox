# -*- coding: utf-8 -*-
"""BRB02 蓝牙 BLE 传输层 (同步接口包装 bleak 异步)。

通道 (实测):
  写: 特征 0000AE41 (0xAE40 服务, write-without-response)
  收: 特征 0000AE04 (0xAE30 服务, notify)
  帧格式与 USB 完全一致: [A5][LEN][CMD][params][CK], 发送不补零
  设备状态上报 (2Hz) 也从 BLE 推送

限制: 设备仅在配对模式广播 (长按物理按键 3-5 秒), 扫描到后可连接。
"""
from __future__ import annotations

import asyncio
import queue
import threading
import time
from typing import Optional

from bleak import BleakClient, BleakScanner

DEVICE_NAME = 'BS BRB02 Cooler Pro'
UUID_WRITE = '0000ae41-0000-1000-8000-00805f9b34fb'
UUID_NOTIFY = '0000ae04-0000-1000-8000-00805f9b34fb'
UUID_AE10 = '0000ae10-0000-1000-8000-00805f9b34fb'


class TransportBLE:
    """与 TransportUSB 同接口: write / read / read_all / close / serial"""

    def __init__(self, address: Optional[str] = None, verbose: bool = False):
        self.address = address
        self.verbose = verbose
        self._rx: queue.Queue = queue.Queue()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._client: Optional[BleakClient] = None
        self._ready = threading.Event()
        self._closed = False
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._thread = threading.Thread(target=self._loop_main, daemon=True)
        self._thread.start()

    # ---- asyncio 循环线程 ----
    def _loop_main(self):
        self._loop.run_forever()

    def _sched(self, coro, timeout: float = 20.0):
        """在 loop 线程执行协程并同步等待结果"""
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout=timeout)

    # ---- 连接 ----
    def connect(self, timeout: float = 45.0) -> bool:
        t0 = time.time()
        ok = self._sched(self._connect_coro(timeout), timeout=timeout + 10)
        if ok:
            print(f'[ble] 已连接 {self.address}')
        else:
            print(f'[ble] 连接失败 ({time.time()-t0:.0f}s)')
        return ok

    async def _connect_coro(self, timeout: float) -> bool:
        deadline = time.time() + timeout
        address = self.address
        # 未指定地址则扫描等待广播
        while address is None and time.time() < deadline:
            devs = await BleakScanner.discover(timeout=2.0, return_adv=True)
            for addr, (d, adv) in devs.items():
                if (d.name or '') == DEVICE_NAME:
                    address = addr
                    self.address = addr
                    break
        if address is None:
            return False
        try:
            self._client = BleakClient(address, timeout=10)
            await self._client.connect()
        except Exception as e:
            print(f'[ble] connect 异常: {e}')
            return False
        if not self._client.is_connected:
            return False

        def _on_rx(ch, data: bytearray):
            if not self._closed:
                self._rx.put(bytes(data))
                if self.verbose:
                    print(f'[ble RX] {bytes(data).hex(" ")}')

        await self._client.start_notify(UUID_NOTIFY, _on_rx)
        self._ready.set()
        return True

    @property
    def is_connected(self) -> bool:
        try:
            return self._client is not None and self._client.is_connected
        except Exception:
            return False

    # ---- 收发 (与 TransportUSB 同接口) ----
    def write(self, data: bytes) -> int:
        """发送协议帧 (不补零, 帧长即 LEN)"""
        if not self.is_connected:
            raise ConnectionError('BLE 未连接')
        self._sched(self._client.write_gatt_char(UUID_WRITE, data, response=False), timeout=5)
        if self.verbose:
            print(f'[ble TX] {data.hex(" ")}')
        return len(data)

    def read(self, timeout_ms: int = 300) -> bytes:
        """阻塞读取一个通知帧 (设备上报/命令应答)"""
        try:
            return self._rx.get(timeout=timeout_ms / 1000)
        except queue.Empty:
            raise TimeoutError(f'BLE read timeout ({timeout_ms}ms)')

    def read_all(self, timeout_ms: int = 100, max_pkts: int = 16) -> list:
        """非阻塞读空接收队列"""
        out = []
        for _ in range(max_pkts):
            try:
                out.append(self._rx.get(timeout=timeout_ms / 1000))
            except queue.Empty:
                break
        return out

    @property
    def serial(self) -> str:
        return self.address or ''

    # ---- 关闭 ----
    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            if self._client and self._client.is_connected:
                self._sched(self._client.disconnect(), timeout=5)
        except Exception:
            pass
        self._ready.clear()


async def scan_for_cooler(timeout: float = 8.0) -> Optional[str]:
    """扫描广播中的散热器, 返回地址或 None"""
    devs = await BleakScanner.discover(timeout=timeout, return_adv=True)
    for addr, (d, adv) in devs.items():
        if (d.name or '') == DEVICE_NAME:
            return addr
    return None


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from brb02 import protocol

    t = TransportBLE(verbose=True)
    if not t.connect(timeout=20):
        print('连接失败: 设备未广播? 请长按散热器按键 3-5 秒进入配对。')
        sys.exit(1)
    print('serial =', t.serial)

    # 读版本
    f = protocol.get_firmware_version()
    t.write(f)
    for _ in range(3):
        try:
            rx = t.read(500)
            p = protocol.parse_frame(rx)
            if p and p[0] == 0x01:
                print('固件版本:', protocol.parse_firmware_version(p[1]))
                break
        except TimeoutError:
            break

    # 状态上报
    st = None
    for _ in range(4):
        try:
            rx = t.read(600)
            p = protocol.parse_frame(rx)
            if p and p[0] == protocol.Cmd.SYSTEM_INFO_REPORT:
                st = protocol.parse_status_report(p[1])
                print('状态:', st)
        except TimeoutError:
            pass

    # 转速控制走 BLE!
    print('\nBLE 设定 1600 RPM...')
    t.write(protocol.set_cooling_fixed(1600))
    time.sleep(2.5)
    t.write(protocol.set_cooling_fixed(1200))
    for _ in range(6):
        try:
            rx = t.read(600)
            p = protocol.parse_frame(rx)
            if p and p[0] == protocol.Cmd.SYSTEM_INFO_REPORT:
                print('状态:', protocol.parse_status_report(p[1]))
        except TimeoutError:
            pass
    t.close()
    print('BLE 测试完成')
