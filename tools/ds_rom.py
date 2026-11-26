"""Read files from the supplied Zoids Saga DS ROM."""

from dataclasses import dataclass
import struct


ROM_SHA256 = "adb2a90a74d2d58a0593bc2d3ec06e95e9b9008a4fa67a8f94ffcb85c68ea8ce"


@dataclass(frozen=True)
class RomFile:
    name: str
    index: int
    offset: int
    data: bytes


def rom_files(rom):
    fnt, fnt_size, fat, fat_size = struct.unpack_from("<IIII", rom, 0x40)
    if fnt + fnt_size > len(rom) or fat + fat_size > len(rom) or fat_size % 8:
        raise ValueError("The DS filesystem extends beyond the ROM.")
    directory_count = struct.unpack_from("<H", rom, fnt + 6)[0]
    visited = set()

    def directory(index, prefix=""):
        if index >= directory_count or index in visited:
            raise ValueError("The DS directory tree contains an invalid link.")
        visited.add(index)
        offset, file_id, _ = struct.unpack_from("<IHH", rom, fnt + index * 8)
        cursor = fnt + offset
        while cursor < fnt + fnt_size:
            flag = rom[cursor]
            cursor += 1
            if not flag:
                return
            length = flag & 127
            if cursor + length + (2 if flag & 128 else 0) > fnt + fnt_size:
                raise ValueError("A DS filename extends beyond the filename table.")
            name = rom[cursor:cursor + length].decode("shift_jis")
            cursor += length
            if flag & 128:
                child = struct.unpack_from("<H", rom, cursor)[0]
                cursor += 2
                yield from directory(child & 0xFFF, prefix + name + "/")
            else:
                if file_id * 8 >= fat_size:
                    raise ValueError("A DS file index exceeds the allocation table.")
                start, end = struct.unpack_from("<II", rom, fat + file_id * 8)
                if not 0 <= start <= end <= len(rom):
                    raise ValueError("A DS file extends beyond the ROM.")
                yield RomFile(prefix + name, file_id, start, rom[start:end])
                file_id += 1
        raise ValueError("A DS directory has no terminator.")

    yield from directory(0)


def decompress_lz10(data):
    if len(data) < 4 or data[0] != 0x10:
        raise ValueError("The event file has no LZ10 header.")
    size = int.from_bytes(data[1:4], "little")
    result = bytearray()
    cursor = 4
    while len(result) < size:
        if cursor >= len(data):
            raise ValueError("The LZ10 stream ends before its declared size.")
        flags = data[cursor]
        cursor += 1
        for bit in range(7, -1, -1):
            if len(result) == size:
                break
            count = 2 if flags & (1 << bit) else 1
            if cursor + count > len(data):
                raise ValueError("The LZ10 stream ends inside a token.")
            if count == 2:
                first, second = data[cursor:cursor + 2]
                length = (first >> 4) + 3
                distance = ((first & 15) << 8 | second) + 1
                if distance > len(result) or len(result) + length > size:
                    raise ValueError("The LZ10 back-reference is outside the output.")
                for _ in range(length):
                    result.append(result[-distance])
            else:
                result.append(data[cursor])
            cursor += count
    return bytes(result)
