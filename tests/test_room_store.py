from src.room_store import RoomStore
from src.room_store import MAX_READ_LIMIT
from src.room_store import UnknownCursorError


def test_room_store_posts_and_reads_messages(tmp_path):
    store = RoomStore(tmp_path / "room.jsonl")

    first = store.post("user", "hello", role="user")
    second = store.post("claude", "hi")

    assert store.read() == [first, second]
    assert store.read(after_id=first["id"]) == [second]


def test_room_store_reports_state(tmp_path):
    store = RoomStore(tmp_path / "room.jsonl")

    first = store.post("user", "topic", role="user")
    store.post("codex", "answer")

    assert store.state() == {
        "event_count": 2,
        "message_count": 2,
        "last_event_id": first["id"].replace(first["id"], store.read()[-1]["id"]),
        "participants": ["codex", "user"],
    }


def test_room_store_state_excludes_system_author_from_participants(tmp_path):
    store = RoomStore(tmp_path / "room.jsonl")

    store.post("system", "broker session started", role="system")
    store.post("user", "topic", role="user")
    store.post("codex", "answer")

    assert store.state()["participants"] == ["codex", "user"]


def test_room_store_rejects_unknown_cursor_and_invalid_limit(tmp_path):
    store = RoomStore(tmp_path / "room.jsonl")
    store.post("user", "topic", role="user")

    try:
        store.read(after_id="missing")
    except UnknownCursorError as exc:
        assert exc.cursor == "missing"
    else:
        raise AssertionError("unknown cursors must not silently return the room tail")

    for limit in (0, -1):
        try:
            store.read(limit=limit)
        except ValueError as exc:
            assert "positive" in str(exc)
        else:
            raise AssertionError("non-positive limits must be rejected")


def test_room_store_caps_large_reads_and_skips_a_corrupt_tail(tmp_path):
    path = tmp_path / "room.jsonl"
    store = RoomStore(path)
    first = store.post("user", "topic", role="user")
    path.write_text(path.read_text(encoding="utf-8") + "{\"incomplete\":\n", encoding="utf-8")

    events = store.read(limit=MAX_READ_LIMIT + 1)

    assert events == [first]


def test_room_store_rejects_unsupported_roles_and_oversized_content(tmp_path):
    store = RoomStore(tmp_path / "room.jsonl")

    try:
        store.post("agent", "content", role="moderator")
    except ValueError as exc:
        assert "role" in str(exc)
    else:
        raise AssertionError("unsupported roles must be rejected")

    try:
        store.post("agent", "x" * 1_000_001)
    except ValueError as exc:
        assert "maximum" in str(exc)
    else:
        raise AssertionError("oversized room content must be rejected")


def test_room_store_skips_object_without_event_id(tmp_path):
    path = tmp_path / "room.jsonl"
    path.write_text('{"author":"x","content":"missing id"}\n', encoding="utf-8")

    assert RoomStore(path).read() == []


def test_room_store_retains_large_unicode_content_within_character_limit(tmp_path):
    content = "汉" * 1_000_000
    store = RoomStore(tmp_path / "room.jsonl")
    store.post("agent", content)

    events = store.read(limit=1)
    assert events[0]["content"] == content


def test_room_store_session_filter_pages_past_other_sessions(tmp_path):
    store = RoomStore(tmp_path / "room.jsonl")
    first = store.post("a", "first", session_id="s1")
    store.post("b", "foreign", session_id="s2")
    current = store.post("a", "current", session_id="s1")

    events = store.read(after_id=first["id"], limit=1, session_id="s1")

    assert [event["id"] for event in events] == [current["id"]]
