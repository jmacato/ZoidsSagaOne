# Zoids Saga (Japan) decompilation and English translation

This repository contains two things for Zoids Saga (Japan) on the Game Boy Advance:

- 1,897 byte-exact code regions from the game, as C, inline assembly, assembler macros and assembly files.
- The source of the English translation: the scripts, the text renderer and the build tools.

It does not contain a ROM, game data, generated ROMs or saves. You supply your own ROMs.

## Requirements

- Python 3 with Capstone.
- A C preprocessor, ARM binutils and ARM GCC (`arm-none-eabi-gcc`).
- Node.js, for the release step.
- agbcc, for the decompilation checks only.

Build agbcc from commit `da598c1d918402c42c0c0d7128ba14567f3175e9` of [pret/agbcc](https://github.com/pret/agbcc). Build its normal compiler with GCC 14, because Apple's Clang build fails on one source file. From the agbcc checkout, run these commands one at a time. The makefile has dependencies that fail with parallel builds.

    make -C gcc clean
    make -C gcc old
    cp gcc/old_agbcc old_agbcc
    make -C gcc clean
    make -C gcc CC=gcc-14
    cp gcc/agbcc agbcc

If Homebrew's GCC 14 cannot find `_bounds.h` or `stdio.h` on macOS, pass the SDK path: `make -C gcc CC="gcc-14 -isysroot $(xcrun --show-sdk-path)"`.

Set `AGBCC` and `OLD_AGBCC` when the compiler executables are outside `../agbcc/`.

## ROMs

| ROM | Used for | Checksum |
|---|---|---|
| Zoids Saga (Japan), Game Boy Advance | Every command | SHA-1 `75d8c15ac281ea93c8ac7cc7641c490799557081` |
| Zoids Saga DS: Legend of Arcadia (Japan), Nintendo DS | The translation build only | SHA-256 `adb2a90a74d2d58a0593bc2d3ec06e95e9b9008a4fa67a8f94ffcb85c68ea8ce` |

The Makefile expects the files `0259 - Zoids Saga (J)(Independent).gba` and `0235 - Zoids Saga DS - Legend of Arcadia (Japan).nds` in this folder. To use other paths, pass `ROM=` and `DS_ROM=`.

The DS ROM supplies the king's portraits.

## Build the English ROM

    make menus

This writes `Zoids Saga English.gba`. Git ignores it.

    make release

This builds the ROM, draws the version from `VERSION` on the title screen and pads the ROM to 16 MiB. The output is `Zoids Saga English <version>.gba`.

## Translation files

| File | Contents |
|---|---|
| `story-en.json` | Story dialogue. Each key is the ROM offset of the Japanese record. |
| `menu-en.json` | Menus, names, descriptions and battle messages. |
| `credits-en.json` | The staff roll. |
| `athle-ds-imports.json` | The DS lines that replace or follow Athle's silent lines. |
| `kerning-choices.json` | Letter spacing, pair kerning and triplet kerning. |
| `message-sites.json` | The code sites that assemble messages from parts. |

## Verify the decompilation

    make verify ROM=/path/to/original.gba

The verifier checks the ROM's SHA-1 before it compiles any code. It compiles each listed region at its original address and compares the result with the ROM. It creates temporary files only and never writes a ROM. The strict C source check currently reports unfinished routines.

Use `python3 tools/verify.py --rom /path/to/rom --region NAME` to verify one region.

## Rebuild the original ROM

    make native ROM=/path/to/original.gba NATIVE_OUTPUT=/path/to/rebuilt.gba

This target permits the current mixed C sources and reports them as warnings. Every listed region must match the original bytes. Bytes outside those regions come from your ROM. The complete output must equal the original ROM.

## License

GNU AGPL 3.0 only (`AGPL-3.0-only`). See `LICENSE`. If you distribute a modified version, or run one for users over a network, you must release its source under the same license. `NOTICE` lists the parts that keep their own licenses.
