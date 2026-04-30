import inspect
import base64

from scripts import room_broker as room_broker_module
from scripts.room_broker import format_agent_prompt
from scripts.room_broker import extract_room_replies
from scripts.room_broker import extract_visible_terminal_reply
from scripts.room_broker import format_room_tail
from scripts.room_broker import format_room_state
from scripts.room_broker import parse_debate_command
from scripts.room_broker import parse_sync_command
from scripts.room_broker import parse_targets
from scripts.room_broker import RoomBroker
from scripts.room_broker import run_debate
from scripts.room_broker import wait_for_room_replies


def test_parse_single_target():
    targets, message = parse_targets("@claude please review")

    assert targets == ["claude"]
    assert message == "please review"


def test_parse_target_followed_by_english_comma():
    targets, message = parse_targets("@codex, continue")

    assert targets == ["codex"]
    assert message == "continue"


def test_parse_target_followed_by_chinese_comma():
    targets, message = parse_targets("@codex\uff0c\u7ee7\u7eed\u53cd\u9a73")

    assert targets == ["codex"]
    assert message == "\u7ee7\u7eed\u53cd\u9a73"


def test_parse_target_followed_by_chinese_text_without_space():
    targets, message = parse_targets("@codex\u7ee7\u7eed\u53cd\u9a73")

    assert targets == ["codex"]
    assert message == "\u7ee7\u7eed\u53cd\u9a73"


def test_parse_all_target():
    targets, message = parse_targets("@all discuss this")

    assert targets == ["claude", "codex"]
    assert message == "discuss this"


def test_should_auto_debate_never_triggers_implicitly():
    assert room_broker_module.should_auto_debate("@all discuss this", ["claude", "codex"], "discuss this") is False
    assert (
        room_broker_module.should_auto_debate("@claude @codex discuss this", ["claude", "codex"], "discuss this")
        is False
    )
    assert room_broker_module.should_auto_debate("@all", ["claude", "codex"], "") is False
    assert room_broker_module.should_auto_debate("@all hello", ["claude", "codex"], "hello") is False


def test_parse_targets_repairs_utf8_text_decoded_as_gbk():
    targets, message = parse_targets("@all \u5a34\u5b2d\u762f")

    assert targets == ["claude", "codex"]
    assert message == "\u6d4b\u8bd5"


def test_normalize_input_text_keeps_valid_chinese_unchanged():
    assert (
        room_broker_module.normalize_input_text("\u6d4b\u8bd5\uff1aClaude \u548c Codex")
        == "\u6d4b\u8bd5\uff1aClaude \u548c Codex"
    )


def test_configure_utf8_stdio_reconfigures_available_streams():
    class Stream:
        def __init__(self):
            self.kwargs = None

        def reconfigure(self, **kwargs):
            self.kwargs = kwargs

    stdin = Stream()
    stdout = Stream()
    stderr = Stream()

    room_broker_module.configure_utf8_stdio(stdin=stdin, stdout=stdout, stderr=stderr)

    assert stdin.kwargs == {"encoding": "utf-8", "errors": "replace"}
    assert stdout.kwargs == {"encoding": "utf-8", "errors": "replace"}
    assert stderr.kwargs == {"encoding": "utf-8", "errors": "replace"}


def test_read_room_input_uses_prompt_toolkit_prompt():
    calls = []

    def prompt_func(prompt_text, **kwargs):
        calls.append((prompt_text, kwargs))
        return " @all \u5a34\u5b2d\u762f "

    text = room_broker_module.read_room_input(
        prompt_func=prompt_func,
        input_func=lambda prompt_text: "fallback",
    )

    assert text == " @all \u6d4b\u8bd5 "
    assert calls[0][0] == "room> "
    assert calls[0][1]["multiline"] is False


