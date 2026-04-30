from src.renderers.tmux import TmuxRenderer


def test_start_creates_three_pane_layout():
    calls = []
    renderer = TmuxRenderer(
        session_name="test-room",
        participants=["claude", "codex"],
        tmux_cmd=["tmux"],
        runner=lambda cmd: calls.append(cmd),
    )

    renderer.start("Topic", ["Claude", "Codex"])

    assert calls[0] == ["tmux", "kill-session", "-t", "test-room"]
    assert calls[1] == ["tmux", "new-session", "-d", "-s", "test-room", "-n", "room"]
    assert ["tmux", "split-window", "-h", "-p", "50", "-t", "test-room:0"] in calls
    assert ["tmux", "split-window", "-v", "-p", "50", "-t", "test-room:0.1"] in calls
    assert ["tmux", "select-layout", "-t", "test-room:0", "tiled"] not in calls


def test_start_writes_moderator_header_before_topic_comment():
    calls = []
    renderer = TmuxRenderer(
        session_name="test-room",
        participants=["claude", "codex"],
        moderator_name="Alice",
        tmux_cmd=["tmux"],
        runner=lambda cmd: calls.append(cmd),
    )

    renderer.start("Topic", ["Claude", "Codex"])

    room_writes = [
        cmd[4]
        for cmd in calls
        if cmd[:4] == ["tmux", "send-keys", "-t", "test-room:0.0"]
        and cmd[-1] == "Enter"
    ]
    assert room_writes[:2] == [
        "printf '%s\\n' 'you: Alice'",
        "printf '%s\\n' '# Topic  (participants: Claude, Codex)'",
    ]
    assert not any("pane ready" in write for write in room_writes)


def test_start_creates_n_pane_layout_for_three_participants():
    calls = []
    renderer = TmuxRenderer(
        session_name="test-room",
        participants=["claude", "codex", "gemini"],
        tmux_cmd=["tmux"],
        runner=lambda cmd: calls.append(cmd),
    )

    renderer.start("Topic", ["Claude", "Codex", "Gemini"])

    split_calls = [cmd for cmd in calls if cmd[:2] == ["tmux", "split-window"]]
    select_calls = [cmd for cmd in calls if cmd[:2] == ["tmux", "select-pane"] and "-T" in cmd]
    assert split_calls == [
        ["tmux", "split-window", "-h", "-p", "50", "-t", "test-room:0"],
        ["tmux", "split-window", "-v", "-p", "50", "-t", "test-room:0.1"],
        ["tmux", "split-window", "-v", "-p", "50", "-t", "test-room:0.2"],
    ]
    assert select_calls == [
        ["tmux", "select-pane", "-t", "test-room:0.0", "-T", "Room"],
        ["tmux", "select-pane", "-t", "test-room:0.1", "-T", "Claude"],
        ["tmux", "select-pane", "-t", "test-room:0.2", "-T", "Codex"],
        ["tmux", "select-pane", "-t", "test-room:0.3", "-T", "Gemini"],
    ]


def test_writes_messages_to_expected_panes():
    calls = []
    renderer = TmuxRenderer(
        session_name="test-room",
        participants=["claude", "codex", "gemini"],
        tmux_cmd=["tmux"],
        runner=lambda cmd: calls.append(cmd),
    )

    renderer.write_room("hello room")
    renderer.write_participant("claude", "hello claude")
    renderer.write_participant("codex", "hello codex")
    renderer.write_participant("gemini", "hello gemini")

    targets = [cmd[3] for cmd in calls if cmd[:3] == ["tmux", "send-keys", "-t"]]
    assert "test-room:0.0" in targets
    assert "test-room:0.1" in targets
    assert "test-room:0.2" in targets
    assert "test-room:0.3" in targets


