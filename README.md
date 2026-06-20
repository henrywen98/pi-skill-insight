# pi-skill-insight

**English** · [中文](README.zh-CN.md)

> **What if a headless AI agent reviewed how *you* work — every two weeks, on a cron, while you sleep?**

A real-world **case study of using `pi -p` (a headless agent CLI) as a scheduled automation engine.**
The worked example: a biweekly job that grades your own Claude Code **skill usage** and tells you
which skills help, which misfire, and exactly how to fix them.

*macOS · launchd · zsh + python3 · powered by the [`pi`](https://github.com/) agent in headless `-p` mode*

📄 See a [sample report](examples/sample_report.md).

## The idea in one picture

```mermaid
flowchart LR
    A["launchd timer<br/>Mon 14:00 + RunAtLoad"] --> B{"run_skill_insight.sh"}
    B -->|"≥13d since last run?"| C["extract_skill_data.py<br/>(~/.claude → compact JSON)"]
    B -->|"too soon / failed recently"| Z["skip silently"]
    C --> D["pi -p<br/>headless agent reads the JSON,<br/>grades every skill call"]
    D --> E["skill_usage_report_DATE.md"]
    E --> F["terminal-notifier<br/>✓ done / ✗ failed banner"]
```

The interesting part isn't the report — it's that **a one-shot AI agent (`pi -p`) is wired up as
a self-healing cron job**. That harness is reusable for *any* "let an agent do X on a schedule"
task. This repo is the smallest complete example of it.

## The case study

**Problem.** You accumulate dozens of custom skills / slash-commands for your AI coding agent,
but you never actually know which ones *work*. Good ones save you keystrokes; bad ones quietly
make you babysit and correct the agent. You have no feedback loop.

**The insight.** The signal is already in your transcripts: **when a skill underperforms, you send
several correction/guidance messages right after invoking it.** Each "post-invocation manual
correction" is ground-truth evidence the skill fell short. Treat every real skill call as a test
case, and your own follow-up messages as the grader.

**The engine.** Rather than build an analysis pipeline by hand, hand the evidence to an agent:
`pi -p "<a long, structured grading prompt>"`. Headless `-p` mode runs the agent non-interactively,
to completion, and exits — exactly what a cron job needs. The agent does the reading, grading,
clustering, baseline comparison, and writes a Markdown report.

**The harness.** Wrapping a `-p` agent call in a robust scheduled task is where the real
engineering is. `run_skill_insight.sh` adds, around the single `pi -p` line:

- **Biweekly gate** — a last-success marker keeps the effective cadence at ≥13 days, regardless of how often the timer fires.
- **Self-heal** — `launchd` fires Monday 14:00 *and* `RunAtLoad`; a Monday missed while the Mac was off is caught up on the next boot.
- **Honest data window** — analyzes the *actual* gap since the last run (clamped 14–28d), so catch-up runs neither skip nor double-count.
- **Failure backoff** — after a failure, hold off retrying/re-notifying for 12h.
- **Single-instance lock** — a stale lock older than 6h is stolen.
- **Notify only on outcomes** — a desktop banner on report-done ✓ / failed ✗; skips stay silent.

**The result.** Every two weeks, a banner; a report like [`examples/sample_report.md`](examples/sample_report.md)
with a scorecard, per-skill intervention analysis, your own quoted words as evidence, copy-paste
`SKILL.md` rewrites, and a follow-up on whether last cycle's fixes actually moved the numbers.

```
┌─────────────────────────────────────────────┐
│  ● Skill Insight                              │
│  Done ✓ — report generated (14 days since)    │
│  skill_usage_report_2026-05-26.md             │
└─────────────────────────────────────────────┘
```

## Reuse the pattern: `pi -p` as a cron job

The whole point of a case study is that you can lift the pattern. To build your *own* scheduled
agent task, keep the harness in `run_skill_insight.sh` and swap two things:

1. **The prompt** — replace the `PROMPT=$(cat <<EOF … EOF)` block with your task.
2. **The pre-extraction** (optional) — `extract_skill_data.py` exists only to compress GBs of
   logs into one compact JSON so the agent reads cheaply. Drop it if your task doesn't need it.

Everything else — gate, self-heal, window, lock, backoff, notify — is task-agnostic boilerplate
you get for free. The same shape works for "summarize my week", "triage new issues nightly",
"diff the docs against the code every Friday", etc.

> Using a different headless agent (e.g. `claude -p`, `codex -p`)? Swap the one `pi -p` line —
> the harness doesn't care which agent it drives.

## Requirements

- **[`pi`](https://github.com/)** — the agent CLI that performs the analysis (headless `-p` mode). The script extends `PATH` with `$HOME/.local/bin` and `/opt/homebrew/bin` to find it.
- **`terminal-notifier`** — desktop banners: `brew install terminal-notifier` (first run may need approval in *System Settings → Notifications*).
- **`python3`** — runs the pre-extractor.
- Reads `~/.claude/projects/**/*.jsonl`, `~/.claude/history.jsonl`, `~/.claude/skills`, `~/.claude/plugins`.

## Install

```sh
git clone https://github.com/henrywen98/pi-skill-insight
cd pi-skill-insight
```

1. Open `com.henry.skill-insight.plist` and replace the two `/ABSOLUTE/PATH/TO/pi-skill-insight`
   placeholders with this folder's real absolute path (the script path and the log path).
2. Install and start the launchd job:

```sh
cp com.henry.skill-insight.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.henry.skill-insight.plist
```

The script self-locates (`BASE_DIR`), so the repo can live anywhere. To uninstall:
`launchctl unload ~/Library/LaunchAgents/com.henry.skill-insight.plist`.

## Run once now

```sh
./run_skill_insight.sh --force   # bypass the biweekly gate; does not shift the schedule
```

## Output & privacy

Everything lands in `skill-log/` — logs, reports, the extraction cache, and state markers.
**`skill-log/` is gitignored**: it derives from your private `~/.claude` transcripts and never
leaves your machine. The only sample in this repo is the **synthetic** one under `examples/`.

## Troubleshooting

- Logs: `tail -f skill-log/skill_insight.log`
- Job status: `launchctl list com.henry.skill-insight` (`LastExitStatus = 0` is healthy)
- No notification: ensure `terminal-notifier` is installed and allowed in *System Settings → Notifications*.

## Layout

| File | Purpose |
| --- | --- |
| `run_skill_insight.sh` | The harness: gate, window, lock, backoff, notify — wraps one `pi -p` call |
| `extract_skill_data.py` | Pre-extracts `~/.claude` skill calls into one compact JSON for the agent |
| `com.henry.skill-insight.plist` | launchd job template (edit the two paths before installing) |
| `examples/sample_report.md` | Synthetic illustrative output |
| `skill-log/` | Real output + state (gitignored, local only) |

## License

[MIT](LICENSE)
