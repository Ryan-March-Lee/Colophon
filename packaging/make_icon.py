"""生成 web/favicon.ico（多尺寸），供浏览器标签页、窗口和 exe 图标共用。

纯标准库，改颜色或形状后重跑：

    python packaging/make_icon.py
"""

from __future__ import annotations

import math
import struct
from pathlib import Path

BG = (0x1F, 0x6B, 0x58)      # 品牌绿（浅色主题 accent）
INK = (0xF5, 0xF4, 0xEF)     # 纸色
SIZES = (16, 24, 32, 48, 64, 128, 256)
SS = 4                        # 超采样倍数，做抗锯齿


def _inside_round_rect(x: float, y: float, size: float) -> bool:
    margin = size * 0.03
    half = size / 2 - margin
    radius = size * 0.23
    cx = cy = size / 2
    dx = max(abs(x - cx) - (half - radius), 0.0)
    dy = max(abs(y - cy) - (half - radius), 0.0)
    if abs(x - cx) > half or abs(y - cy) > half:
        return False
    return math.hypot(dx, dy) <= radius


def _in_ring(x: float, y: float, size: float) -> bool:
    cx = cy = size / 2
    radius = math.hypot(x - cx, y - cy) / size
    if not (0.225 <= radius <= 0.335):
        return False
    # 右侧开口，读作字母 C
    angle = math.atan2(y - cy, x - cx)
    return abs(angle) > 0.52


def render(size: int) -> list[tuple[int, int, int, int]]:
    pix: list[tuple[int, int, int, int]] = []
    step = 1.0 / SS
    for py in range(size):
        for px in range(size):
            bg_hits = ink_hits = 0
            for sy in range(SS):
                for sx in range(SS):
                    x = px + (sx + 0.5) * step
                    y = py + (sy + 0.5) * step
                    if _inside_round_rect(x, y, size):
                        bg_hits += 1
                        if _in_ring(x, y, size):
                            ink_hits += 1
            total = SS * SS
            if bg_hits == 0:
                pix.append((0, 0, 0, 0))
                continue
            ink_ratio = ink_hits / bg_hits
            r = round(BG[0] + (INK[0] - BG[0]) * ink_ratio)
            g = round(BG[1] + (INK[1] - BG[1]) * ink_ratio)
            b = round(BG[2] + (INK[2] - BG[2]) * ink_ratio)
            a = round(255 * bg_hits / total)
            pix.append((r, g, b, a))
    return pix


def _dib(pix: list[tuple[int, int, int, int]], size: int) -> bytes:
    header = struct.pack(
        "<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, size * size * 4, 0, 0, 0, 0
    )
    rows = []
    for y in range(size - 1, -1, -1):
        row = bytearray()
        for x in range(size):
            r, g, b, a = pix[y * size + x]
            row += bytes((b, g, r, a))
        rows.append(bytes(row))
    mask_row = ((size + 31) // 32) * 4
    mask = bytes(mask_row * size)
    return header + b"".join(rows) + mask


def build_ico(path: Path) -> None:
    images = []
    for size in SIZES:
        images.append((size, _dib(render(size), size)))

    out = bytearray(struct.pack("<HHH", 0, 1, len(images)))
    offset = 6 + 16 * len(images)
    for size, data in images:
        dim = 0 if size >= 256 else size
        out += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    for _size, data in images:
        out += data
    path.write_bytes(out)
    print(f"wrote {path} ({len(out)} bytes, {len(images)} sizes)")


if __name__ == "__main__":
    build_ico(Path(__file__).resolve().parent.parent / "web" / "favicon.ico")
