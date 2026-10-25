# -*- coding: utf-8 -*-
"""黑鲨风神Pro 工具箱 — 入口。

用法: python main.py [--page status|curve|screen|control|devices|about]
管理员运行可读取 CPU 温度。
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from brb02.config import Config
from brb02.gui.main_window import APP_NAME, MainWindow, _make_app_icon
from brb02.gui.theme import build_palette, build_qss
from brb02.service import DeviceWorker

SINGLE_KEY = 'AlpToolbox-SingleInstance'    # 单实例命名管道 (托盘驻留防双开抢设备)


def load_fonts():
    """注册内嵌字体 (来自 FanControl, MIT): Manrope 静态字重族 (400-800)"""
    from PySide6.QtGui import QFontDatabase
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    fonts_dir = os.path.join(base, 'brb02', 'gui', 'fonts')
    if os.path.isdir(fonts_dir):
        for name in sorted(os.listdir(fonts_dir)):
            if name.lower().endswith('.ttf'):
                QFontDatabase.addApplicationFont(os.path.join(fonts_dir, name))


def _ensure_admin():
    """打包运行时请求管理员权限 (CPU 温度需 PawnIO); 用户拒绝 UAC 则普通权限继续。"""
    if not getattr(sys, 'frozen', False) or '--no-elevate' in sys.argv:
        return
    try:
        import ctypes
        if ctypes.windll.shell32.IsUserAnAdmin():
            return
        args = ' '.join(f'"{a}"' for a in sys.argv[1:])
        ret = ctypes.windll.shell32.ShellExecuteW(None, 'runas', sys.executable, args, None, 1)
        if ret > 32:
            sys.exit(0)
    except Exception:
        pass  # 提权失败 → 普通权限继续 (GPU 温度可用, CPU 显示 --)


def _err_log(msg: str):
    try:
        d = os.path.join(os.environ.get('APPDATA', '.'), 'Brb02Toolbox')
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'startup_err.log'), 'a', encoding='utf-8') as f:
            f.write(msg + '\n')
    except Exception:
        pass


def main():
    # ALP_RHI=1: 控件走 GPU 合成 (Qt6 RHI)。此前不敢开是因为 QGraphicsEffect
    # 与 RHI 不兼容 —— 现在阴影已烘焙、effect 全部退出热路径, 可以 A/B 了。
    if os.environ.get('ALP_RHI') == '1':
        os.environ['QT_WIDGETS_RHI'] = '1'
    page = None
    if '--page' in sys.argv:
        page = sys.argv[sys.argv.index('--page') + 1]

    global T0
    T0 = time.perf_counter()
    try:
        _run(page)
    except SystemExit:
        raise
    except Exception:
        import traceback
        _err_log(traceback.format_exc())
        raise


class _PerfLog(QObject):
    """--perf-log: 记录真实帧间隔 (>25ms 判肥帧), 退出时写文件让用户回传。

    用法: Alp工具箱.exe --perf-log     → 切几页、悬停、等几秒, 退出后
    %APPDATA%/Brb02Toolbox/perf_log.txt 即真实绘制节奏 (_widget 列为肥帧元凶)。"""

    def __init__(self):
        super().__init__()
        self._last = None
        self.rows = []
        self.n_paints = 0

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Paint:
            self.n_paints += 1
            now = time.perf_counter()
            if self._last is not None:
                gap = now - self._last
                if gap > 0.025:
                    self.rows.append(f'{now - T0:8.3f}s  gap={gap * 1000:6.1f}ms  '
                                     f'{type(obj).__name__}')
            self._last = now
        return False


def _run(page):
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle('Fusion')
    global perf
    perf = _PerfLog()
    app.installEventFilter(perf)      # 常开: 退出自动写 perf_log.txt, 排查不用复现
    app.setWindowIcon(_make_app_icon())
    load_fonts()
    _ensure_admin()        # 提权最先做: 提权重启的新实例不能撞见旧实例的单实例管道

    # 运行日志: stderr 分流 (异常 traceback 进关于页日志, 可复制)
    from brb02.logbuf import LOGBUF
    LOGBUF.write(f'{APP_NAME} 启动')
    LOGBUF.install_stderr_tee()

    # 单实例守卫 (两段式): 托盘驻留时再双击 exe → 唤醒已有窗口后退出。
    # 第二次探测是为了放行 UAC 提权重启的竞态 (旧实例管道尚未完全关闭)。
    def _already_running() -> bool:
        sock = QLocalSocket()
        sock.connectToServer(SINGLE_KEY)
        if not sock.waitForConnected(300):
            return False
        sock.write(b'show\n')
        sock.flush()
        sock.waitForBytesWritten(300)
        sock.disconnectFromServer()
        return True

    if _already_running():
        time.sleep(1.5)
        if _already_running():
            print('Alp 工具箱已在运行 (托盘驻留) — 已唤醒现有窗口')
            return
    server = QLocalServer()
    QLocalServer.removeServer(SINGLE_KEY)      # 清理上次异常退出的残留命名管道
    server.listen(SINGLE_KEY)

    cfg = Config.load()
    app.setStyleSheet(build_qss(cfg.dark))
    app.setPalette(build_palette(cfg.dark))   # 裸 QLabel 文字兜底 (深色防黑字)

    worker = DeviceWorker(cfg)
    win = MainWindow(cfg, worker)
    if page:
        win.switch_page(page)
    worker.start()
    if '--tray' in sys.argv:
        win.hide_to_tray()   # 开机自启动静默驻留: 不弹主窗口 (托盘气泡提示一次)
    else:
        win.show()

    def _wake_second():
        """第二实例接入 → 唤醒主窗口。"""
        while server.hasPendingConnections():
            s = server.nextPendingConnection()
            s.readAll()
            s.disconnectFromServer()
        win._show()

    server.newConnection.connect(_wake_second)
    if cfg.restore_on_start and not cfg.curve_enabled:
        # quiet: 启动 2.5s 时可能还没连上, 静默跳过; 连上后引擎会自行恢复固定转速。
        # 曲线模式下引擎按曲线控速, 恢复固定转速是白做且会扰动曲线, 直接跳过。
        # 走后台线程下发, 避免在 GUI 主线程同步等待 USB 锁与 0.3s 应答窗口。
        import threading as _th
        QTimer.singleShot(
            2500,
            lambda: _th.Thread(
                target=lambda: worker.apply_fixed_rpm(cfg.fixed_rpm, quiet=True),
                daemon=True,
            ).start(),
        )

    # --snap N [页名]: N 秒后自截 UI (验证用), 可选 --snap-quit 截完退出
    if '--snap' in sys.argv:
        secs = int(sys.argv[sys.argv.index('--snap') + 1])
        if getattr(sys, 'frozen', False):
            out = os.path.join(os.environ.get('APPDATA', '.'), 'Brb02Toolbox', 'ui_self.png')
        else:
            out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tools', 'ui_self.png')

        def _snap():
            win.grab().save(out)
            print('UI 已保存:', out)
            if '--snap-quit' in sys.argv:
                win.real_quit()
        QTimer.singleShot(secs * 1000, _snap)

    rc = app.exec()
    if perf is not None:
        try:
            import os as _os
            out = _os.path.join(_os.environ.get('APPDATA', '.'), 'Brb02Toolbox', 'perf_log.txt')
            _os.makedirs(_os.path.dirname(out), exist_ok=True)
            with open(out, 'a', encoding='utf-8') as f:
                f.write('\n==== session ' + time.strftime('%Y-%m-%d %H:%M:%S') + ' ====\n')
                f.write('paints=%d fat_frames(>25ms)=%d\n' % (perf.n_paints, len(perf.rows)))
                if perf.rows:
                    f.write(chr(10).join(perf.rows[-400:]) + '\n')
            print(f'perf log: {out}')
        except Exception:
            pass
    sys.exit(rc)


if __name__ == '__main__':
    main()