def test_read_room_input_falls_back_to_builtin_input():
    readline_calls = []

    def prompt_func(prompt_text, **kwargs):
        raise ImportError("prompt_toolkit unavailable")

    text = room_broker_module.read_room_input(
        prompt_func=prompt_func,
        input_func=lambda prompt_text: " @codex hello ",
        readline_loader=lambda: readline_calls.append("readline"),
    )

    assert text == " @codex hello "
    assert readline_calls == ["readline"]


def test_parse_multiple_targets():
    targets, message = parse_targets("@claude @codex compare options")

    assert targets == ["claude", "codex"]
    assert message == "compare options"


def test_resolve_target_message_uses_default_continuation_for_bare_target():
    message, generated = room_broker_module.resolve_target_message(["codex"], "")

    assert generated is True
    assert "\u4e0a\u4e00\u6761\u53d1\u8a00" in message
    assert "\u4e0a\u6587" in message


def test_resolve_target_message_keeps_explicit_message():
    message, generated = room_broker_module.resolve_target_message(["codex"], "reply to Claude")

    assert generated is False
    assert message == "reply to Claude"


def test_parse_sync_command_with_target_and_lines():
    parsed = parse_sync_command("/sync @codex 120")

    assert parsed == (["codex"], 120)


def test_parse_sync_command_all_can_use_dynamic_targets():
    parsed = parse_sync_command("/sync @all 120", known_targets=["claude", "codex", "gemini"])

    assert parsed == (["claude", "codex", "gemini"], 120)


def test_parse_debate_command_with_rounds():
    parsed = parse_debate_command("/debate 4 vibe coding")

    assert parsed == (4, "vibe coding")


def test_parse_debate_command_defaults_to_three_rounds():
    parsed = parse_debate_command("/debate vibe coding")

    assert parsed == (3, "vibe coding")


def test_parse_debate_command_ignores_invalid_round_count():
    parsed = parse_debate_command("/debate 0 vibe coding")

    assert parsed == (3, "0 vibe coding")


def test_verbose_flag_and_set_verbose(tmp_path):
    printed = []
    broker = RoomBroker(
        session="test-room",
        targets=["claude", "codex"],
        verbose=True,
        log_path=tmp_path / "broker.jsonl",
        room_file=tmp_path / "room.jsonl",
        runner=lambda cmd: None,
        sleeper=lambda seconds: None,
        logger=lambda data: None,
        printer=printed.append,
    )

    assert broker.verbose is True

    broker.set_verbose(False)

    assert broker.verbose is False
    assert printed == ["[verbose] off"]


def test_quiet_mode_suppresses_status_noise(tmp_path):
    printed = []
    broker = RoomBroker(
        session="test-room",
        targets=["claude", "codex"],
        verbose=False,
        log_path=tmp_path / "broker.jsonl",
        room_file=tmp_path / "room.jsonl",
        runner=lambda cmd: None,
        sleeper=lambda seconds: None,
        logger=lambda data: None,
        printer=printed.append,
    )

    run_debate(
        broker,
        topic="vibe coding",
        rounds=1,
        wait_func=lambda broker_arg, after_id, targets: after_id,
        printer=broker._say_status,
    )

    assert printed == []


def test_verbose_mode_shows_status_noise(tmp_path):
    printed = []
    broker = RoomBroker(
        session="test-room",
        targets=["claude", "codex"],
        verbose=True,
        log_path=tmp_path / "broker.jsonl",
        room_file=tmp_path / "room.jsonl",
        runner=lambda cmd: None,
        sleeper=lambda seconds: None,
        logger=lambda data: None,
        printer=printed.append,
    )

    run_debate(
        broker,
        topic="vibe coding",
        rounds=1,
        wait_func=lambda broker_arg, after_id, targets: after_id,
        printer=broker._say_status,
    )

    assert any("[debate] round 1/1 turn 1/2 sent -> claude" in line for line in printed)


