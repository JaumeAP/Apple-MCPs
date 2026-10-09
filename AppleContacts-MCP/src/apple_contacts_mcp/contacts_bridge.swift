// Native Contacts helper for apple-contacts-mcp. It replaces the AppleScript files the
// server used to run through osascript and talks to the Contacts framework
// (CNContactStore) instead, so it never launches or waits on Contacts.app and needs
// only the Privacy > Contacts permission. AppleContactsBridge compiles it on first use.
//
// Usage: apple-contacts-bridge <command> [args...]
// Each command takes the same positional arguments as the AppleScript of the same
// name and prints the same JSON object on stdout; errors go to stderr with exit 1.
//
//   permission_check
//   list_contacts LIMIT OFFSET
//   search_contacts QUERY LIMIT
//   get_contact ID
//   create_contact FIRST LAST ORG PHONES EMAILS NOTE
//   update_contact ID FIRST LAST ORG PHONES EMAILS NOTE
//   delete_contact ID
//
// PHONES and EMAILS are "label\u{1F}value" records joined by "\u{1E}"; for
// update_contact "__NOCHANGE__" leaves them untouched and anything else replaces
// the whole set. Empty FIRST/LAST/ORG/NOTE on update leave the field unchanged.
//
// When APPLE_CONTACTS_MCP_BACKUP_DIR is set, update_contact and delete_contact first
// save the contact there as a .vcf file (or .json when macOS refuses the vCard export).
import Contacts
import Foundation

let fieldSeparator: Character = "\u{1F}"
let recordSeparator: Character = "\u{1E}"
let noChangeSentinel = "__NOCHANGE__"

func fail(_ message: String) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(1)
}

func emit(_ object: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]) else {
        fail("Could not encode the result as JSON.")
    }
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write("\n".data(using: .utf8)!)
}

let store = CNContactStore()

func requireAccess() {
    switch CNContactStore.authorizationStatus(for: .contacts) {
    case .authorized:
        return
    case .denied, .restricted:
        fail("Not authorized to access Contacts. Allow it in System Settings > Privacy & Security > Contacts.")
    default:
        break
    }
    let semaphore = DispatchSemaphore(value: 0)
    var granted = false
    store.requestAccess(for: .contacts) { ok, _ in
        granted = ok
        semaphore.signal()
    }
    semaphore.wait()
    if !granted {
        fail("Not authorized to access Contacts. Allow it in System Settings > Privacy & Security > Contacts.")
    }
}

// The note field is deliberately absent: reading it needs an entitlement that an
// unsigned command-line tool cannot have, and the AppleScript version never
// returned it either.
let summaryKeys: [CNKeyDescriptor] = [
    CNContactIdentifierKey as CNKeyDescriptor,
    CNContactGivenNameKey as CNKeyDescriptor,
    CNContactFamilyNameKey as CNKeyDescriptor,
    CNContactOrganizationNameKey as CNKeyDescriptor,
    CNContactPhoneNumbersKey as CNKeyDescriptor,
    CNContactEmailAddressesKey as CNKeyDescriptor,
    CNContactFormatter.descriptorForRequiredKeys(for: .fullName),
]

// Contacts.app shows built-in labels as plain words; the framework stores them as
// "_$!<Word>!$_" constants. Map both ways so labels round-trip like before.
let labelNames: [(String, String)] = [
    (CNLabelHome, "home"), (CNLabelWork, "work"), (CNLabelOther, "other"), (CNLabelSchool, "school"),
    (CNLabelPhoneNumberMobile, "mobile"), (CNLabelPhoneNumberiPhone, "iPhone"),
    (CNLabelPhoneNumberMain, "main"), (CNLabelPhoneNumberHomeFax, "home fax"),
    (CNLabelPhoneNumberWorkFax, "work fax"), (CNLabelPhoneNumberOtherFax, "other fax"),
    (CNLabelPhoneNumberPager, "pager"), (CNLabelEmailiCloud, "iCloud"),
]

func displayLabel(_ raw: String?) -> String {
    guard let raw, !raw.isEmpty else { return "" }
    if let match = labelNames.first(where: { $0.0 == raw }) { return match.1 }
    if raw.hasPrefix("_$!<") && raw.hasSuffix(">!$_") {
        return String(raw.dropFirst(4).dropLast(4)).lowercased()
    }
    return raw
}

func storedLabel(_ display: String) -> String {
    labelNames.first(where: { $0.1.lowercased() == display.lowercased() })?.0 ?? display
}

