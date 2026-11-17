// The ZIP subset of a .haru file. Writing: STORE only (no compression), so the Mac app's
// `ditto -x -k` and any unzip tool read it. Reading: STORE and DEFLATE (other tools may
// Compress), DEFLATE with DecompressionStream("deflate-raw").

// Bytes in an ordinary ArrayBuffer (what Blob accepts).
export type Bytes = Uint8Array<ArrayBuffer>;
export type ZipEntry = { name: string; data: Bytes };

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) {
      c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    }
    table[n] = c >>> 0;
  }
  return table;
})();

export const crc32 = (data: Uint8Array): number => {
  let crc = 0xffffffff;
  for (let i = 0; i < data.length; i++) {
    crc = CRC_TABLE[(crc ^ data[i]) & 0xff] ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
};

// MS-DOS time and date of `when` (local time, as unzip tools show it).
const dosDateTime = (when: Date) => ({
  time: (when.getHours() << 11) | (when.getMinutes() << 5) | Math.floor(when.getSeconds() / 2),
  date: (Math.max(0, when.getFullYear() - 1980) << 9) | ((when.getMonth() + 1) << 5) | when.getDate(),
});

// UTF-8 names: general purpose flag bit 11.
const UTF8_FLAG = 0x0800;

export const writeZip = (entries: ZipEntry[], when = new Date()): Blob => {
  const encoder = new TextEncoder();
  const { time, date } = dosDateTime(when);
  const parts: Bytes[] = [];
  const central: Bytes[] = [];
  let offset = 0;
  for (const entry of entries) {
    const name = encoder.encode(entry.name);
    const crc = crc32(entry.data);
    const size = entry.data.length;
    const local = new Uint8Array(30 + name.length);
    const l = new DataView(local.buffer);
    l.setUint32(0, 0x04034b50, true);
    l.setUint16(4, 20, true);  // Version needed: 2.0
    l.setUint16(6, UTF8_FLAG, true);
    l.setUint16(8, 0, true);  // STORE
    l.setUint16(10, time, true);
    l.setUint16(12, date, true);
    l.setUint32(14, crc, true);
    l.setUint32(18, size, true);
    l.setUint32(22, size, true);
    l.setUint16(26, name.length, true);
    l.setUint16(28, 0, true);
    local.set(name, 30);

    const header = new Uint8Array(46 + name.length);
    const c = new DataView(header.buffer);
    c.setUint32(0, 0x02014b50, true);
    c.setUint16(4, 20, true);  // Made by: MS-DOS, 2.0
    c.setUint16(6, 20, true);
    c.setUint16(8, UTF8_FLAG, true);
    c.setUint16(10, 0, true);
    c.setUint16(12, time, true);
    c.setUint16(14, date, true);
    c.setUint32(16, crc, true);
    c.setUint32(20, size, true);
    c.setUint32(24, size, true);
    c.setUint16(28, name.length, true);
    // Extra, comment, disk, internal and external attributes: 0
    c.setUint32(42, offset, true);
    header.set(name, 46);

    parts.push(local, entry.data);
    central.push(header);
    offset += local.length + size;
  }
  const centralSize = central.reduce((sum, h) => sum + h.length, 0);
  const end = new Uint8Array(22);
  const e = new DataView(end.buffer);
  e.setUint32(0, 0x06054b50, true);
  e.setUint16(8, entries.length, true);
  e.setUint16(10, entries.length, true);
  e.setUint32(12, centralSize, true);
  e.setUint32(16, offset, true);
  return new Blob([...parts, ...central, end], { type: "application/zip" });
};

const MAX_ARCHIVE_BYTES = 50 * 1024 * 1024;
const MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024;
const MAX_ENTRIES = 64;

const inflateRaw = async (data: Bytes, limit: number): Promise<Bytes> => {
  const stream = new Blob([data]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
  const reader = stream.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > limit) {
        throw new Error("ZIP entry is too large");
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const out = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    out.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return out;
};

const safeEntryName = (name: string): boolean => {
  if (!name || name.startsWith("/") || name.includes("\\") || name.includes("\0")) return false;
  return !name.split("/").some(part => part === "..");
};

// Every file of the archive (folders are left out). Throws when it is not a safe ZIP file.
export const readZip = async (buffer: ArrayBuffer): Promise<ZipEntry[]> => {
  if (buffer.byteLength > MAX_ARCHIVE_BYTES) {
    throw new Error("ZIP archive is too large");
  }
  const bytes = new Uint8Array(buffer);
  const view = new DataView(buffer);
  // End of central directory: the last 22 bytes, plus a comment of at most 65535 bytes.
  let end = -1;
  for (let i = bytes.length - 22; i >= Math.max(0, bytes.length - 22 - 0xffff); i--) {
    if (view.getUint32(i, true) === 0x06054b50) {
      end = i;
      break;
    }
  }
  if (end < 0) {
    throw new Error("not a ZIP file");
  }
  const count = view.getUint16(end + 10, true);
  if (count > MAX_ENTRIES) {
    throw new Error("too many entries in ZIP archive");
  }
  let pos = view.getUint32(end + 16, true);
  const utf8 = new TextDecoder();
  const entries: ZipEntry[] = [];
  let totalUncompressed = 0;
  for (let n = 0; n < count; n++) {
    if (pos + 46 > bytes.length || view.getUint32(pos, true) !== 0x02014b50) {
      throw new Error("bad ZIP central directory");
    }
    const method = view.getUint16(pos + 10, true);
    const crc = view.getUint32(pos + 16, true);
    const compressedSize = view.getUint32(pos + 20, true);
    const uncompressedSize = view.getUint32(pos + 24, true);
    const nameLength = view.getUint16(pos + 28, true);
    const extraLength = view.getUint16(pos + 30, true);
    const commentLength = view.getUint16(pos + 32, true);
    const externalAttr = view.getUint32(pos + 38, true);
    const localOffset = view.getUint32(pos + 42, true);
    if (pos + 46 + nameLength + extraLength + commentLength > bytes.length) {
      throw new Error("truncated ZIP central directory");
    }
    if (((externalAttr >>> 16) & 0o170000) === 0o120000) {
      throw new Error("ZIP symbolic links are not allowed");
    }
    const name = utf8.decode(bytes.subarray(pos + 46, pos + 46 + nameLength));
    pos += 46 + nameLength + extraLength + commentLength;
    if (name.endsWith("/")) {
      continue;
    }
    if (!safeEntryName(name)) {
      throw new Error(`unsafe ZIP entry path: ${name}`);
    }
    if (totalUncompressed + uncompressedSize > MAX_UNCOMPRESSED_BYTES) {
      throw new Error("ZIP archive expands beyond safety limit");
    }
    if (localOffset + 30 > bytes.length || view.getUint32(localOffset, true) !== 0x04034b50) {
      throw new Error("bad ZIP local header");
    }
    const dataStart = localOffset + 30 + view.getUint16(localOffset + 26, true) + view.getUint16(localOffset + 28, true);
    if (dataStart + compressedSize > bytes.length) {
      throw new Error("truncated ZIP entry");
    }
    const raw = bytes.subarray(dataStart, dataStart + compressedSize);
    let data: Bytes;
    if (method === 0) {
      if (raw.byteLength > MAX_UNCOMPRESSED_BYTES - totalUncompressed) {
        throw new Error("ZIP archive expands beyond safety limit");
      }
      data = raw.slice();
    } else if (method === 8) {
      data = await inflateRaw(raw, MAX_UNCOMPRESSED_BYTES - totalUncompressed);
    } else {
      throw new Error(`unsupported ZIP method ${method}`);
    }
    totalUncompressed += data.byteLength;
    if (crc32(data) !== crc) {
      throw new Error(`bad checksum: ${name}`);
    }
    entries.push({ name, data });
  }
  return entries;
};