def test_quiet_wait_prints_clean_agent_chat_without_framing(tmp_path):
    printed = []
    broker = RoomBroker(
        session="test-room",
        targets=["claude", "codex"],
        verbose=False,
        log_path=tmp_path / "broker.jsonl",
        room_file=tmp_path / "room.jsonl",
        runner=lambda cmd: None,
        sleeper=lambda seconds: None,
        logger=lambda data: None,
        printer=printed.append,
    )
    user_event = broker.room_store.post("user", "topic", role="user")
    broker.room_store.post("claude", "final answer")

    wait_for_room_replies(
        broker,
        after_id=user_event["id"],
        targets=["claude"],
        interval=0.1,
        max_seconds=1,
        status_printer=broker._say_status,
        error_printer=broker._say_error,
        reply_printer=broker._say_chat,
        sleeper=lambda seconds: None,
    )

    assert printed == ["claude> final answer"]


def test_run_debate_sends_turns_one_agent_at_a_time(tmp_path):
    sent = []
    waits = []

    broker = RoomBroker(
        session="test-room",
        targets=["claude", "codex"],
        log_path=tmp_path / "broker.jsonl",
        room_file=tmp_path / "room.jsonl",
        runner=lambda cmd: None,
        sleeper=lambda seconds: None,
        logger=lambda data: None,
    )

    def wait_func(broker_arg, after_id, targets):
        waits.append((after_id, targets))
        return f"after-{len(waits)}"

    original_send_many = broker.send_many

    def send_many(targets, message):
        sent.append((targets, message))
        return original_send_many(targets, message)

    broker.send_many = send_many

    run_debate(broker, topic="vibe coding", rounds=2, wait_func=wait_func, printer=lambda text: None)

    assert len(sent) == 4
    assert [targets for targets, _message in sent] == [["claude"], ["codex"], ["claude"], ["codex"]]
    assert all("vibe coding" in message for _targets, message in sent)
    assert "Round 1/2" in sent[0][1]
    assert "opening analysis" in sent[0][1]
    assert "Respond to the other participant" in sent[1][1]
    assert "Round 2/2" in sent[2][1]
    assert "final position" in sent[2][1]
    assert waits == [
        (broker.room_store.read()[0]["id"], ["claude"]),
        (broker.room_store.read()[1]["id"], ["codex"]),
        (broker.room_store.read()[2]["id"], ["claude"]),
        (broker.room_store.read()[3]["id"], ["codex"]),
    ]


def test_run_debate_uses_broker_targets_for_three_participants(tmp_path):
    sent = []
    broker = RoomBroker(
        session="test-room",
        targets=["claude", "codex", "gemini"],
        log_path=tmp_path / "broker.jsonl",
        room_file=tmp_path / "room.jsonl",
        runner=lambda cmd: None,
        sleeper=lambda seconds: None,
        logger=lambda data: None,
    )

    original_send_many = broker.send_many

    def send_many(targets, message):
        sent.append(targets)
        return original_send_many(targets, message)

    broker.send_many = send_many

    run_debate(
        broker,
        topic="vibe coding",
        rounds=2,
        wait_func=lambda broker_arg, after_id, targets: after_id,
        printer=lambda text: None,
    )

    assert sent == [["claude"], ["codex"], ["gemini"], ["claude"], ["codex"], ["gemini"]]


def test_format_agent_prompt_requires_mcp_room_post():
    prompt = format_agent_prompt("hello", event_id="event-123")

    assert "room_read" in prompt
    assert "room_post" in prompt
    assert "ROOM_POST_FALLBACK_BEGIN" in prompt
    assert "ROOM_POST_FALLBACK_END" in prompt
    assert "ROOM_POST_FALLBACK_BASE64_BEGIN" in prompt
    assert "Do not use shell" in prompt
    assert "event-123" in prompt


