# -*- coding: utf-8 -*-
"""0xC3 参数页布局读回 (0xC2 的读回对) —— 离线单测 + 真机读取。

用法:
  python tools/check_lcd_c3.py            # 仅离线单测 (不碰设备)
  python tools/check_lcd_c3.py --live     # 连接设备读一次 0xC3

背景: 固件 FUN_0001b496 从 0x18860+1 拷 15B, 与 0xC2 同源 (2026-10-08 固件线实证)。
实测应答 (三次逐字节恒定): 00 00 03 00 6E 00 68 01 D7 00 68 07 26 01 68
  -> pos=0, count=3, items=[(0,110,104),(1,215,104),(7,294,104)] (id=官方默认 CPU温/GPU温/时间)

完整 D3 实验 (写两条不同布局 → 读 0xC3 看是否跟随) 见 tools/verify_crosscheck.py --only D3。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brb02 import protocol as pc

FAILS = []


def check(name, cond, detail=''):
    tag = 'PASS' if cond else 'FAIL'
    print(f'  [{tag}] {name}' + (f'  ({detail})' if detail else ''))
    if not cond:
        FAILS.append(name)


# ---- 实测样本 (PROTOCOL §3.1 盲扫 + §3.3) ----
SAMPLE_15 = bytes.fromhex('000003' '006E0068' '01D70068' '07260168')


def offline():
    print('== 离线单测 ==')
    # 1. 请求帧形态: A5 03 C3 6B (空载荷短形态)
    f = pc.get_lcd_show_pos()
    check('请求帧 = A5 03 C3 6B', f == bytes.fromhex('A503C36B'), f.hex(' ').upper())
    check('LEN=3 短形态', f[1] == 0x03, f'LEN={f[1]}')
    check('cmd=0xC3', f[2] == 0xC3)
    check('累加和自洽', pc.checksum(f[:-1]) == f[-1], f'CK={f[-1]:02X}')

    # 2. 解析实测 15B 样本
    r = pc.parse_lcd_show_pos(SAMPLE_15)
    check('解析非空', r is not None)
    if r:
        check('pos=0', r['pos'] == 0, str(r['pos']))
        check('count=3', r['count'] == 3, str(r['count']))
        check('items=[(0,110,104),(1,215,104),(7,294,104)]',
              r['items'] == [(0, 110, 104), (1, 215, 104), (7, 294, 104)],
              str(r['items']))
        check('id=[0,1,7] 官方默认', [it[0] for it in r['items']] == [0, 1, 7])

    # 3. 尾字节为 CRC16 (非累加和) 时 parse_frame 会保留 -> 解析器须容忍多 1 字节
    r16 = pc.parse_lcd_show_pos(SAMPLE_15 + bytes([0x3B]))
    check('容忍尾字节 (16B) 结果不变',
          r16 is not None and r16['items'] == [(0, 110, 104), (1, 215, 104), (7, 294, 104)])

    # 4. 边界: 太短 / count=0
    check('长度<3 返回 None', pc.parse_lcd_show_pos(b'\x00\x00') is None)
    r0 = pc.parse_lcd_show_pos(bytes.fromhex('000000'))
    check('count=0 -> items=[]', r0 is not None and r0['items'] == [] and r0['count'] == 0)

    # 5. Cmd 常量登记
    check('Cmd.GET_LCD_SHOW_POS=0xC3', pc.Cmd.GET_LCD_SHOW_POS == 0xC3)

    print(f'-- 离线单测: {"全过 ✓" if not FAILS else "有失败 ✗ " + str(FAILS)} --')
    return not FAILS


def live():
    print('== 真机读取 (0xC3) ==')
    from brb02.device import Brb02Device
    dev = Brb02Device('usb')
    if not dev.connect():
        print('  设备未连接 (USB), 跳过真机读取')
        return True
    try:
        rb = dev.get_lcd_show_pos()
        if rb is None:
            print('  0xC3 无应答 (设备可能未就绪)')
            return False
        ids = [it[0] for it in rb['items']]
        print(f"  应答: pos={rb['pos']} count={rb['count']}")
        for i, (pid, x, y) in enumerate(rb['items']):
            name = dict(pc.LCD_PARAM_DEFS).get(pid, '?')
            print(f'    格{i + 1}: id={pid} ({name}) x={x} y={y}')
        print(f'  id={ids}')
        return True
    finally:
        dev.disconnect()


if __name__ == '__main__':
    ok = offline()
    if '--live' in sys.argv:
        ok = live() and ok
    sys.exit(0 if ok else 1)
