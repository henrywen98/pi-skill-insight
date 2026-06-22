from extract_skill_data import cmd_head


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