def test_format_agent_prompt_is_ascii_only_even_for_chinese_room_message():
    message = "\u6d4b\u8bd5\uff1aClaude \u548c Codex \u5404\u7528\u4e00\u53e5\u8bdd\u786e\u8ba4\u6536\u5230\u3002"
    prompt = format_agent_prompt(message, event_id="event-123")

    prompt.encode("ascii")
    assert "\u6d4b\u8bd5" not in prompt
    encoded = base64.b64encode(message.encode("utf-8")).decode("ascii")
    assert encoded in prompt


def test_send_many_writes_user_message_to_room_store(tmp_path):
    broker = RoomBroker(
        session="test-room",
        targets=["claude", "codex"],
        log_path=tmp_path / "broker.jsonl",
        room_file=tmp_path / "room.jsonl",
        runner=lambda cmd: None,
        sleeper=lambda seconds: None,
        logger=lambda data: None,
    )

    event = broker.send_many(["claude"], "hello")

    assert event["author"] == "user"
    assert event["content"] == "hello"
    assert broker.room_events_after()[0]["content"] == "hello"


def test_format_room_state_summarizes_current_room(tmp_path):
    broker = RoomBroker(
        session="test-room",
        targets=["claude", "codex"],
        log_path=tmp_path / "broker.jsonl",
        room_file=tmp_path / "room.jsonl",
        runner=lambda cmd: None,
        sleeper=lambda seconds: None,
        logger=lambda data: None,
    )
    broker.room_store.post("user", "topic", role="user")
    last = broker.room_store.post("codex", "answer")

    summary = format_room_state(broker)

    assert "messages=2" in summary
    assert "participants=codex, user" in summary
    assert last["id"] in summary


def test_broker_posts_session_marker_on_start(tmp_path):
    broker = RoomBroker(
        session="test-room",
        targets=["claude", "codex"],
        log_path=tmp_path / "broker.jsonl",
        room_file=tmp_path / "room.jsonl",
        runner=lambda cmd: None,
        sleeper=lambda seconds: None,
        logger=lambda data: None,
    )

    marker = broker.post_session_marker()

    assert marker["author"] == "system"
    assert marker["role"] == "system"
    assert "broker session started" in marker["content"]
    assert "test-room" in marker["content"]


def test_format_room_tail_shows_recent_messages(tmp_path):
    broker = RoomBroker(
        session="test-room",
        targets=["claude", "codex"],
        log_path=tmp_path / "broker.jsonl",
        room_file=tmp_path / "room.jsonl",
        runner=lambda cmd: None,
        sleeper=lambda seconds: None,
        logger=lambda data: None,
    )
    broker.room_store.post("user", "old", role="user")
    broker.room_store.post("claude", "hello")
    broker.room_store.post("codex", "world")

    tail = format_room_tail(broker, limit=2)

    assert "claude: hello" in tail
    assert "codex: world" in tail
    assert "user: old" not in tail


def test_send_selects_target_pane_before_pressing_enter(tmp_path):
    calls = []
    sleeps = []

    broker = RoomBroker(
        session="test-room",
        targets=["claude", "codex"],
        log_path=tmp_path / "broker.jsonl",
        room_file=tmp_path / "room.jsonl",
        runner=lambda cmd: calls.append(cmd),
        sleeper=lambda seconds: sleeps.append(seconds),
        logger=lambda data: None,
    )
    broker.send("codex", "hello", event_id="event-123")

    assert calls[0] == ["tmux", "select-pane", "-t", "test-room:0.2"]
    assert calls[1][:5] == ["tmux", "send-keys", "-t", "test-room:0.2", "-l"]
    assert "room_post" in calls[1][-1]
    calls[1][-1].encode("ascii")
    assert calls[2] == ["tmux", "send-keys", "-t", "test-room:0.2", "C-m"]
    assert sleeps == [0.35]


