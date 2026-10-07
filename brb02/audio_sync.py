# -*- coding: utf-8 -*-
"""音频同步电平源 (0.1.8): WASAPI loopback 采集系统声音 → RMS 分档 (0-3) → 回调。

协议: 0x15 = `A5 05 15 [档位]` @约 4.9Hz, 档位 0=静音 1/2/3=渐强 (cap3 实测 0-3)。
依赖 pyaudiowpatch (WASAPI loopback); 不可用时 available=False。
"""
import threading, time

# RMS → 档位阈值 (16bit 归一化到 0..1)
TH_1, TH_2, TH_3 = 0.002, 0.012, 0.05
PUSH_HZ = 4.9


def _level_from_rms(rms: float) -> int:
    if rms < TH_1:
        return 0
    if rms < TH_2:
        return 1
    if rms < TH_3:
        return 2
    return 3


class AudioLevelPusher:
    """采集 + 分档 + 定时回调 (level 0-3)。回调在独立线程执行, 须线程安全。"""

    def __init__(self, level_cb, log=None):
        self.level_cb = level_cb
        self.log = log or (lambda s: None)
        self.available = True
        self._stop = threading.Event()
        self._t: threading.Thread | None = None

    def start(self):
        if self._t is not None:
            return
        self._stop.clear()
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def stop(self):
        self._stop.set()
        if self._t is not None:
            self._t.join(timeout=3)
            self._t = None

    def _run(self):
        try:
            import pyaudiowpatch as pyaudio
            import math
        except Exception as e:
            self.available = False
            self.log(f'[音频] pyaudiowpatch 不可用: {e}')
            return
        p = None
        try:
            p = pyaudio.PyAudio()
            try:
                dev = p.get_default_wasapi_loopback()
            except Exception:
                self.log('[音频] 未找到 WASAPI loopback 设备')
                return
            rate = int(dev['defaultSampleRate'])
            ch = min(2, int(dev['maxInputChannels']))
            frames = 1024
            stream = p.open(format=pyaudio.paInt16, channels=ch, rate=rate,
                            input=True, input_device_index=dev['index'],
                            frames_per_buffer=frames)
            self.log(f'[音频] loopback 采集: {dev["name"]} @{rate}Hz')
            last_push = 0.0
            cur_level = -1
            while not self._stop.is_set():
                data = stream.read(frames, exception_on_overflow=False)
                import struct as _s
                n = len(data) // 2
                if n == 0:
                    continue
                samples = _s.unpack(f'<{n}h', data[:n * 2])
                rms = math.sqrt(sum(s * s for s in samples) / n) / 32768.0
                level = _level_from_rms(rms)
                now = time.time()
                if level != cur_level or now - last_push >= 1.0 / PUSH_HZ:
                    cur_level = level
                    last_push = now
                    try:
                        self.level_cb(level)
                    except Exception as e:
                        self.log(f'[音频] 回调异常: {e}')
        except Exception as e:
            self.available = False
            self.log(f'[音频] 采集异常: {e}')
        finally:
            try:
                if p is not None:
                    p.terminate()
            except Exception:
                pass
