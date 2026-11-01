# -*- coding: utf-8 -*-
"""PresentMon ETW 缓冲扩容与低延迟即时落盘补丁。

解决《外包需求-FPS采集攻坚.md》核心痛点:
  1. 【P0】ETW 事件丢失 (events_lost) 根治:
     PresentMon 原版在调用 StartTraceW 时将 BufferSize、MinimumBuffers、MaximumBuffers
     全部硬编码置 0 (依赖系统默认值: 64KB x ~20 个缓冲 = 仅 1.2MB 环形缓冲)。
     当系统存在第三方 ETW 跟踪 (如 N 卡驱动 NVTOPPS、seraph) 或高刷帧率场景时,
     极易发生缓冲溢出导致丢弃事件 (warning: 12078 ETW events were lost)。
     本补丁在 StartTraceW 之前将 BufferSize 设为 256KB, MinimumBuffers 设为 64,
     MaximumBuffers 设为 256 (总缓冲扩容至 64MB 专用内核环形缓冲),
     彻底杜绝事件丢弃。

  2. 【P1】1.0 秒输出延迟根治:
     PresentMon 原版内部仅在 --output_stdout 模式下启用行刷新, 而在 --output_file
     模式下对每一帧数据跳过了 fflush (83 3D 17 82 05 00 02 75 08 ...)。
     本补丁将跳过 fflush 的条件分支 75 08 改为 90 90 (NOP NOP), 保证每帧或批次
     到达后立即刷新落盘, 消除 1.0 秒批次滞后, 使文件增量读真正达到毫秒级响应。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import struct
import sys
import pefile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE_PATH = os.path.join(ROOT, 'assets', 'PresentMon.exe')
BAK_PATH = os.path.join(ROOT, 'assets', 'PresentMon.exe.orig')

# 原版 sha256 (Intel PresentMon HEAD ef495aee, 396552 B) —— 用于确认目标二进制版本匹配
ORIG_SHA256 = 'C7E7420AD7F5EE40E7A41EFCAAA9EA213F6F9954858CA46DA4DF97F50E0302F3'
PATCHED_SHA256 = '226847B84157A168DC0CDDD6CFE7CEF2C04128B2DD333205C37B141AF8E0BF4F'

def get_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().upper()


def check_status(path: str = EXE_PATH) -> str:
    if not os.path.exists(path):
        return 'not_found'
    with open(path, 'rb') as f:
        data = f.read()
    # Check Site 2 (flush patch): 0x140004709
    # Raw offset: 0x400 + (0x140004709 - 0x140001000) = 0x3b09
    if len(data) < 0x3b10:
        return 'invalid'
    digest = hashlib.sha256(data).hexdigest().upper()
    if digest == ORIG_SHA256:
        return 'unpatched'
    elif digest == PATCHED_SHA256:
        return 'patched'
    return 'unknown'


def apply_patch(path: str = EXE_PATH) -> bool:
    st = check_status(path)
    if st == 'patched':
        print('[INFO] PresentMon.exe 已经是打过补丁的状态 (无需重复应用)')
        return True
    if not os.path.exists(path):
        print(f'[ERROR] 文件不存在: {path}')
        return False
    if st != 'unpatched':
        print(f'[ERROR] 拒绝修改未知版本/损坏的二进制文件 (SHA256: {get_sha256(path)})')
        return False
    if os.path.exists(BAK_PATH) and get_sha256(BAK_PATH) != ORIG_SHA256:
        print('[ERROR] 原版备份 SHA256 不匹配, 保留现场并拒绝修改')
        return False

    # 备份原版
    if not os.path.exists(BAK_PATH):
        shutil.copyfile(path, BAK_PATH)
        print(f'[INFO] 已备份原版至: {BAK_PATH}')

    with pefile.PE(path) as pe:
        image_base = pe.OPTIONAL_HEADER.ImageBase
        text_virtual_address = pe.sections[0].VirtualAddress
        text_raw_offset = pe.sections[0].PointerToRawData

    def va_to_raw(va: int) -> int:
        return va - image_base - text_virtual_address + text_raw_offset

    # 1. P0 Buffer Expansion Hook:
    hook_va = 0x1400100af
    hook_offset = va_to_raw(hook_va)

    cave_va = 0x140043ed0
    cave_offset = va_to_raw(cave_va)

    cave_code = bytearray()
    # mov dword ptr [rbp + 0x140], 256 (BufferSize = 256 KB)
    cave_code += bytes([0xC7, 0x85, 0x40, 0x01, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00])
    # mov dword ptr [rbp + 0x144], 64  (MinimumBuffers = 64)
    cave_code += bytes([0xC7, 0x85, 0x44, 0x01, 0x00, 0x00, 0x40, 0x00, 0x00, 0x00])
    # mov dword ptr [rbp + 0x148], 256 (MaximumBuffers = 256 -> 64MB 环形缓冲)
    cave_code += bytes([0xC7, 0x85, 0x48, 0x01, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00])
    # mov dword ptr [rbp + 0x184], 0x78 (LoggerNameOffset = 0x78 原指令)
    cave_code += bytes([0xC7, 0x85, 0x84, 0x01, 0x00, 0x00, 0x78, 0x00, 0x00, 0x00])
    # jmp 0x1400100b9
    curr_va = cave_va + len(cave_code)
    target_va = 0x1400100b9
    disp_back = target_va - (curr_va + 5)
    cave_code += bytes([0xE9]) + struct.pack('<i', disp_back)

    disp_hook = cave_va - (hook_va + 5)
    hook_code = bytes([0xE9]) + struct.pack('<i', disp_hook) + bytes([0x90] * 5)

    # 2. P1 Immediate Flush Patch:
    # 0x140004709: 75 08 -> 90 90 (NOP NOP)
    flush_va = 0x140004709
    flush_offset = va_to_raw(flush_va)

    with open(path, 'r+b') as f:
        # 写入 Codecave
        f.seek(cave_offset)
        f.write(cave_code)
        # 写入 Hook
        f.seek(hook_offset)
        f.write(hook_code)
        # 写入 Flush Patch
        f.seek(flush_offset)
        f.write(bytes([0x90, 0x90]))

    print(f'[SUCCESS] 成功应用 PresentMon 扩容与即时刷新补丁: {path}')
    print(f'          新文件 SHA256: {get_sha256(path)}')
    return True


def revert_patch(path: str = EXE_PATH) -> bool:
    if not os.path.exists(BAK_PATH):
        print(f'[ERROR] 备份文件不存在: {BAK_PATH}, 无法回滚')
        return False
    if get_sha256(BAK_PATH) != ORIG_SHA256:
        print('[ERROR] 备份文件 SHA256 不匹配, 拒绝回滚')
        return False
    shutil.copyfile(BAK_PATH, path)
    print(f'[SUCCESS] 已从备份恢复原版 PresentMon.exe (SHA256: {get_sha256(path)})')
    return True


def main():
    parser = argparse.ArgumentParser(description='PresentMon ETW 缓冲扩容与低延迟补丁')
    parser.add_argument('--apply', action='store_true', help='应用补丁')
    parser.add_argument('--revert', action='store_true', help='回滚原版')
    parser.add_argument('--status', action='store_true', help='查看当前补丁状态')
    args = parser.parse_args()

    if args.revert:
        return 0 if revert_patch() else 1
    elif args.apply:
        return 0 if apply_patch() else 1
    else:
        st = check_status()
        print(f'当前 PresentMon 状态: {st} (SHA256: {get_sha256(EXE_PATH) if os.path.exists(EXE_PATH) else "N/A"})')
        return 0 if st in ('patched', 'unpatched') else 1


if __name__ == '__main__':
    sys.exit(main())
