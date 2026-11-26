"""Render Saga's Latin glyphs with Legacy's spacing rules."""

# Kerning data and spacing rules adapt jmacato/ZoidsLegacyDecomp.
# Copyright (c) 2026 Jumar Macato
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import struct

from rom import read_rom


ROOT = Path(__file__).resolve().parents[1]
PUNCTUATION = dict(zip(" !?.,:;()+-/'\"%", (
    0x8140, 0x8149, 0x8148, 0x8144, 0x8143, 0x8146, 0x8147,
    0x8169, 0x816A, 0x817B, 0x817C, 0x815E, 0x8166, 0x8168, 0x8193)))
PUNCTUATION.update({"[": 0x816D, "]": 0x816E, "=": 0x8181,
                    "<": 0x8183, ">": 0x8184, "&": 0x8195,
                    "*": 0x8196, "#": 0x8194})
PUNCTUATION_OFFSETS = {
    "!": -2, "?": -2, ".": -2, ",": -2, ":": -1, ";": -1,
    "(": -1, ")": -1, "[": -1, "]": -1, "/": -1,
    "+": -2, "-": -2, "=": -2, "<": -2, ">": -2,
    "%": -2, "&": -2, "*": -2, "#": -2,
}


def character_code(char):
    if char in PUNCTUATION:
        return PUNCTUATION[char]
    if char.isascii() and char.isalnum():
        char = chr(ord(char) + 0xFEE0)
    return int.from_bytes(char.encode("shift_jis"), "big")


@dataclass(frozen=True)
class Glyph:
    code: int
    pixels: bytes
    left: int
    width: int
    advance: int


class Font:
    def __init__(self, rom, choices):
        self.rom = rom
        self.choices = choices
        self.ranges = [struct.unpack_from("<HHI", rom, 0x6BF920 + i * 8) for i in range(111)]
        self.palette = struct.unpack_from("<16H", rom, 0x6BF870)
        self.cache = {}

    def glyph(self, char):
        code = character_code(char)
        if code in self.cache:
            return self.cache[code]
        for first, count, pointer in self.ranges:
            if first <= code < first + count:
                page, slot = divmod(code - first, 32)
                start = pointer - 0x08000000 + page * 0x800 + slot * 32
                pixels = bytes(pixel for tile in (start, start + 0x400)
                               for value in self.rom[tile:tile + 32]
                               for pixel in (value & 15, value >> 4))
                columns = [x for x in range(8) if any(pixels[y * 8 + x] != 1 for y in range(16))]
                left = min(columns) if columns else 0
                width = max(columns) - left + 1 if columns else 0
                advance = self.choices["advances"].get(f"{code:04X}", min(width + 1, 8) if width else 4)
                advance += self.choices["letter_spacing"]
                if not 1 <= advance <= 16:
                    raise ValueError(f"Invalid advance for {char!r}.")
                result = Glyph(code, pixels, left, width, advance)
                self.cache[code] = result
                return result
        raise ValueError(f"Font lacks {char!r} (0x{code:04X}).")

    def line(self, text, *, aligned=True, proportional=True):
        placements = []
        pen = 0
        previous = None
        before = None
        right = 0
        for char in text:
            glyph = self.glyph(char)
            if proportional and previous is not None:
                key = f"{previous.code:04X}:{glyph.code:04X}"
                adjust = self.choices["pairs"].get(key, 0)
                # After a single space the word gap for the glyphs on both sides also applies.
                if before is not None and previous.code == 0x8140:
                    adjust += self.choices.get("triplets", {}).get(f"{before.code:04X}:{glyph.code:04X}", 0)
                pen += max(1, previous.advance + adjust) - previous.advance
            shift = 0
            if aligned:
                shift = (-2 if char.isascii() and (char.isupper() or char.isdigit())
                         else PUNCTUATION_OFFSETS.get(char, 0))
            placements.append((glyph, pen, shift))
            right = max(right, pen + (glyph.width if proportional else 8))
            pen += glyph.advance if proportional else 8
            before = previous if glyph.code == 0x8140 and previous is not None and previous.code != 0x8140 else None
            previous = glyph
        rows = [bytearray([1] * max(right, pen)) for _ in range(16)]
        for glyph, origin, shift in placements:
            for y in range(16):
                for x in range(8):
                    pixel = glyph.pixels[y * 8 + x]
                    if pixel == 1:
                        continue
                    target_y = y + shift
                    target_x = origin + x - (glyph.left if proportional else 0)
                    if not 0 <= target_y < 16:
                        raise ValueError("Baseline offset clips a glyph.")
                    rows[target_y][target_x] = max(rows[target_y][target_x], pixel)
        return rows


def rgb(color):
    return tuple((((color >> shift) & 31) << 3) | (((color >> shift) & 31) >> 2)
                 for shift in (0, 5, 10))


def sample(font, output):
    from PIL import Image, ImageDraw, ImageFont

    palette = [rgb(word) for word in font.palette]
    sheet = Image.new("RGB", (1040, 960), "#F1F4F8")
    draw = ImageDraw.Draw(sheet)
    label_font = "/System/Library/Fonts/Supplemental/Arial.ttf"
    def label(size):
        return ImageFont.truetype(label_font, size)
    draw.text((40, 25), "Zoids Saga: Latin rendering sample", font=label(31), fill="#172338")
    draw.text((40, 70), "Original ROM pixels and colors, enlarged 4x. Each text panel is 240 pixels wide.",
              font=label(19), fill="#526071")
    variants = [("Original baseline and fixed spacing", False, False),
                ("Aligned baseline and fixed spacing", True, False),
                ("Aligned baseline with Legacy proportional spacing and kerning", True, True)]
    y = 120
    for title, aligned, proportional in variants:
        rows = []
        widths = []
        for text in ("Zoids Saga: Ready to launch!", "Agility and power 100"):
            line = font.line(text, aligned=aligned, proportional=proportional)
            width = len(line[0])
            if width > 240:
                raise ValueError("Sample exceeds the panel width.")
            widths.append(width)
            rows.extend([bytearray([1] * 240)] * 2)
            rows.extend(row + bytearray([1] * (240 - width)) for row in line)
            rows.extend([bytearray([1] * 240)] * 2)
        image = Image.new("RGB", (240, 40))
        image.putdata([palette[pixel] for row in rows for pixel in row])
        draw.text((40, y), title, font=label(21), fill="#172338")
        sheet.paste(image.resize((960, 160), Image.Resampling.NEAREST), (40, y + 38))
        draw.text((40, y + 208), f"Line widths: {widths[0]} px / {widths[1]} px", font=label(17), fill="#526071")
        y += 254
    draw.text((40, 890), "Rendering study only. The game patch is still queued.", font=label(19), fill="#526071")
    draw.text((40, 921), "Saga glyph widths + Legacy advance overrides and pair adjustments. No added smoothing.",
              font=label(17), fill="#526071")
    sheet.save(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    choices = json.loads((ROOT / "kerning-choices.json").read_text())
    sample(Font(read_rom(args.rom), choices), args.output)


if __name__ == "__main__":
    main()