def test_start_interactive_runs_real_agent_clis_in_panes():
    calls = []
    renderer = TmuxRenderer(
        session_name="test-room",
        participants=["claude", "codex"],
        tmux_cmd=["tmux"],
        runner=lambda cmd: calls.append(cmd),
    )

    renderer.start_interactive(participant_commands={"claude": "claude", "codex": "codex"})

    assert calls[1] == ["tmux", "new-session", "-d", "-s", "test-room", "-n", "room"]
    assert ["tmux", "send-keys", "-t", "test-room:0.0", "python scripts/room_broker.py --session test-room", "Enter"] in calls
    assert ["tmux", "split-window", "-h", "-p", "50", "-t", "test-room:0", "claude"] in calls
    assert ["tmux", "split-window", "-v", "-p", "50", "-t", "test-room:0.1", "codex"] in calls


def test_start_interactive_writes_clean_moderator_header():
    calls = []
    renderer = TmuxRenderer(
        session_name="test-room",
        participants=["claude", "codex"],
        moderator_name="Alice",
        tmux_cmd=["tmux"],
        runner=lambda cmd: calls.append(cmd),
    )

    renderer.start_interactive(participant_commands={"claude": "claude", "codex": "codex"}, topic="Topic")

    room_writes = [
        cmd[4]
        for cmd in calls
        if cmd[:4] == ["tmux", "send-keys", "-t", "test-room:0.0"]
        and cmd[-1] == "Enter"
        and cmd[4].startswith("printf")
    ]
    assert room_writes[:2] == [
        "printf '%s\\n' 'you: Alice'",
        "printf '%s\\n' '# Topic  (participants: claude, codex)'",
    ]
    assert not any("Interactive mode: right panes run" in write for write in room_writes)


def test_start_interactive_runs_all_panes_from_project_directory():
    calls = []
    renderer = TmuxRenderer(
        session_name="test-room",
        participants=["claude", "codex"],
        tmux_cmd=["tmux"],
        runner=lambda cmd: calls.append(cmd),
    )

    renderer.start_interactive(
        participant_commands={"claude": "claude", "codex": "codex"},
        room_command="python scripts/room_broker.py --session test-room",
        working_directory="/mnt/d/projects/ai-roundtable-codex",
    )

    assert [
        "tmux",
        "new-session",
        "-d",
        "-s",
        "test-room",
        "-n",
        "room",
        "-c",
        "/mnt/d/projects/ai-roundtable-codex",
    ] in calls
    assert [
        "tmux",
        "split-window",
        "-h",
        "-p",
        "50",
        "-t",
        "test-room:0",
        "-c",
        "/mnt/d/projects/ai-roundtable-codex",
        "claude",
    ] in calls
    assert [
        "tmux",
        "split-window",
        "-v",
        "-p",
        "50",
        "-t",
        "test-room:0.1",
        "-c",
        "/mnt/d/projects/ai-roundtable-codex",
        "codex",
    ] in calls


def test_start_interactive_enables_mouse_and_scrollback():
    calls = []
    renderer = TmuxRenderer(
        session_name="test-room",
        participants=["claude", "codex"],
        tmux_cmd=["tmux"],
        runner=lambda cmd: calls.append(cmd),
    )

    renderer.start_interactive(participant_commands={"claude": "claude", "codex": "codex"})

    assert ["tmux", "set-option", "-t", "test-room", "mouse", "on"] in calls
    assert ["tmux", "set-option", "-t", "test-room", "history-limit", "50000"] in calls
    assert ["tmux", "setw", "-t", "test-room", "mode-keys", "vi"] in calls


def test_send_prompt_to_agent_pane():
    calls = []
    renderer = TmuxRenderer(
        session_name="test-room",
        participants=["claude", "codex"],
        tmux_cmd=["tmux"],
        runner=lambda cmd: calls.append(cmd),
    )

    renderer.send_prompt("claude", "@codex give your view")

    assert ["tmux", "send-keys", "-t", "test-room:0.1", "@codex give your view", "Enter"] in calls
