# -*- coding: utf-8 -*-
"""BRB02 风神Pro 散热器协议层 (v3.0.3 固件, 全部经实机验证)。

=== 帧格式 (2026-10 实测确认) ===
  下行/上行: [A5][LEN][CMD][params...][CK] + 0x00 填充至 65 字节
  - LEN = 帧总长 (含 A5、LEN、CMD、CK 所有字节!)
  - CK  = 前面所有字节累加和 & 0xFF
  - USB 传输: libusb 中断传输, EP OUT=0x01 / IN=0x81, 65 字节
  - 设备: VID 0xE2B7 PID 0x7001 (杰理芯片), HID 接口

=== 关键行为 ===
  - 设备每 ~500ms 主动上报状态 (cmd 0x06): 转速/标志
  - 大多数写/查询命令有 5 字节 ACK: [A5][05][CMD][status][tail]
  - ⚠️ 禁止发送 LEN 与实际长度不符、或含非法曲线锚点的帧 —— 会被设备
    存入 flash 并导致开机循环崩溃 (本次联调踩过的坑, 靠恢复出厂救回)

=== 命令总表 (✓=已实测, △=静态分析待验) ===
"""
from __future__ import annotations

import struct
from typing import Callable, Optional

PKT_SIZE = 65
HEADER = 0xA5

# ---- 已验证命令号 ----
class Cmd:
    ISSUE_SYSTEM_INFO      = 0x07   # ✓ 主机参数推送 (屏幕参数页数据源, 见 build_host_info)
    SYSTEM_INFO_REPORT     = 0x06   # ✓ 设备主动上报: [06][rpm_lo][rpm_hi][flag][ck]
    GET_FIRMWARE_VERSION   = 0xC1   # ✓ [A5][04][C1][6A]
    GET_RGB_SWITCH         = 0x11   # ✓ [A5][04][11][BA] -> [05][11][on][x]
    SET_COOLING_CONFIG     = 0x24   # ✓ 固定9字节 / 曲线19字节
    GET_CUR_COOLING_CONFIG = 0x25   # ✓ -> [A5][09][25][00][00][01][rpm16][pct]
    GET_ANY_COOLING_CONFIG = 0x26   # ✓ +slot -> 19字节曲线
    GET_CUR_RGB_EFFECTS    = 0x13   # ✓ -> 9字节数据: [配置头5B][R][G][B][动态1B]
    GET_ANY_RGB_EFFECTS    = 0x14   # ✓ +idx -> 12字节
    SET_RGB_EFFECTS        = 0x12   # ✓ (2026-10-02 USBPcap 实测) 参数=GET前5字节原样+RGB
    GET_LCD_SCREEN_SWITCH  = 0x6A   # ✓ -> [05][6A][on][x]
    RESTORE_FACTORY        = 0xF0   # ✓ [A5][04][F0][99] (rescue 用过)
    ENTER_BOOT_MODE        = 0xF5   # ✓ [A5][04][F5][9E] (升级引导)
    GET_FIRMWARE_STRING    = 0x01   # ✓ [A5][03][01][A9] -> 完整版本串


def checksum(body: bytes) -> int:
    """累加和校验 (不含 CK 自身)"""
    return sum(body) & 0xFF


def build_frame(cmd: int, params: bytes = b'') -> bytes:
    """构建完整帧: [A5][LEN][CMD][params][CK], LEN=总长。调用方负责 pad 到 65。"""
    body = bytes([HEADER, 3 + len(params) + 1, cmd]) + params
    return body + bytes([checksum(body)])


def parse_frame(rx: bytes) -> Optional[tuple[int, bytes]]:
    """解析 65 字节包 -> (cmd, data)。

    尾字节歧义处理: 先按累加和自检 —— 对得上则尾字节是校验 (裁掉),
    对不上则尾字节是数据 (保留, 如 0x25 应答的百分比、版本应答的尾部字节)。"""
    if not rx or len(rx) < 4 or rx[0] != HEADER:
        return None
    payload = rx.rstrip(b'\x00')
    if len(payload) < 4:
        return None
    ln = payload[1]
    if ln < 4 or ln > len(payload):
        return None
    frame = payload[:ln]
    cmd = frame[2]
    rest = frame[3:]
    if rest and checksum(frame[:-1]) == frame[-1]:
        rest = rest[:-1]
    return cmd, rest


# ---- 高层构建器 (全部实测) ----
# 注意: 部分查询帧用 LEN=3 短形态 (实测本固件接受并返回完整数据),
# 与 DLL 静态模板 (LEN=4) 略有出入, 以实机验证结果为准。

def get_firmware_version() -> bytes:
    """实测: -> [A5][09][01]['3.0.3'][tail]"""
    return bytes([HEADER, 0x03, 0x01, checksum(bytes([HEADER, 0x03, 0x01]))])


