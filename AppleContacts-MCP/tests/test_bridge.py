import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from apple_contacts_mcp import contacts_bridge
from apple_contacts_mcp.contacts_bridge import AppleContactsBridge, ContactsBridgeError

# For tests that replace _run_script: the helper is never compiled or run.
UNUSED_HELPER = (Path("/tmp/contacts_bridge.swift"), Path("/tmp/apple-contacts-bridge"))


def hashed_binary(bridge: AppleContactsBridge) -> Path:
    digest = hashlib.sha256(bridge.helper_source.read_bytes()).hexdigest()[:12]
    return bridge.helper_binary.with_name(f"{bridge.helper_binary.name}-{digest}")


def ready_bridge(tmp_path: Path) -> AppleContactsBridge:
    """A bridge whose hashed helper binary already exists, so nothing is compiled."""
    source = tmp_path / "contacts_bridge.swift"
    source.touch()
    bridge = AppleContactsBridge(source, tmp_path / "apple-contacts-bridge")
    hashed_binary(bridge).touch()
    return bridge


def contact_item(
    contact_id: str,
    *,
    name: str = "Directory Contact",
    phone: str = "",
    email: str = "",
) -> dict[str, object]:
    phones = [{"label": "mobile", "value": phone}] if phone else []
    emails = [{"label": "work", "value": email}] if email else []
    return {
        "contact_id": contact_id,
        "name": name,
        "first_name": name.split()[0],
        "last_name": name.split()[-1],
        "organization": "",
        "phone_count": len(phones),
        "email_count": len(emails),
        "phones": phones,
        "emails": emails,
    }


def test_search_contacts_matches_phone_number(monkeypatch) -> None:
    bridge = AppleContactsBridge(*UNUSED_HELPER)

    def fake_run_script(script_name: str, *args: str) -> dict[str, object]:
        if script_name == "search_contacts.applescript":
            return {"items": []}
        assert script_name == "list_contacts.applescript"
        return {
            "total": 1,
            "items": [
                {
                    "contact_id": "contact-1",
                    "name": "Alice Doe",
                    "first_name": "Alice",
                    "last_name": "Doe",
                    "organization": "Example",
                    "phone_count": 1,
                    "email_count": 1,
                    "phones": [{"label": "mobile", "value": "+1 (555) 123-4567"}],
                    "emails": [{"label": "work", "value": "alice@example.com"}],
                }
            ]
        }

    monkeypatch.setattr(bridge, "_run_script", fake_run_script)
    contacts = bridge.search_contacts("5551234567")

    assert len(contacts) == 1
    assert contacts[0].contact_id == "contact-1"


@pytest.mark.parametrize(
    ("query", "field", "value"),
    [
        ("5550001001", "phone", "+1 (555) 000-1001"),
        ("late@example.com", "email", "late@example.com"),
    ],
)
def test_search_contacts_scans_past_first_thousand(monkeypatch, query, field, value) -> None:
    bridge = AppleContactsBridge(*UNUSED_HELPER)
    monkeypatch.setattr(contacts_bridge, "DIRECTORY_SCAN_PAGE_SIZE", 1000)
    directory = [contact_item(f"contact-{index}") for index in range(1001)]
    directory.append(
        contact_item(
            "contact-late",
            name="Late Match",
            phone=value if field == "phone" else "",
            email=value if field == "email" else "",
        )
    )
    calls: list[int] = []

    def fake_run_script(script_name: str, *args: str) -> dict[str, object]:
        if script_name == "search_contacts.applescript":
            return {"items": []}
        assert script_name == "list_contacts.applescript"
        limit, offset = map(int, args)
        calls.append(offset)
        return {"items": directory[offset : offset + limit], "total": len(directory)}

    monkeypatch.setattr(bridge, "_run_script", fake_run_script)

    matches = bridge.search_contacts(query)

    assert [contact.contact_id for contact in matches] == ["contact-late"]
    assert calls == [0, 1000]


