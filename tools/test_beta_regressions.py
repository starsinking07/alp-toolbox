"""Offline regression checks for the 0.2.1-beta audit (no hardware/network)."""
import atexit
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import sys
import threading
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from brb02.fps_monitor import FpsMonitor
from brb02.config import Config
from brb02.overlay import PerformanceOverlay
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from PySide6.QtNetwork import QNetworkReply
from brb02.update_check import AssetDownloader, is_newer

APP = QApplication.instance() or QApplication([])
PM_SPEC = importlib.util.spec_from_file_location(
    'audit_patch_presentmon',Path(__file__).with_name('patch_presentmon.py'))
PM = importlib.util.module_from_spec(PM_SPEC)
PM_SPEC.loader.exec_module(PM)


class BetaRegressions(unittest.TestCase):
    def monitor(self):
        monitor = FpsMonitor(binary_path='__offline_missing_presentmon__')
        atexit.unregister(monitor.stop)
        self.addCleanup(monitor.stop)
        return monitor

    def test_start_cannot_clear_stop_for_a_live_previous_reader(self):
        monitor = self.monitor()
        release = threading.Event()
        entered = threading.Event()
        observed = []
        def reader():
            entered.set()
            release.wait(2)
            observed.append(monitor._stop_evt.is_set())
        monitor._run_loop = reader
        monitor.start()
        self.assertTrue(entered.wait(1))
        previous = monitor._thread
        try:
            with patch.object(previous, 'join'):
                monitor.stop()  # Simulate cleanup timing out before the reader exits.
            monitor.start()
            self.assertIs(monitor._thread, previous)
            self.assertTrue(monitor._stop_evt.is_set())
        finally:
            release.set()
            previous.join(2)
        self.assertEqual(observed, [True])

    def test_stop_prevents_late_child_spawn(self):
        monitor = self.monitor()
        monitor.stop()
        with patch('brb02.fps_monitor.subprocess.Popen') as spawn:
            self.assertIsNone(monitor._spawn_capture(['fake']))
        spawn.assert_not_called()

    def test_chunk_child_is_terminated_and_reader_exits_on_stop(self):
        monitor = self.monitor()
        entered = threading.Event()
        terminated = threading.Event()
        child = Mock(spec=['communicate','terminate','wait','returncode'])
        child.returncode = 0
        def communicate(**kwargs):
            entered.set()
            terminated.wait(2)
            return '', ''
        child.communicate.side_effect = communicate
        child.terminate.side_effect = terminated.set
        monitor._run_loop = monitor._chunk_loop
        with patch('brb02.fps_monitor.subprocess.Popen', return_value=child), \
             patch('brb02.fps_monitor._live_csv_path', return_value='__unused_chunk__'):
            monitor.start()
            self.assertTrue(entered.wait(1))
            monitor.stop()
        child.terminate.assert_called_once()
        self.assertFalse(monitor._thread.is_alive())

    def test_slow_stop_does_not_block_qt_events_and_latest_toggle_wins(self):
        monitor = self.monitor()
        waiting = threading.Event()
        release = threading.Event()
        def slow_stop():
            waiting.set()
            release.wait(2)
        started = []
        monitor.stop = slow_stop
        monitor.start = lambda: started.append(True)
        overlay = PerformanceOverlay(Config(), fps_monitor=monitor)
        ticks = []
        try:
            overlay.set_overlay_enabled(False, persist=False)
            self.assertTrue(waiting.wait(1))
            QTimer.singleShot(0, lambda: ticks.append(True))
            APP.processEvents()
            self.assertEqual(ticks, [True])
            monitor.request_running(True)
            release.set()
            with monitor._control_lock:
                worker = monitor._control_thread
            if worker:
                worker.join(2)
            self.assertEqual(started, [True])
        finally:
            release.set()
            overlay.shutdown()
            overlay.close()

    def test_download_close_error_never_emits_success(self):
        for expected in (0, 8):
            with self.subTest(expected_size=expected):
                downloader = AssetDownloader()
                downloader._dest = '__offline_no_file__.exe'
                downloader._expect_size = expected
                downloader._file = Mock()
                downloader._file.close.side_effect = OSError('disk full on flush')
                downloader._reply = Mock()
                downloader._reply.error.return_value = QNetworkReply.NoError
                done, failures = [], []
                downloader.done.connect(done.append)
                downloader.fail.connect(failures.append)
                with patch('brb02.update_check.os.path.getsize', return_value=8), \
                     patch('brb02.update_check.os.remove') as remove:
                    downloader._on_finished(downloader._seq)
                self.assertEqual(done, [])
                self.assertEqual(len(failures), 1)
                self.assertIn('disk full', failures[0])
                remove.assert_called_once_with(downloader._dest)

    def test_complete_unknown_size_download_still_succeeds(self):
        downloader = AssetDownloader()
        downloader._dest = '__offline_no_file__.exe'
        downloader._file = Mock()
        downloader._reply = Mock()
        downloader._reply.error.return_value = QNetworkReply.NoError
        done = []
        downloader.done.connect(done.append)
        with patch('brb02.update_check.os.path.getsize', return_value=8):
            downloader._on_finished(downloader._seq)
        self.assertEqual(done, [downloader._dest])

    def test_idle_frames_do_not_restart_but_explicit_error_can_recover(self):
        monitor = self.monitor()
        cfg = Config(overlay_game_only=True)
        overlay = PerformanceOverlay(cfg, fps_monitor=monitor)
        monitor.diagnose = Mock(return_value={
            'raw_lines':1, 'status':'running', 'last_error':''})
        monitor.request_running = Mock()
        overlay._raw_seen = 1
        overlay._raw_ts = time.monotonic() - 31
        overlay._ever_rows = True
        try:
            overlay.refresh_metrics()
            monitor.request_running.assert_not_called()
            self.assertEqual(getattr(overlay, '_selfheal_n', 0), 0)
            monitor.diagnose.return_value.update(status='error',last_error='capture failure')
            overlay.refresh_metrics()
            monitor.request_running.assert_called_once_with(True, restart=True)
            self.assertEqual(overlay._selfheal_n, 1)
        finally:
            overlay.shutdown()
            overlay.close()

    def test_fps_keeps_stall_frames_in_average(self):
        monitor = self.monitor()
        monitor.feed_csv_line(
            'Application,ProcessID,SwapChainAddress,Dropped,msBetweenPresents',now=10)
        for ms in [10]*9+[100]:
            monitor.feed_csv_line(f'game.exe,43210,0xA,0,{ms}',now=10)
        snapshot = monitor.snapshot(now=10,fg_pid=43210)
        self.assertEqual(snapshot['fps'], 53)
        self.assertEqual(snapshot['fps1low'], 10)

    def test_recognized_foreground_game_keeps_priority_when_it_slows(self):
        monitor = self.monitor()
        monitor.feed_csv_line(
            'Application,ProcessID,SwapChainAddress,Dropped,msBetweenPresents',now=10)
        for _ in range(8):
            monitor.feed_csv_line('game.exe,43210,0xA,0,10',now=10)
        self.assertEqual(monitor.snapshot(now=10,fg_pid=43210)['fps'],100)
        for _ in range(8):
            monitor.feed_csv_line('game.exe,43210,0xA,0,200',now=20)
            monitor.feed_csv_line('background.exe,43211,0xB,0,16.6667',now=20)
        snapshot = monitor.snapshot(now=20,fg_pid=43210)
        self.assertEqual((snapshot['app'],snapshot['fps']),('game.exe',5))

    def test_explicit_target_works_even_if_it_starts_at_low_fps(self):
        monitor = self.monitor()
        monitor.feed_csv_line(
            'Application,ProcessID,SwapChainAddress,Dropped,msBetweenPresents',now=10)
        for _ in range(8):
            monitor.feed_csv_line('game.exe,43210,0xA,0,200',now=10)
            monitor.feed_csv_line('background.exe,43211,0xB,0,16.6667',now=10)
        snapshot = monitor.snapshot(now=10,fg_pid=43210,process_names=['game.exe'])
        self.assertEqual((snapshot['app'],snapshot['fps']),('game.exe',5))

    def test_prerelease_order_and_build_metadata(self):
        for latest, current, expected in [
            ('0.2.1','0.2.1-beta',True),
            ('0.2.1-beta','0.2.1',False),
            ('v0.2.1-rc.1','0.2.1-beta.10',True),
            ('0.2.1-beta.10','0.2.1-beta.2',True),
            ('0.2.1-beta.2','0.2.1-beta.10',False),
            ('0.2.1+build.2','0.2.1+build.1',False),
            ('0.2.1-beta+build.2','0.2.1-beta+build.1',False),
            ('0.2.2-beta','0.2.1',True),
            ('0.2.1','0.2.1.0',False),
        ]:
            with self.subTest(latest=latest,current=current):
                self.assertEqual(is_newer(latest,current),expected)

    def test_patch_tool_rejects_unknown_input_without_writing_or_backing_it_up(self):
        with tempfile.TemporaryDirectory(prefix='alp-patch-test-') as folder:
            target = Path(folder)/'PresentMon.exe'
            backup = Path(folder)/'PresentMon.exe.orig'
            for flush_bytes in (b'\x75\x08',b'\x90\x90'):
                data = bytearray(0x4000)
                data[0x3b09:0x3b0b] = flush_bytes
                target.write_bytes(data)
                with patch.object(PM,'BAK_PATH',str(backup)),contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(PM.check_status(str(target)),'unknown')
                    self.assertFalse(PM.apply_patch(str(target)))
                self.assertEqual(target.read_bytes(),data)
                self.assertFalse(backup.exists())

    def test_patch_tool_roundtrip_and_corrupt_backup_protection(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='alp-patch-test-') as folder:
            target = Path(folder)/'PresentMon.exe'
            backup = Path(folder)/'PresentMon.exe.orig'
            original = (root/'assets/PresentMon.exe.orig').read_bytes()
            target.write_bytes(original)
            with patch.object(PM,'BAK_PATH',str(backup)),contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(PM.apply_patch(str(target)))
                self.assertEqual(PM.get_sha256(str(target)),PM.PATCHED_SHA256)
                self.assertEqual(backup.read_bytes(),original)
                self.assertTrue(PM.revert_patch(str(target)))
                self.assertEqual(target.read_bytes(),original)
                backup.write_bytes(b'corrupted')
                self.assertFalse(PM.apply_patch(str(target)))
                self.assertFalse(PM.revert_patch(str(target)))
                self.assertEqual(target.read_bytes(),original)

    def test_self_test_resumes_capture_unless_user_disabled_it(self):
        for disable_during_probe in (False,True):
            with self.subTest(disable=disable_during_probe):
                monitor = self.monitor()
                # No executable is launched: only the self-test coordination runs.
                monitor._bin = str(Path(__file__).resolve())
                monitor._thread = Mock()
                monitor._thread.is_alive.return_value = True
                def stopped():
                    monitor._stop_evt.set()
                    monitor._thread.is_alive.return_value = False
                def probe(*args):
                    # A concurrent GUI start cannot obtain the lifecycle lock.
                    started = threading.Thread(target=monitor.start)
                    started.start()
                    started.join(1)
                    self.assertFalse(started.is_alive())
                    if disable_during_probe:
                        monitor.request_running(False)
                    return {'ok':True}
                with patch.object(monitor,'stop',side_effect=stopped), \
                     patch.object(monitor,'_run_self_test_inner',side_effect=probe), \
                     patch.object(monitor,'_start_locked') as start, \
                     patch('brb02.fps_monitor.tempfile.mkdtemp',return_value='__not_created__'):
                    self.assertTrue(monitor.run_self_test()['ok'])
                    self.assertEqual(start.call_count,0 if disable_during_probe else 1)
                monitor._bin = '__offline_missing_presentmon__'
                monitor.shutdown()


if __name__ == '__main__':
    unittest.main()
