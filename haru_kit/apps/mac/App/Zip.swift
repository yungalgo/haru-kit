// ZIP files for .haru: the writer stores the entries without compression (the
// STORE method) with their CRC-32; the reader checks archive headers/limits and
// Extracts via ditto (which reads STORE and DEFLATE).

import Foundation

private let maxArchiveBytes = 50 * 1024 * 1024
private let maxUncompressedBytes = 100 * 1024 * 1024
private let maxArchiveEntries = 64

private let crcTable: [UInt32] = (0..<256).map { n in
    var c = UInt32(n)
    for _ in 0..<8 { c = c & 1 != 0 ? 0xEDB8_8320 ^ (c >> 1) : c >> 1 }
    return c
}

/// CRC-32 (IEEE), as ZIP uses it.
func crc32(_ data: Data) -> UInt32 {
    var c: UInt32 = 0xFFFF_FFFF
    for byte in data { c = crcTable[Int((c ^ UInt32(byte)) & 0xFF)] ^ (c >> 8) }
    return c ^ 0xFFFF_FFFF
}

private extension Data {
    mutating func u16(_ v: Int) { Swift.withUnsafeBytes(of: UInt16(truncatingIfNeeded: v).littleEndian) { append(contentsOf: $0) } }
    mutating func u32(_ v: Int) { Swift.withUnsafeBytes(of: UInt32(truncatingIfNeeded: v).littleEndian) { append(contentsOf: $0) } }
    mutating func u32(_ v: UInt32) { Swift.withUnsafeBytes(of: v.littleEndian) { append(contentsOf: $0) } }
    func readU16(_ at: Int) -> Int { Int(self[at]) | (Int(self[at + 1]) << 8) }
    func readU32(_ at: Int) -> UInt32 {
        UInt32(self[at]) | (UInt32(self[at + 1]) << 8) | (UInt32(self[at + 2]) << 16) | (UInt32(self[at + 3]) << 24)
    }
}

/// A ZIP archive of `entries` (path in the archive, content), STORE method, UTF-8 names.
func storeZip(_ entries: [(name: String, data: Data)], date: Date = Date()) -> Data {
    let c = Calendar(identifier: .gregorian).dateComponents([.year, .month, .day, .hour, .minute, .second], from: date)
    let dosTime = (c.hour ?? 0) << 11 | (c.minute ?? 0) << 5 | (c.second ?? 0) / 2
    let dosDate = max(0, (c.year ?? 1980) - 1980) << 9 | (c.month ?? 1) << 5 | (c.day ?? 1)
    let utf8Names = 0x0800
    var out = Data(), central = Data()
    for entry in entries {
        let name = Data(entry.name.utf8)
        let crc = crc32(entry.data)
        let offset = out.count
        out.u32(0x0403_4B50)  // Local file header
        out.u16(20); out.u16(utf8Names); out.u16(0)  // Version needed, flags, STORE
        out.u16(dosTime); out.u16(dosDate)
        out.u32(crc); out.u32(entry.data.count); out.u32(entry.data.count)
        out.u16(name.count); out.u16(0)
        out.append(name)
        out.append(entry.data)
        central.u32(0x0201_4B50)  // Central directory header
        central.u16(0x0314); central.u16(20); central.u16(utf8Names); central.u16(0)  // Made by Unix, needed, flags, STORE
        central.u16(dosTime); central.u16(dosDate)
        central.u32(crc); central.u32(entry.data.count); central.u32(entry.data.count)
        central.u16(name.count); central.u16(0); central.u16(0)  // Name, extra, comment
        central.u16(0); central.u16(0); central.u32(0o100644 << 16)  // Disk, internal, external (-rw-r--r--)
        central.u32(offset)
        central.append(name)
    }
    let centralOffset = out.count
    out.append(central)
    out.u32(0x0605_4B50)  // End of central directory
    out.u16(0); out.u16(0); out.u16(entries.count); out.u16(entries.count)
    out.u32(central.count); out.u32(centralOffset); out.u16(0)
    return out
}