def test_search_contacts_keeps_exact_matches_before_earlier_partial_matches(monkeypatch) -> None:
    bridge = AppleContactsBridge(*UNUSED_HELPER)
    directory = [contact_item("contact-partial", phone="+1 555 000 10010")]
    directory.extend(contact_item(f"contact-{index}") for index in range(1, 1000))
    directory.append(contact_item("contact-exact", phone="555 000 1001"))

    def fake_run_script(script_name: str, *args: str) -> dict[str, object]:
        limit, offset = map(int, args)
        return {"items": directory[offset : offset + limit], "total": len(directory)}

    monkeypatch.setattr(bridge, "_run_script", fake_run_script)

    matches = bridge.search_contacts("5550001001")

    assert [contact.contact_id for contact in matches] == ["contact-exact", "contact-partial"]


def test_resolve_recipient_finds_contact_after_first_thousand(monkeypatch) -> None:
    bridge = AppleContactsBridge(*UNUSED_HELPER)
    directory = [contact_item(f"contact-{index}") for index in range(1001)]
    late_contact = contact_item("contact-late", name="Late Recipient", phone="+1 555 777 9999")
    directory.append(late_contact)

    def fake_run_script(script_name: str, *args: str) -> dict[str, object]:
        if script_name == "list_contacts.applescript":
            limit, offset = map(int, args)
            return {"items": directory[offset : offset + limit], "total": len(directory)}
        if script_name == "get_contact.applescript":
            return {"found": True, "contact": {**late_contact, "note": ""}}
        raise AssertionError(f"Unexpected script {script_name}")

    monkeypatch.setattr(bridge, "_run_script", fake_run_script)

    recipient = bridge.resolve_message_recipient("5557779999")

    assert recipient.contact.contact_id == "contact-late"
    assert recipient.recipient_value == "+1 555 777 9999"


def test_search_contacts_prefers_direct_name_search(monkeypatch) -> None:
    bridge = AppleContactsBridge(*UNUSED_HELPER)

    def fake_run_script(script_name: str, *args: str) -> dict[str, object]:
        assert script_name == "search_contacts.applescript"
        assert args == ("Sona", "25")
        return {
            "items": [
                {
                    "contact_id": "contact-sona",
                    "name": "Sonali (Sona) Chellan",
                    "first_name": "",
                    "last_name": "",
                    "organization": "",
                    "phone_count": 1,
                    "email_count": 1,
                    "phones": [{"label": "Phone", "value": "(832) 361-5976"}],
                    "emails": [{"label": "Email", "value": "sonalichellan@gmail.com"}],
                }
            ]
        }

    monkeypatch.setattr(bridge, "_run_script", fake_run_script)

    contacts = bridge.search_contacts("Sona")

    assert len(contacts) == 1
    assert contacts[0].name == "Sonali (Sona) Chellan"


def test_get_contact_raises_when_missing(monkeypatch) -> None:
    bridge = AppleContactsBridge(*UNUSED_HELPER)

    def fake_run_script(script_name: str, *args: str) -> dict[str, object]:
        assert script_name == "get_contact.applescript"
        return {"found": False}

    monkeypatch.setattr(bridge, "_run_script", fake_run_script)

    try:
        bridge.get_contact("contact-1")
    except ContactsBridgeError as exc:
        assert exc.error_code == "CONTACT_NOT_FOUND"
    else:
        raise AssertionError("Expected ContactsBridgeError")


