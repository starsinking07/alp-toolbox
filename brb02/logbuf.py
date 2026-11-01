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
        self._installed = False          # tee 是否已包裹 stderr (幂等防重复包裹)
        self._file_path: str | None = None   # 落盘路径 (诊断用; 内存缓冲仍是权威来源)
        self._lock_fh = None                 # 主实例持有的 app.log 独占锁句柄

    # ---- 落盘 (0.2.1): 打包后无控制台, 内存日志一关就没了, 排障只能靠导出诊断包。
    # 这里额外把每行追加到 %APPDATA%\Brb02Toolbox\app.log, 出问题可直接看文件。 ----
    def enable_file(self, path: str | None = None) -> str | None:
        """追加写入 + 单文件超 1MB 时轮转为 app.log.1。

        ⚠️ 早期版本用 'w' 截断 —— 用户重启一次就把上一次的现场全抹掉了
        (排障时正好需要"上一次为什么坏")。改成追加, 只做大小轮转。

        ⚠️ 两个实例同时写同一个 app.log 会把日志交错成天书 —— 实测就遇到过
        (排查时看到"非管理员实例"的心跳里居然有 33 万行帧数据, 其实是另一个
        管理员实例写的)。这里用独占锁保证**只有主实例写 app.log**,
        后来者自动落到 `app.<pid>.log`, 谁的现场都不会被搅乱。
        """
        import os
        try:
            if path is None:
                base = os.environ.get('APPDATA', os.path.expanduser('~'))
                d = os.path.join(base, 'Brb02Toolbox')
                os.makedirs(d, exist_ok=True)
                path = os.path.join(d, 'app.log')
            own = False
            try:
                import msvcrt
                # 用**独立的 .lock 文件**加锁, 不要锁 app.log 本身:
                # 在自己持锁的句柄上再 open(app.log, 'a') 会被拒绝 (Permission denied),
                # 实测踩过。锁文件只用于"谁是主实例"的仲裁, 不参与写日志。
                self._lock_fh = open(path + '.lock', 'a+', encoding='utf-8')
                try:
                    msvcrt.locking(self._lock_fh.fileno(), msvcrt.LK_NBLCK, 1)
                    own = True
                except OSError:
                    self._lock_fh.close()
                    self._lock_fh = None
            except Exception:
                own = True
            if not own:
                root, ext = os.path.splitext(path)
                path = f'{root}.{os.getpid()}{ext}'
            try:
                if os.path.getsize(path) > 1024 * 1024:
                    os.replace(path, path + '.1')
            except OSError:
                pass
            self._file_path = path
            with open(path, 'a', encoding='utf-8') as f:
                f.write(f'\n==== app.log start {datetime.now().isoformat(timespec="seconds")}'
                        f' pid={os.getpid()} ====\n')
            return path
        except Exception:
            self._file_path = None
            return None

    def _append_file(self, line: str) -> None:
        p = self._file_path
        if not p:
            return
        try:
            with open(p, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except Exception:
            pass

    def write(self, text: str):
        """写入一行或多行 (自动加时间戳于首行)"""
        with self._lock:
            now = datetime.now().strftime('%H:%M:%S')
            lines = text.rstrip('\n').split('\n')
            for i, line in enumerate(lines):
                prefix = f'[{now}] ' if i == 0 else '        '
                entry = prefix + line
                self._buf.append(entry)
                self._append_file(entry)
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
        """stderr 分流: 异常 traceback 同时进日志缓冲 (幂等: 重复调用不会二次包裹)"""
        if self._installed:
            return
        self._installed = True
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
        if self._installed:
            sys.stderr = self._original_stderr
            self._original_stderr = None
            self._installed = False


LOGBUF = LogBuffer()


def log(msg: str):
    """显式日志入口 (各模块调用)"""
    LOGBUF.write(str(msg))