struct UnzipError: LocalizedError {
    var errorDescription: String? { t("file.notHaru") }
}

private func verifySafeZip(_ file: URL) throws {
    let attrs = try FileManager.default.attributesOfItem(atPath: file.path)
    guard let fileSize = (attrs[.size] as? NSNumber)?.intValue, fileSize >= 22, fileSize <= maxArchiveBytes else {
        throw UnzipError()
    }
    let data = try Data(contentsOf: file, options: .mappedIfSafe)
    var eocd = -1
    let minOffset = max(0, data.count - 22 - 0xFFFF)
    for i in stride(from: data.count - 22, through: minOffset, by: -1) where data.readU32(i) == 0x0605_4B50 {
        eocd = i
        break
    }
    guard eocd >= 0 else { throw UnzipError() }
    let count = data.readU16(eocd + 10)
    guard count <= maxArchiveEntries else { throw UnzipError() }
    var pos = Int(data.readU32(eocd + 16))
    var totalUncompressed = 0
    for _ in 0..<count {
        guard pos >= 0, pos + 46 <= data.count, data.readU32(pos) == 0x0201_4B50 else { throw UnzipError() }
        let uncompressed = data.readU32(pos + 24)
        let nameLen = data.readU16(pos + 28)
        let extraLen = data.readU16(pos + 30)
        let commentLen = data.readU16(pos + 32)
        let externalAttr = data.readU32(pos + 38)
        guard uncompressed != 0xFFFF_FFFF, pos + 46 + nameLen + extraLen + commentLen <= data.count else {
            throw UnzipError()
        }
        let unixType = (externalAttr >> 16) & 0o170000
        if unixType != 0 && unixType != 0o100000 && unixType != 0o040000 {
            throw UnzipError()  // Reject symlinks, devices, FIFOs
        }
        guard let name = String(data: data.subdata(in: (pos + 46)..<(pos + 46 + nameLen)), encoding: .utf8),
              !name.isEmpty, !name.hasPrefix("/"), !name.contains("\\"), !name.contains("\0"),
              !name.split(separator: "/").contains("..") else {
            throw UnzipError()
        }
        totalUncompressed += Int(uncompressed)
        guard totalUncompressed <= maxUncompressedBytes else { throw UnzipError() }
        pos += 46 + nameLen + extraLen + commentLen
    }
}

private func verifyExtractedTree(_ folder: URL) throws {
    guard let enumerator = FileManager.default.enumerator(
        at: folder, includingPropertiesForKeys: [.isSymbolicLinkKey, .isRegularFileKey, .fileSizeKey]) else {
        throw UnzipError()
    }
    var total = 0
    for case let url as URL in enumerator {
        let values = try url.resourceValues(forKeys: [.isSymbolicLinkKey, .isRegularFileKey, .fileSizeKey])
        if values.isSymbolicLink == true { throw UnzipError() }
        if values.isRegularFile == true {
            total += values.fileSize ?? 0
            if total > maxUncompressedBytes { throw UnzipError() }
        }
    }
}

/// Extracts a safe ZIP file (STORE or DEFLATE) into a new temporary folder; the caller removes it.
func unzip(_ file: URL) throws -> URL {
    try verifySafeZip(file)
    let folder = FileManager.default.temporaryDirectory.appendingPathComponent("haru-import-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
    let ditto = Process()
    ditto.executableURL = URL(fileURLWithPath: "/usr/bin/ditto")
    ditto.arguments = ["-x", "-k", file.path, folder.path]
    ditto.standardOutput = FileHandle.nullDevice
    ditto.standardError = FileHandle.nullDevice
    do {
        try ditto.run()
        ditto.waitUntilExit()
        guard ditto.terminationStatus == 0 else { throw UnzipError() }
        try verifyExtractedTree(folder)
    } catch {
        try? FileManager.default.removeItem(at: folder)
        throw UnzipError()
    }
    return folder
}