def test_run_script_times_out_and_terminates_child(monkeypatch, tmp_path) -> None:
    script_path = tmp_path / "permission_check.applescript"
    bridge = ready_bridge(tmp_path)
    real_popen = subprocess.Popen

    def sleeping_popen(command, **kwargs):
        return real_popen([sys.executable, "-c", "import time; time.sleep(10)"], **kwargs)

    monkeypatch.setattr(contacts_bridge, "SCRIPT_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(contacts_bridge.subprocess, "Popen", sleeping_popen)

    with pytest.raises(ContactsBridgeError, match="timeout") as exc_info:
        bridge._run_script(script_path.name)

    assert exc_info.value.error_code == "APPLESCRIPT_TIMEOUT"


def test_stop_process_handles_exit_before_terminate() -> None:
    class ExitedProcess:
        def terminate(self) -> None:
            raise ProcessLookupError

        def wait(self, timeout=None) -> int:
            return 0

    AppleContactsBridge(*UNUSED_HELPER)._stop_process(ExitedProcess())


def test_run_script_rejects_oversized_output(monkeypatch, tmp_path) -> None:
    script_path = tmp_path / "permission_check.applescript"
    bridge = ready_bridge(tmp_path)
    real_popen = subprocess.Popen

    def noisy_popen(command, **kwargs):
        return real_popen([sys.executable, "-c", "print('x' * 1024)"], **kwargs)

    monkeypatch.setattr(contacts_bridge, "MAX_SCRIPT_OUTPUT_BYTES", 32)
    monkeypatch.setattr(contacts_bridge.subprocess, "Popen", noisy_popen)

    with pytest.raises(ContactsBridgeError) as exc_info:
        bridge._run_script(script_path.name)

    assert exc_info.value.error_code == "APPLESCRIPT_OUTPUT_TOO_LARGE"


def test_search_contacts_matches_parenthetical_nickname(monkeypatch) -> None:
    bridge = AppleContactsBridge(*UNUSED_HELPER)

    def fake_run_script(script_name: str, *args: str) -> dict[str, object]:
        assert script_name == "search_contacts.applescript"
        assert args == ("Sona", "25") or args == ("Sonali", "25")
        return {
            "items": [
                {
                    "contact_id": "contact-sona",
                    "name": "Sonali (Sona) Chellan",
                    "first_name": "",
                    "last_name": "",
                    "organization": "",
                    "phone_count": 0,
                    "email_count": 1,
                    "phones": [],
                    "emails": [{"label": "home", "value": "sona@example.com"}],
                }
            ]
        }

    monkeypatch.setattr(bridge, "_run_script", fake_run_script)

    sona_matches = bridge.search_contacts("Sona")
    sonali_matches = bridge.search_contacts("Sonali")

    assert len(sona_matches) == 1
    assert sona_matches[0].contact_id == "contact-sona"
    assert len(sonali_matches) == 1
    assert sonali_matches[0].contact_id == "contact-sona"


def test_create_contact_serializes_methods(monkeypatch) -> None:
    bridge = AppleContactsBridge(*UNUSED_HELPER)
    captured: dict[str, object] = {}

    def fake_run_script(script_name: str, *args: str) -> dict[str, object]:
        captured["script_name"] = script_name
        captured["args"] = args
        return {"contact_id": "contact-1", "name": "Alice Doe", "created": True}

    monkeypatch.setattr(bridge, "_run_script", fake_run_script)

    result = bridge.create_contact(
        first_name="Alice",
        last_name="Doe",
        phones=[bridge._normalize_methods([{"label": "mobile", "value": "+15551234567"}])[0]],
        emails=[bridge._normalize_methods([{"label": "work", "value": "alice@example.com"}])[0]],
    )

    assert result.contact_id == "contact-1"
    assert captured["script_name"] == "create_contact.applescript"
    assert "+15551234567" in captured["args"][3]
    assert "alice@example.com" in captured["args"][4]


def test_update_contact_supports_no_change_sentinel(monkeypatch) -> None:
    bridge = AppleContactsBridge(*UNUSED_HELPER)
    calls: list[tuple[str, tuple[str, ...]]] = []

    def fake_run_script(script_name: str, *args: str) -> dict[str, object]:
        calls.append((script_name, args))
        if script_name == "update_contact.applescript":
            return {"contact_id": "contact-1", "name": "Alice Doe", "updated": True}
        if script_name == "get_contact.applescript":
            return {
                "found": True,
                "contact": {
                    "contact_id": "contact-1",
                    "name": "Alice Doe",
                    "first_name": "Alice",
                    "last_name": "Doe",
                    "organization": "",
                    "phone_count": 1,
                    "email_count": 1,
                    "phones": [{"label": "mobile", "value": "+15551234567"}],
                    "emails": [{"label": "work", "value": "alice@example.com"}],
                    "note": "",
                },
            }
        raise AssertionError(f"Unexpected script {script_name}")

    monkeypatch.setattr(bridge, "_run_script", fake_run_script)

    detail = bridge.update_contact("contact-1", first_name="Alicia")

    assert detail.first_name == "Alice"
    assert calls[0][0] == "update_contact.applescript"
    assert calls[0][1][4] == "__NOCHANGE__"
    assert calls[0][1][5] == "__NOCHANGE__"


def test_update_contact_treats_empty_method_lists_as_no_change(monkeypatch) -> None:
    bridge = AppleContactsBridge(*UNUSED_HELPER)
    calls: list[tuple[str, tuple[str, ...]]] = []

    def fake_run_script(script_name: str, *args: str) -> dict[str, object]:
        calls.append((script_name, args))
        if script_name == "update_contact.applescript":
            return {"updated": True}
        return {"found": True, "contact": contact_item("contact-1", name="Alice Doe", phone="+15551234567")}

    monkeypatch.setattr(bridge, "_run_script", fake_run_script)

    bridge.update_contact("contact-1", phones=[], emails=[])

    assert calls[0][1][4:6] == ("__NOCHANGE__", "__NOCHANGE__")


def test_resolve_recipient_rejects_several_name_matches(monkeypatch) -> None:
    bridge = AppleContactsBridge(*UNUSED_HELPER)
    # "Ann" exactly matches only the first contact; before the fix the exact match won
    # over the second name match, and a letters-only query normalized to "" exact-matched
    # any digitless phone.
    items = [contact_item("ann", name="Ann Smith"), contact_item("joanne", name="Joanne Roe", phone="+15550001111")]
    digitless = bridge._normalize_summary(contact_item("other", name="Other Person", phone="mobile"))

    def fake_run_script(script_name: str, *args: str) -> dict[str, object]:
        assert script_name == "search_contacts.applescript"
        return {"items": items[: int(args[1])]}

    monkeypatch.setattr(bridge, "_run_script", fake_run_script)

    assert not bridge._is_exact_match(digitless, "ann", bridge._normalize_lookup_value("Ann"))
    with pytest.raises(ContactsBridgeError) as failure:
        bridge.resolve_message_recipient("Ann")
    assert failure.value.error_code == "AMBIGUOUS_CONTACT"


@pytest.mark.parametrize(
    ("query", "target", "other"),
    [
        ("ann@example.com", contact_item("ann", name="Ann Smith", email="ann@example.com"), contact_item("joann", name="Joann Roe", email="joann@example.com")),
        ("+15551234567", contact_item("plain", name="Plain Number", phone="+15551234567"), contact_item("ext", name="Ext Number", phone="+1 555 123 4567 ext. 89")),
    ],
)
def test_resolve_recipient_complete_value_ignores_partial_match_and_returns_queried_method(monkeypatch, query, target, other) -> None:
    bridge = AppleContactsBridge(*UNUSED_HELPER)
    # The queried value is the target's second method: the result must be that one.
    decoy = {"label": "home", "value": "+15550000000" if target["phones"] else "decoy@example.com"}
    target_detail = {**target, "note": ""}
    methods_key = "phones" if target["phones"] else "emails"
    target_detail[methods_key] = [decoy, *target[methods_key]]

    def fake_run_script(script_name: str, *args: str) -> dict[str, object]:
        if script_name == "list_contacts.applescript":
            return {"total": 2, "items": [other, target]}
        assert script_name == "get_contact.applescript"
        assert args == (target["contact_id"],)
        return {"found": True, "contact": target_detail}

    monkeypatch.setattr(bridge, "_run_script", fake_run_script)

    result = bridge.resolve_message_recipient(query, channel="any")

    assert result.contact.contact_id == target["contact_id"]
    assert result.recipient_value == target[methods_key][0]["value"]


def test_resolve_recipient_rejects_complete_value_held_by_two_contacts(monkeypatch) -> None:
    bridge = AppleContactsBridge(*UNUSED_HELPER)
    items = [contact_item("one", email="shared@example.com"), contact_item("two", email="Shared@Example.com")]

    def fake_run_script(script_name: str, *args: str) -> dict[str, object]:
        assert script_name == "list_contacts.applescript"
        return {"total": 2, "items": items}

    monkeypatch.setattr(bridge, "_run_script", fake_run_script)

    with pytest.raises(ContactsBridgeError) as failure:
        bridge.resolve_message_recipient("shared@example.com", channel="email")
    assert failure.value.error_code == "AMBIGUOUS_CONTACT"
    assert "contact_id" not in (failure.value.suggestion or "")


@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("swiftc") is None,
    reason="swiftc is only available on macOS with the Xcode tools",
)
def test_contacts_helper_compiles() -> None:
    helper_source = Path(__file__).resolve().parents[1] / "src" / "apple_contacts_mcp" / "contacts_bridge.swift"
    completed = subprocess.run(
        ["swiftc", "-typecheck", str(helper_source)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_run_script_calls_helper_with_command_name(monkeypatch, tmp_path) -> None:
    bridge = ready_bridge(tmp_path)
    seen: list[list[str]] = []

    class FinishedProcess:
        returncode = 0

        def poll(self) -> int:
            return 0

    def fake_popen(command, *, stdout, stderr):
        seen.append(command)
        stdout.write(b'{"found": false}')
        return FinishedProcess()

    monkeypatch.setattr(contacts_bridge.subprocess, "Popen", fake_popen)

    assert bridge._run_script("get_contact.applescript", "contact-1") == {"found": False}
    assert seen == [[str(hashed_binary(bridge)), "get_contact", "contact-1"]]


def fake_swiftc(calls: list[list[str]], returncode: int = 0):
    def fake_run(command, **kwargs):
        calls.append(command)
        assert kwargs["timeout"] == contacts_bridge.HELPER_COMPILE_TIMEOUT_SECONDS
        Path(command[-1]).touch()
        return subprocess.CompletedProcess(command, returncode, "", "error: boom" if returncode else "")

    return fake_run


def test_ensure_helper_compiles_once_to_hashed_name(monkeypatch, tmp_path) -> None:
    source = tmp_path / "contacts_bridge.swift"
    source.write_text("// v1")
    bridge = AppleContactsBridge(source, tmp_path / "build" / "apple-contacts-bridge")
    calls: list[list[str]] = []
    monkeypatch.setattr(contacts_bridge.subprocess, "run", fake_swiftc(calls))

    first = bridge._ensure_helper()
    second = bridge._ensure_helper()

    expected = hashed_binary(bridge)
    temporary = expected.with_name(f"{expected.name}.{os.getpid()}.tmp")
    assert first == second == expected
    assert expected.exists() and not temporary.exists()
    assert calls == [["swiftc", "-O", str(source), "-o", str(temporary)]]


def test_ensure_helper_reuses_existing_hashed_binary(monkeypatch, tmp_path) -> None:
    bridge = ready_bridge(tmp_path)

    def unexpected_run(command, **kwargs):
        raise AssertionError("swiftc must not run when the hashed binary exists")

    monkeypatch.setattr(contacts_bridge.subprocess, "run", unexpected_run)

    assert bridge._ensure_helper() == hashed_binary(bridge)


def test_ensure_helper_recompiles_changed_source_under_new_name(monkeypatch, tmp_path) -> None:
    source = tmp_path / "contacts_bridge.swift"
    source.write_text("// v1")
    bridge = AppleContactsBridge(source, tmp_path / "apple-contacts-bridge")
    calls: list[list[str]] = []
    monkeypatch.setattr(contacts_bridge.subprocess, "run", fake_swiftc(calls))

    old_binary = bridge._ensure_helper()
    source.write_text("// v2")
    new_binary = bridge._ensure_helper()

    assert old_binary != new_binary
    assert old_binary.exists() and new_binary.exists()
    assert len(calls) == 2


def test_ensure_helper_removes_temporary_file_on_failure(monkeypatch, tmp_path) -> None:
    source = tmp_path / "contacts_bridge.swift"
    source.touch()
    bridge = AppleContactsBridge(source, tmp_path / "apple-contacts-bridge")
    calls: list[list[str]] = []
    monkeypatch.setattr(contacts_bridge.subprocess, "run", fake_swiftc(calls, returncode=1))

    with pytest.raises(ContactsBridgeError, match="boom") as exc_info:
        bridge._ensure_helper()

    assert exc_info.value.error_code == "HELPER_COMPILE_FAILED"
    assert not Path(calls[0][-1]).exists()
    assert not hashed_binary(bridge).exists()


def test_ensure_helper_maps_compile_timeout(monkeypatch, tmp_path) -> None:
    source = tmp_path / "contacts_bridge.swift"
    source.touch()
    bridge = AppleContactsBridge(source, tmp_path / "apple-contacts-bridge")

    def slow_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(contacts_bridge.subprocess, "run", slow_run)

    with pytest.raises(ContactsBridgeError, match="timeout") as exc_info:
        bridge._ensure_helper()

    assert exc_info.value.error_code == "HELPER_COMPILE_FAILED"


def test_ensure_helper_reports_missing_source(tmp_path) -> None:
    bridge = AppleContactsBridge(tmp_path / "missing.swift", tmp_path / "apple-contacts-bridge")

    with pytest.raises(ContactsBridgeError) as exc_info:
        bridge._ensure_helper()

    assert exc_info.value.error_code == "HELPER_SOURCE_MISSING"


def test_ensure_helper_reports_compile_failure(monkeypatch, tmp_path) -> None:
    source = tmp_path / "contacts_bridge.swift"
    source.touch()
    bridge = AppleContactsBridge(source, tmp_path / "apple-contacts-bridge")
    monkeypatch.setattr(
        contacts_bridge.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, "", "error: boom"),
    )

    with pytest.raises(ContactsBridgeError, match="boom") as exc_info:
        bridge._ensure_helper()

    assert exc_info.value.error_code == "HELPER_COMPILE_FAILED"


def test_find_duplicates_groups_by_shared_email_and_name(monkeypatch) -> None:
    bridge = AppleContactsBridge(*UNUSED_HELPER)

    def fake_run_script(script_name: str, *args: str) -> dict[str, object]:
        if script_name == "list_contacts.applescript":
            return {
                "total": 2,
                "items": [
                    {
                        "contact_id": "contact-1",
                        "name": "Example Person",
                        "first_name": "Example",
                        "last_name": "Person",
                        "organization": "",
                        "phone_count": 1,
                        "email_count": 1,
                        "phones": [{"label": "mobile", "value": "+1 (555) 123-4567"}],
                        "emails": [{"label": "home", "value": "person@example.com"}],
                    },
                    {
                        "contact_id": "contact-2",
                        "name": "Example Person",
                        "first_name": "Example",
                        "last_name": "Person",
                        "organization": "",
                        "phone_count": 0,
                        "email_count": 1,
                        "phones": [],
                        "emails": [{"label": "work", "value": "person@example.com"}],
                    },
                ]
            }
        raise AssertionError(f"Unexpected script {script_name}")

    monkeypatch.setattr(bridge, "_run_script", fake_run_script)

    groups = bridge.find_duplicates()

    assert len(groups) == 1
    assert groups[0].merge_recommended is True
    assert {contact.contact_id for contact in groups[0].contacts} == {"contact-1", "contact-2"}
    assert {item.kind for item in groups[0].evidence} >= {"name", "email"}


def test_find_duplicates_scans_past_first_thousand(monkeypatch) -> None:
    bridge = AppleContactsBridge(*UNUSED_HELPER)
    directory = [contact_item(f"contact-{index}", name=f"Unique{index}") for index in range(1000)]
    directory.extend(
        [
            contact_item("contact-late-1", name="Late One", email="shared@example.com"),
            contact_item("contact-late-2", name="Late Two", email="shared@example.com"),
        ]
    )

    def fake_run_script(script_name: str, *args: str) -> dict[str, object]:
        limit, offset = map(int, args)
        return {"items": directory[offset : offset + limit], "total": len(directory)}

    monkeypatch.setattr(bridge, "_run_script", fake_run_script)

    groups = bridge.find_duplicates()

    assert any(
        {contact.contact_id for contact in group.contacts} == {"contact-late-1", "contact-late-2"}
        for group in groups
    )


def test_directory_scan_reads_large_pages(monkeypatch) -> None:
    bridge = AppleContactsBridge(*UNUSED_HELPER)
    directory = [contact_item(f"contact-{index}") for index in range(1500)]
    calls: list[tuple[str, ...]] = []

    def fake_run_script(script_name: str, *args: str) -> dict[str, object]:
        calls.append(args)
        limit, offset = map(int, args)
        return {"items": directory[offset : offset + limit], "total": len(directory)}

    monkeypatch.setattr(bridge, "_run_script", fake_run_script)

    assert len(list(bridge._iter_all_contacts())) == 1500
    assert calls == [(str(contacts_bridge.DIRECTORY_SCAN_PAGE_SIZE), "0")]


def test_directory_scan_rejects_total_above_bound(monkeypatch) -> None:
    bridge = AppleContactsBridge(*UNUSED_HELPER)
    monkeypatch.setattr(
        bridge,
        "_run_script",
        lambda *args: {"items": [], "total": contacts_bridge.MAX_DIRECTORY_CONTACTS + 1},
    )

    with pytest.raises(ContactsBridgeError) as exc_info:
        list(bridge._iter_all_contacts())

    assert exc_info.value.error_code == "CONTACT_DIRECTORY_TOO_LARGE"


def test_directory_scan_rejects_short_page(monkeypatch) -> None:
    bridge = AppleContactsBridge(*UNUSED_HELPER)
    monkeypatch.setattr(bridge, "_run_script", lambda *args: {"items": [], "total": 1})

    with pytest.raises(ContactsBridgeError) as exc_info:
        list(bridge._iter_all_contacts())

    assert exc_info.value.error_code == "INCOMPLETE_CONTACT_DIRECTORY"


def test_suggest_merge_candidates_filters_query(monkeypatch) -> None:
    bridge = AppleContactsBridge(*UNUSED_HELPER)

    def fake_run_script(script_name: str, *args: str) -> dict[str, object]:
        if script_name == "list_contacts.applescript":
            return {
                "total": 3,
                "items": [
                    {
                        "contact_id": "contact-1",
                        "name": "Example Person",
                        "first_name": "Example",
                        "last_name": "Person",
                        "organization": "",
                        "phone_count": 0,
                        "email_count": 1,
                        "phones": [],
                        "emails": [{"label": "home", "value": "person@example.com"}],
                    },
                    {
                        "contact_id": "contact-2",
                        "name": "Example Person",
                        "first_name": "Example",
                        "last_name": "Person",
                        "organization": "",
                        "phone_count": 0,
                        "email_count": 1,
                        "phones": [],
                        "emails": [{"label": "work", "value": "person@example.com"}],
                    },
                    {
                        "contact_id": "contact-3",
                        "name": "Alice Doe",
                        "first_name": "Alice",
                        "last_name": "Doe",
                        "organization": "",
                        "phone_count": 0,
                        "email_count": 1,
                        "phones": [],
                        "emails": [{"label": "work", "value": "alice@example.com"}],
                    },
                ]
            }
        raise AssertionError(f"Unexpected script {script_name}")

    monkeypatch.setattr(bridge, "_run_script", fake_run_script)

    groups = bridge.suggest_merge_candidates("example")

    assert len(groups) == 1
    assert all(contact.name == "Example Person" for contact in groups[0].contacts)
