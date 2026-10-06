# -*- coding: utf-8 -*-
"""诊断包导出: manifest + 运行时快照 + 配置 + 日志 打包为 zip (模仿 Flydigi FanControl)。"""
from __future__ import annotations

import json
import os
import zipfile
from dataclasses import asdict
from datetime import datetime

APP_VERSION = '0.1.8'


def export_diagnostics(cfg, worker, log_text: str, out_path: str) -> str:
    """收集运行状态打包为诊断 zip, 返回写入的文件路径。"""
    manifest = {
        'appName': 'Alp 工具箱',
        'version': APP_VERSION,
        'generatedAt': datetime.now().astimezone().isoformat(timespec='seconds'),
        'os': 'windows',
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
        'deviceRpm': (worker._last_status or {}).get('rpm'),
        'cpuTemp': t.cpu, 'gpuTemp': t.gpu,
        'cpuPower': t.cpu_power, 'gpuPower': t.gpu_power,
        'cpuName': t.cpu_name, 'gpuName': t.gpu_name,
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
