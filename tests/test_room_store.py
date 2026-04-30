from src.room_store import RoomStore


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