def test_send_records_latest_prompt_per_target(tmp_path):
    broker = RoomBroker(
        session="test-room",
        targets=["claude", "codex"],
        log_path=tmp_path / "broker.jsonl",
        room_file=tmp_path / "room.jsonl",
        runner=lambda cmd: None,
        sleeper=lambda seconds: None,
        logger=lambda data: None,
    )

    broker.send("claude", "first message", event_id="evt-1")
    broker.send("claude", "second message", event_id="evt-2")

    latest = broker.last_prompt_for("claude")
    assert latest is not None
    assert "Target room event id: evt-2" in latest
    assert "first message" not in latest
    assert broker.last_prompt_for("never-sent") is None


def test_wait_for_room_replies_prints_only_room_post_events(tmp_path):
    printed = []
    sleeps = []
    broker = RoomBroker(
        session="test-room",
        targets=["claude", "codex"],
        log_path=tmp_path / "broker.jsonl",
        room_file=tmp_path / "room.jsonl",
        runner=lambda cmd: None,
        sleeper=lambda seconds: None,
        logger=lambda data: None,
    )
    user_event = broker.room_store.post("user", "topic", role="user")
    broker.room_store.post("claude", "final answer")

    wait_for_room_replies(
        broker,
        after_id=user_event["id"],
        targets=["claude"],
        interval=0.1,
        max_seconds=1,
        printer=lambda text: printed.append(text),
        sleeper=lambda seconds: sleeps.append(seconds),
    )

    assert any("--- claude reply ---" in line for line in printed)
    assert any("final answer" in line for line in printed)
    assert not any("topic" in line for line in printed)


def test_wait_for_room_replies_matches_author_case_insensitively(tmp_path):
    printed = []
    broker = RoomBroker(
        session="test-room",
        targets=["claude", "codex"],
        log_path=tmp_path / "broker.jsonl",
        room_file=tmp_path / "room.jsonl",
        runner=lambda cmd: None,
        sleeper=lambda seconds: None,
        logger=lambda data: None,
    )
    user_event = broker.room_store.post("user", "topic", role="user")
    broker.room_store.post("Codex", "final answer")

    wait_for_room_replies(
        broker,
        after_id=user_event["id"],
        targets=["codex"],
        interval=0.1,
        max_seconds=1,
        printer=lambda text: printed.append(text),
        sleeper=lambda seconds: None,
    )

    assert any("--- codex reply ---" in line for line in printed)
    assert any("final answer" in line for line in printed)


def test_extract_room_replies_from_marked_terminal_fallback():
    output = """
Some tool failure text
ROOM_POST_FALLBACK_BEGIN
Claude final answer
ROOM_POST_FALLBACK_END
"""

    assert extract_room_replies(output) == ["Claude final answer"]


def test_extract_room_replies_from_base64_terminal_fallback():
    content = "\u6536\u5230\uff0cCodex \u5df2\u5c31\u4f4d\u3002"
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    output = f"""
ROOM_POST_FALLBACK_BASE64_BEGIN
{encoded}
ROOM_POST_FALLBACK_BASE64_END
"""

    assert extract_room_replies(output) == [content]


def test_extract_room_replies_from_wrapped_base64_terminal_fallback():
    content = "\u6211\u5bf9 vibecoding \u7684\u770b\u6cd5\u662f\uff1a\u5b83\u662f\u5f88\u597d\u7684\u52a0\u901f\u5668\u3002"
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    wrapped = "\n".join(encoded[index : index + 12] for index in range(0, len(encoded), 12))
    output = f"""
ROOM_POST_FALLBACK_BASE64_BEGIN
{wrapped}
ROOM_POST_FALLBACK_BASE64_END
"""

    assert extract_room_replies(output) == [content]


