// A minimal check helper for the standalone test programs (run with Tests/run.sh).

import Foundation

private var failures = 0

func check(_ condition: Bool, _ what: String, file: String = #file, line: Int = #line) {
    if !condition {
        failures += 1
        print("FAIL \((file as NSString).lastPathComponent):\(line) \(what)")
    }
}

func finish(_ name: String) {
    if failures > 0 {
        print("\(name): \(failures) failed")
        exit(1)
    }
    print("\(name): ok")
}
