import json
from extract_skill_data import cmd_head, parse_session_index, estimate_tokens, build_cmd_census


def _write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")


def _assistant_tool(name, **inp):
    return {"type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]}}


def _user(text):
    return {"type": "user", "message": {"content": text}}


def test_cmd_head_plain():
    assert cmd_head("ffmpeg -i in.mp4 out.mp4") == "ffmpeg"


def test_cmd_head_generic_gets_subcommand():
    assert cmd_head("git rebase -i main") == "git rebase"
    assert cmd_head("docker build -t x .") == "docker build"
    assert cmd_head("npm run lint") == "npm run"


def test_cmd_head_strips_env_sudo_and_path():
    assert cmd_head("FOO=bar sudo /opt/homebrew/bin/terminal-notifier -m hi") == "terminal-notifier"


def test_cmd_head_pipeline_takes_first():
    assert cmd_head("cat big.log | grep error") == "cat"


def test_cmd_head_empty():
    assert cmd_head("") == ""
    assert cmd_head("   ") == ""


def test_parse_session_index_basic(tmp_path):
    f = tmp_path / "s.jsonl"
    _write_jsonl(f, [
        _user("convert all my videos to gifs please"),
        _assistant_tool("Bash", command="ffmpeg -i a.mp4 a.gif"),
        _assistant_tool("Write", file_path="/tmp/convert.sh"),
        _user("now do the second one"),
        _assistant_tool("Bash", command="git rebase main"),
    ])
    r = parse_session_index(str(f))
    assert r["has_skill"] is False
    assert r["first_user_msg"] == "convert all my videos to gifs please"
    assert r["n_turns"] == 2
    assert r["cmd_sig"] == ["ffmpeg", "git rebase"]
    assert r["wrote"] == ["sh"]


def test_parse_session_index_flags_skill(tmp_path):
    f = tmp_path / "mixed.jsonl"
    _write_jsonl(f, [
        _user("do the thing"),
        _assistant_tool("Skill", skill="some-skill"),
        _assistant_tool("Bash", command="ffmpeg -i a.mp4 a.gif"),
    ])
    r = parse_session_index(str(f))
    assert r["has_skill"] is True
    assert r["cmd_sig"] == ["ffmpeg"]


def test_parse_session_index_none_without_tools(tmp_path):
    f = tmp_path / "chat.jsonl"
    _write_jsonl(f, [_user("what is the capital of France?")])
    assert parse_session_index(str(f)) is None


def test_parse_session_index_skips_noise_and_dedups(tmp_path):
    f = tmp_path / "n.jsonl"
    _write_jsonl(f, [
        _user("<system-reminder>ignore me</system-reminder>"),
        _user("real first ask"),
        _assistant_tool("Bash", command="ffmpeg -i a b"),
        _assistant_tool("Bash", command="ffmpeg -i c d"),
    ])
    r = parse_session_index(str(f))
    assert r["first_user_msg"] == "real first ask"
    assert r["n_turns"] == 1
    assert r["cmd_sig"] == ["ffmpeg"]


def test_estimate_tokens_grows_with_size():
    assert estimate_tokens({}) < estimate_tokens({"k": "x" * 400})


def test_build_cmd_census_counts_and_dedups():
    sessions = [
        {"cmd_sig": ["ffmpeg", "ffmpeg"], "project": "p1", "file": "f1"},
        {"cmd_sig": ["ffmpeg"], "project": "p2", "file": "f2"},
        {"cmd_sig": ["git rebase"], "project": "p1", "file": "f3"},
    ]
    census = build_cmd_census(sessions)
    assert census["ffmpeg"]["sessions"] == 2
    assert census["ffmpeg"]["projects"] == 2
    assert set(census["ffmpeg"]["examples"]) == {"f1", "f2"}
    assert list(census)[0] == "ffmpeg"  # sorted by sessions desc


def test_build_cmd_census_caps_examples():
    sessions = [{"cmd_sig": ["x"], "project": "p", "file": f"f{i}"} for i in range(10)]
    assert len(build_cmd_census(sessions, examples_cap=5)["x"]["examples"]) == 5
