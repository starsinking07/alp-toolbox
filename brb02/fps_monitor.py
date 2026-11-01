# -*- coding: utf-8 -*-
"""实时 FPS 采集器 —— 基于 Intel PresentMon (ETW 事件流, 零进程注入)。

设计要点 (对齐《需求文档-性能浮窗.md》§3.2 / §3.4 / §6):
  1. 100% 后台 daemon 线程管理子进程与 CSV 流解析, GUI 主线程只做无锁/微秒级内存读取;
  2. 兼容 PresentMon v1 (`msBetweenPresents` / `MsBetweenPresents`) 与 v2 (`FrameTime`) CSV 列;
  3. 帧率换算: 按 1.0 秒滑动窗口聚合 `fps = 1000.0 / mean(msBetweenPresents)`;
     桌面无 Present 事件或超过 1.5s 无新帧时返回 None (UI 显示 `--`);
  4. 焦点智能选择: 优先取当前前台窗口 PID 的帧率; 若前台是本工具自身或无呈现事件,
     则取窗口期内最活跃的游戏/3D 进程 (自动排除 dwm/explorer 等系统壳进程);
  5. 进程零残留保证:
     - 绑定 Windows Job Object (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000`),
       即使主进程被强制结束, 内核也会立即终止 PresentMon 子进程;
     - `stop()` 主动终止子进程并清理同名 ETW 会话 (`--terminate_existing_session`);
     - 注册 `atexit` 退出钩子兜底。
"""
from __future__ import annotations

import atexit
import collections
import itertools
import ctypes
from ctypes import wintypes
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Optional

SESSION_NAME = 'AlpToolbox_PresentMon'
_SESS_SEQ = itertools.count(1)      # 会话名自增序号 (见 _unique_session)


CREATE_NO_WINDOW = 0x08000000


def etw_session_names() -> Optional[list]:
    """列出当前所有 ETW 会话名; logman 不可用返回 None。"""
    try:
        q = subprocess.run(['logman', 'query', '-ets'], capture_output=True,
                           timeout=10, creationflags=CREATE_NO_WINDOW)
    except Exception:
        return None
    if q.returncode != 0:
        return None
    out = (q.stdout or b'').decode('mbcs', 'replace')
    names = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and not parts[0].startswith('-') and parts[0] != '数据收集器集':
            names.append(parts[0])
    return names


def _heavy_sessions() -> list:
    """返回**第三方**重量级 ETW 跟踪会话名 (可能挤占 PresentMon 的缓冲)。

    2026-10-28 修正: 起初把 `WPR` 当成元凶, 拿到完整 `logman query -ets` 后发现
    名单里只有 `WPR_initiated_DiagTrackMiniLogger_*` —— 那是 Windows 遥测的常规会话,
    `wpr -cancel` 停不掉, 判断是错的。真正的第三方跟踪是:
      NVIDIA-NVTOPPS-NOCAT / -FILTER / -NOCAT-SDK  (N 卡驱动跟踪)
      seraph-etw-monitor                            (来源不明的监控)
    Windows 内建 (NT/Circular/EventLog/WiFi/Defender 等) 一律不算。
    """
    names = etw_session_names() or []
    third_party = ('nvidia', 'seraph', 'rtss', 'afterburner', 'msi ', 'hwinfo',
                   'hwmonitor', 'capframex', 'processmonitor', 'procmon', 'wpr ')
    out = []
    for n in names:
        ln = n.lower()
        if ln.startswith('wpr_initiated'):      # Windows 遥测, 不算
            continue
        if any(ln.startswith(t) or t in ln for t in third_party):
            out.append(n)
    return out


def stop_interfering_sessions() -> list[str]:
    """尝试停止挤占 ETW 缓冲的第三方重量级跟踪会话 (需要管理员权限)。返回成功停止的会话名列表。"""
    stopped = []
    for name in _heavy_sessions():
        try:
            r = subprocess.run(['logman', 'stop', name, '-ets'], capture_output=True,
                               timeout=10, creationflags=CREATE_NO_WINDOW)
            if r.returncode == 0:
                stopped.append(name)
        except Exception:
            pass
    return stopped


def sweep_stale_sessions(prefix: str = SESSION_NAME) -> int:
    """停掉本工具遗留的 ETW 会话, 返回停掉的个数; logman 不可用返回 -1。

    2026-10-27 真机定位 (用户诊断包): 日志里每一块都是 `0 帧 + warning: N ETW events
    were lost` (N≈1400~2600), 从头丢到尾, CSV 一行都出不来。
    根因是**遗留会话堆积**: 会话名带 pid+时间戳后缀是为了躲 4201 (见 _unique_session),
    代价就是旧会话永远不会被同名覆盖 —— 进程被强杀/异常退出时, PresentMon 不会清理
    自己建的会话, 它们会持续占用 ETW 缓冲, 把新会话的事件挤掉。
    所以启动前必须按前缀扫一遍。会话名随后改回固定名 (扫描成功后不存在冲突)。
    """
    try:
        # ⚠️ logman 的输出走控制台代码页 (简体中文 = GBK), 不能按 UTF-8 解 ——
        # 实测 text=True 会在 reader 线程里抛 UnicodeDecodeError, 结果静默返回空。
        q = subprocess.run(['logman', 'query', '-ets'], capture_output=True,
                           timeout=10, creationflags=CREATE_NO_WINDOW)
    except Exception:
        return -1
    if q.returncode != 0:
        return -1
    out = (q.stdout or b'').decode('mbcs', 'replace')
    stopped = 0
    for line in out.splitlines():
        parts = line.split()
        if not parts or not parts[0].startswith(prefix):
            continue
        try:
            subprocess.run(['logman', 'stop', parts[0], '-ets'], capture_output=True,
                           timeout=10, creationflags=CREATE_NO_WINDOW)
            stopped += 1
        except Exception:
            pass
    return stopped


def _unique_session(tag: str = '') -> str:
    """每次启动用**唯一** ETW 会话名。

    为什么不能用固定名: PresentMon 的 `--stop_existing_session` 会先把同名旧会话停掉,
    紧接着再以**同一个名字**新建 —— ETW 子系统还没释放该名字, 于是 `StartTrace` 返回
    **error code 4201 (WMI 实例名无效/已占用)**, 采集直接失败。
    这个坑在「上次异常退出留下僵尸会话」时 100% 复现。
    改用 pid+时间戳后缀后, 永不冲突; 退出时按自己的名字精确清理。
    """
    # ⚠️ 必须用自增序号, 不能用毫秒时间戳: 同一毫秒内连续构造两次命令会生成同一个
    # 会话名 (实测复现), 而重试恰恰发生在毫秒级间隔内 ⇒ 又撞回 "already running"。
    suffix = f'{os.getpid()}_{next(_SESS_SEQ)}'
    return f'{SESSION_NAME}_{tag}{suffix}' if tag else f'{SESSION_NAME}_{suffix}'

STATUS_TEXT = {
    'stopped': '未启动',
    'starting': '启动中…',
    'running': '运行中',
    'no_admin': '权限不足 (需以管理员身份运行)',
    'no_binary': '未找到 PresentMon.exe',
    'error': '采集异常',
}

# 排除桌面壳与系统后台进程, 避免桌面无游戏时把 DWM 合成或开始菜单动画当成游戏 FPS
EXCLUDED_PROCS = (
    'dwm.exe',
    'explorer.exe',
    'shellexperiencehost.exe',
    'searchhost.exe',
    'startmenuexperiencehost.exe',
    'textinputhost.exe',
    'applicationframehost.exe',
    'systemsettings.exe',
    'lockapp.exe',
    'taskmgr.exe',
    'alptoolbox.exe',
    'alp工具箱.exe',
)


def find_presentmon_binary() -> Optional[str]:
    """按优先级查找 PresentMon 可执行文件:
    1. 环境变量 ALP_PRESENTMON_PATH
    2. PyInstaller _MEIPASS/assets/PresentMon.exe
    3. 项目目录 assets/PresentMon.exe
    4. 系统常见安装位置 (AMD CNext / NVIDIA FrameViewSDK / PATH)
    """
    env_p = os.environ.get('ALP_PRESENTMON_PATH', '').strip()
    if env_p and os.path.isfile(env_p):
        return env_p

    cands = []
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        cands.append(os.path.join(meipass, 'assets', 'PresentMon.exe'))
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cands.extend([
        os.path.join(root, 'assets', 'PresentMon.exe'),
        os.path.join(root, 'PresentMon.exe'),
        r'C:\Program Files\AMD\CNext\CNext\PresentMon-x64.exe',
        r'C:\Program Files\NVIDIA Corporation\FrameViewSDK\bin\PresentMon_x64.exe',
    ])
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


_BAK_ERR = ''          # _enable_backup_privilege 的失败原因 (进诊断包)


