# -*- coding: utf-8 -*-
"""诊断包导出: manifest + 运行时快照 + 配置 + 日志 打包为 zip (模仿 Flydigi FanControl)。"""
from __future__ import annotations

import json
import os
import platform
import time
import zipfile
from dataclasses import asdict
from datetime import datetime

APP_VERSION = '0.1.18'


def export_diagnostics(cfg, worker, log_text: str, out_path: str) -> str:
    """收集运行状态打包为诊断 zip, 返回写入的文件路径。"""
    manifest = {
        'appName': 'Alp 工具箱',
        'version': APP_VERSION,
        'generatedAt': datetime.now().astimezone().isoformat(timespec='seconds'),
        'os': 'windows',
        'osBuild': f'{platform.release()} {platform.version()}',   # 驱动黑名单与系统版本强相关
        'installType': 'exe' if getattr(__import__('sys'), 'frozen', False) else 'source',
        'privacyNote': '文件含散热器蓝牙地址与设备曲线, 包含本机文件路径 (如上次选图路径与临时目录), '
                       '公开分享前请自行斟酌',
    }
    t = worker.temps
    runtime = {
        'connected': worker.device.connected,
        'channel': worker.device.conn_type,
        'bleAddress': worker.device.ble_address,
        'tecAutoEnabled': cfg.tec_auto_enabled,
        'tecLevel': worker._tec_level,
        'lastSentRpm': worker._last_sent_rpm,
        'lastCmdRpm': getattr(worker, '_last_cmd_rpm', None),   # 实际下发值 (含补偿)
        'rpmTrimMode': getattr(cfg, 'rpm_trim_mode', 'off'),
        'rpmTrim': (getattr(cfg, 'rpm_trim_offset', 0)
                    if getattr(cfg, 'rpm_trim_mode', 'off') == 'fixed'
                    else getattr(worker, '_rpm_trim', 0)),
        'deviceRpm': (worker._last_status or {}).get('rpm'),
        'statusAgeS': (round(time.time() - getattr(worker, '_last_status_ts', 0), 1)
                       if getattr(worker, '_last_status_ts', 0) else None),
        'cpuTemp': t.cpu, 'gpuTemp': t.gpu,
        'cpuPower': t.cpu_power, 'gpuPower': t.gpu_power,
        'cpuName': t.cpu_name, 'gpuName': t.gpu_name,
        # 温度链路诊断 (v0.1.16): CPU 温度投诉曾因缺这几项无法定位
        'isAdmin': getattr(t, 'is_admin', None),
        'pawnioVersion': getattr(t, 'pawnio_version', None),
        'lhmVersion': getattr(t, 'lhm_version', ''),
        'cpuReason': getattr(t, 'cpu_reason', None),
        'lhmError': getattr(t, 'lhm_error', '') or None,
        'historySamples': len(worker.history),
    }
    try:
        fw = worker.device.get_firmware_version()
        runtime['firmware'] = fw
    except Exception:
        runtime['firmware'] = None

    files = {
        'manifest.json': json.dumps(manifest, ensure_ascii=False, indent=1),
        'runtime-snapshot.json': json.dumps(runtime, ensure_ascii=False, indent=1),
        'config.json': json.dumps(asdict(cfg), ensure_ascii=False, indent=1),
        'logs/app.log': log_text or '(空)',
    }
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(name, text)
    return out_path