def test_extract_room_replies_from_legacy_claude_fallback_text():
    output = """
\u0072\u006f\u006f\u006d\u005f\u0070\u006f\u0073\u0074 \u5de5\u5177\u8fde\u63a5\u5df2\u5173\u95ed\uff0c\u56de\u9000\u5230\u7ec8\u7aef\u8f93\u51fa\u6700\u7ec8\u56de\u590d\uff1a

\u0043\u006c\u0061\u0075\u0064\u0065 \u5df2\u6536\u5230\uff0c\u51c6\u5907\u597d\u4e0e \u0043\u006f\u0064\u0065\u0078 \u4e00\u8d77\u5f00\u59cb\u8ba8\u8bba\u3002
"""

    assert extract_room_replies(output) == [
        "\u0043\u006c\u0061\u0075\u0064\u0065 \u5df2\u6536\u5230\uff0c\u51c6\u5907\u597d\u4e0e \u0043\u006f\u0064\u0065\u0078 \u4e00\u8d77\u5f00\u59cb\u8ba8\u8bba\u3002"
    ]


def test_extract_visible_terminal_reply_ignores_prompt_echo():
    changed = """
Read the shared roundtable room via the MCP tool room_read.
Target room event id: abc
If room_read fails, decode this UTF-8 base64 fallback room message: xxxx
Answer the target user message from the room, then post only your final visible reply with room_post.
ROOM_POST_FALLBACK_BASE64_BEGIN
<utf8-base64 final visible reply>
ROOM_POST_FALLBACK_BASE64_END

Claude final answer
"""

    assert extract_visible_terminal_reply(changed) == "Claude final answer"


def test_terminal_fallback_replies_recovers_stable_unmarked_visible_output(tmp_path):
    content = "Claude final answer"
    capture_outputs = iter(
        [
            "baseline",
            f"baseline\n{content}",
            f"baseline\n{content}",
        ]
    )

    broker = RoomBroker(
        session="test-room",
        targets=["claude", "codex"],
        log_path=tmp_path / "broker.jsonl",
        room_file=tmp_path / "room.jsonl",
        runner=lambda cmd: None,
        capture_runner=lambda cmd: next(capture_outputs),
        sleeper=lambda seconds: None,
        logger=lambda data: None,
    )
    broker.send_many(["claude"], "topic")

    assert broker.terminal_fallback_replies(["claude"]) == {}
    assert broker.terminal_fallback_replies(["claude"]) == {"claude": content}


def test_terminal_fallback_strips_recorded_prompt_echo(tmp_path):
    outputs = {}

    def capture_runner(cmd):
        target = "claude" if cmd[4] == "test-room:0.1" else "codex"
        return outputs[target]

    broker = RoomBroker(
        session="test-room",
        targets=["claude", "codex"],
        log_path=tmp_path / "broker.jsonl",
        room_file=tmp_path / "room.jsonl",
        runner=lambda cmd: None,
        capture_runner=capture_runner,
        sleeper=lambda seconds: None,
        logger=lambda data: None,
    )
    broker._fallback_baselines = {"claude": "baseline\n"}
    broker.send("claude", "what's the topic?", event_id="evt-1")
    prompt = broker.last_prompt_for("claude")
    assert prompt is not None
    outputs["claude"] = f"baseline\n{prompt}\n\nThe topic is unclear.\n"

    first = broker.terminal_fallback_replies(["claude"])
    second = broker.terminal_fallback_replies(["claude"])

    assert first == {}
    assert second == {"claude": "The topic is unclear."}