def _enable_backup_privilege() -> bool:
    """启用 SeBackupPrivilege —— 有了它就能读「被别的进程独占打开」的文件。

    真机实测: PresentMon 用 fopen 打开 CSV 时**不带 FILE_SHARE_READ**,
    于是普通 open() 直接 ERROR_SHARING_VIOLATION (32)。
    管理员令牌默认持有但**未启用**该特权, 这里显式 AdjustTokenPrivileges 打开它,
    配合 CreateFileW 的 FILE_FLAG_BACKUP_SEMANTICS 即可绕过共享模式读取。
    """
    if sys.platform != 'win32':
        return False
    global _BAK_ERR
    _BAK_ERR = ''
    try:
        k32 = ctypes.windll.kernel32
        adv = ctypes.windll.advapi32

        # ⚠️ 2026-10-28 真机根因: 这里原先没声明 argtypes/restype ——
        # `GetCurrentProcess()` 返回的是伪句柄 **-1**, 未声明原型时 ctypes 按 32 位
        # c_int 传参, 64 位 API 收到的是被截断的 0x00000000FFFFFFFF (而非
        # 0xFFFFFFFFFFFFFFFF), 于是 OpenProcessToken 直接失败并返回
        # **err=6 (ERROR_INVALID_HANDLE)**。
        # 后果: 备份特权永远拿不到 ⇒ 文件增量读档被**静默跳过** ⇒ 永远退化到
        # 分块模式 (每 2s 新建一个 ETW 会话, 每次开局丢一批事件)。
        # 这与 SetWindowPos 的 HWND_TOPMOST 是同一类错误 —— 64 位 Win32 必须显式原型。
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                         ctypes.POINTER(wintypes.HANDLE)]
        adv.OpenProcessToken.restype = wintypes.BOOL
        adv.LookupPrivilegeValueW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR,
                                              ctypes.c_void_p]
        adv.LookupPrivilegeValueW.restype = wintypes.BOOL
        adv.AdjustTokenPrivileges.argtypes = [wintypes.HANDLE, wintypes.BOOL,
                                              ctypes.c_void_p, wintypes.DWORD,
                                              ctypes.c_void_p, ctypes.c_void_p]
        adv.AdjustTokenPrivileges.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]

        class LUID(ctypes.Structure):
            _fields_ = [('LowPart', wintypes.DWORD), ('HighPart', wintypes.LONG)]

        class LUID_AND_ATTRIBUTES(ctypes.Structure):
            _fields_ = [('Luid', LUID), ('Attributes', wintypes.DWORD)]

        class TOKEN_PRIVILEGES(ctypes.Structure):
            _fields_ = [('PrivilegeCount', wintypes.DWORD),
                        ('Privileges', LUID_AND_ATTRIBUTES * 1)]

        TOKEN_ADJUST_PRIVILEGES, TOKEN_QUERY = 0x0020, 0x0008
        tok = wintypes.HANDLE()
        if not adv.OpenProcessToken(k32.GetCurrentProcess(),
                                    TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
                                    ctypes.byref(tok)):
            _BAK_ERR = f'OpenProcessToken 失败 (err={k32.GetLastError()})'
            return False
        try:
            luid = LUID()
            if not adv.LookupPrivilegeValueW(None, 'SeBackupPrivilege', ctypes.byref(luid)):
                _BAK_ERR = f'LookupPrivilegeValue 失败 (err={k32.GetLastError()})'
                return False
            tp = TOKEN_PRIVILEGES()
            tp.PrivilegeCount = 1
            tp.Privileges[0].Luid = luid
            tp.Privileges[0].Attributes = 0x00000002      # SE_PRIVILEGE_ENABLED
            # ⚠️ AdjustTokenPrivileges 即使"部分成功"也返回 TRUE, 真正的判据是
            # GetLastError(): ERROR_SUCCESS(0)=全部成功; ERROR_NOT_ALL_ASSIGNED(1300)=没这个特权。
            # GetLastError 会被上一条 Win32 调用污染, 必须先清零再调用, 否则会误判成失败
            # (真机踩过: 管理员环境也报 backup_privilege=false, 白白退化成分块模式)。
            ctypes.windll.kernel32.SetLastError(0)
            ok = adv.AdjustTokenPrivileges(tok, False, ctypes.byref(tp), 0, None, None)
            err = ctypes.windll.kernel32.GetLastError()
            if not ok or err != 0:
                _BAK_ERR = f'AdjustTokenPrivileges ok={bool(ok)} err={err}'
            return bool(ok) and err == 0
        finally:
            k32.CloseHandle(tok)
    except Exception as e:
        _BAK_ERR = f'异常: {e!r}'
        return False


def _read_locked(path: str, offset: int, size: int) -> bytes:
    """从可能被独占占用的文件里读一段 (优先普通读, 失败则用备份语义打开)。"""
    try:
        with open(path, 'rb') as f:
            f.seek(offset)
            return f.read(size)
    except OSError:
        pass
    if sys.platform != 'win32':
        return b''
    try:
        k32 = ctypes.windll.kernel32
        GENERIC_READ = 0x80000000
        FILE_SHARE_ALL = 0x1 | 0x2 | 0x4
        OPEN_EXISTING = 3
        FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
        INVALID = ctypes.c_void_p(-1).value
        # ⚠️ 同样必须显式原型: 不声明 argtypes 时 Python str 会按 ANSI 传给
        # CreateFileW (它要 UTF-16), 且返回值按 32 位截断 ⇒ 路径含中文或句柄高位非零
        # 时静默失败, 文件档永远读到空 (于是又退化到分块模式)。
        k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                    ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                    wintypes.HANDLE]
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.SetFilePointerEx.argtypes = [wintypes.HANDLE, wintypes.LARGE_INTEGER,
                                         ctypes.c_void_p, wintypes.DWORD]
        k32.SetFilePointerEx.restype = wintypes.BOOL
        k32.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                 ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        k32.ReadFile.restype = wintypes.BOOL
        h = k32.CreateFileW(path, GENERIC_READ, FILE_SHARE_ALL, None,
                            OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, None)
        if not h or int(h) == INVALID:
            return b''
        try:
            k32.SetFilePointerEx(wintypes.HANDLE(h), wintypes.LARGE_INTEGER(offset),
                                 None, 0)
            buf = ctypes.create_string_buffer(size)
            got = wintypes.DWORD(0)
            if not k32.ReadFile(wintypes.HANDLE(h), buf, size, ctypes.byref(got), None):
                return b''
            return buf.raw[:got.value]
        finally:
            k32.CloseHandle(wintypes.HANDLE(h))
    except Exception:
        return b''


def _safe_size(path: str) -> int:
    try:
        return os.path.getsize(path) if path else 0
    except OSError:
        return 0


def _live_csv_path() -> str:
    """PresentMon 实时 CSV 的落盘位置 (%APPDATA%\\Brb02Toolbox\\pm_live.csv)。"""
    base = os.environ.get('APPDATA', os.path.expanduser('~'))
    d = os.path.join(base, 'Brb02Toolbox')
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        d = tempfile.gettempdir()
    return os.path.join(d, 'pm_live.csv')


def _foreground_pid() -> int:
    """获取当前前台窗口所属进程 PID (失败返回 0)。"""
    if sys.platform != 'win32':
        return 0
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return 0
        pid = wintypes.DWORD(0)
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value or 0)
    except Exception:
        return 0


def _bind_kill_on_close_job(proc_handle: int) -> Optional[int]:
    """将子进程绑定到带 JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE 的 Windows Job 对象。
    返回 Job 句柄 (需保持存活直到父进程退出)。"""
    if sys.platform != 'win32' or not proc_handle:
        return None
    try:
        k32 = ctypes.windll.kernel32

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ('ReadOperationCount', ctypes.c_ulonglong),
                ('WriteOperationCount', ctypes.c_ulonglong),
                ('OtherOperationCount', ctypes.c_ulonglong),
                ('ReadTransferCount', ctypes.c_ulonglong),
                ('WriteTransferCount', ctypes.c_ulonglong),
                ('OtherTransferCount', ctypes.c_ulonglong),
            ]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ('PerProcessUserTimeLimit', ctypes.c_longlong),
                ('PerJobUserTimeLimit', ctypes.c_longlong),
                ('LimitFlags', wintypes.DWORD),
                ('MinimumWorkingSetSize', ctypes.c_size_t),
                ('MaximumWorkingSetSize', ctypes.c_size_t),
                ('ActiveProcessLimit', wintypes.DWORD),
                ('Affinity', ctypes.c_size_t),
                ('PriorityClass', wintypes.DWORD),
                ('SchedulingClass', wintypes.DWORD),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ('BasicLimitInformation', JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ('IoInfo', IO_COUNTERS),
                ('ProcessMemoryLimit', ctypes.c_size_t),
                ('JobMemoryLimit', ctypes.c_size_t),
                ('PeakProcessMemoryUsed', ctypes.c_size_t),
                ('PeakJobMemoryUsed', ctypes.c_size_t),
            ]

        k32.CreateJobObjectW.restype = wintypes.HANDLE
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
        info.BasicLimitInformation.LimitFlags = 0x00002000
        # JobObjectExtendedLimitInformation = 9
        ok = k32.SetInformationJobObject(
            job, 9, ctypes.byref(info), ctypes.sizeof(info)
        )
        if not ok:
            k32.CloseHandle(job)
            return None
        k32.AssignProcessToJobObject(job, wintypes.HANDLE(proc_handle))
        return int(job)
    except Exception:
        return None