func methods(_ raw: String) -> [(label: String, value: String)] {
    raw.split(separator: recordSeparator, omittingEmptySubsequences: true).compactMap { record in
        let parts = record.split(separator: fieldSeparator, maxSplits: 1, omittingEmptySubsequences: false)
        guard parts.count == 2 else { return nil }
        return (String(parts[0]), String(parts[1]))
    }
}

func displayName(_ contact: CNContact) -> String {
    let formatted = CNContactFormatter.string(from: contact, style: .fullName) ?? ""
    return formatted.isEmpty ? contact.organizationName : formatted
}

func personJSON(_ contact: CNContact) -> [String: Any] {
    let phones = contact.phoneNumbers.map { ["label": displayLabel($0.label), "value": $0.value.stringValue] }
    let emails = contact.emailAddresses.map { ["label": displayLabel($0.label), "value": $0.value as String] }
    return [
        "contact_id": contact.identifier,
        "name": displayName(contact),
        "first_name": contact.givenName,
        "last_name": contact.familyName,
        "organization": contact.organizationName,
        "phone_count": phones.count,
        "email_count": emails.count,
        "phones": phones,
        "emails": emails,
        "note": "",
    ]
}

func allContacts(keys: [CNKeyDescriptor] = summaryKeys) -> [CNContact] {
    let request = CNContactFetchRequest(keysToFetch: keys)
    request.sortOrder = .userDefault
    var result: [CNContact] = []
    do {
        try store.enumerateContacts(with: request) { contact, _ in result.append(contact) }
    } catch {
        fail("Could not read Contacts: \(error.localizedDescription)")
    }
    return result
}

func findContact(_ id: String, keys: [CNKeyDescriptor] = summaryKeys) -> CNContact? {
    let predicate = CNContact.predicateForContacts(withIdentifiers: [id])
    do {
        return try store.unifiedContacts(matching: predicate, keysToFetch: keys).first
    } catch {
        fail("Could not read Contacts: \(error.localizedDescription)")
    }
}

// Writes to "<name>.<ext>", or "<name>-2.<ext>", "<name>-3.<ext>"... when that file
// already exists, so two backups never overwrite each other.
func writeNewFile(_ data: Data, dir: URL, name: String, ext: String) throws {
    var suffix = 1
    while true {
        let file = dir.appendingPathComponent(suffix == 1 ? name : "\(name)-\(suffix)").appendingPathExtension(ext)
        do {
            try data.write(to: file, options: .withoutOverwriting)
            return
        } catch where FileManager.default.fileExists(atPath: file.path) {
            suffix += 1
        }
    }
}

func backup(_ contact: CNContact) {
    guard let rawDir = ProcessInfo.processInfo.environment["APPLE_CONTACTS_MCP_BACKUP_DIR"], !rawDir.isEmpty else {
        return
    }
    let dir = URL(fileURLWithPath: (rawDir as NSString).expandingTildeInPath)
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    let stamp = formatter.string(from: Date()).replacingOccurrences(of: ":", with: "-")
    let safeId = contact.identifier.map { $0.isLetter || $0.isNumber || $0 == "-" ? $0 : "_" }
    let name = "\(stamp)_\(String(safeId))"
    do {
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let vCardKeys = [CNContactVCardSerialization.descriptorForRequiredKeys()]
        if let full = try? store.unifiedContact(withIdentifier: contact.identifier, keysToFetch: vCardKeys),
           let data = try? CNContactVCardSerialization.data(with: [full]) {
            try writeNewFile(data, dir: dir, name: name, ext: "vcf")
        } else {
            let data = try JSONSerialization.data(withJSONObject: personJSON(contact), options: [.prettyPrinted, .sortedKeys])
            try writeNewFile(data, dir: dir, name: name, ext: "json")
        }
    } catch {
        fail("Backup failed, nothing was changed: \(error.localizedDescription)")
    }
}

let noteUnavailableMessage = "Notes cannot be written: macOS reserves the note field for apps with a special entitlement. Leave note empty."

func setNote(_ note: String, on contact: CNMutableContact) {
    // Setting a note needs the same entitlement as reading one. Report it plainly
    // instead of letting the framework throw an Objective-C exception.
    guard contact.isKeyAvailable(CNContactNoteKey) else {
        fail(noteUnavailableMessage)
    }
    contact.note = note
}

func execute(_ request: CNSaveRequest) {
    do {
        try store.execute(request)
    } catch {
        fail("Contacts refused the change: \(error.localizedDescription)")
    }
}