def test_wait_for_room_replies_uses_terminal_fallback_when_room_post_missing(tmp_path):
    printed = []
    content = "\u6536\u5230\uff0cCodex \u5df2\u5c31\u4f4d\u3002"
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    capture_outputs = iter(
        [
            "baseline",
            f"""
baseline
ROOM_POST_FALLBACK_BASE64_BEGIN
{encoded}
ROOM_POST_FALLBACK_BASE64_END
""",
        ]
    )

    broker = RoomBroker(
        session="test-room",
        targets=["claude", "codex"],
        log_path=tmp_path / "broker.jsonl",
        room_file=tmp_path / "room.jsonl",
        runner=lambda cmd: None,
        capture_runner=lambda cmd: next(capture_outputs),
        sleeper=lambda seconds: None,
        logger=lambda data: None,
    )
    user_event = broker.send_many(["claude"], "topic")

    wait_for_room_replies(
        broker,
        after_id=user_event["id"],
        targets=["claude"],
        interval=0.1,
        max_seconds=1,
        printer=lambda text: printed.append(text),
        sleeper=lambda seconds: None,
    )

    assert any("--- claude terminal fallback ---" in line for line in printed)
    assert any(content in line for line in printed)
    assert broker.room_store.read()[-1]["content"] == content


def test_wait_for_room_replies_waits_indefinitely_by_default():
    timeout_default = inspect.signature(wait_for_room_replies).parameters["max_seconds"].default

    assert timeout_default is None


def test_wait_for_room_replies_returns_to_prompt_on_keyboard_interrupt(tmp_path):
    printed = []
    broker = RoomBroker(
        session="test-room",
        targets=["claude", "codex"],
        log_path=tmp_path / "broker.jsonl",
        room_file=tmp_path / "room.jsonl",
        runner=lambda cmd: None,
        sleeper=lambda seconds: None,
        logger=lambda data: None,
    )
    user_event = broker.room_store.post("user", "topic", role="user")

    def sleeper(seconds):
        raise KeyboardInterrupt()

    last_id = wait_for_room_replies(
        broker,
        after_id=user_event["id"],
        targets=["codex"],
        interval=0.1,
        printer=lambda text: printed.append(text),
        sleeper=sleeper,
    )

    assert last_id == user_event["id"]
    assert any("wait interrupted" in line for line in printed)


def test_wait_for_room_replies_prints_missing_targets_on_timeout(tmp_path):
    printed = []
    broker = RoomBroker(
        session="test-room",
        targets=["claude", "codex"],
        log_path=tmp_path / "broker.jsonl",
        room_file=tmp_path / "room.jsonl",
        runner=lambda cmd: None,
        sleeper=lambda seconds: None,
        logger=lambda data: None,
    )
    user_event = broker.room_store.post("user", "topic", role="user")

    wait_for_room_replies(
        broker,
        after_id=user_event["id"],
        targets=["claude", "codex"],
        interval=0.1,
        max_seconds=0,
        printer=lambda text: printed.append(text),
        sleeper=lambda seconds: None,
    )

    assert any("timed out waiting for: claude, codex" in line for line in printed)


def test_wait_for_room_replies_prints_periodic_waiting_status(tmp_path):
    printed = []
    now = 0.0

    def clock():
        return now

    def sleeper(seconds):
        nonlocal now
        now += seconds

    broker = RoomBroker(
        session="test-room",
        targets=["claude", "codex"],
        log_path=tmp_path / "broker.jsonl",
        room_file=tmp_path / "room.jsonl",
        runner=lambda cmd: None,
        sleeper=lambda seconds: None,
        logger=lambda data: None,
    )
    user_event = broker.room_store.post("user", "topic", role="user")

    wait_for_room_replies(
        broker,
        after_id=user_event["id"],
        targets=["codex"],
        interval=10,
        max_seconds=31,
        printer=lambda text: printed.append(text),
        sleeper=sleeper,
        clock=clock,
    )

    assert any("still waiting for: codex" in line for line in printed)


def test_sync_captures_agent_panes_as_debug_fallback(tmp_path):
    captured = []

    def capture_runner(cmd):
        captured.append(cmd)
        return "agent output"

    broker = RoomBroker(
        session="test-room",
        targets=["claude", "codex"],
        log_path=tmp_path / "broker.jsonl",
        room_file=tmp_path / "room.jsonl",
        runner=lambda cmd: None,
        capture_runner=capture_runner,
        logger=lambda data: None,
    )

    synced = broker.sync(["claude"], lines=20)

    assert captured == [["tmux", "capture-pane", "-p", "-t", "test-room:0.1", "-S", "-20"]]
    assert synced == {"claude": "agent output"}


