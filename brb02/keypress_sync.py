# -*- coding: utf-8 -*-
"""按键事件源 (0.1.8): 全局鼠标/键盘按下 → 回调 (节流 50ms)。

协议: 0x16 = `A5 04 16 CK` 空载荷, 事件驱动 (配合"响应"灯效, 槽位 6)。
依赖 pynput; 不可用时 available=False。
"""
import threading, time


class KeyPressPusher:
    """全局按键监听 → 节流回调。回调在 pynput 线程执行, 须线程安全。"""

    THROTTLE = 0.05

    def __init__(self, press_cb, log=None):
        self.press_cb = press_cb
        self.log = log or (lambda s: None)
        self.available = True
        self._stop = threading.Event()
        self._listeners = []
        self._last = 0.0
        self._lock = threading.Lock()

    def start(self):
        if self._listeners:
            return
        try:
            from pynput import mouse, keyboard
        except Exception as e:
            self.available = False
            self.log(f'[按键] pynput 不可用: {e}')
            return
        try:
            self._listeners = [
                mouse.Listener(on_click=self._on_any),
                keyboard.Listener(on_press=self._on_any),
            ]
            for l in self._listeners:
                l.daemon = True
                l.start()
        except Exception as e:
            self.available = False
            self.log(f'[按键] 监听启动失败: {e}')

    def stop(self):
        self._stop.set()
        for l in self._listeners:
            try:
                l.stop()
            except Exception:
                pass
        self._listeners = []

    def _on_any(self, *args, **kwargs):
        now = time.time()
        with self._lock:
            if now - self._last < self.THROTTLE:
                return
            self._last = now
        try:
            self.press_cb()
        except Exception as e:
            self.log(f'[按键] 回调异常: {e}')
