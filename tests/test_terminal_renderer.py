import io
import pytest
from src.renderers.terminal import TerminalRenderer

def test_render_header():
    out = io.StringIO()
    r = TerminalRenderer(output=out)
    r.render_header("Test Topic", ["Claude", "Codex"])
    text = out.getvalue()
    assert "Test Topic" in text
    assert "Claude" in text

def test_render_turn():
    out = io.StringIO()
    r = TerminalRenderer(output=out)
    r.render_turn("Claude", "I think Python is better.", 1)
    text = out.getvalue()
    assert "Claude" in text
    assert "Python" in text

def test_render_status():
    out = io.StringIO()
    r = TerminalRenderer(output=out)
    r.render_status("converging", 3, 12)
    text = out.getvalue()
    assert "3" in text

def test_render_user_input():
    out = io.StringIO()
    r = TerminalRenderer(output=out)
    r.render_user_message("我们团队只有3个人")
    text = out.getvalue()
    assert "3个人" in text
