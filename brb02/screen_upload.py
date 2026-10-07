# -*- coding: utf-8 -*-
"""屏幕图片上传构建器 —— 纯构建, **不碰设备** (执行器由 device/service 层调用)。

协议来源: cap8 官方成功上传实录 (USBPcap), 全部逐字节复核。

═══════════════════════════════════════════════════════════════════════
官方成功流 (cap8 会话1, 2114 帧 OUT, ~16.32s)
═══════════════════════════════════════════════════════════════════════
  C4  (A5 0F C4 [11B 元数据] CK)              握手, 设备 ~530ms 后回 ACK
  A4-40 ×2095                                 数据块, 块号 2096→2 递减
  A4-30 ×1                                    尾帧(末段短块), 块号 1
  A5 1A 07 ×16                                尾部心跳 (1Hz 定时器积压冲刷)
  C5  (A5 04 C5 6E)                           提交, 设备回 15B 元数据回显

块结构 (线上 64B 包):
  A4 40 [号低] [号高&0x0F | 0x80(仅首块)] C6 [58B 数据] [CK@63=sum(b[0:63])&0xFF]
  - byte1 = 0x40 = 64 = **本帧长度**(含 CK, 不含线上零填充) —— 巧合? 见尾帧同规律
  - byte4 恒 0xC6 (标记)
  - 块号从 2096 递减到 2

尾帧结构 (线上 64B 包, 实为 48B 帧 + 零填充):
  A4 30 01 00 C6 [42B 数据] [CK@47=sum(b[0:47])&0xFF] 00 00 ... 00
  - byte1 = 0x30 = 48 = 本帧长度 (5 头 + 42 数据 + 1 CK)
  - **尾帧 = 画布末段 "短块"**: 官方主机把画布按 58B/块切分, 2095 个满块后余 42B,
    这 42B 就是尾帧载荷 (块号 1)。这正是它命令字 0x40→0x30、帧长 64→48 的原因。

═══════════════════════════════════════════════════════════════════════
画布尺寸 = 428×142×2 = 121,552B  (= 元数据[4:8] LE u32 = 0x0001DAD0)
═══════════════════════════════════════════════════════════════════════
  - 块流 2095×58 = 121,510B = 419×145×2 …… 但 **这不是整张图**!
  - 121,552 - 121,510 = **42B** = 尾帧载荷 = 画布最后 21 个像素。
  - 判定依据 (cap8 实测, 两会话):
      ① meta[4:8] = 121,552 = 428×142×2 (干净整除); 419×145 无法整除 121,552。
      ② meta[8:10] = **crc16_XMODEM(整张 121,552B 画布)** —— CRC 覆盖 121,552B 而非
         121,510B, 说明提交给设备的是完整 121,552B。
      ③ 尾帧载荷 = 真实图像像素 (红图 21×F8 00 / 绿图 21×07 E0), **不是零填充**;
         若主机给 121,510B 图补零到 121,552B, 尾帧应是 00, 实测不是 → 主机画布本就是
         121,552B (官方软件把用户图缩放到 428×142)。
  - 像素格式: RGB565 **大端** (红 F8 00 / 绿 07 E0)。

═══════════════════════════════════════════════════════════════════════
时序 = 0x0C 的根因 (v3.8/v3.9 破案, v23 设备验证通过)
═══════════════════════════════════════════════════════════════════════
官方块流**并非均匀**: 基速 ~7.0ms/帧, 但**每跨一个 4096B flash 页边界停顿 ~40ms**,
全图共 **29 处** (121,510B = 29.66 页)。规则可确定性生成:
    在第 k 个数据块之后停顿  <=>  (58·k)//4096 > (58·(k-1))//4096
实测停顿块序号 (29 处, 与规则完全一致):
  71,142,212,283,354,424,495,565,636,707,777,848,919,989,1060,1130,
  1201,1272,1342,1413,1484,1554,1625,1695,1766,1837,1907,1978,2048
均匀流会在第一个页边界 (~71 块) 把设备页缓冲打穿 → 固件此后每块回 C6 状态 0x0C
(官方全程 0x00)。**回放绝不能抹平这些停顿** —— 这是上传成功的唯一关键变量。

尾部: 末块 →7ms→ A4-30 →63.5ms→ 16×心跳(6ms 间隔) →6ms→ C5。

用法:
    from brb02.screen_upload import image_to_rgb565_be, build_upload
    data = image_to_rgb565_be(r'<图片路径>.png')   # 121,552B (428×142 RGB565BE)
    frames = build_upload(data)                    # list[UploadFrame], 带 at_ms 时刻表
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from . import protocol

# ─────────────────────────── 常量 (cap8 实测) ───────────────────────────
CANVAS_W, CANVAS_H = 428, 142             # 设备画布 (== meta[4:8]/2 的干净因数分解)
CANVAS_BYTES = CANVAS_W * CANVAS_H * 2    # 121,552
BLOCK_DATA = 58                           # 每满块有效数据字节
BLOCK_COUNT = CANVAS_BYTES // BLOCK_DATA  # 2095 (满块数; 余 42B 走尾帧)
STREAM_BYTES = BLOCK_COUNT * BLOCK_DATA   # 121,510 (块流总量 = 2095×58)
TAIL_BYTES = CANVAS_BYTES - STREAM_BYTES  # 42 (尾帧载荷 = 画布末段短块)
WIRE_LEN = 64                             # 线上包长 (零填充)
FLASH_PAGE = 4096                         # 设备 flash 页 (停顿规则的分母)

# 兼容旧名 (曾误用 419×145/121,510B 当作整张图; 见模块 docstring 判定依据)
IMG_W, IMG_H = CANVAS_W, CANVAS_H
IMG_BYTES = CANVAS_BYTES

BASE_MS = 7.0                             # 帧基速 (cap8 实测 2094 间隔均值 6.995 / 中位 6.96)
PAGE_PAUSE_MS = 40.0                      # 每跨 4096B 页边界的停顿
ACK_WAIT_MS = 532.6                       # C4 -> ACK (cap8 实测)
FIRST_BLOCK_AFTER_ACK_MS = 4.2            # ACK -> 首块 (cap8 实测 4.2 / 会话2 9.9)
LAST_BLOCK_TO_TAIL_MS = 7.0               # 末数据块 -> A4-30
TAIL_TO_HB_MS = 63.5                      # A4-30 -> 首心跳
HB_GAP_MS = 6.0                           # 心跳间隔
HB_TO_C5_MS = 6.0                         # 末心跳 -> C5
HB_COUNT = 16

CMD_C4 = 0xC4
CMD_C5 = 0xC5
BLK_CMD_DATA = 0x40                       # 满块 (帧长 64)
BLK_CMD_TAIL = 0x30                       # 末段短块 (帧长 48)
BLK_MARK = 0xC6

# 红图元数据 (cap8 会话1 实录)。meta[4:8]=画布字节数(121,552); meta[8:10]=crc16_XMODEM(画布);
# meta[0] 公式未解, 但 v22/v3.10 已实锤 **设备不绑内容** → 任意上传恒用此原值。
META_RED = bytes.fromhex('18 de c0 6a d0 da 01 00 e6 9b 00')


def crc16_xmodem(data: bytes) -> int:
    """CRC-16/XMODEM: poly 0x1021, init 0x0000, 输入/输出不反转, 无 xorout。

    本会话破解: meta[8:10] (LE u16) = 该值 (整张 121,552B 画布), cap8 两会话均命中。
    """
    crc = 0x0000
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def meta_for(image_bytes: bytes, template: bytes = META_RED) -> bytes:
    """按画布重算 meta[4:8](=len) 与 meta[8:10](=crc16_XMODEM), 其余沿用 template。

    ⚠️ meta[0] 公式仍未解, 只能沿用 template[0] (v22 已证设备忽略该字节)。
    默认上传路径**无需**本函数 (恒用 META_RED); 仅在想要"字段自洽"的 meta 时使用。
    """
    if len(image_bytes) != CANVAS_BYTES:
        raise ValueError(f'画布长度必须 {CANVAS_BYTES}, 实际 {len(image_bytes)}')
    if len(template) != 11:
        raise ValueError(f'模板必须 11B, 实际 {len(template)}')
    m = bytearray(template)
    m[4:8] = len(image_bytes).to_bytes(4, 'little')
    m[8:10] = crc16_xmodem(image_bytes).to_bytes(2, 'little')
    return bytes(m)

# cap8 会话1 尾部 16 条心跳的 (ID, 值) 序列 (官方实录, 含主机实时温度/功耗/时钟)。
# 存 entries 而非裸帧: 由 protocol.build_host_info 重建, 已逐字节校验 16/16 全等。
# 生产集成应传自己的实时 0x07 帧 (见 heartbeats 参数)。
CAP8_TAIL_HB_ENTRIES: list[list[tuple[int, int]]] = [
    [(0x00, 78), (0x01, 46), (0x02, 20), (0x03, 0), (0x05, 74), (0x06, 52), (0x07, 1131)],
    [(0x00, 78), (0x01, 46), (0x02, 19), (0x03, 0), (0x05, 74), (0x06, 52), (0x07, 1131)],
    [(0x00, 78), (0x01, 46), (0x02, 19), (0x03, 0), (0x05, 74), (0x06, 52), (0x07, 1131)],
    [(0x00, 78), (0x01, 46), (0x02, 19), (0x03, 0), (0x05, 74), (0x06, 52), (0x07, 1131)],
    [(0x00, 78), (0x01, 46), (0x02, 19), (0x03, 0), (0x05, 74), (0x06, 52), (0x07, 1131)],
    [(0x00, 78), (0x01, 46), (0x02, 19), (0x03, 0), (0x05, 74), (0x06, 52), (0x07, 1131)],
    [(0x00, 79), (0x01, 46), (0x02, 21), (0x03, 0), (0x05, 74), (0x06, 52), (0x07, 1131)],
    [(0x00, 79), (0x01, 46), (0x02, 21), (0x03, 0), (0x05, 74), (0x06, 52), (0x07, 1131)],
    [(0x00, 79), (0x01, 46), (0x02, 21), (0x03, 0), (0x05, 74), (0x06, 52), (0x07, 1131)],
    [(0x00, 79), (0x01, 46), (0x02, 21), (0x03, 0), (0x05, 74), (0x06, 52), (0x07, 1131)],
    [(0x00, 79), (0x01, 46), (0x02, 23), (0x03, 0), (0x05, 74), (0x06, 52), (0x07, 1131)],
    [(0x00, 79), (0x01, 46), (0x02, 23), (0x03, 0), (0x05, 74), (0x06, 52), (0x07, 1131)],
    [(0x00, 79), (0x01, 46), (0x02, 23), (0x03, 0), (0x05, 74), (0x06, 52), (0x07, 1131)],
    [(0x00, 79), (0x01, 46), (0x02, 23), (0x03, 0), (0x05, 74), (0x06, 52), (0x07, 1131)],
    [(0x00, 79), (0x01, 46), (0x02, 23), (0x03, 0), (0x05, 74), (0x06, 52), (0x07, 1131)],
    [(0x00, 79), (0x01, 46), (0x02, 23), (0x03, 0), (0x05, 74), (0x06, 52), (0x07, 1131)],
]


# ─────────────────────────── 数据结构 ───────────────────────────
@dataclass(frozen=True)
class UploadFrame:
    """一帧上传数据 + 计划时刻。"""
    payload: bytes      # 线上 64B 帧 (已零填充)
    at_ms: float        # 相对 C4 发送时刻的计划偏移 (ms); C4 自身 = 0.0
    kind: str           # 'C4' | 'A4-40' | 'A4-30' | 'HB' | 'C5'

    def __len__(self) -> int:
        return len(self.payload)


# ─────────────────────────── 图像 → RGB565 大端 ───────────────────────────
def _canvas_bytes(canvas) -> bytes:
    """RGB888 QImage (428×142) → RGB565 **大端** 121,552B (逐行按 bytesPerLine 取)。

    428/142 均为偶数, 但 QImage 扫描行仍可能 4B 对齐 → 一律按 bytesPerLine 逐行取。
    """
    stride = canvas.bytesPerLine()
    out = bytearray(CANVAS_BYTES)
    p = 0
    for y in range(CANVAS_H):
        line = bytes(canvas.constScanLine(y))[:stride]
        for x in range(CANVAS_W):
            r = line[x * 3]
            g = line[x * 3 + 1]
            b = line[x * 3 + 2]
            v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            out[p] = (v >> 8) & 0xFF                  # 大端: 高字节在前
            out[p + 1] = v & 0xFF
            p += 2
    return bytes(out)


def qimage_to_rgb565_be(img, fit: str = 'stretch',
                        zoom: int = 100, pan_x: int = 0, pan_y: int = 0) -> bytes:
    """内存 QImage → 缩放 428×142 → RGB565 **大端** 121,552B (**不落盘**)。

    监控上屏路径专用: `monitor_canvas.render()` 直接产出 QImage, 走这里量化。
    缩放/合成规则与 `image_to_rgb565_be` **完全一致** (同一实现), 带 alpha 按**黑底**合成。

    ⚠️ 输出 = **整张画布** 121,552B (428×142); 块流只取前 121,510B, 末 42B 走尾帧。
    ⚠️ zoom/pan 仅 fit='cover' 生效 (0.1.8 图片页自由调节): zoom=100 为 cover 基准
    放大, pan_x/pan_y = -100..100 (相对可平移余量的百分比)。zoom=100/pan=0 = 原版
    cover 居中裁边 (真机验证路径, 不变)。
    """
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage, QPainter

    if img is None or img.isNull():
        raise ValueError('QImage 为空')

    canvas = QImage(CANVAS_W, CANVAS_H, QImage.Format.Format_RGB888)
    canvas.fill(0)                                   # 黑底 (alpha 合成用)
    painter = QPainter(canvas)
    try:
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        dst = QRect(0, 0, CANVAS_W, CANVAS_H)
        if fit == 'cover':
            sw, sh = img.width(), img.height()
            tar = CANVAS_W / CANVAS_H
            if sw <= 0 or sh <= 0:
                painter.drawImage(dst, img)
            else:
                z = max(100, int(zoom or 100)) / 100.0
                cw, ch = int(round(sh * tar / z)), int(round(sh / z))
                if cw > sw:                          # zoom 过小不足覆盖 → 回退基准
                    cw, ch = int(round(sh * tar)), sh
                if ch > sw * tar and ch > sh:
                    pass
                sx = int((sw - cw) * (max(-100, min(100, int(pan_x or 0))) + 100) / 200)
                sy = int((sh - ch) * (max(-100, min(100, int(pan_y or 0))) + 100) / 200)
                painter.drawImage(dst, img, QRect(sx, sy, cw, ch))
        else:
            painter.drawImage(dst, img)              # stretch: 原封不动 (封版路径)
    finally:
        painter.end()
    return _canvas_bytes(canvas)


def image_to_rgb565_be(path: str, fit: str = 'stretch',
                       zoom: int = 100, pan_x: int = 0, pan_y: int = 0) -> bytes:
    """任意 PNG/JPG → 缩放 428×142 → RGB565 **大端** 121,552B。

    fit='stretch' (**默认, 设备已验证路径**): 拉伸铺满画布, 不保持宽高比 (会形变)。
    fit='cover': 等比放大到覆盖画布后**居中裁边** (照片更直觉, 无形变, 但会裁掉边缘)。

    用 PySide6 QImage 解码 (本机无 PIL, 不引入新依赖; QImage 原生支持 PNG/JPG/BMP/WebP)。
    带 alpha 的图按**黑底**合成 (与屏幕黑背景一致)。

    ⚠️ 输出 = **整张画布** 121,552B (428×142); 块流只取前 121,510B, 末 42B 走尾帧。
    ⚠️ 'stretch' 分支字节级封版 (v24/v25 设备验证) —— 改动后务必重跑 selfcheck_screen_upload.py。
    """
    from PySide6.QtGui import QImage

    src = QImage(str(path))
    if src.isNull():
        raise ValueError(f'无法解码图片 (QImage 返回 null): {path}')
    return qimage_to_rgb565_be(src, fit=fit, zoom=zoom, pan_x=pan_x, pan_y=pan_y)


# ─────────────────────────── 帧构建 ───────────────────────────
def _wire(frame: bytes) -> bytes:
    """补齐/裁剪到 64B 线上包。"""
    if len(frame) > WIRE_LEN:
        raise ValueError(f'帧超长 {len(frame)} > {WIRE_LEN}')
    return frame.ljust(WIRE_LEN, b'\x00')


def _block(cmd: int, seq: int, data: bytes, first: bool = False) -> bytes:
    b = bytearray(WIRE_LEN)
    b[0] = 0xA4
    b[1] = cmd
    b[2] = seq & 0xFF
    b[3] = (seq >> 8) & 0x0F
    if first:
        b[3] |= 0x80                      # 首块标志 (cap8: 块号 2096 -> byte3 = 0x88)
    b[4] = BLK_MARK
    b[5:5 + BLOCK_DATA] = data
    b[63] = sum(b[:63]) & 0xFF
    return bytes(b)


def build_blocks(image_bytes: bytes) -> list[bytes]:
    """画布前 121,510B → 2095 个 A4-40 数据块 (块号 2096→2 递减)。"""
    if len(image_bytes) != CANVAS_BYTES:
        raise ValueError(f'画布长度必须 {CANVAS_BYTES}, 实际 {len(image_bytes)}')
    frames = []
    for i in range(BLOCK_COUNT):
        seq = BLOCK_COUNT + 1 - i         # 2096 .. 2
        data = image_bytes[i * BLOCK_DATA:(i + 1) * BLOCK_DATA]
        frames.append(_block(BLK_CMD_DATA, seq, data, first=(i == 0)))
    return frames


def build_tail_block(image_bytes: bytes) -> bytes:
    """A4-30 尾帧 (块号 1): 画布末 42B。

    结构 = `A4 30 01 00 C6` + 42B 数据 + `CK@47=sum(b[0:47])&0xFF` (48B 帧), 线上补零到 64B。
    byte1=0x30=48 恰为帧长; 满块 byte1=0x40=64 同理 —— 尾帧即"末段短块"。
    """
    if len(image_bytes) != CANVAS_BYTES:
        raise ValueError(f'画布长度必须 {CANVAS_BYTES}, 实际 {len(image_bytes)}')
    data = image_bytes[STREAM_BYTES:CANVAS_BYTES]
    if len(data) != TAIL_BYTES:
        raise ValueError(f'尾帧数据必须 {TAIL_BYTES}B, 实际 {len(data)}')
    b = bytearray(5 + TAIL_BYTES + 1)             # 48B
    b[0] = 0xA4
    b[1] = BLK_CMD_TAIL
    b[2] = 1 & 0xFF
    b[3] = (1 >> 8) & 0x0F
    b[4] = BLK_MARK
    b[5:5 + TAIL_BYTES] = data
    b[5 + TAIL_BYTES] = sum(b[:5 + TAIL_BYTES]) & 0xFF   # CK@47
    return _wire(bytes(b))


def build_c4(meta11: bytes = META_RED) -> bytes:
    if len(meta11) != 11:
        raise ValueError(f'元数据必须 11B, 实际 {len(meta11)}')
    return _wire(protocol.build_frame(CMD_C4, meta11))


def build_c5() -> bytes:
    return _wire(protocol.build_frame(CMD_C5))


def build_heartbeats(heartbeats: Optional[Iterable[bytes]] = None) -> list[bytes]:
    """16 条尾部 0x07 心跳。默认 = cap8 实录 entries 经 build_host_info 重建。"""
    if heartbeats is None:
        return [_wire(protocol.build_host_info(e)) for e in CAP8_TAIL_HB_ENTRIES]
    out = [_wire(bytes(h)) for h in heartbeats]
    if not out:
        raise ValueError('heartbeats 不能为空')
    return out


# ─────────────────────────── 时序 ───────────────────────────
def page_pause_after(n_blocks: int = BLOCK_COUNT) -> list[int]:
    """返回"第 k 块之后停顿"的 k 列表 (1-based)。

    规则: 累计数据字节 58·k 跨过 4096 的整数倍时, 设备要刷一页 flash (~40ms)。
    对 2095 块 = 29 处, 与 cap8 实测完全一致。
    """
    out = []
    for k in range(1, n_blocks + 1):
        if (BLOCK_DATA * k) // FLASH_PAGE > (BLOCK_DATA * (k - 1)) // FLASH_PAGE:
            out.append(k)
    return out


def build_schedule(n_blocks: int = BLOCK_COUNT, base_ms: float = BASE_MS) -> list[float]:
    """每个数据块相对首块的计划偏移 (ms), 长度 n_blocks。

    首块 = 0; 之后每块 +base_ms, 若前一块在停顿点上则 +40ms。

    base_ms 默认 7.0 (= cap8 官方实录, 字节级封版)。监控上屏用 **4.0**
    (v3.18 压测定档: 4ms 通过 / 3ms 首拒; 全屏刷新预算 ~10.5s)。
    """
    pauses = set(page_pause_after(n_blocks))
    out = [0.0]
    for k in range(1, n_blocks):
        gap = PAGE_PAUSE_MS if k in pauses else base_ms
        out.append(out[-1] + gap)
    return out


# ─────────────────────────── 总装 ───────────────────────────
def build_upload(image_bytes: bytes,
                 meta11: bytes = META_RED,
                 heartbeats: Optional[Iterable[bytes]] = None,
                 base_ms: float = BASE_MS) -> list[UploadFrame]:
    """构建整条上传序列 (含时刻表), 返回 2114 帧。

    at_ms 相对 **C4 发送时刻**。执行器应: 发 C4 → 等真实 ACK → 以"首块落在
    ACK+4.2ms"重新锚定, 再按各帧相对偏移推进 (这样既尊重真实 ACK 时延,
    又保留 29 处页停顿的精确结构)。

    heartbeats=None 时用 cap8 实录的 16 条心跳 (使本函数单独即可逐字节复刻 cap8);
    生产集成应传入自己的实时 0x07 帧。
    base_ms 默认 7.0 (官方节奏, 封版); 监控上屏传 4.0 (v3.18 压测定档)。
    """
    blocks = build_blocks(image_bytes)
    tail = build_tail_block(image_bytes)
    hbs = build_heartbeats(heartbeats)
    if len(hbs) != HB_COUNT:
        raise ValueError(f'心跳必须 {HB_COUNT} 条, 实际 {len(hbs)}')

    sched = build_schedule(len(blocks), base_ms=base_ms)   # 首块 = 0
    t_first = ACK_WAIT_MS + FIRST_BLOCK_AFTER_ACK_MS
    frames: list[UploadFrame] = [UploadFrame(build_c4(meta11), 0.0, 'C4')]
    for k, blk in enumerate(blocks):
        frames.append(UploadFrame(blk, t_first + sched[k], 'A4-40'))
    t = t_first + sched[-1] + LAST_BLOCK_TO_TAIL_MS
    frames.append(UploadFrame(tail, t, 'A4-30'))
    t += TAIL_TO_HB_MS
    for hb in hbs:
        frames.append(UploadFrame(hb, t, 'HB'))
        t += HB_GAP_MS
    t += HB_TO_C5_MS - HB_GAP_MS                 # 末心跳之后再等 6ms
    frames.append(UploadFrame(build_c5(), t, 'C5'))
    return frames


def total_duration_ms(frames: Sequence[UploadFrame]) -> float:
    return frames[-1].at_ms if frames else 0.0


__all__ = [
    'CANVAS_W', 'CANVAS_H', 'CANVAS_BYTES', 'BLOCK_COUNT', 'BLOCK_DATA',
    'STREAM_BYTES', 'TAIL_BYTES', 'WIRE_LEN', 'IMG_W', 'IMG_H', 'IMG_BYTES',
    'FLASH_PAGE', 'BASE_MS', 'PAGE_PAUSE_MS', 'HB_COUNT', 'META_RED',
    'CAP8_TAIL_HB_ENTRIES', 'UploadFrame',
    'crc16_xmodem', 'meta_for',
    'image_to_rgb565_be', 'qimage_to_rgb565_be', 'build_blocks', 'build_tail_block',
    'build_c4', 'build_c5', 'build_heartbeats', 'page_pause_after', 'build_schedule',
    'build_upload', 'total_duration_ms',
]