def get_rgb_switch() -> bytes:
    """DLL 模板帧 [A5][04][11][BA]"""
    return bytes([HEADER, 0x04, 0x11, 0xBA])


def get_cur_cooling() -> bytes:
    """实测: -> [A5][09][25][00][00][01][rpm16][pct]"""
    return bytes([HEADER, 0x03, 0x25, checksum(bytes([HEADER, 0x03, 0x25]))])


def get_any_cooling(slot: int) -> bytes:
    """slot 1..4, 实测: -> 19 字节曲线"""
    body = bytes([HEADER, 0x06, Cmd.GET_ANY_COOLING_CONFIG, 0x01, 0x01, slot])
    return body + bytes([checksum(body)])


def get_cur_rgb() -> bytes:
    """实测: -> 9 字节数据 (parse_rgb_effect)"""
    return bytes([HEADER, 0x03, 0x13, checksum(bytes([HEADER, 0x03, 0x13]))])


def get_any_rgb(idx: int) -> bytes:
    body = bytes([HEADER, 0x04, Cmd.GET_ANY_RGB_EFFECTS, idx])
    return body + bytes([checksum(body)])


def get_lcd_switch() -> bytes:
    return bytes([HEADER, 0x03, 0x6A, checksum(bytes([HEADER, 0x03, 0x6A]))])


def set_cooling_fixed(rpm: int, level: int = 1) -> bytes:
    """固定转速 + 制冷档位 (实测可用)。
    帧: [A5][09][24][00][00][level][rpm16][ck]
    ⚠️ 第三参数是制冷档位 01-04 (同时决定 TEC 强度与风扇上限, L4 曲线上限 4000),
    不是开关; rpm 范围随档位不同 (L1 上限 2800, L4 曲线上限 4000)。"""
    rpm = max(0, min(4800, int(rpm)))
    level = max(1, min(4, int(level)))
    return build_frame(Cmd.SET_COOLING_CONFIG,
                       bytes([0x00, 0x00, level, rpm & 0xFF, (rpm >> 8) & 0xFF]))


def set_cooling_curve(anchors: list[tuple[int, int]], level: int = 1) -> bytes:
    """智能变频曲线: 4 个 (温度°C, 转速RPM) 锚点, 写入指定档位 (01-04) 的曲线槽。
    帧: [A5][13][24][00][01][level][t1][rpm1_16][t2][rpm2_16]...[t4][rpm4_16][C8][ck]
    ⚠️ 锚点必须单调合法, 否则会写坏设备配置导致开机循环!"""
    assert len(anchors) == 4, '需要 4 个锚点'
    params = bytes([0x00, 0x01, max(1, min(4, int(level)))])
    for t, rpm in anchors:
        params += bytes([t & 0xFF, rpm & 0xFF, (rpm >> 8) & 0xFF])
    params += bytes([0xC8])
    return build_frame(Cmd.SET_COOLING_CONFIG, params)


def restore_factory() -> bytes:
    """恢复出厂设置 (抢救用)"""
    return build_frame(Cmd.RESTORE_FACTORY)


def enter_boot_mode() -> bytes:
    """进入升级引导 (固件重刷前置)"""
    return build_frame(Cmd.ENTER_BOOT_MODE)


# ---- 解析器 ----

def parse_status_report(data: bytes) -> Optional[dict]:
    """cmd 0x06 自动上报: data = [rpm_lo][rpm_hi][flag]"""
    if len(data) < 3:
        return None
    return {'rpm': data[0] | (data[1] << 8), 'flag': data[2]}


def parse_cur_cooling(data: bytes) -> Optional[dict]:
    """cmd 0x25 应答: data = [mode][x][on][rpm_lo][rpm_hi][pct]。
    ⚠️ pct 实测为 0~255 原始量 (观测 127~239), 并非 0~100 百分比;
    疑似 TEC/负载占空比, 确切语义未解。key 名保留 percent 以兼容历史调用。"""
    if len(data) < 6:
        return None
    return {'mode': data[0], 'on': data[2],
            'rpm': data[3] | (data[4] << 8), 'percent': data[5]}


def parse_firmware_version(data: bytes) -> str:
    """cmd 0x01 应答: ASCII 版本串 (如 3.0.3), 可能带尾部杂字节"""
    import re
    m = re.search(rb'\d+\.\d+\.\d+', data)
    return m.group().decode() if m else ''


def parse_curve(data: bytes) -> Optional[list[tuple[int, int]]]:
    """cmd 0x26 应答 data[3..14]: 4×(温度, 转速16)"""
    if len(data) < 15:
        return None
    pts = []
    for i in range(4):
        t = data[3 + i * 3]
        rpm = data[4 + i * 3] | (data[5 + i * 3] << 8)
        pts.append((t, rpm))
    return pts


