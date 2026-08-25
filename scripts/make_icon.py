"""生成应用图标 assets/icon.ico（多尺寸 PNG-in-ICO，纯标准库实现）。

无 Pillow 依赖：手工构造 PNG（IHDR/IDAT/IEND + crc32）后装入 ICO 容器。
图案：蓝色圆角方块 + 白色双向箭头（上条朝右、下条朝左）。

@author ai-lhg
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

SIZES = (16, 32, 48, 256)

BG = (37, 99, 235)        # 蓝色底
FG = (255, 255, 255)      # 白色图案


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    """构造一个 PNG chunk：长度 + 类型 + 数据 + CRC32。"""
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))


def _encode_png(size: int, pixels: list[list[tuple[int, int, int, int]]]) -> bytes:
    """把 RGBA 像素矩阵编码为 PNG 字节流。"""
    raw = bytearray()
    for row in pixels:
        raw.append(0)  # filter type: none
        for r, g, b, a in row:
            raw.extend((r, g, b, a))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8bit RGBA
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + _png_chunk(b"IEND", b"")
    )


def _point_in_triangle(px: float, py: float, tri: tuple[tuple[float, float], ...]) -> bool:
    """符号面积法判断点是否在三角形内。"""
    (x1, y1), (x2, y2), (x3, y3) = tri
    d1 = (px - x2) * (y1 - y2) - (x1 - x2) * (py - y2)
    d2 = (px - x3) * (y2 - y3) - (x2 - x3) * (py - y3)
    d3 = (px - x1) * (y3 - y1) - (x3 - x1) * (py - y1)
    has_neg = d1 < 0 or d2 < 0 or d3 < 0
    has_pos = d1 > 0 or d2 > 0 or d3 > 0
    return not (has_neg and has_pos)


def _pixel(u: float, v: float) -> tuple[int, int, int, int]:
    """归一化坐标 (u,v) ∈ [0,1) 的取色逻辑。"""
    margin, radius = 0.04, 0.16
    lo, hi = margin, 1.0 - margin
    inside = lo <= u <= hi and lo <= v <= hi
    if inside:
        # 四角圆角判定：与角圆心的距离
        for cx, cy in ((lo + radius, lo + radius), (hi - radius, lo + radius),
                       (lo + radius, hi - radius), (hi - radius, hi - radius)):
            corner_x = u < lo + radius and cx < 0.5 or u > hi - radius and cx > 0.5
            corner_y = v < lo + radius and cy < 0.5 or v > hi - radius and cy > 0.5
            if corner_x and corner_y and ((u - cx) ** 2 + (v - cy) ** 2) > radius ** 2:
                return (0, 0, 0, 0)

    bar_up = 0.28 <= v <= 0.44 and 0.24 <= u <= 0.64
    head_up = _point_in_triangle(u, v, ((0.58, 0.22), (0.58, 0.50), (0.80, 0.36)))
    bar_down = 0.56 <= v <= 0.72 and 0.36 <= u <= 0.76
    head_down = _point_in_triangle(u, v, ((0.42, 0.50), (0.42, 0.78), (0.20, 0.64)))

    if bar_up or head_up or bar_down or head_down:
        return (*FG, 255)
    return (*BG, 255)


def _render(size: int) -> bytes:
    pixels = [
        [_pixel((x + 0.5) / size, (y + 0.5) / size) for x in range(size)]
        for y in range(size)
    ]
    return _encode_png(size, pixels)


def build_ico(out_path: Path) -> None:
    """按 16/32/48/256 四尺寸生成 ICO 容器并写入 out_path。"""
    pngs = [_render(size) for size in SIZES]

    header = struct.pack("<HHH", 0, 1, len(SIZES))
    entries = bytearray()
    offset = 6 + 16 * len(SIZES)
    for size, png in zip(SIZES, pngs, strict=True):
        dim = 0 if size >= 256 else size
        entries += struct.pack(
            "<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset
        )
        offset += len(png)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(header + bytes(entries) + b"".join(pngs))
    print(f"[icon] written {out_path} ({out_path.stat().st_size} bytes)")


if __name__ == "__main__":
    target = Path(__file__).resolve().parent.parent / "assets" / "icon.ico"
    build_ico(target)
