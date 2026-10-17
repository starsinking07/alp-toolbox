# -*- coding: utf-8 -*-
"""温度诊断: 打印 LHM 暴露的全部温度/功耗传感器 + nvidia-smi 仲裁值。

用途: 用户反馈"CPU/GPU 温度不准"时, 让对方在**出问题的机器**上跑这个脚本,
把输出发回来即可定位是哪颗传感器、哪个数据源。

用法: python tools/dump_temps.py
"""
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LHM = os.path.join(ROOT, 'LHM')


def main():
    print('=' * 62)
    print(' 温度诊断 (LHM 传感器明细 + nvidia-smi 仲裁)')
    print('=' * 62)

    # ---- 1. LHM 全量传感器 ----
    try:
        import clr
        os.add_dll_directory(LHM)
        if LHM not in sys.path:
            sys.path.append(LHM)
        clr.AddReference('LibreHardwareMonitorLib')
        from LibreHardwareMonitor import Hardware
        import System
        ver = System.Reflection.Assembly.Load('LibreHardwareMonitorLib').GetName().Version
        print(f'\n[LHM] 版本 {ver}  ({LHM})')

        c = Hardware.Computer()
        c.IsCpuEnabled = True
        c.IsGpuEnabled = True
        c.Open()
        for hw in c.Hardware:
            ht = str(hw.HardwareType).replace('HardwareType.', '')
            if ht not in ('Cpu', 'GpuNvidia', 'GpuAmd', 'GpuIntel'):
                continue
            hw.Update()
            print(f'\n■ {ht}: {hw.Name}')
            temps, powers = [], []
            for s in hw.Sensors:
                st = str(s.SensorType)
                if 'Temperature' in st:
                    v = s.Value
                    ok = (v is not None and 1.0 < float(v) < 120.0)
                    flag = '' if ok else '   ← 无效值, 程序会跳过'
                    temps.append((str(s.Name), v, ok))
                    print(f'    [温度] {str(s.Name):<28} = {v}{flag}')
                elif st == 'Power' and s.Value is not None:
                    powers.append((str(s.Name), float(s.Value)))
                    print(f'    [功耗] {str(s.Name):<28} = {s.Value:.1f} W')
            # 复刻程序的选取规则
            if ht.startswith('Gpu'):
                core = [v for n, v, ok in temps
                        if ok and ('GPU Core' in n or 'GPU Temperature' in n)]
                print(f'    → 程序取值 (GPU Core) = {core[0] if core else "无"}')
            elif ht == 'Cpu':
                hits = [v for n, v, ok in temps
                        if ok and ('Tctl' in n or 'Tdie' in n or 'Core' in n or 'Package' in n)]
                print(f'    → 程序取值 (以上匹配项的最大值) = {max(hits) if hits else "无 (需管理员)"}')
    except Exception as e:
        print(f'\n[LHM] 读取失败: {e}')

    # ---- 2. nvidia-smi 仲裁 ----
    print('\n' + '-' * 62)
    smi = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'nvidia-smi.exe')
    if os.path.isfile(smi):
        try:
            r = subprocess.run([smi, '--query-gpu=name,temperature.gpu,power.draw',
                                '--format=csv'], capture_output=True, text=True, timeout=30)
            print('[nvidia-smi] 独立数据源:')
            for line in r.stdout.strip().split('\n'):
                print('   ', line)
        except Exception as e:
            print(f'[nvidia-smi] 调用失败: {e}')
    else:
        print('[nvidia-smi] 未安装 (非 NVIDIA 显卡?)')

    print('\n说明: 程序 GPU 温度取自 LHM 的 "GPU Core"; 若与 nvidia-smi 一致即准确。')
    print('      AIDA64 的 "GPU Diode" 与 nvidia-smi 可能存在数度偏差, 以 nvidia-smi 为准。')


if __name__ == '__main__':
    main()