def parse_rgb_effect(data: bytes) -> Optional[dict]:
    """cmd 0x13 应答 (9 字节数据): [模式][速度16LE][亮度][单彩][R][G][B][动态1B]。
    字段语义 2026-10-02 USBPcap 抓官方软件实测。"""
    if len(data) < 9:
        return None
    return {'mode': data[0],
            'speed': data[1] | (data[2] << 8),
            'brightness': data[3],
            'color_mode': data[4],
            'rgb': (data[5], data[6], data[7]),
            'extra': data[8]}


# 灯效模式 ID (全部实测: 用户切换各模式时 USBPcap 抓取 0x12 帧)
RGB_MODE_FLOW = 0x11        # 彩色流动
RGB_MODE_CYCLE = 0x02       # 彩色循环
RGB_MODE_BREATH = 0x03      # 呼吸
RGB_MODE_STEADY = 0x04      # 常亮
RGB_MODE_BLINK = 0x05       # 闪烁
RGB_MODE_REACTIVE = 0x08    # 响应
RGB_MODE_REFRESH = 0x06     # 刷新
RGB_MODE_AUDIO = 0x07       # 音频同步 (需另以 ~5Hz 推 0x15 电平流, 未实现)
RGB_COLOR_SINGLE = 0x01     # 单色
RGB_COLOR_MULTI = 0x0A      # 彩色


def set_rgb_effect(mode: int, speed: int, brightness: int,
                   color_mode: int, rgb: tuple[int, int, int]) -> bytes:
    """完整灯效写入: [A5][0C][12][模式][速度16LE][亮度][单彩][R][G][B][CK]。
    speed = 周期 ms (官方滑条范围 1000~5000, 左慢右快); brightness 0~100;
    color_mode 01=单色 0a=彩色; rgb = 色调 (单色模式下的颜色)。
    ⚠️ 音频同步(0x07)需要配套 0x15 电平流, GUI 暂不提供该模式。"""
    r, g, b = rgb
    params = bytes([mode & 0xFF, speed & 0xFF, (speed >> 8) & 0xFF,
                    max(0, min(100, int(brightness))) & 0xFF,
                    color_mode & 0xFF, r & 0xFF, g & 0xFF, b & 0xFF])
    return build_frame(Cmd.SET_RGB_EFFECTS, params)


def set_rgb_color(cfg_head: bytes, rgb: tuple[int, int, int]) -> bytes:
    """挡位灯: 写当前灯效颜色。

    真格式 (2026-10-02 USBPcap 抓官方软件实测确认):
      [A5][0C][12][cfg_head 5B 原样][R][G][B][CK]
    cfg_head = 最近一次 GET 0x13 应答的前 5 字节。
    颜色是当前灯效 (流动/呼吸等) 的色调, 换挡联动即改这 3 字节。
    ⚠️ DLL 里的 nibble 打包版本 (0x1000E4A0) 是旧固件遗留, 3.0.3 会无视, 勿用。"""
    r, g, b = rgb
    params = bytes(cfg_head[:5]) + bytes([r & 0xFF, g & 0xFF, b & 0xFF])
    return build_frame(Cmd.SET_RGB_EFFECTS, params)


def build_host_info(entries: list[tuple[int, int]]) -> bytes:
    """0x07 主机参数推送 —— 散热器屏幕参数页的唯一数据源, 官方 1-9Hz 持续推;
    第三方从不推则参数页纯白 (本工具箱白屏问题的根因)。

    帧: [A5][1A][07][项数] + 项数×[ID][值lo][值hi] + [CK], 官方 7 项 = 26 字节。
    ID 语义 (cap6 152s/153 帧 USBPcap 实测, tools/analyze_07_ids.py):
      00=CPU温(73-78) 01=CPU功(42-43, 疑, 备选负载%) 02=GPU功(17-25, 疑, 备选负载%)
      03=未明(0-13 跳动, 疑磁盘) 04=官方跳过不发 05=未明(恒74, 疑内存%)
      06=GPU温(52-54) 07=时钟=当日分钟数 (1108 = 18:28 与抓包时刻吻合; 屏幕按
      值/60:值%60 渲染, 误发 HHMM 会显示 PM 33:58, 用户实测定案)。
    ⚠️ 早期抓包曾误判为 5×f32 全零保活信封 —— 值全零时两种视图字节相同,
    cap6 真值帧定案为 [ID][值16LE] 结构。"""
    params = bytes([len(entries) & 0xFF])
    for k, v in entries:
        v = max(0, min(0xFFFF, int(v)))
        params += bytes([k & 0xFF, v & 0xFF, (v >> 8) & 0xFF])
    return build_frame(Cmd.ISSUE_SYSTEM_INFO, params)


def set_control_source(src: int = 0x01) -> bytes:
    """0x22 控制源 set (实测; 01=PC 主机)。官方会话开场发一次, replay_07 同款前置。"""
    return build_frame(0x22, bytes([src & 0xFF]))
