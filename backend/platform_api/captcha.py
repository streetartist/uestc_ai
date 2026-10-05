"""Short-lived, single-use numeric image challenges stored across API workers."""
from __future__ import annotations

import base64
import secrets
import struct
import zlib


def numeric_image(code: str) -> str:
    # Render pixels rather than sending digits as text or SVG source.
    width, height = 180, 56
    pixels = [[[244, 246, 239] for _ in range(width)] for _ in range(height)]
    segments = [(5, 1, 19, 4), (19, 4, 22, 19), (19, 23, 22, 38),
                (5, 38, 19, 41), (2, 23, 5, 38), (2, 4, 5, 19), (5, 19, 19, 23)]
    masks = ['abcdef', 'bc', 'abdeg', 'abcdg', 'bcfg', 'acdfg', 'acdefg', 'abc', 'abcdefg', 'abcdfg']
    for index, digit in enumerate(code):
        x, y = 12 + index * 27, 6 + secrets.randbelow(5)
        for name, (left, top, right, bottom) in zip('abcdefg', segments):
            if name in masks[int(digit)]:
                for row in range(top + y, bottom + y):
                    for column in range(left + x, right + x):
                        pixels[row][column] = [40, 70, 52]
    for _ in range(160):
        pixels[secrets.randbelow(height)][secrets.randbelow(width)] = [125, 147, 127]
    raw = b''.join(b'\x00' + bytes(channel for pixel in row for channel in pixel) for row in pixels)
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
    png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', width, height, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')
    return 'data:image/png;base64,' + base64.b64encode(png).decode()
