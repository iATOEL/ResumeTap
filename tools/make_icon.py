"""生成应用图标（ICO），零外部依赖，使用 Windows GDI+ 绘制。

用法：python tools/make_icon.py
"""

import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "icon.ico"

# 16x16 手绘风格卡牌图形（1=金色描边, 2=紫色填充, 3=白色星芒, 0=透明）
ART = [
    "0000000000000000",
    "0000111111110000",
    "0001222222221000",
    "0012233333222100",
    "0012233333222100",
    "0012233333222100",
    "0012223332222100",
    "0012222332222100",
    "0012222332222100",
    "0012223332222100",
    "0012233333222100",
    "0012233333222100",
    "0012233333222100",
    "0001222222221000",
    "0000111111110000",
    "0000000000000000",
]

COLORS = {
    "0": (0, 0, 0, 0),
    "1": (245, 196, 81, 255),
    "2": (61, 42, 110, 255),
    "3": (255, 246, 214, 255),
}


def render(size: int) -> bytes:
    """生成 BGRA 位图（自下而上）。用最近邻放大 16x16 图案。"""
    scale = max(1, size // 16)
    pixels = []
    for y in range(size):
        for x in range(size):
            src_y = min(15, y // scale)
            src_x = min(15, x // scale)
            color = COLORS[ART[src_y][src_x]]
            alpha = color[3]
            if 0 < alpha < 255:
                b, g, r = color[2], color[1], color[0]
            else:
                b, g, r = color[2], color[1], color[0]
            pixels.append((b, g, r, alpha))

    # ICO 位图要求自下而上、BGRA
    rows = []
    for y in range(size - 1, -1, -1):
        row = b"".join(bytes(pixels[y * size + x]) for x in range(size))
        rows.append(row)
    body = b"".join(rows)

    # AND 掩码：全 0（表示使用 alpha 通道），每行按 32 位对齐
    mask_row_bytes = ((size + 31) // 32) * 4
    mask = b"\x00" * (mask_row_bytes * size)

    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    return header + body + mask


def build_ico(sizes=(16, 24, 32, 48, 64, 128, 256)) -> bytes:
    images = [(size, render(size)) for size in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = b""
    blobs = b""
    for size, data in images:
        entries += struct.pack(
            "<BBBBHHII",
            size if size < 256 else 0,
            size if size < 256 else 0,
            0, 0, 1, 32, len(data), offset,
        )
        blobs += data
        offset += len(data)
    return header + entries + blobs


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(build_ico())
    print("created", OUT, OUT.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