func intArgument(_ args: [String], _ index: Int, _ name: String) -> Int {
    guard index < args.count, let value = Int(args[index]) else { fail("\(name) must be an integer.") }
    return value
}

func argument(_ args: [String], _ index: Int) -> String {
    index < args.count ? args[index] : ""
}

let arguments = Array(CommandLine.arguments.dropFirst())
guard let command = arguments.first else { fail("Missing command.") }
let args = Array(arguments.dropFirst())
requireAccess()

switch command {
case "permission_check":
    emit(["ok": true, "count": allContacts(keys: [CNContactIdentifierKey as CNKeyDescriptor]).count])

case "list_contacts":
    let limit = max(0, intArgument(args, 0, "LIMIT"))
    let offset = max(0, intArgument(args, 1, "OFFSET"))
    let contacts = allContacts()
    let page = contacts.dropFirst(offset).prefix(limit)
    emit(["items": page.map(personJSON), "total": contacts.count])

case "search_contacts":
    let query = argument(args, 0)
    let limit = max(0, intArgument(args, 1, "LIMIT"))
    // Same rule as the AppleScript: name or organization contains the query, ignoring case.
    let matches = allContacts().filter {
        displayName($0).range(of: query, options: .caseInsensitive) != nil
            || $0.organizationName.range(of: query, options: .caseInsensitive) != nil
    }
    emit(["items": matches.prefix(limit).map(personJSON)])

case "get_contact":
    if let contact = findContact(argument(args, 0)) {
        emit(["found": true, "contact": personJSON(contact)])
    } else {
        emit(["found": false])
    }

case "create_contact":
    let contact = CNMutableContact()
    contact.givenName = argument(args, 0)
    contact.familyName = argument(args, 1)
    contact.organizationName = argument(args, 2)
    contact.phoneNumbers = methods(argument(args, 3)).map {
        CNLabeledValue(label: storedLabel($0.label), value: CNPhoneNumber(stringValue: $0.value))
    }
    contact.emailAddresses = methods(argument(args, 4)).map {
        CNLabeledValue(label: storedLabel($0.label), value: $0.value as NSString)
    }
    // A fresh CNMutableContact reports the note key as available, so setNote's guard
    // would pass and the save would fail on the missing entitlement. Refuse up front.
    if !argument(args, 5).isEmpty { fail(noteUnavailableMessage) }
    let request = CNSaveRequest()
    request.add(contact, toContainerWithIdentifier: nil)
    execute(request)
    emit(["contact_id": contact.identifier, "name": displayName(contact), "created": true])

case "update_contact":
    let id = argument(args, 0)
    let note = argument(args, 6)
    guard let original = findContact(id) else { fail("Can’t get person id \"\(id)\".") }
    var keys = summaryKeys
    if !note.isEmpty { keys.append(CNContactNoteKey as CNKeyDescriptor) }
    let fetched = note.isEmpty ? original : ((try? store.unifiedContact(withIdentifier: id, keysToFetch: keys)) ?? original)
    let contact = fetched.mutableCopy() as! CNMutableContact
    if !argument(args, 1).isEmpty { contact.givenName = argument(args, 1) }
    if !argument(args, 2).isEmpty { contact.familyName = argument(args, 2) }
    if !argument(args, 3).isEmpty { contact.organizationName = argument(args, 3) }
    if argument(args, 4) != noChangeSentinel {
        contact.phoneNumbers = methods(argument(args, 4)).map {
            CNLabeledValue(label: storedLabel($0.label), value: CNPhoneNumber(stringValue: $0.value))
        }
    }
    if argument(args, 5) != noChangeSentinel {
        contact.emailAddresses = methods(argument(args, 5)).map {
            CNLabeledValue(label: storedLabel($0.label), value: $0.value as NSString)
        }
    }
    if !note.isEmpty { setNote(note, on: contact) }
    backup(original)
    let request = CNSaveRequest()
    request.update(contact)
    execute(request)
    emit(["contact_id": id, "name": displayName(contact), "updated": true])

case "delete_contact":
    let id = argument(args, 0)
    guard let original = findContact(id) else {
        emit(["contact_id": id, "deleted": false])
        exit(0)
    }
    backup(original)
    let request = CNSaveRequest()
    request.delete(original.mutableCopy() as! CNMutableContact)
    execute(request)
    // Deleting a unified contact can leave some of its linked cards behind, so report
    // what the store holds now rather than assuming the save removed everything.
    emit(["contact_id": id, "deleted": findContact(id) == nil])

default:
    fail("Unknown command '\(command)'.")
}
