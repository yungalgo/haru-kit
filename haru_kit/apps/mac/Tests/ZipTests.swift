// Swiftc -parse-as-library App/Zip.swift App/Strings.swift App/Strings.gen.swift Tests/Check.swift Tests/ZipTests.swift

import Foundation

@main
struct ZipTests {
    static func run(_ tool: String, _ args: [String]) -> (Int32, String) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: tool)
        p.arguments = args
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        try! p.run()
        let out = String(decoding: pipe.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
        p.waitUntilExit()
        return (p.terminationStatus, out)
    }

    static func main() throws {
        check(crc32(Data("123456789".utf8)) == 0xCBF4_3926, "CRC-32 check value")
        check(crc32(Data()) == 0, "CRC-32 of nothing")

        let audio = Data((0..<100_000).map { UInt8(truncatingIfNeeded: $0 * 7) })
        let entries = [
            (name: "haru.json", data: Data(#"{"format": 1, "kind": "haru-kit", "characters": [], "voices": ["u-0123456789ab"]}"#.utf8)),
            (name: "voices/u-0123456789ab.voice.json", data: Data(#"{"id": "u-0123456789ab", "name": "ハル"}"#.utf8)),
            (name: "voices/u-0123456789ab.wav", data: audio),
            (name: "images/ハル.png", data: Data()),
        ]
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("zip-test-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let file = dir.appendingPathComponent("test.haru")
        try storeZip(entries).write(to: file)

        // Info-ZIP's unzip checks every CRC and lists the method of each entry.
        let (status, test) = run("/usr/bin/unzip", ["-t", file.path])
        check(status == 0 && test.contains("No errors detected"), "unzip -t: \(test)")
        let (_, list) = run("/usr/bin/unzip", ["-v", file.path])
        check(list.components(separatedBy: "Stored").count - 1 == entries.count, "every entry is STORE: \(list)")
        check(!list.contains("Defl"), "no DEFLATE entry")

        // The reader (ditto) gets the same bytes and names back.
        let out = try unzip(file)
        defer { try? FileManager.default.removeItem(at: out) }
        for entry in entries {
            let data = try? Data(contentsOf: out.appendingPathComponent(entry.name))
            check(data == entry.data, "extracted \(entry.name)")
        }
        // Not a ZIP file: the reader fails with "not a .haru file".
        let bad = dir.appendingPathComponent("bad.haru")
        try Data("hello".utf8).write(to: bad)
        do {
            _ = try unzip(bad)
            check(false, "a file that is not a ZIP is refused")
        } catch {}

        // A ZIP entry with '..' path traversal is refused before extraction.
        let traversal = dir.appendingPathComponent("traversal.haru")
        try storeZip([(name: "../escape.txt", data: Data("x".utf8))]).write(to: traversal)
        do {
            _ = try unzip(traversal)
            check(false, "a ZIP with '..' path traversal is refused")
        } catch {}
        finish("ZipTests")
    }
}
