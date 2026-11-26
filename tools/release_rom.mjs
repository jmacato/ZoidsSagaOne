import { readFileSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';

const [inputPath, outputPath, version] = process.argv.slice(2);
if (!version || !/^[0-9]+\.[0-9]+\.[0-9]+$/.test(version)) {
  console.error('usage: node tools/release_rom.mjs TRANSLATED_ROM OUTPUT_ROM VERSION');
  process.exit(2);
}

const ROM_SIZE = 16 * 1024 * 1024;
const RACY_STACK_SWITCH = Buffer.from('6d46b768bd46776880b475600027b76000f019f8194e80bc77606f46b760ad460ee0', 'hex');
const COPYRIGHT_GRAPHICS = 0x62ff4;
const COPYRIGHT_GRAPHICS_POINTER = 0x26ac;
const COPYRIGHT_FRAMES = 0x67b9c;
const COPYRIGHT_FRAMES_POINTER = 0x67cb8;
const COPYRIGHT_PIECES = 14;
const COPYRIGHT_TILES = 46;
let VERSION_GRAPHICS_AREA = 0;
let VERSION_FRAMES_AREA = 0;
const VERSION_GAP = 10;
const INK = 15;
const SHADOW = 13;
const GLYPHS = {
  V: [17, 17, 17, 17, 10, 10, 4],
  0: [7, 5, 5, 5, 5, 5, 7],
  1: [3, 1, 1, 1, 1, 1, 1],
  2: [7, 1, 1, 7, 4, 4, 7],
  3: [7, 1, 1, 7, 1, 1, 7],
  4: [5, 5, 5, 7, 1, 1, 1],
  5: [7, 4, 4, 7, 1, 1, 7],
  6: [7, 4, 4, 7, 5, 5, 7],
  7: [7, 1, 1, 1, 1, 1, 1],
  8: [7, 5, 5, 7, 5, 5, 7],
  9: [7, 5, 5, 7, 1, 1, 1],
  '.': [0, 0, 0, 0, 0, 0, 1],
  A: [2, 5, 5, 7, 5, 5, 5],
  B: [6, 5, 5, 6, 5, 5, 6],
  C: [7, 4, 4, 4, 4, 4, 7],
  I: [1, 1, 1, 1, 1, 1, 1],
  J: [1, 1, 1, 1, 1, 5, 7],
  L: [4, 4, 4, 4, 4, 4, 7],
  M: [17, 27, 21, 17, 17, 17, 17],
  N: [9, 13, 11, 9, 9, 9, 9],
  O: [7, 5, 5, 5, 5, 5, 7],
  R: [6, 5, 5, 6, 5, 5, 5],
  S: [7, 4, 4, 7, 1, 1, 7],
  T: [7, 2, 2, 2, 2, 2, 2],
  U: [5, 5, 5, 5, 5, 5, 7],
  Y: [5, 5, 5, 2, 2, 2, 2],
  ' ': [0, 0, 0, 0, 0, 0, 0],
  ',': [0, 0, 0, 0, 0, 3, 2],
  '\u00b7': [0, 0, 0, 1, 0, 0, 0],
  '-': [0, 0, 0, 7, 0, 0, 0],
  '(': [1, 2, 2, 2, 2, 2, 1],
  ')': [2, 1, 1, 1, 1, 1, 2],
  D: [6, 5, 5, 5, 5, 5, 6],
  E: [7, 4, 4, 6, 4, 4, 7],
  G: [7, 4, 4, 5, 5, 5, 7],
  P: [7, 5, 5, 7, 4, 4, 4],
};
const CREDIT = 'DECOMP, ENG SCRIPT \u00a9 J. MACATO \u00b7 AGPL-3.0';
const CREDIT_Y = -4;
const COPYRIGHT_ANIMATIONS_POINTER = 0x663ebc;
const COPYRIGHT_FRAME_TABLE_POINTER = 0x663ec0;
const COPYRIGHT_ANIMATIONS = 0x67cc4;
const CYCLE_FRAMES = 180;
const COPYRIGHT_SPRITE_FLAGS = 0x663ea8;
const SPRITE_VISIBLE = 0x8000;
const SPRITE_ANIMATED = 0x800;

function decompress(rom, offset) {
  if (rom[offset] !== 0x10) throw new Error('Expected BIOS LZ77 graphics.');
  const size = rom.readUIntLE(offset + 1, 3);
  const output = Buffer.alloc(size);
  let position = offset + 4;
  let written = 0;
  while (written < size) {
    const flags = rom[position++];
    for (let bit = 7; bit >= 0 && written < size; bit--) {
      if (flags & (1 << bit)) {
        const first = rom[position];
        const second = rom[position + 1];
        position += 2;
        const distance = ((first & 15) << 8) + second + 1;
        for (let count = (first >> 4) + 3; count > 0 && written < size; count--) {
          output[written] = output[written - distance];
          written++;
        }
      } else {
        output[written++] = rom[position++];
      }
    }
  }
  return output;
}

function literalLz77(data) {
  const parts = [Buffer.from([0x10, data.length & 255, (data.length >> 8) & 255, (data.length >> 16) & 255])];
  for (let offset = 0; offset < data.length; offset += 8) {
    parts.push(Buffer.from([0]), data.subarray(offset, offset + 8));
  }
  const packed = Buffer.concat(parts);
  return Buffer.concat([packed, Buffer.alloc(-packed.length & 3)]);
}

function writeFree(rom, offset, data) {
  for (let index = 0; index < data.length; index++) {
    if (rom[offset + index] !== 0xff) throw new Error(`Area at 0x${offset.toString(16)} is not free.`);
  }
  data.copy(rom, offset);
}

const RAW = {};

function rowGlyph(sheet, list, count) {
  const parts = Array.from({ length: count }, (_, index) => ({
    tile: list.readUInt32LE(index * 20),
    x: list.readInt16LE(index * 20 + 4),
    y: list.readInt16LE(index * 20 + 6),
    w: list.readUInt16LE(index * 20 + 8),
    h: list.readUInt16LE(index * 20 + 10),
  }));
  const top = Math.min(...parts.map(part => part.y));
  const pixel = (x, y) => {
    const part = parts.find(item => x >= item.x && x < item.x + item.w && y >= item.y && y < item.y + item.h);
    if (!part) return 0;
    const dx = x - part.x;
    const dy = y - part.y;
    const value = sheet[(part.tile + (dy >> 3) * (part.w >> 3) + (dx >> 3)) * 32 + (dy & 7) * 4 + ((dx & 7) >> 1)];
    return dx & 1 ? value >> 4 : value & 15;
  };
  const column = x => Array.from({ length: 8 }, (_, y) => pixel(x, top + y));
  let x = Math.min(...parts.filter(part => part.y === top).map(part => part.x));
  while (column(x).every(value => !value)) x++;
  const rows = Array.from({ length: 8 }, () => []);
  for (; column(x).some(value => value); x++) column(x).forEach((value, y) => rows[y].push(value));
  return rows;
}

function renderVersion(text, limit = 0) {
  const widths = [...text].map(char => {
    if (RAW[char]) return RAW[char][0].length;
    if (!GLYPHS[char]) throw new Error(`Title version uses an unsupported glyph: ${char}`);
    return char === ' ' ? 2 : Math.max(...GLYPHS[char]).toString(2).length;
  });
  const width = widths.reduce((sum, value) => sum + value, 0) + text.length - 1;
  if (!limit) limit = Math.ceil(width / 8) * 8;
  if (width > limit) throw new Error(`Title text is ${width} pixels wide, the sprite holds ${limit}.`);
  const pixels = Array.from({ length: 8 }, () => new Uint8Array(limit));
  let left = 0;
  [...text].forEach((char, index) => {
    if (RAW[char]) {
      RAW[char].forEach((row, y) => row.forEach((value, x) => {
        if (value) pixels[y][left + x] = value;
      }));
      left += widths[index] + 1;
      return;
    }
    GLYPHS[char].forEach((row, y) => {
      for (let x = 0; x < widths[index]; x++) {
        if (row & (1 << (widths[index] - x - 1))) pixels[y][left + x] = INK;
      }
    });
    left += widths[index] + 1;
  });
  for (let y = 1; y < 8; y++) {
    for (let x = 0; x < limit; x++) {
      if (!pixels[y][x] && pixels[y - 1][x] === INK) pixels[y][x] = SHADOW;
    }
  }
  return { pixels, width };
}

function patchVersion(rom, text) {
  if (rom.readUInt32LE(COPYRIGHT_GRAPHICS_POINTER) !== 0x08000000 + COPYRIGHT_GRAPHICS ||
      rom.readUInt32LE(COPYRIGHT_FRAMES_POINTER) !== 0x08000000 + COPYRIGHT_FRAMES) {
    throw new Error('Unexpected title copyright pointer.');
  }
  let tiles = decompress(rom, COPYRIGHT_GRAPHICS);
  if (tiles.length !== 64 * 32) throw new Error('Unexpected title copyright graphics size.');
  if (tiles.subarray(COPYRIGHT_TILES * 32).some(value => value)) throw new Error('Title version tiles are in use.');
  const frames = Buffer.from(rom.subarray(COPYRIGHT_FRAMES, COPYRIGHT_FRAMES_POINTER));
  RAW['\u00a9'] = rowGlyph(tiles, frames, COPYRIGHT_PIECES);
  if (frames.length !== COPYRIGHT_PIECES * 20 + 4 || frames.readUInt32LE(frames.length - 4) !== 0xffff) {
    throw new Error('Unexpected title copyright frame layout.');
  }
  let inkLeft = Infinity;
  let inkRight = -Infinity;
  for (let piece = 0; piece < COPYRIGHT_PIECES; piece++) {
    const tile = frames.readUInt32LE(piece * 20);
    const x = frames.readInt16LE(piece * 20 + 4);
    const width = frames.readUInt16LE(piece * 20 + 8);
    for (let column = 0; column < width; column++) {
      let ink = false;
      for (let y = 0; y < 8; y++) {
        const value = tiles[(tile + (column >> 3)) * 32 + y * 4 + ((column & 7) >> 1)];
        if (column & 1 ? value >> 4 : value & 15) ink = true;
      }
      if (ink) {
        inkLeft = Math.min(inkLeft, x + column);
        inkRight = Math.max(inkRight, x + column);
      }
    }
  }
  const { pixels, width } = renderVersion(text, 32);
  for (let y = 0; y < 8; y++) {
    for (let x = 0; x < 32; x += 2) {
      tiles[(COPYRIGHT_TILES + (x >> 3)) * 32 + y * 4 + ((x & 7) >> 1)] = pixels[y][x] | pixels[y][x + 1] << 4;
    }
  }
  const total = inkRight - inkLeft + 1 + VERSION_GAP + width;
  const shift = -Math.floor(total / 2) - inkLeft;
  for (let piece = 0; piece < COPYRIGHT_PIECES; piece++) {
    frames.writeInt16LE(frames.readInt16LE(piece * 20 + 4) + shift, piece * 20 + 4);
  }
  const versionPiece = Buffer.from(frames.subarray(0, 20));
  versionPiece.writeUInt32LE(COPYRIGHT_TILES, 0);
  versionPiece.writeInt16LE(inkRight + shift + 1 + VERSION_GAP, 4);
  versionPiece.writeInt16LE(-4, 6);
  versionPiece.writeUInt16LE(32, 8);
  const credit = renderVersion(CREDIT);
  const CREDIT_TILES = Math.ceil(credit.width / 8);
  const creditTile = COPYRIGHT_TILES + 4;
  if (creditTile + CREDIT_TILES > 64) tiles = Buffer.concat([tiles, Buffer.alloc((creditTile + CREDIT_TILES - 64) * 32)]);
  for (let y = 0; y < 8; y++) {
    for (let x = 0; x < CREDIT_TILES * 8; x += 2) {
      tiles[(creditTile + (x >> 3)) * 32 + y * 4 + ((x & 7) >> 1)] = credit.pixels[y][x] | credit.pixels[y][x + 1] << 4;
    }
  }
  const creditPieces = [];
  for (let left = 0, size; left < CREDIT_TILES * 8; left += size) {
    const rest = CREDIT_TILES * 8 - left;
    size = rest >= 32 ? 32 : rest >= 16 ? 16 : 8;
    const piece = Buffer.from(frames.subarray(0, 20));
    piece.writeUInt32LE(creditTile + (left >> 3), 0);
    piece.writeInt16LE(left - Math.floor(credit.width / 2), 4);
    piece.writeInt16LE(CREDIT_Y, 6);
    piece.writeUInt16LE(size, 8);
    creditPieces.push(piece);
  }
  if (rom.readUInt32LE(COPYRIGHT_ANIMATIONS_POINTER) !== 0x08000000 + COPYRIGHT_ANIMATIONS ||
      rom.readUInt32LE(COPYRIGHT_FRAME_TABLE_POINTER) !== 0x08000000 + COPYRIGHT_FRAMES_POINTER) {
    throw new Error('Unexpected title copyright sprite record.');
  }
  if (rom.readUInt16LE(COPYRIGHT_SPRITE_FLAGS) !== SPRITE_VISIBLE) throw new Error('Unexpected title copyright sprite flags.');
  rom.writeUInt16LE(SPRITE_VISIBLE | SPRITE_ANIMATED, COPYRIGHT_SPRITE_FLAGS);
  const end = frames.subarray(COPYRIGHT_PIECES * 20);
  const notice = Buffer.concat([frames.subarray(0, COPYRIGHT_PIECES * 20), versionPiece, end]);
  const credits = Buffer.concat([...creditPieces, end]);
  const base = 0x08000000 + VERSION_FRAMES_AREA;
  const table = Buffer.alloc(8);
  table.writeUInt32LE(base, 0);
  table.writeUInt32LE(base + notice.length, 4);
  const script = Buffer.alloc(12);
  script.writeUInt16LE(0, 0);
  script.writeUInt16LE(CYCLE_FRAMES, 2);
  script.writeUInt16LE(1, 4);
  script.writeUInt16LE(CYCLE_FRAMES, 6);
  script.writeUInt16LE(0xffff, 8);
  const tableAt = notice.length + credits.length;
  const scriptAt = tableAt + table.length;
  const animations = Buffer.alloc(4);
  animations.writeUInt32LE(base + scriptAt, 0);
  writeFree(rom, VERSION_GRAPHICS_AREA, literalLz77(tiles));
  writeFree(rom, VERSION_FRAMES_AREA, Buffer.concat([notice, credits, table, script, animations]));
  rom.writeUInt32LE(0x08000000 + VERSION_GRAPHICS_AREA, COPYRIGHT_GRAPHICS_POINTER);
  rom.writeUInt32LE(base + tableAt, COPYRIGHT_FRAME_TABLE_POINTER);
  rom.writeUInt32LE(base + scriptAt + script.length, COPYRIGHT_ANIMATIONS_POINTER);
}

const source = readFileSync(inputPath);
if (source.length > ROM_SIZE) throw new Error('Translated ROM is larger than 16 MiB.');
const rom = Buffer.alloc(ROM_SIZE, 0xff);
source.copy(rom);
VERSION_GRAPHICS_AREA = Math.ceil(source.length / 0x1000) * 0x1000;
VERSION_FRAMES_AREA = VERSION_GRAPHICS_AREA + 0x1000;
if (rom.includes(RACY_STACK_SWITCH)) throw new Error('This build has the old stack switch that resets in attract mode. Rebuild from the fixed src/text_stack.s.');
patchVersion(rom, `V${version}`);
writeFileSync(outputPath, rom);
console.log(`${createHash('sha1').update(rom).digest('hex')} ${rom.length} ${outputPath}`);
