// Native application lookup for apple-system-mcp. It replaces the System Events
// AppleScript the server used to find the frontmost application, an application by
// name or bundle identifier, and the running applications, using NSWorkspace instead,
// so it sends no Apple Events and needs no Automation permission. SystemBridge
// compiles it on first use.
//
// Usage: apple-system-apps-bridge <command> [arg]
//   frontmost            the frontmost application
//   bundle-id ID         the first running application with that bundle identifier
//   name NAME            the first running application with that name
//   installed-name NAME  the bundle identifier and path of the installed application with that name
//   installed-bundle-id ID  the same, for the installed application with that bundle identifier
//   running              every running regular (Dock) application
//
// Prints a JSON object {"name", "bundle_id", "process_id"} (a JSON array of them for
// `running`) on stdout. Errors go to stderr with exit status 1.
import AppKit
import Foundation

func fail(_ message: String) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(1)
}

// System Events names a process after its executable ("Contacts"), not the
// localized display name ("Contactes"); keep its naming.
func processName(_ app: NSRunningApplication) -> String {
    app.executableURL?.lastPathComponent ?? app.localizedName ?? ""
}

func record(_ app: NSRunningApplication) -> [String: Any] {
    ["name": processName(app), "bundle_id": app.bundleIdentifier ?? "", "process_id": Int(app.processIdentifier)]
}

func emit(_ object: Any) {
    guard let data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]) else {
        fail("Could not encode the result as JSON.")
    }
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write("\n".data(using: .utf8)!)
}

let arguments = Array(CommandLine.arguments.dropFirst())
guard let command = arguments.first else { fail("Missing command.") }
let argument = arguments.count > 1 ? arguments[1] : ""
// System Events' "application process whose background only is false" matches the
// regular (Dock) applications only; menu-bar agents count as background-only there.
let visible = NSWorkspace.shared.runningApplications.filter { $0.activationPolicy == .regular }

switch command {
case "frontmost":
    guard let app = NSWorkspace.shared.frontmostApplication else { fail("No frontmost application.") }
    emit(record(app))
case "bundle-id":
    guard let app = NSWorkspace.shared.runningApplications.first(where: { $0.bundleIdentifier == argument }) else {
        fail("Can't get application process whose bundle identifier is \"\(argument)\".")
    }
    emit(record(app))
case "name":
    let byName = NSWorkspace.shared.runningApplications.first {
        processName($0) == argument || $0.localizedName == argument
    }
    guard let app = byName else {
        fail("Can't get application process \"\(argument)\".")
    }
    emit(record(app))
case "installed-name", "installed-bundle-id":
    // Launch Services lookup of an installed (not necessarily running) application.
    // It returns any registered bundle anywhere on disk, so the caller checks `path`.
    let url = command == "installed-name"
        ? NSWorkspace.shared.fullPath(forApplication: argument).map { URL(fileURLWithPath: $0) }
        : NSWorkspace.shared.urlForApplication(withBundleIdentifier: argument)
    guard let url, let bundleID = Bundle(url: url)?.bundleIdentifier else {
        fail("Can't find an installed application \"\(argument)\".")
    }
    emit(["name": argument, "bundle_id": bundleID, "path": url.resolvingSymlinksInPath().path])
case "running":
    emit(visible.map(record))
default:
    fail("Unknown command '\(command)'.")
}
