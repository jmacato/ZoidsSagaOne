"""Read the text records embedded in Saga's scripts."""

from dataclasses import dataclass
import struct


@dataclass(frozen=True)
class Record:
    start: int
    end: int
    text: str


def read_text(rom, start):
    if not 0 <= start < len(rom) or rom[start] != 0x20:
        raise ValueError("The record does not start with a text command.")
    cursor = start + 1
    result = []
    while cursor < min(start + 4096, len(rom)):
        value = rom[cursor]
        size = 3 if value == 0x1F else 2 if value == 0x1C or value > 0x3F else 1
        if cursor + size > len(rom):
            raise ValueError("The text record ends inside a character or control.")
        if value == 0x1D:
            return Record(start, cursor, "".join(result))
        if value == 0x0D:
            result.append("\n")
            cursor += 1
        elif value == 0x1C:
            result.append(f"{{window:{rom[cursor + 1]:02X}}}")
            cursor += 2
        elif value == 0x1E:
            result.append("{player}")
            cursor += 1
        elif value == 0x1F:
            result.append(f"{{number:{rom[cursor + 1]:02X}:{rom[cursor + 2]:02X}}}")
            cursor += 3
        elif value > 0x3F:
            code = struct.unpack_from("<H", rom, cursor)[0]
            raw = code.to_bytes(2, "big")
            try:
                char = raw.decode("shift_jis")
            except UnicodeDecodeError:
                char = raw.decode("cp932")
            if len(char) != 1:
                raise ValueError("The character is not a two-byte Shift-JIS code.")
            result.append(char)
            cursor += 2
        else:
            raise ValueError(f"Unknown text control: {value:02X}.")
    raise ValueError("The record has no text terminator.")