# PresentMon 进程创建标志 (2026-10-28):
# CREATE_NO_WINDOW 不弹窗; **HIGH_PRIORITY_CLASS** 让它的实时 ETW 消费线程优先拿到 CPU ——
# 真机上 PresentMon 6s 丢 12078 条事件, 典型就是消费线程被抢占、缓冲来不及排空。
PM_CREATIONFLAGS = 0x08000000 | 0x00000080

# CSV 尾部轮询间隔 (2026-10-27: 250ms -> 60ms, 见 _tail_csv 内注释)
TAIL_POLL_S = 0.06

class FpsMonitor:
    """PresentMon CSV 流式帧率监控器。

    公开接口:
      - start() / stop()
      - snapshot() -> {'fps': int | None, 'app': str, 'pid': int, 'status': str}
      - get_fps() -> int | None
      - feed_csv_line(line: str, now: float | None = None)  (供离线单测直接喂数据)
    """

    WINDOW_S = 1.0          # 兼容保留: 旧的时间窗常量 (现在改用帧时间预算, 见 WINDOW_MS)
    # 2026-10-26 实测结论: PresentMon 每 ~1.0s 才 flush 一批 stdout, 所以「按时间
    # 切 1 秒窗」在批量到达时会退化 (批与批之间窗口大半是空的), 而且 1 秒均值
    # 天然比游戏原生 HUD 的 ±0.1s 瞬时值低 3~10%, 观望上就是"慢半拍 + 偏低"。
    # 改成**按帧时间预算回退累加**: 从最新一帧往前累加, 累到 ~350ms 就停。
    # 这样不管数据是逐行来还是 1 秒一批, 得到的都是"最近约 0.35 秒"的帧率,
    # 既跟手又稳定 (实测与游戏原生 0.1s 口径相差 ≤3%)。
    WINDOW_MS = 350.0       # 帧时间预算 (毫秒)
    MIN_SAMPLES = 8         # 窗口内最少样本数, 不足则回借历史; 仍不足就沿用上次值
    SOLO_MAX_MS = 100.0     # 只有 1 帧时, 帧时间超过此值就不出数 (避免算出 fps=2)
    MIN_PLAUSIBLE_FPS = 10  # 低于此值的候选只在"没有任何像样候选"时才采用
    STALE_S = 1.5           # 超过 1.5s 无新帧即视为无活动呈现 (返回 None -> 显示 --)

    def __init__(self, binary_path: Optional[str] = None):
        self._bin = binary_path or find_presentmon_binary()
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.RLock()
        self._process_lock = threading.Lock()
        self._control_lock = threading.Lock()
        self._control_thread = None
        self._control_seq = 0
        self._desired_running = False
        self._restart_pending = False
        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._proc: Optional[subprocess.Popen] = None
        self._job_handle: Optional[int] = None
        self._own_pid = os.getpid()
        self._session: str = _unique_session()
        # 2026-10-27 延迟优化: CSV 批次到达回调 (在 tail 线程里调用)。
        # 浮窗挂上去后, 数据一落盘就刷新, 不必等下一个定时器 (省最多 150ms)。
        self.on_batch = None
        # 批次节奏采样 [(monotonic, 字节数)] —— 用于判定延迟是"定时 flush"还是"缓冲满" (见 _note_batch)
        self._batch_ts = collections.deque(maxlen=10)

        # {pid: {'app': str, 'samples': deque[(ts, ms_between_presents)]}}
        self._procs: dict[int, dict] = {}
        self._foreground_targets: dict[int, str] = {}
        self._col_map: dict[str, int] = {}
        self._status: str = 'stopped'    # 'stopped' | 'running' | 'no_admin' | 'no_binary' | 'error'
        self._last_err: str = ''
        # ---- 诊断字段 (0.2.1 真机排障: FPS 不显示时必须能说清卡在哪一步) ----
        self._raw_count: int = 0        # 累计收到的 CSV 原始行数 (含表头)
        self._row_count: int = 0        # 累计成功解析的帧行数
        self._stderr_lines: collections.deque = collections.deque(maxlen=40)
        self._last_cmd: list = []
        # ---- 心跳遥测 (「采一会就断」必须能看出断在哪) ----
        self._chunk_count: int = 0          # 分块模式已完成的采集轮数
        self._chunk_fail: int = 0           # 空产出/异常的轮数
        self._last_feed_ts: float = 0.0     # 最近一次成功 append 采样帧的 monotonic
        self._seen_apps: collections.Counter = collections.Counter()   # 原始行里出现过的进程
        self._filtered: collections.Counter = collections.Counter()    # 被过滤掉的进程
        atexit.register(self.stop)

    # ---- 诊断 ----
    def status_text(self) -> str:
        st = self.status
        return STATUS_TEXT.get(st, st)

    def diagnose(self) -> dict:
        """返回可供 UI 展示的完整采集器诊断快照 (纯内存读取, 零阻塞)。"""
        with self._lock:
            return {
                'status': self._status,
                'status_text': STATUS_TEXT.get(self._status, self._status),
                'binary': self._bin or '',
                'binary_exists': bool(self._bin and os.path.isfile(self._bin)),
                'raw_lines': self._raw_count,
                'frame_rows': self._row_count,
                'active_procs': len(self._procs),
                'command': list(self._last_cmd),
                'last_error': self._last_err,
                'stderr_tail': list(self._stderr_lines),
                'csv_path': getattr(self, '_csv_path', '') or '',
                'csv_size': _safe_size(getattr(self, '_csv_path', '') or ''),
                'backup_privilege': bool(getattr(self, '_bak_ok', False)),
                'backup_error': _BAK_ERR,
                'transport': ('chunk' if self._chunk_count else
                              ('file' if getattr(self, '_csv_path', '') else 'stdout')),
                'chunk_count': self._chunk_count,
                'chunk_fail': self._chunk_fail,
                'last_feed_age_s': (round(time.monotonic() - self._last_feed_ts, 1)
                                    if self._last_feed_ts else None),
                'top_apps': [f'{k}:{v}' for k, v in self._seen_apps.most_common(6)],
                'filtered': [f'{k}:{v}' for k, v in self._filtered.most_common(6)],
                'chains': self._chain_summary_locked(),   # 必须在已持锁状态下调用
            }

    @property
    def status(self) -> str:
        if self._stop_evt.is_set():
            return 'stopped'
        with self._lock:
            return self._status

    @property
    def is_running(self) -> bool:
        t = self._thread
        return bool(t is not None and t.is_alive() and not self._stop_evt.is_set())

    def start(self) -> None:
        """启动后台采集线程 (幂等, 绝不阻塞调用线程)。"""
        # A stop may be waiting for the previous reader. Never clear its event,
        # and never wait for that cleanup from a GUI caller.
        if not self._lifecycle_lock.acquire(blocking=False):
            return
        try:
            if self._thread is not None and self._thread.is_alive():
                return
            self._start_locked()
        finally:
            self._lifecycle_lock.release()

    def _start_locked(self) -> None:
        self._stop_evt.clear()
        with self._lock:
            self._procs.clear()
            self._foreground_targets.clear()
            self._col_map.clear()
            self._status = 'starting'
            self._raw_count = 0
            self._row_count = 0
            self._last_err = ''
            self._stderr_lines.clear()
        self._thread = threading.Thread(
            target=self._run_loop, name='PresentMon-Reader', daemon=True
        )
        self._thread.start()

    def request_running(self, enabled: bool, *, restart: bool = False) -> None:
        """Queue lifecycle work; GUI callers never wait for child cleanup.

        Rapid toggles are coalesced to the latest requested state. A stop that
        arrives during a restart is processed before the control thread exits.
        """
        with self._control_lock:
            self._desired_running = bool(enabled)
            if not enabled:
                self._stop_evt.set()
            self._restart_pending = bool(enabled and (restart or self._restart_pending))
            self._control_seq += 1
            if self._control_thread is not None:
                return
            if enabled and self.is_running and not restart:
                return
            self._control_thread = threading.Thread(
                target=self._control_loop, name='PresentMon-Control', daemon=True)
            self._control_thread.start()

    def _control_loop(self) -> None:
        while True:
            with self._control_lock:
                seq = self._control_seq
                enabled = self._desired_running
                restart, self._restart_pending = self._restart_pending, False
            try:
                if enabled:
                    if restart:
                        self.restart()
                    else:
                        self.start()
                else:
                    self.stop()
            except Exception as exc:
                with self._lock:
                    self._status = 'error'
                    self._last_err = f'lifecycle: {exc!r}'
            with self._control_lock:
                if seq == self._control_seq:
                    self._control_thread = None
                    return

    def shutdown(self) -> None:
        """Final application exit waits for the asynchronous child cleanup."""
        self.request_running(False)
        with self._control_lock:
            control = self._control_thread
        if control is not None and control is not threading.current_thread():
            control.join()

    def stop(self) -> None:
        with self._lifecycle_lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        """停止后台采集线程并确保 PresentMon 子进程与 ETW 会话彻底退出。

        ⚠️ 退出顺序很关键: PresentMon.exe 是从 PyInstaller 的 _MEIPASS 目录直接启动的,
        只要它还有一个句柄没关掉, onefile 引导程序就会在退出时报
        「Failed to remove temporary directory: ..._MEIxxxx」。因此这里必须**等到进程真正
        退出** (含清理用的 --terminate_existing_session helper) 才返回。
        """
        self._stop_evt.set()
        with self._process_lock:
            proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=0.6)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    try:
                        proc.wait(timeout=1.5)      # 等内核回收, 否则 _MEI 删不掉
                    except subprocess.TimeoutExpired:
                        pass
            except Exception:
                pass

        reader = self._thread
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=3.0)

        # 清理自己的 ETW 会话: 必须 wait, 否则这个 helper 进程会占住 _MEI 里的 exe
        if self._bin and os.path.isfile(self._bin) and sys.platform == 'win32':
            for sess in {self._session, getattr(self, '_selftest_session', '')}:
                if not sess:
                    continue
                try:
                    helper = subprocess.Popen(
                        [self._bin, '--terminate_existing_session',
                         '--session_name', sess],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=0x08000000,
                    )
                    try:
                        helper.wait(timeout=1.5)
                    except subprocess.TimeoutExpired:
                        helper.kill()
                except Exception:
                    pass

        if self._job_handle and sys.platform == 'win32':
            try:
                ctypes.windll.kernel32.CloseHandle(wintypes.HANDLE(self._job_handle))
            except Exception:
                pass
            self._job_handle = None
        with self._lock:
            self._procs.clear()
            self._status = 'stopped'

    def _spawn_capture(self, cmd: list, **kwargs):
        """Publish the child before stop can take it; no child starts after stop."""
        with self._process_lock:
            if self._stop_evt.is_set():
                return None
            proc = subprocess.Popen(cmd, **kwargs)
            self._proc = proc
            if sys.platform == 'win32' and hasattr(proc, '_handle'):
                if self._job_handle:
                    ctypes.windll.kernel32.CloseHandle(wintypes.HANDLE(self._job_handle))
                self._job_handle = _bind_kill_on_close_job(int(proc._handle))
            return proc

    def _chain_summary(self) -> list:
        """各进程的交换链条数 (排障用)。自己加锁, 供外部调用。"""
        with self._lock:
            return self._chain_summary_locked()

    def _chain_summary_locked(self) -> list:
        """同 `_chain_summary`, 但要求调用方**已经持有 self._lock**。

        ⚠️ 踩坑记录: diagnose() 内部本身就在 `with self._lock:` 里, 直接调用会再去
        acquire 同一把非重入 Lock → 自己把自己锁死 (表现为界面卡住 / 测试挂死)。
        所以这里特意拆成两个入口, 明确"持锁版本"owern 语义。
        """
        out = []
        try:
            for pid, entry in self._procs.items():
                chains = entry.get('chains') if hasattr(entry, 'get') else None
                if not chains:
                    continue
                total = sum(chains.values())
                top = chains.most_common(3)
                out.append(f"{entry.get('app', '?')}:{len(chains)}链/"
                           f"{total}帧:" + ','.join(f'{k or "-"}={v}' for k, v in top))
        except Exception:
            pass
        return out[:5]

    def reset_parser(self) -> None:
        with self._lock:
            self._procs.clear()
            self._foreground_targets.clear()
            self._col_map.clear()

    def restart(self) -> None:
        """整机重启采集线程 (自愈): 停干净 → 清状态 → 换新会话名 → 重开。"""
        try:
            self.stop()
        except Exception:
            pass
        # stop() 会把 _status 置 stopped; 稍等旧线程退净再起新线程
        t = self._thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            try:
                t.join(2.0)
            except Exception:
                pass
        self._session = _unique_session()
        self._bak_tried = False
        with self._lock:
            self._procs.clear()
            self._col_map.clear()
            self._stderr_lines.clear()
        self.start()

    def _note_batch(self, nbytes: int) -> None:
        """记录 CSV 批次节奏, 每 10 批写一条日志 (2026-10-27 延迟定性用)。

        目的: 分清延迟到底是哪种机制 —— 两者的应对完全不同, 不量就只能猜:
        - **定时 flush**: 间隔稳定 ~1.0s, 字节数随帧率大幅变化 (高帧率批次更大)
          ⇒ PresentMon 自己按秒刷, 我们这侧再怎么优化也只能干等。
        - **缓冲满 flush**: 字节数稳定在 ~4096 (CRT 全缓冲), 间隔随帧率缩短
          ⇒ 帧率越高越跟手, 高帧率场景不该有延迟。
        """
        now = time.monotonic()
        self._batch_ts.append((now, nbytes))
        if len(self._batch_ts) < 10:
            return
        ts = [t for t, _ in self._batch_ts]
        gaps = [b - a for a, b in zip(ts, ts[1:])]
        sizes = [n for _, n in self._batch_ts]
        avg = sum(gaps) / len(gaps)
        try:
            from .logbuf import LOGBUF
            LOGBUF.write(f'[FPS] 批次节奏: 间隔 均 {avg*1000:.0f}ms '
                         f'(最小 {min(gaps)*1000:.0f} / 最大 {max(gaps)*1000:.0f}) | '
                         f'字节 均 {sum(sizes)//len(sizes)} | 样本 {len(sizes)}')
        except Exception:
            pass
        self._batch_ts.clear()

    def feed_csv_line(self, line: str, now: Optional[float] = None) -> None:
        """解析单行 PresentMon CSV 输出 (线程安全, 亦可由单元测试直接调用)。"""
        line = (line or '').strip()
        if not line:
            return
        self._raw_count += 1
        parts = [p.strip() for p in line.split(',')]
        if len(parts) < 4:
            return

        # 识别表头行
        first_lower = parts[0].lower()
        if first_lower == 'application':
            col_map = {name.strip().lower(): idx for idx, name in enumerate(parts)}
            with self._lock:
                self._col_map = col_map
            return

        ts = time.monotonic() if now is None else float(now)
        with self._lock:
            col_map = self._col_map
            if not col_map:
                # 默认按 v1_metrics 列位置兜底:
                # Application(0),ProcessID(1),...,Dropped(6),TimeInSeconds(7),msInPresentAPI(8),msBetweenPresents(9)
                col_map = {
                    'application': 0,
                    'processid': 1,
                    'swapchainaddress': 2,     # v1_metrics 固定第 3 列
                    'dropped': 6,
                    'msbetweenpresents': 9,
                }
                self._col_map = col_map

            app_idx = col_map.get('application', 0)
            pid_idx = col_map.get('processid', 1)
            dropped_idx = col_map.get('dropped', -1)
            ms_idx = col_map.get('msbetweenpresents', col_map.get('frametime', -1))
            if ms_idx < 0 or max(app_idx, pid_idx, ms_idx) >= len(parts):
                return

            # 跳过未实际显示的丢弃帧
            if 0 <= dropped_idx < len(parts):
                if parts[dropped_idx] in ('1', 'true', 'True'):
                    return

            app_name = parts[app_idx]
            # 遥测: 记录"谁在呈现", 便于判断是"没有帧"还是"帧被过滤光了"
            if app_name:
                self._seen_apps[app_name] += 1
                if sum(self._seen_apps.values()) > 4000:
                    self._seen_apps = collections.Counter(
                        dict(self._seen_apps.most_common(20)))
            if not app_name or app_name.lower() in EXCLUDED_PROCS:
                if app_name:
                    self._filtered[app_name] += 1
                return
            try:
                pid = int(parts[pid_idx], 0)
            except (ValueError, TypeError):
                pid = 0
            if pid == self._own_pid:
                self._filtered[f'{app_name}(self)'] += 1
                return
            try:
                ms = float(parts[ms_idx])
            except (ValueError, TypeError):
                self._filtered[f'{app_name}(NA)'] += 1
                return
            # 过滤极端异常值 (<0.2ms 即 >5000fps 或 >1000ms 即 <1fps 的单帧卡顿毛刺之外)
            if not (0.2 <= ms <= 1000.0):
                return

            entry = self._procs.get(pid)
            if entry is None:
                entry = {
                    'app': app_name,
                    'samples': collections.deque(maxlen=600),
                }
                self._procs[pid] = entry
            else:
                entry['app'] = app_name
            # 按 SwapChainAddress 分组: 同一个进程可能有**多条交换链**
            # (主渲染 + 覆盖层/回放/选单)。把不同链的帧间隔混在一个窗口里平均,
            # 算出来的"帧率"就不是任何一条链的真实帧率 —— 真机实测某游戏
            # 浮窗 350 而游戏自带 HUD 只有 244, 差 43%, 极可能就是混链导致。
            chain = ''
            sc_idx = col_map.get('swapchainaddress', col_map.get('swapchain', -1))
            if 0 <= sc_idx < len(parts):
                chain = parts[sc_idx].strip() or ''
            samples = entry['samples']
            samples.append((ts, ms, chain))
            entry['chains'] = entry.get('chains', collections.Counter())
            entry['chains'][chain] += 1
            self._row_count += 1
            self._last_feed_ts = ts
            # 裁剪超出窗口的历史帧
            cutoff = ts - self.STALE_S
            while samples and samples[0][0] < cutoff:
                samples.popleft()

    def snapshot(self, now: Optional[float] = None,
                 fg_pid: Optional[int] = None,
                 process_names: Optional[list[str]] = None) -> dict:
        """读取当前 FPS 快照:
        返回 {'fps': int | None, 'app': str, 'pid': int, 'status': str}。
        无活跃呈现进程时 'fps' 为 None。"""
        ts = time.monotonic() if now is None else float(now)
        target_fg = _foreground_pid() if fg_pid is None else int(fg_pid)
        allowed_names = {str(name).strip().lower() for name in (process_names or [])
                         if str(name).strip()}

        with self._lock:
            status = self._status
            stale_pids = []
            candidates = []  # (is_fg, sample_count, last_ts, pid, app, fps)

            for pid, entry in self._procs.items():
                samples = entry['samples']
                while samples and samples[0][0] < ts - self.STALE_S:
                    samples.popleft()
                if not samples:
                    stale_pids.append(pid)
                    continue
                last_ts = samples[-1][0]
                if ts - last_ts > self.STALE_S:
                    stale_pids.append(pid)
                    continue
                # 按帧时间预算回溯累加 (最近约 WINDOW_MS 毫秒), 不受批量到达影响。
                # 先按交换链分组: 同一进程可能有多条交换链 (主渲染 + 回放/选单/覆盖层),
                # 混在一起平均算出来的"帧率"不是任何一条链的真实帧率。
                groups = {}
                for t_s, ms, ch in samples:
                    groups.setdefault(ch, []).append((t_s, ms))
                best = None          # (样本数, 末时刻, fps, 1%low, 链标识)
                for ch, arr in groups.items():
                    ch_ms = []
                    acc = 0.0
                    for t_s, ms in reversed(arr):
                        ch_ms.append(ms)
                        acc += ms
                        if acc >= self.WINDOW_MS or len(ch_ms) >= 120:
                            break
                    # 样本太少就别硬算: 历史出现过 1 帧落进窗口时算出 fps=2
                    # (1 帧 / 0.5s), 且会被 FPS 保持逻辑复读几十秒。
                    if len(ch_ms) < self.MIN_SAMPLES:
                        ch_ms = [ms for _, ms in arr[-self.MIN_SAMPLES:]]
                    if len(ch_ms) < 2 and not (
                            len(ch_ms) == 1 and ch_ms[0] <= self.SOLO_MAX_MS):
                        continue
                    srt = sorted(ch_ms)
                    # Keep slow frames in FPS: discarding the slowest 10%
                    # conceals real stalls and inflates the displayed rate.
                    avg_ms = sum(ch_ms) / len(ch_ms)
                    if avg_ms <= 0:
                        continue
                    f = int(round(1000.0 / avg_ms))
                    if f <= 0:
                        continue
                    p99 = srt[min(len(srt) - 1, max(0, int(len(srt) * 0.99 - 0.5)))]
                    l1 = int(round(1000.0 / p99)) if p99 > 0 else None
                    cand = (len(ch_ms), arr[-1][0], f, l1, ch)
                    # 主链 = 窗口内样本最多、且最新的一条 (玩家真正在看的那条)
                    if best is None or cand[:2] > best[:2]:
                        best = cand
                if best is None:
                    continue
                if allowed_names and entry.get('app', '').strip().lower() not in allowed_names:
                    continue
                _cnt, _lts, fps_val, low1, _chain = best
                is_fg = (target_fg > 0 and pid == target_fg)
                if is_fg and fps_val >= self.MIN_PLAUSIBLE_FPS:
                    self._foreground_targets[pid] = entry['app']
                candidates.append((is_fg, _cnt, _lts, pid, entry['app'],
                                   fps_val, low1))

            for pid in stale_pids:
                self._procs.pop(pid, None)
                self._foreground_targets.pop(pid, None)

            if not candidates:
                return {'fps': None, 'app': '', 'pid': 0, 'status': status,
                        'fps1low': None}

            # 优先前台窗口 PID; 否则选窗口期内呈现帧数最多且最新的 3D 进程。
            # ⚠️ 但要先排除"极低帧率"的候选: 真机出现过浮窗连续 8.4 秒显示
            # `fps=2 1%low=2`, 而同期游戏真实 209~215 —— 那是选中了一个只偶尔重绘的
            # 桌面程序 (聊天/壁纸/IDE 之类, 2fps = 每 500ms 一帧), 不是采集坏了。
            # 规则: 只要还有 ≥10fps 的候选, 就只在它们里挑; 全都低帧才退回全集。
            # A selected/previously recognized foreground target remains the
            # target when it stalls. Keep the desktop low-FPS filter for unknown
            # presenters (e.g. a 2 FPS chat window in front of a running game).
            foreground = [c for c in candidates if c[0] and (
                allowed_names or self._foreground_targets.get(c[3]) == c[4])]
            pool = foreground or [c for c in candidates if c[5] >= self.MIN_PLAUSIBLE_FPS]
            if not pool:
                pool = candidates
            pool.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
            _is_fg, _cnt, _lts, pid, app, fps_val, low1 = pool[0]
            return {'fps': fps_val, 'app': app, 'pid': pid, 'status': status,
                    'fps1low': low1}

    def get_fps(self, now: Optional[float] = None,
                fg_pid: Optional[int] = None,
                process_names: Optional[list[str]] = None) -> Optional[int]:
        """便捷方法: 返回当前整数 FPS 或 None (无呈现事件)。"""
        return self.snapshot(now=now, fg_pid=fg_pid,
                             process_names=process_names)['fps']

    # ---- 后台子进程管理 ----
    EXCLUDE_ARGS = ('dwm.exe', 'explorer.exe', 'ShellExperienceHost.exe',
                    'SearchHost.exe', 'StartMenuExperienceHost.exe',
                    'TextInputHost.exe', 'SystemSettings.exe',
                    'Alp工具箱.exe', 'AlpToolbox.exe')

    def _build_cmd(self, out_path: Optional[str] = None,
                   timed: Optional[float] = None,
                   session: Optional[str] = None) -> list:
        """构造 PresentMon 命令行。out_path=None → `--output_stdout`; 否则 `--output_file`。

        ⚠️ `--no_track_display` 是命门 (2026-10-26 真机定位):
        PresentMon 默认「追踪帧一直到显示」→ 需要额外启用 **DWM 的 ETW provider**,
        那玩意儿在 165Hz 屏 + 全屏游戏/视频播放时每秒能吐上万条事件。
        本机实测 2 秒采集就出现 `warning: 26728 ETW events were lost.` —— 会话缓冲被
        DWM 事件灌爆, **我们真正要的 Present 事件被整体丢弃**, 于是 CSV 一行都没有,
        而进程状态仍是 running, 表现为「有时能采到, 一会儿就断, 再也回不来」。
        关掉 display 追踪后只剩 DXGI/D3D 的 Present 事件, 量级降到每秒几百条, 稳定。
        (`--exclude_dropped` 在无 display 追踪时 Dropped 列不可靠, 因此一并去掉。)
        """
        # ⚠️ 2026-10-28 真机致命坑: 每次构造命令都必须用**全新**会话名。
        # 之前为了"能清理旧会话"改成固定名 AlpToolbox_PresentMon, 结果重试时上一个
        # 会话尚未释放, 新进程直接报
        #   error: a trace session named "AlpToolbox_PresentMon" is already running
        # 然后立刻退出 ⇒ 表现为 "8s 读不到 CSV → 降级 → 再失败" 的死循环
        # (真机 stderr 原文已抓到)。会话名的唯一性优先于可清理性,
        # 遗留会话由 _run_loop 开头的 sweep_stale_sessions() 按前缀统一收尸。
        sess = session or _unique_session()
        self._session = sess
        cmd = [self._bin or '', '--no_console_stats',
               '--session_name', sess,
               '--v1_metrics', '--no_track_display',
               '--no_track_input', '--no_track_gpu']
        # 实测: 无 display 追踪时 CSV 的 Dropped 列恒为 0, `--exclude_dropped`
        # 既不起作用, 万一该列在未来版本变成脏值还会误杀帧, 所以不再传。
        extra = os.environ.get('ALP_PM_EXTRA', '').strip()
        if extra:
            cmd += extra.split()
        if out_path:
            cmd += ['--output_file', out_path]
        else:
            cmd += ['--output_stdout']
        if timed:
            cmd += ['--timed', f'{float(timed):.1f}', '--terminate_after_timed']
        for ex in self.EXCLUDE_ARGS:
            cmd += ['--exclude', ex]
        return cmd

    def _drain_stderr(self, proc: subprocess.Popen) -> None:
        """后台线程持续抽取 stderr (PresentMon 的权限/会话错误只在 stderr 出现)。"""
        stream = proc.stderr
        if stream is None:
            return
        try:
            for line in stream:
                txt = (line or '').strip()
                if not txt:
                    continue
                with self._lock:
                    self._stderr_lines.append(txt)
                    self._last_err = txt
        except Exception:
            pass

    def _tail_csv(self, path: str, proc: subprocess.Popen,
                  give_up_after: float = 0.0) -> bool:
        """轮询追加读取 PresentMon 写出的 CSV 文件。返回是否读到过数据。

        为什么不用 stdout 管道: PresentMon 的 CSV 走 C 运行库 `fprintf`, 当 stdout
        是**管道**时 CRT 采用全缓冲 (4096B), 而它只在特定模式下逐行 flush ——
        实测真机 `--output_stdout` 跑满 70s 仍 `原始行=0` (连表头都没出来),
        但进程状态是 running, 极易被误判成"没权限/没帧"。
        改成 `--output_file` + 文件增读后, 既有数据落盘可回查, 也不受管道缓冲影响。
        """
        pos = 0
        buf = ''
        t_nodata = time.monotonic()
        warned = False
        while not self._stop_evt.is_set():
            chunk = b''
            size = _safe_size(path)
            if size > pos:
                chunk = _read_locked(path, pos, size - pos)
                if chunk:
                    pos += len(chunk)
            if chunk:
                buf += chunk.decode('utf-8', 'replace')
                while '\n' in buf:
                    line, buf = buf.split('\n', 1)
                    self.feed_csv_line(line)
                t_nodata = time.monotonic()
                warned = False
                self._note_batch(len(chunk))
                cb = getattr(self, 'on_batch', None)
                if cb is not None:
                    try:
                        cb()          # 通知 UI 立即刷新 (本函数跑在 tail 线程)
                    except Exception:
                        pass
            elif not warned and time.monotonic() - t_nodata > 8.0:
                warned = True
                with self._lock:
                    sz = _safe_size(path)
                    self._last_err = (
                        f'PresentMon 运行中但 8s 内未读到 CSV 行 '
                        f'(文件 {sz}B, 已读 {pos}B, 备份特权={getattr(self, "_bak_ok", False)})')
            if give_up_after and pos == 0 and time.monotonic() - t_nodata > give_up_after:
                return False
            if proc.poll() is not None:
                # 进程已退出: 把最后一段尾巴读完再返回
                sz2 = _safe_size(path)
                if sz2 > pos:
                    tail = _read_locked(path, pos, sz2 - pos)
                    if tail:
                        buf += tail.decode('utf-8', 'replace')
                        for line in buf.split('\n'):
                            self.feed_csv_line(line)
                return pos > 0
            # 2026-10-27 延迟优化: 250ms → TAIL_POLL_S(60ms)。PresentMon 每 ~1s 才 flush
            # 一批, 数据落盘后我们最多再压 250ms 才读到, 与浮窗 300ms 刷新叠加就是
            # "最多 550ms 的额外滞后"。轮询本身只是 stat + 增量读, 16Hz 开销可忽略。
            time.sleep(TAIL_POLL_S)
        return pos > 0

    def _feed_chunk(self, lines: list) -> int:
        """把一整块 CSV 喂进解析器, 并按行内 TimeInSeconds **还原真实时间间隔**。

        为什么必须还原: 分块模式下整块数据是同时到达的, 如果全用一个 now 打时间戳,
        浮窗的「最近 1 秒」窗口就失去意义 —— 读到的是整块 2.5s 的均值, 表现为
        「数字慢半拍、过场时不会立刻掉帧」。这里把块内时间戳线性映射到
        [now - span, now], 于是不管块多大, 窗口统计都等价于实时。
        """
        if not lines:
            return 0
        t_idx = self._col_map.get('timeinseconds', 7)
        parsed = []
        for ln in lines:
            if not ln:
                continue
            parts = ln.split(',')
            if parts and parts[0].strip().lower() == 'application':
                self.feed_csv_line(ln)
                t_idx = {n.strip().lower(): i for i, n in enumerate(parts)}.get(
                    'timeinseconds', t_idx)
                continue
            try:
                parsed.append((float(parts[t_idx]), ln))
            except (ValueError, IndexError, TypeError):
                parsed.append((None, ln))
        known = [t for t, _ in parsed if t is not None]
        if known:
            t0, t1 = min(known), max(known)
            span = max(0.0, t1 - t0)
            base = time.monotonic() - span
        else:
            t0, span, base = 0.0, 0.0, time.monotonic()
        fed = 0
        for t, ln in parsed:
            now = (base + (t - t0)) if (t is not None and span) else base
            self.feed_csv_line(ln, now=now)
            fed += 1
        return fed

    def _chunk_loop(self) -> None:
        """后备采集模式: 定时「开会话 → 采 N 秒 → 退出」。文件随进程退出而关闭, 一定可读。

        用于 SeBackupPrivilege 不可用 (非管理员 / 被策略禁用) 的机器。
        默认块长已压到 **1.0s** (原先 2.5s): 2.5s 块 + 3s 保持 = 最长近 6 秒的观感延迟,
        实测用户能明确感知"慢半拍"。1.0s 块的实际延迟约 1.3~1.6s, 已接近实时。
        """
        try:
            chunk = float(os.environ.get('ALP_PM_CHUNK', '2.0'))
        except ValueError:
            chunk = 2.0
        # ⚠️ 更正 (任务书#3 实验证据): 之前写的"ETW 建会话要 2s"是错的 ——
        # `--output_file` 模式下 CSV 的 TimeInSeconds 从 t≈0 就开始记, 60 秒采满 59.99s。
        # 那个 2.087s 是 **--output_stdout 独有的首次 flush 延迟**, 与 ETW 无关。
        # 所以分块的下限只需避开会话创建/退出开销, 取 1.5s 即可 (块内有 ~1s 有效数据)。
        chunk = max(1.5, chunk)
        self.STALE_S = max(self.STALE_S, chunk * 2.0)
        path = _live_csv_path() + '.chunk'
        with self._lock:
            self._status = 'running'
        while not self._stop_evt.is_set():
            # 每轮用**独立文件名**: 万一下一轮 os.remove 因占用失败, PresentMon 会
            # 追加到旧文件里 → 历史行被反复以新时间戳喂进解析器 → 帧队列被灌爆、
            # 表现就是"采一会就乱/就断"。独立文件名从根上排除这种串味。
            cur_path = f'{path}{self._chunk_count}'
            try:
                if os.path.exists(cur_path):
                    os.remove(cur_path)
            except OSError:
                pass
            cmd = self._build_cmd(out_path=cur_path, timed=chunk,
                                 session=_unique_session('chunk_'))
            with self._lock:
                self._last_cmd = list(cmd)
            rc = -1
            err = ''
            try:
                proc = self._spawn_capture(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    stdin=subprocess.DEVNULL, text=True,
                    encoding='utf-8', errors='replace', creationflags=PM_CREATIONFLAGS)
                if proc is None:
                    return
                try:
                    _out, stderr = proc.communicate(timeout=chunk + 20)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate()
                    raise
                rc = proc.returncode
                err = (stderr or '').strip()
                if err:
                    with self._lock:
                        for ln in err.splitlines():
                            if ln.strip():
                                self._stderr_lines.append(ln.strip())
                        self._last_err = err.splitlines()[-1][:300]
            except Exception as e:
                with self._lock:
                    self._last_err = f'chunk 采集异常: {e!r}'
            if self._stop_evt.is_set():
                return
            lines = []
            for _ in range(20):
                try:
                    with open(cur_path, encoding='utf-8-sig', errors='replace') as f:
                        lines = f.read().splitlines()
                    break
                except OSError:
                    if self._stop_evt.wait(0.15):
                        return
            fed = self._feed_chunk(lines)
            self._chunk_count += 1
            if fed <= 1:
                # 只有表头/空文件 = 这一轮什么都没采到
                self._chunk_fail += 1
                with self._lock:
                    self._last_err = (f'chunk#{self._chunk_count} 0 帧 '
                                      f'(rc={rc}) {err.splitlines()[-1][:160] if err else ""}')
            else:
                self._chunk_fail = 0
            try:
                os.remove(cur_path)
            except OSError:
                pass
            with self._lock:
                self._status = 'running'
            # 2026-10-28: 连续失败时退避。分块模式**每块都新建一个 ETW 会话**
            # (会话名带 chunk_<pid>_<ms>), 失败时 0.4s 一轮 = 每秒建 2 个会话,
            # 反而加重本已拥塞的 ETW 子系统 (真机日志: 40 分块 / 57 个活动会话)。
            # 失败 6 次起逐步放慢到 5s 一轮, 既不放弃, 也不再添乱。
            if self._chunk_fail >= 6:
                delay = min(5.0, 0.4 + 0.6 * (self._chunk_fail - 5))
            else:
                delay = 0.4
            if self._stop_evt.wait(delay):
                return

    def _run_loop(self) -> None:
        """传输阶梯: stdout 管道 → 文件增量读 → 分块采集, 逐级降级。

        | 档位 | 延迟 | 前提 |
        |---|---|---|
        | `--output_stdout` 管道 | 实测 ~1.0s(PresentMon 内部按秒 flush) | 无 |
        | `--output_file` 增量读 | ≤0.25s, 但需 SeBackupPrivilege 绕独占 | 管理员 + 特权可用 |
        | `--timed` 分块 | 块长 + 0.4s | 无, 永远可用 |

        注意: `filehandle`(自建文件句柄当 stdout) 通道已撤销 —— 真机实测证明它的
        1 秒 flush 根本没被绕过(文件仍按 1.0/1.1s 一批增长), 还把输出变成 UTF-16LE
        导致一行都解析不出。想突破 1 秒只能自己做 ETW 消费端, 不在本模块范围。
        可用 `ALP_PM_TRANSPORT=stdout|file|chunk` 强制某一档。
        """
        if not self._bin or not os.path.isfile(self._bin):
            with self._lock:
                self._status = 'no_binary'
            return
        # 2026-10-27: 启动前先扫掉遗留 ETW 会话, 再改回固定会话名。
        # 遗留会话会持续吃缓冲, 表现为"每块 0 帧 + 大量 events were lost"。
        try:
            swept = sweep_stale_sessions()
            if swept >= 0:
                others = etw_session_names() or []
                from .logbuf import LOGBUF
                if swept:
                    LOGBUF.write(f'[FPS] 已清理遗留 ETW 会话 {swept} 个 (此前会挤掉新会话的事件)')
                LOGBUF.write(f'[FPS] ETW 活动会话 {len(others)} 个: '
                             f'{", ".join(others[:40])}{"…" if len(others) > 40 else ""}')
                if len(others) > 20:
                    # 2026-10-28 真机: 用户机器上常年 52~57 个 ETW 会话, PresentMon 每块
                    # 丢 3000+ 事件 → 0 帧。正常 Windows 一般 10~20 个, 超出多半是某些软件
                    # 开了实时跟踪却没停 (我们自己的旧版本就犯过这个错)。
                    LOGBUF.write(f'[FPS] ⚠️ 会话数异常 ({len(others)} > 20): '
                                 f'PresentMon 很可能因缓冲不足而丢事件; '
                                 f'请在管理员 CMD 跑 logman query -ets 找出占用者')
        except Exception:
            pass
        self._bak_tried = True
        self._bak_ok = _enable_backup_privilege()
        mode = os.environ.get('ALP_PM_TRANSPORT', '').strip().lower()
        if mode and mode not in ('stdout', 'file', 'chunk'):
            with self._lock:
                self._last_err = f'未知传输模式 {mode!r}, 已忽略并按默认阶梯走'
            mode = ''
        # 2026-10-28: 文件档是否启用直接取决于备份特权; 失败时是**静默**跳过到分块模式
        # (每 2s 新建一个 ETW 会话, 每次开局丢一批事件)。不记日志就永远查不到原因。
        try:
            from .logbuf import LOGBUF as _LB
            _LB.write(f'[FPS] 传输能力: 备份特权={bool(self._bak_ok)}'
                      f'{"" if self._bak_ok else " (" + (_BAK_ERR or "未知") + ")"}'
                      f' | 指定模式={mode or "阶梯"}')
        except Exception:
            pass

        # 2026-10-28 真机: 桌面(无游戏)时整条阶梯跑完都拿不到数据, 线程就退出了;
        # 浮窗 150ms 的 tick 又会 start() 一次 ⇒ "每 2~3 秒重建一次采集 + 一个 ETW 会话"
        # 的抖动 (日志里 传输能力 一行连打 4 次)。没有呈现事件不是故障, 等就行。
        # 所以整条阶梯放进 while: 只要没 stop, 线程就常驻。
        while not self._stop_evt.is_set():
            if mode in ('', 'stdout'):
                if self._stdout_loop():
                    return
                if self._stop_evt.is_set():
                    return
                with self._lock:
                    self._last_err = 'stdout 通道 8s 无数据, 降级到文件增量读'
            if mode in ('', 'file'):
                if mode == 'file' or self._bak_ok:
                    self._file_loop()
                    if self._stop_evt.is_set():
                        return
                    self._stop_evt.wait(1.0)
                    continue
            self._chunk_loop()
            if self._stop_evt.is_set():
                return
            self._stop_evt.wait(1.0)

    def _stdout_loop(self) -> bool:
        """直读 PresentMon stdout 管道。返回 True 表示一直流到停止。"""
        cmd = self._build_cmd()
        with self._lock:
            self._last_cmd = list(cmd)
        try:
            proc = self._spawn_capture(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True, encoding='utf-8', errors='replace', bufsize=1,
                creationflags=PM_CREATIONFLAGS,
            )
        except Exception as e:
            with self._lock:
                self._last_err = f'stdout 启动失败: {e!r}'
            return False
        if proc is None:
            return True
        threading.Thread(target=self._drain_stderr, args=(proc,),
                         name='PresentMon-Stderr', daemon=True).start()

        def _pump():
            try:
                assert proc.stdout is not None
                for raw in proc.stdout:
                    self.feed_csv_line(raw)
            except Exception:
                pass

        threading.Thread(target=_pump, name='PresentMon-Stdout', daemon=True).start()
        with self._lock:
            self._status = 'running'
        base = self._raw_count
        t0 = time.monotonic()
        streamed = False
        while not self._stop_evt.is_set():
            time.sleep(0.4)
            if self._raw_count > base:
                streamed = True
                break
            if proc.poll() is not None:
                break
            if time.monotonic() - t0 > 8.0:
                break
        if not streamed:
            try:
                proc.terminate()
                proc.wait(timeout=1.0)
            except Exception:
                pass
            return False
        while not self._stop_evt.is_set() and proc.poll() is None:
            time.sleep(0.5)
        try:
            proc.terminate()
            proc.wait(timeout=1.0)
        except Exception:
            pass
        return True

    def _file_loop(self) -> None:
        restarts = 0
        while not self._stop_evt.is_set():
            use_file = True
            tmpdir = None
            csv_path = None
            if use_file:
                # 落盘到 APPDATA 而不是临时目录: 出问题时这个文件就是被告,
                # 直接看它有没有表头/有没有行, 比任何猜测都硬。
                csv_path = _live_csv_path()
                try:
                    if os.path.exists(csv_path):
                        os.remove(csv_path)
                except OSError:
                    pass
                self._csv_path = csv_path
                cmd = self._build_cmd(out_path=csv_path)
            else:
                cmd = self._build_cmd()
            with self._lock:
                self._last_cmd = list(cmd)
            try:
                proc = self._spawn_capture(
                    cmd,
                    stdout=subprocess.DEVNULL if use_file else subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    bufsize=-1 if use_file else 1,
                    creationflags=PM_CREATIONFLAGS,
                )
                if proc is None:
                    return
                self._drain_thread = threading.Thread(
                    target=self._drain_stderr, args=(proc,),
                    name='PresentMon-Stderr', daemon=True)
                self._drain_thread.start()
                with self._lock:
                    self._status = 'running'

                if use_file and csv_path:
                    got = self._tail_csv(csv_path, proc)
                    # ⚠️ 2026-10-28: 以前"20s 读不到数据"就降级成分块采集。这是错的 ——
                    # 桌面没有游戏时本来就没有 Present 事件, "没数据"不等于"传输坏了"。
                    # 而分块模式每 2s 新建一个 ETW 会话, 在本来就拥塞的机器上每次开局
                    # 都丢一批事件, 严格劣于文件档。既然备份特权已拿到 (文件档可用),
                    # 就一直用文件档等着, 不再降级。
                    if not got and not self._stop_evt.is_set():
                        with self._lock:
                            self._last_err = ('文件档运行中, 暂未读到 CSV 行 '
                                              '(无程序呈现时属正常, 保持等待)')
                        # 继续下一轮 (重新拉起 PresentMon), 但不切换传输档位
                else:
                    assert proc.stdout is not None
                    for raw_line in proc.stdout:
                        if self._stop_evt.is_set():
                            break
                        self.feed_csv_line(raw_line)

                rc = proc.wait(timeout=1.0)
                if self._stop_evt.is_set():
                    break

                with self._lock:
                    err_txt = '\n'.join(self._stderr_lines)
                    saw_rows = self._row_count
                lower_err = err_txt.lower()

                if 'error code 4201' in lower_err or 'already running' in lower_err:
                    # 会话名冲突: 换一个全新唯一名重启, 最多 2 次
                    with self._lock:
                        self._status = 'error'
                        self._last_err = err_txt.strip() or 'ETW session 4201'
                    self._session = _unique_session()
                    restarts += 1
                    if restarts > 2:
                        return
                    continue

                if 'access denied' in lower_err or 'administrative privileges' in lower_err \
                        or 'failed to start trace session' in lower_err or rc == 6:
                    # 权限不足 (未以管理员启动且不在 Performance Log Users 组): 不再死循环重启
                    with self._lock:
                        self._status = 'no_admin'
                        self._last_err = err_txt.strip() or 'PresentMon: access denied'
                    return

                with self._lock:
                    self._status = 'error'
                    self._last_err = err_txt.strip() or f'PresentMon 退出码 {rc}'
                    if saw_rows == 0 and not err_txt.strip():
                        self._last_err = f'PresentMon 退出码 {rc}, 未收到任何 CSV 行'

                restarts += 1
                if restarts > 3:
                    return
                if self._stop_evt.wait(3.0):
                    break
            except Exception as e:
                with self._lock:
                    self._status = 'error'
                    self._last_err = repr(e)
                return
            finally:
                if tmpdir:
                    shutil.rmtree(tmpdir, ignore_errors=True)

    # ---- 自检 (设置页「FPS 采集自检」按钮调用, 必须在后台线程执行) ----
    def run_self_test(self, seconds: float = 5.0) -> dict:
        # Keep GUI timer requests from starting a second capture during a probe.
        with self._lifecycle_lock:
            return self._run_self_test_locked(seconds)

    def _run_self_test_locked(self, seconds: float) -> dict:
        """同步跑一次限时采集, 返回可读诊断报告 (含 PresentMon 原始输出样本)。

        `seconds` 建议 ≥5s。理由 (任务书#3 已更正): ETW 几乎立刻出数据, 但
        `--output_stdout` 首次 flush 要 ~2.1s, 再叠加每 ~1.0s 一批的节拍,
        取 3s 以下很容易在"第一批还没到"时就收工, 把正常误判成"抓不到帧"。
        """
        binp = self._bin or find_presentmon_binary()
        if not binp or not os.path.isfile(binp):
            return {'ok': False, 'stage': 'binary',
                    'title': '未找到 PresentMon.exe',
                    'detail': '请确认 assets/PresentMon.exe 存在, 或设置环境变量 ALP_PRESENTMON_PATH。'}

        tmpdir = tempfile.mkdtemp(prefix='alp_pm_')
        csv_path = os.path.join(tmpdir, 'pm.csv')
        # 自检用独立会话名
        self._selftest_session = _unique_session('st_')
        # ⚠️ 关键: 自检必须**先停掉主采集**再跑。
        # 实测证据 (任务书#3 第 6 节): 助手在跑两个 60 秒 PresentMon 采集的那 2 分钟里,
        # 主采集出现 5 次 fps=2 (窗口样本被会话竞争打空), 之后 28 分钟一次都没有。
        # 也就是说两个 PresentMon 会话并存会互相丢事件 —— 自检自己就是那个"第二个会话"。
        with self._control_lock:
            request_seq = self._control_seq
            was_running = self.is_running
        if was_running:
            try:
                self.stop()
            except Exception:
                pass
        try:
            if self._thread is not None and self._thread.is_alive():
                shutil.rmtree(tmpdir, ignore_errors=True)
                return {'ok':False, 'stage':'busy', 'title':'采集线程仍在退出，请稍后重试',
                        'detail':'未启动第二个 PresentMon 会话。'}
            return self._run_self_test_inner(seconds, csv_path, tmpdir)
        finally:
            with self._control_lock:
                resume = (was_running if request_seq == self._control_seq
                          else self._desired_running)
            if resume:
                try:
                    self.start()
                except Exception:
                    pass

    def _run_self_test_inner(self, seconds: float, csv_path: str, tmpdir: str) -> dict:
        cmd = self._build_cmd(out_path=csv_path, timed=seconds,
                             session=self._selftest_session)
        out = err = ''
        rc = -1
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, encoding='utf-8',
                errors='replace', timeout=seconds + 20,
                creationflags=PM_CREATIONFLAGS,
            )
            rc, out, err = proc.returncode, proc.stdout or '', proc.stderr or ''
        except subprocess.TimeoutExpired:
            return {'ok': False, 'stage': 'timeout', 'title': 'PresentMon 超时未退出',
                    'detail': f'超过 {seconds + 20:.0f}s 无结果。命令: {" ".join(cmd)}'}
        except Exception as e:
            return {'ok': False, 'stage': 'spawn', 'title': 'PresentMon 启动失败',
                    'detail': repr(e)}

        lower = (err or '').lower()
        # ⚠️ 判定顺序很重要: PresentMon 在**任何**会话创建失败时都会打印
        # 「requires either administrative privileges or ...」通用提示, 先判权限
        # 会把 4201 会话冲突误报成"没提权"(实测踩过)。所以先判会话冲突。
        if 'error code 4201' in lower or 'already running' in lower:
            return {'ok': False, 'stage': 'session_conflict',
                    'title': 'ETW 会话名冲突 (4201) —— 上次异常退出留下了僵尸会话',
                    'detail': ('已在用唯一会话名重试; 若仍失败, 请以管理员运行一次 '
                               '`logman stop <会话名> -ets` 或重启电脑清掉残留会话。'),
                    'raw': ((out or '') + '\n' + (err or '')).strip()[:800],
                    'command': ' '.join(cmd)}
        if 'access denied' in lower or 'administrative privileges' in lower:
            return {'ok': False, 'stage': 'privilege',
                    'title': '权限不足: PresentMon 无法创建 ETW 会话',
                    'detail': ('当前进程不是管理员。请以「管理员身份运行」Alp 工具箱 '
                               '(或把当前用户加入「Performance Log Users」组)。'
                               f' 管理员状态={bool(ctypes.windll.shell32.IsUserAnAdmin())}'),
                    'raw': (err or '').strip()[:800], 'command': ' '.join(cmd)}
        if rc == 6:
            return {'ok': False, 'stage': 'privilege',
                    'title': 'PresentMon 退出码 6 (通常是权限/会话被拒绝)',
                    'detail': f'管理员状态={bool(ctypes.windll.shell32.IsUserAnAdmin())}',
                    'raw': (err or '').strip()[:800], 'command': ' '.join(cmd)}

        rows = []
        try:
            with open(csv_path, encoding='utf-8-sig', errors='replace') as f:
                rows = f.read().splitlines()
        except Exception:
            pass
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

        frame_rows = [r for r in rows[1:] if r.strip()] if rows else []
        if not frame_rows:
            # 「ETW 事件丢失」是独立病因: 会话建起来了, 但缓冲被别的 provider 灌爆,
            # 我们要的 Present 事件被整体丢弃 —— 和"没程序呈现"必须分开报。
            import re as _re
            m = _re.search(r'(\d+)\s+ETW events were lost', err or '', _re.I)
            if m:
                lost = int(m.group(1))
                # 2026-10-28 真机: 用户机器上有 51 个 ETW 会话, 其中 **WPR**
                # (Windows 性能记录器) 一旦在跑, 会抓取全部 provider, 把 ETW 缓冲吃光,
                # PresentMon 每 6s 丢 1.2 万条事件 → 0 帧。这是可修的, 得指出来。
                heavy = _heavy_sessions()
                if heavy:
                    hint = (f'检测到第三方 ETW 跟踪: {", ".join(heavy[:3])}。'
                            f'它们会和 PresentMon 抢 ETW 缓冲。'
                            f'请关闭对应软件 (如 N 卡驱动的性能跟踪/监控工具) 后点「自检」重试。')
                else:
                    hint = ('会话建起来了, 但缓冲仍被灌爆。本版已加 --no_track_display 并提高了'
                            ' PresentMon 进程优先级; 若仍出现, 请导出诊断包发回作者。')
                return {'ok': False, 'stage': 'events_lost',
                        'title': f'ETW 会话溢出: {lost} 条事件被丢弃, 因此 0 帧',
                        'detail': hint,
                        'raw': ((out or '') + '\n' + (err or '')).strip()[:800],
                        'command': ' '.join(cmd)}
            return {'ok': False, 'stage': 'no_frames',
                    'title': f'PresentMon 已运行 {seconds:.0f}s, 但未捕获到任何呈现事件',
                    'detail': ('ETW 会话正常, 但当前没有程序在 Present 新帧。'
                               '请启动一个游戏/播放器后重试; 若游戏已在运行仍为空, '
                               '该游戏可能使用了被反作弊拦截的呈现路径。'),
                    'raw': ((out or '') + '\n' + (err or '')).strip()[:800],
                    'command': ' '.join(cmd)}

        mono = FpsMonitor()
        mono.reset_parser()
        for ln in rows:
            mono.feed_csv_line(ln)
        snap = mono.snapshot(fg_pid=_foreground_pid())
        apps = sorted({r.split(',')[0] for r in frame_rows if ',' in r})
        return {'ok': True, 'stage': 'ok',
                'title': f'采集正常: 收到 {len(frame_rows)} 帧, {len(apps)} 个进程',
                'detail': ('当前实时 FPS = ' +
                           (str(snap['fps']) if snap.get('fps') else '未定 (窗口内帧数不足)') +
                           ' · 进程: ' + ', '.join(apps[:6])),
                'frames': len(frame_rows), 'apps': apps,
                'fps': snap.get('fps'), 'command': ' '.join(cmd)}
