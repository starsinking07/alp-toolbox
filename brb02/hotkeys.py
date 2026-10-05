# -*- coding: utf-8 -*-
"""全局快捷键 (功能 #6): RegisterHotKey + 消息循环线程。

默认组合:
  Ctrl+Alt+F1  循环切换挡位
  Ctrl+Alt+F2  开关智能变频
"""
from __future__ import annotations

import ctypes
import threading

from PySide6.QtCore import Signal, QObject

from brb02.logbuf import LOGBUF

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

MOD_ALT, MOD_CONTROL = 0x0001, 0x0002
WM_HOTKEY = 0x0312

HK_CYCLE_GEAR = 1
HK_TOGGLE_SMART = 2

BINDINGS = [
    (HK_CYCLE_GEAR, MOD_CONTROL | MOD_ALT, 0x70, 'Ctrl+Alt+F1', '循环切换挡位'),
    (HK_TOGGLE_SMART, MOD_CONTROL | MOD_ALT, 0x71, 'Ctrl+Alt+F2', '开关智能变频'),
]


class HotkeyManager(QObject):
    triggered = Signal(int)           # hotkey id

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread: threading.Thread | None = None
        self._thread_id = None
        self._stop_evt = threading.Event()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_evt.set()
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)   # WM_QUIT

    def join(self, timeout: float | None = None):
        """等待消息循环线程退出 (先 stop 再调用; 防旧线程残留占用热键注册)。"""
        t = self._thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(timeout)

    def _run(self):
        self._thread_id = kernel32.GetCurrentThreadId()
        registered = []
        for hk_id, mod, vk, keys, _desc in BINDINGS:
            if user32.RegisterHotKey(None, hk_id, mod, vk):
                registered.append(hk_id)
            else:
                # --noconsole 无控制台, 注册失败必须进 LOGBUF 才可见
                LOGBUF.write(f'[快捷键] 注册失败: {keys} 可能被其他程序占用, 该组合键不可用')
        msg = ctypes.wintypes.MSG()
        while not self._stop_evt.is_set():
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r <= 0:
                break
            if msg.message == WM_HOTKEY:
                self.triggered.emit(int(msg.wParam))
        for hk_id in registered:
            user32.UnregisterHotKey(None, hk_id)
