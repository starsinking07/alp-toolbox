# -*- coding: utf-8 -*-
"""运行日志缓冲: 捕获 stderr (异常 traceback) + 显式写入, 供关于页展示/复制。"""
from __future__ import annotations

import collections
import io
import sys
import threading
import time
from datetime import datetime


class LogBuffer:
    def __init__(self, maxlen: int = 800):
        self._buf: collections.deque = collections.deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._version = 0
        self._original_stderr = None

    def write(self, text: str):
        """写入一行或多行 (自动加时间戳于首行)"""
        with self._lock:
            now = datetime.now().strftime('%H:%M:%S')
            lines = text.rstrip('\n').split('\n')
            for i, line in enumerate(lines):
                prefix = f'[{now}] ' if i == 0 else '        '
                self._buf.append(prefix + line)
            self._version += 1

    def text(self) -> str:
        with self._lock:
            return '\n'.join(self._buf)

    def version(self) -> int:
        with self._lock:
            return self._version

    def clear(self):
        with self._lock:
            self._buf.clear()
            self._version += 1

    def install_stderr_tee(self):
        """stderr 分流: 异常 traceback 同时进日志缓冲"""
        if self._original_stderr is not None:
            return
        self._original_stderr = sys.stderr
        buf = self

        class _Tee(io.TextIOBase):
            def write(self, s):
                try:
                    if s.strip():
                        buf.write(s)
                except Exception:
                    pass
                if buf._original_stderr is None:      # --noconsole 打包下 stderr 为 None
                    return len(s)
                return buf._original_stderr.write(s)

            def flush(self):
                if buf._original_stderr is not None:
                    try:
                        buf._original_stderr.flush()
                    except Exception:
                        pass

            @property
            def encoding(self):
                return getattr(buf._original_stderr, 'encoding', 'utf-8')

        sys.stderr = _Tee()

    def uninstall(self):
        if self._original_stderr is not None:
            sys.stderr = self._original_stderr
            self._original_stderr = None


LOGBUF = LogBuffer()


def log(msg: str):
    """显式日志入口 (各模块调用)"""
    LOGBUF.write(str(msg))
