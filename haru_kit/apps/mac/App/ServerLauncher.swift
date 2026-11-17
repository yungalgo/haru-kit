// Modified from higurashi-ai/Haru (Apache-2.0) for haru-kit.
// Starts the Python model server when none is listening, and stops it with the app.

import Foundation

/// The percent of a model file download in a server log line (a Hugging Face
/// Progress bar such as "model.safetensors:  37%|███▋  | 3.9G/10.5G"); nil for
/// Other lines and for the bar that counts files.
func downloadProgress(_ line: String) -> Int? {
    guard !line.hasPrefix("Fetching"), let bar = line.range(of: #"(\d{1,3})%\|"#, options: .regularExpression) else {
        return nil
    }
    return Int(line[bar].dropLast(2)).map { min($0, 100) }
}

final class ServerLauncher {
    private var process: Process?
    private var pipe: Pipe?
    private var ready = false

    /// Connect to a running server, or start one and connect once it is ready.
    /// Callbacks run on the main queue.
    func connect(socket: String, python: String, arguments: [String],
                 onLog: @escaping (String) -> Void,
                 onConnected: @escaping (Connection) -> Void,
                 onFailure: @escaping (String) -> Void) {
        if let conn = Connection(path: socket) {
            onConnected(conn)
            return
        }
        start(socket: socket, python: python, arguments: arguments,
              onLog: onLog, onConnected: onConnected, onFailure: onFailure)
    }

    /// This app started the running server (only then can it restart it).
    var ownsServer: Bool { process?.isRunning == true }

    /// Stop the server this app started, wait until it has exited, and start
    /// One with these arguments. Servers started elsewhere are never stopped.
    func restart(socket: String, python: String, arguments: [String],
                 onLog: @escaping (String) -> Void,
                 onConnected: @escaping (Connection) -> Void,
                 onFailure: @escaping (String) -> Void) {
        let old = process
        pipe?.fileHandleForReading.readabilityHandler = nil
        pipe = nil
        process = nil  // Its exit is not a start failure
        DispatchQueue.global().async {
            if let old, old.isRunning {
                old.terminate()
                for _ in 0..<100 where old.isRunning { usleep(100_000) }
                if old.isRunning { kill(old.processIdentifier, SIGKILL) }
                old.waitUntilExit()
            }
            DispatchQueue.main.async {
                self.start(socket: socket, python: python, arguments: arguments,
                           onLog: onLog, onConnected: onConnected, onFailure: onFailure)
            }
        }
    }

    private func start(socket: String, python: String, arguments: [String],
                       onLog: @escaping (String) -> Void,
                       onConnected: @escaping (Connection) -> Void,
                       onFailure: @escaping (String) -> Void) {
        ready = false
        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = ["-m", "haru_mlx.native", "--socket", socket] + arguments
        var env = ProcessInfo.processInfo.environment
        env["PYTHONUNBUFFERED"] = "1"
        p.environment = env
        let pipe = Pipe()
        self.pipe = pipe
        p.standardOutput = pipe
        p.standardError = pipe
        var pending = Data()
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if data.isEmpty {
                handle.readabilityHandler = nil
                return
            }
            pending.append(data)
            while let newline = pending.firstIndex(where: { $0 == 0x0A || $0 == 0x0D }) {
                let lineBytes = pending[..<newline]
                pending.removeSubrange(...newline)
                let line = String(decoding: lineBytes, as: UTF8.self).trimmingCharacters(in: .whitespaces)
                guard !line.isEmpty else { continue }
                DispatchQueue.main.async { onLog(line) }
                if line.contains("[native] ready on"), let self, !self.ready {
                    self.ready = true
                    DispatchQueue.global().async {
                        let conn = Connection(path: socket)
                        DispatchQueue.main.async {
                            if let conn { onConnected(conn) } else { onFailure(t("error.connect")) }
                        }
                    }
                }
            }
        }
        p.terminationHandler = { [weak self] proc in
            pipe.fileHandleForReading.readabilityHandler = nil
            guard let self, !self.ready, self.process === proc else { return }
            DispatchQueue.main.async {
                onFailure(t("error.exited", ["code": proc.terminationStatus]))
            }
        }
        do {
            try p.run()
            process = p
            onLog("$ \(python) -m haru_mlx.native \(arguments.joined(separator: " "))")
        } catch {
            pipe.fileHandleForReading.readabilityHandler = nil
            onFailure(t("error.launch", ["error": error.localizedDescription]) + "\nPython: \(python)")
        }
    }

    /// Stop the server if this app started it.
    func stop() {
        pipe?.fileHandleForReading.readabilityHandler = nil
        pipe = nil
        if let p = process, p.isRunning { p.terminate() }
        process = nil
    }
}