def test_main_loads_broker_targets_from_config(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import patch

    config = tmp_path / "config.yaml"
    config.write_text("participants: []\n", encoding="utf-8")
    created = {}

    class FakeBroker:
        def __init__(self, session, targets, room_file=None, verbose=False):
            created["session"] = session
            created["targets"] = targets
            created["room_file"] = room_file
            created["verbose"] = verbose
            self.targets = targets

        def post_session_marker(self):
            return {}

    inputs = iter(["/quit"])

    with (
        patch.dict(
            "sys.modules",
            {
                "yaml": SimpleNamespace(
                    safe_load=lambda stream: {
                        "participants": [
                            {"id": "claude"},
                            {"id": "codex"},
                            {"id": "gemini"},
                        ]
                    }
                )
            },
        ),
        patch.object(room_broker_module, "RoomBroker", FakeBroker),
        patch.object(room_broker_module, "read_room_input", lambda: next(inputs)),
        patch("sys.argv", ["room_broker.py", "--session", "test-room", "--config", str(config)]),
    ):
        room_broker_module.main()

    assert created["session"] == "test-room"
    assert created["targets"] == ["claude", "codex", "gemini"]


def test_main_verbose_command_toggles_broker(tmp_path):
    from unittest.mock import patch

    created = {}

    class FakeBroker:
        def __init__(self, session, targets, room_file=None, verbose=False):
            created["broker"] = self
            self.targets = targets
            self.verbose = verbose

        def post_session_marker(self):
            return {}

        def set_verbose(self, value):
            self.verbose = value
            print(f"[verbose] {'on' if value else 'off'}")

    inputs = iter(["/verbose on", "/quit"])

    with (
        patch.object(room_broker_module, "load_participant_ids", return_value=["claude", "codex"]),
        patch.object(room_broker_module, "RoomBroker", FakeBroker),
        patch.object(room_broker_module, "read_room_input", lambda: next(inputs)),
        patch("sys.argv", ["room_broker.py", "--session", "test-room"]),
    ):
        room_broker_module.main()

    assert created["broker"].verbose is True


def test_main_prints_quiet_startup_banner(capsys):
    from unittest.mock import patch

    class FakeBroker:
        def __init__(self, session, targets, room_file=None, verbose=False):
            self.targets = targets

        def post_session_marker(self):
            return {}

    inputs = iter(["/quit"])

    with (
        patch.object(room_broker_module, "load_participant_ids", return_value=["claude", "codex"]),
        patch.object(room_broker_module, "RoomBroker", FakeBroker),
        patch.object(room_broker_module, "read_room_input", lambda: next(inputs)),
        patch("sys.argv", ["room_broker.py", "--session", "test-room"]),
    ):
        room_broker_module.main()

    output = capsys.readouterr().out
    assert "room ready. type /help for commands." in output
    assert "Room broker ready." not in output
    assert "Usage:" not in output


def test_main_help_command_prints_full_usage(capsys):
    from unittest.mock import patch

    class FakeBroker:
        def __init__(self, session, targets, room_file=None, verbose=False):
            self.targets = targets

        def post_session_marker(self):
            return {}

    inputs = iter(["/help", "/quit"])

    with (
        patch.object(room_broker_module, "load_participant_ids", return_value=["claude", "codex"]),
        patch.object(room_broker_module, "RoomBroker", FakeBroker),
        patch.object(room_broker_module, "read_room_input", lambda: next(inputs)),
        patch("sys.argv", ["room_broker.py", "--session", "test-room"]),
    ):
        room_broker_module.main()

    output = capsys.readouterr().out
    assert "room ready. type /help for commands." in output
    assert "Usage: @claude message | @codex message | @all message |" in output
