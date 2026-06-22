# Skill Gap Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `skill-insight` so the biweekly report also discovers *missing* skills — mine tool-using sessions for recurring manual workflows and propose new skills.

**Architecture:** `extract_skill_data.py` gains a second, independent extraction path that emits one new top-level JSON key `no_skill_index` (a token-bounded navigation index: an uncapped enriched `cmd_census` + deduped `intent_groups`). The `pi -p` prompt in `run_skill_insight.sh` gains one analysis step that reads this index, autonomously drills into example transcripts, and writes a new report section. No changes to the cron/launchd/lock/gate/notify harness.

**Tech Stack:** Python 3 (stdlib only — `json`, `re`, `os`, `subprocess`, `collections`), pytest 9, zsh heredoc prompt.

## Global Constraints

- **Stdlib only** — no new Python dependencies; no embeddings / external API. Clustering is the `pi -p` agent's job; Python does lexical aggregation only.
- **Memory discipline** — grep pre-filter → line-by-line parse → drop per-file state; never read a whole large jsonl into memory.
- **No silent truncation** — when the index is budget-clipped, record `omitted`/`estimated_tokens` in JSON *and* log one line to stderr; the prompt also requires the report to declare it.
- **Do not touch** the harness in `run_skill_insight.sh` (gate, window, lock, backoff, notify) or `com.henry.skill-insight.plist`. Only the `PROMPT` heredoc changes there.
- **Backward-safe** — add the new key alongside existing `out` keys; never rename or remove existing keys (`calls`, `per_skill_summary`, `installed`, etc.).
- **Constants** (define once near the top of `extract_skill_data.py`):
  - `FIRST_MSG_LIMIT = 240`, `CMD_SIG_CAP = 15`, `EXAMPLES_CAP = 5`
  - `INDEX_TOKEN_BUDGET = 60000`, `NDC_RESERVE_FRAC = 0.25`
  - `GENERIC_HEADS = {"git","docker","npm","npx","pnpm","yarn","pip","pip3","cargo","kubectl","go","make","brew","apt","systemctl"}`

---

## File Structure

- `extract_skill_data.py` (modify) — add pure helpers (`cmd_head`, `intent_key`, `estimate_tokens`, `build_cmd_census`, `build_intent_groups`, `build_no_skill_index`), the fixture-testable `parse_session_index`, the grep glue `tool_candidate_files`, and wiring in `main()`.
- `tests/test_extract_skill_data.py` (create) — unit tests for all pure helpers + `parse_session_index` against fixture jsonl written to `tmp_path`.
- `tests/conftest.py` (create) — put repo root on `sys.path` so tests can `import extract_skill_data`.
- `run_skill_insight.sh` (modify) — add the `no_skill_index` data-source bullet, one new analysis step, and the new report section in the `PROMPT` heredoc.
- `README.md` (modify) — one line in the Layout table noting the extractor also emits the missing-skill index.

---

### Task 1: Test harness + `cmd_head` command-head normalization

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/test_extract_skill_data.py`
- Modify: `extract_skill_data.py` (add constants block + `cmd_head` after the existing `is_noise`, around line 46)

**Interfaces:**
- Produces: `cmd_head(command: str) -> str` — first meaningful token of a bash command; for a head in `GENERIC_HEADS`, returns `"<head> <subcommand>"`. Strips leading env-assignments (`FOO=bar`), `sudo`, pipeline tails (`a | b` → `a`), and path prefixes (`/opt/bin/foo` → `foo`). Returns `""` for empty/blank input.

- [ ] **Step 1: Create `tests/conftest.py`**

```python
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_extract_skill_data.py`:

```python
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python3 -m pytest tests/test_extract_skill_data.py -q`
Expected: FAIL with `ImportError: cannot import name 'cmd_head'`

- [ ] **Step 4: Add constants + implement `cmd_head`**

In `extract_skill_data.py`, after the existing module constants (after `AFTER_MSG_CAP = 6`, ~line 24) add:

```python
FIRST_MSG_LIMIT = 240
CMD_SIG_CAP = 15
EXAMPLES_CAP = 5
INDEX_TOKEN_BUDGET = 60000
NDC_RESERVE_FRAC = 0.25
GENERIC_HEADS = {"git", "docker", "npm", "npx", "pnpm", "yarn", "pip", "pip3",
                 "cargo", "kubectl", "go", "make", "brew", "apt", "systemctl"}
ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
```

After `is_noise` (~line 46) add:

```python
def cmd_head(command):
    """Normalized head of a bash command for frequency aggregation.

    First meaningful token; for a GENERIC_HEADS head, append its first
    non-flag subcommand (git -> 'git rebase'). Strips env-assignments, sudo,
    path prefixes, and ignores all but the first command in a pipeline/seq.
    """
    if not command or not command.strip():
        return ""
    seg = re.split(r"[|&;]", command.strip(), maxsplit=1)[0]
    toks = seg.split()
    i = 0
    while i < len(toks) and (ENV_ASSIGN_RE.match(toks[i]) or toks[i] == "sudo"):
        i += 1
    if i >= len(toks):
        return ""
    head = os.path.basename(toks[i])
    if head in GENERIC_HEADS:
        for sub in toks[i + 1:]:
            if not sub.startswith("-"):
                return f"{head} {sub}"
    return head
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_extract_skill_data.py -q`
Expected: PASS (5 tests)

- [ ] **Step 6: Commit**

```bash
git add tests/conftest.py tests/test_extract_skill_data.py extract_skill_data.py
git commit -m "feat(extract): add cmd_head normalization + test harness"
```

---

### Task 2: `parse_session_index` — thin per-session payload from a transcript

**Files:**
- Modify: `extract_skill_data.py` (add `parse_session_index` after `parse_file`, ~line 96)
- Modify: `tests/test_extract_skill_data.py` (add fixture-based tests)

**Interfaces:**
- Consumes: `cmd_head`, `msg_text`, `is_noise`, `FIRST_MSG_LIMIT`, `CMD_SIG_CAP`.
- Produces: `parse_session_index(path: str) -> dict | None`. Returns `None` when the session has no Bash/Write/Edit `tool_use`. Otherwise returns
  `{"has_skill": bool, "first_user_msg": str, "n_turns": int, "cmd_sig": list[str], "wrote": list[str]}`.
  Caller adds `file` and `project`. `cmd_sig` is ordered, de-duplicated, capped at `CMD_SIG_CAP`. `wrote` is sorted unique file extensions of Write/Edit targets.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_extract_skill_data.py`:

```python
import json
from extract_skill_data import parse_session_index


def _write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")


def _assistant_tool(name, **inp):
    return {"type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]}}


def _user(text):
    return {"type": "user", "message": {"content": text}}


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_extract_skill_data.py -q`
Expected: FAIL with `ImportError: cannot import name 'parse_session_index'`

- [ ] **Step 3: Implement `parse_session_index`**

In `extract_skill_data.py`, after `parse_file` (~line 96) add:

```python
def parse_session_index(path):
    """One pass over a transcript -> thin missing-skill index payload.

    Returns None if the session has no Bash/Write/Edit tool use (not a
    manual-workflow candidate). Caller adds file/project.
    """
    first_user_msg = ""
    n_turns = 0
    has_skill = False
    has_tool = False
    cmd_sig = []
    wrote = set()
    with open(path, errors="replace") as f:
        for line in f:
            is_user = '"type":"user"' in line
            is_asst = '"type":"assistant"' in line
            if not (is_user or is_asst):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = obj.get("message") or {}
            if obj.get("type") == "user":
                text = msg_text(msg)
                if is_noise(text):
                    continue
                n_turns += 1
                if not first_user_msg:
                    first_user_msg = text[:FIRST_MSG_LIMIT]
            elif obj.get("type") == "assistant":
                for item in (msg.get("content") or []):
                    if not (isinstance(item, dict) and item.get("type") == "tool_use"):
                        continue
                    name = item.get("name")
                    inp = item.get("input") or {}
                    if name == "Skill":
                        has_skill = True
                    elif name == "Bash":
                        has_tool = True
                        h = cmd_head(inp.get("command", ""))
                        if h and h not in cmd_sig and len(cmd_sig) < CMD_SIG_CAP:
                            cmd_sig.append(h)
                    elif name in ("Write", "Edit"):
                        has_tool = True
                        fp = inp.get("file_path") or inp.get("filePath") or ""
                        base = os.path.basename(fp)
                        if "." in base:
                            wrote.add(base.rsplit(".", 1)[-1])
    if not has_tool:
        return None
    return {
        "has_skill": has_skill,
        "first_user_msg": first_user_msg,
        "n_turns": n_turns,
        "cmd_sig": cmd_sig,
        "wrote": sorted(wrote),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_extract_skill_data.py -q`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add extract_skill_data.py tests/test_extract_skill_data.py
git commit -m "feat(extract): parse_session_index thin per-session payload"
```

---

### Task 3: `estimate_tokens` + `build_cmd_census`

**Files:**
- Modify: `extract_skill_data.py` (add both functions after `parse_session_index`)
- Modify: `tests/test_extract_skill_data.py`

**Interfaces:**
- Produces: `estimate_tokens(obj) -> int` — `len(json.dumps(obj, ensure_ascii=False)) // 4`.
- Produces: `build_cmd_census(sessions: list[dict], examples_cap=EXAMPLES_CAP) -> dict`. Each session dict has at least `cmd_sig`, `project`, `file`. Returns `{head: {"sessions": int, "projects": int, "examples": list[str]}}` sorted by `sessions` desc. `sessions` counts distinct sessions containing the head; `projects` counts distinct projects; `examples` ≤ `examples_cap` file paths.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_extract_skill_data.py`:

```python
from extract_skill_data import estimate_tokens, build_cmd_census


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_extract_skill_data.py -q`
Expected: FAIL with `ImportError: cannot import name 'estimate_tokens'`

- [ ] **Step 3: Implement both functions**

After `parse_session_index` add:

```python
def estimate_tokens(obj):
    return len(json.dumps(obj, ensure_ascii=False)) // 4


def build_cmd_census(sessions, examples_cap=EXAMPLES_CAP):
    agg = defaultdict(lambda: {"sessions": 0, "projects": set(), "examples": []})
    for s in sessions:
        for h in dict.fromkeys(s["cmd_sig"]):  # distinct heads, order-preserving
            a = agg[h]
            a["sessions"] += 1
            a["projects"].add(s["project"])
            if len(a["examples"]) < examples_cap:
                a["examples"].append(s["file"])
    return {h: {"sessions": a["sessions"], "projects": len(a["projects"]),
                "examples": a["examples"]}
            for h, a in sorted(agg.items(), key=lambda kv: -kv[1]["sessions"])}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_extract_skill_data.py -q`
Expected: PASS (12 tests)

- [ ] **Step 5: Commit**

```bash
git add extract_skill_data.py tests/test_extract_skill_data.py
git commit -m "feat(extract): estimate_tokens + enriched cmd_census"
```

---

### Task 4: `intent_key` + `build_intent_groups` (dedup + budgeted stratified fill)

**Files:**
- Modify: `extract_skill_data.py` (add both after `build_cmd_census`)
- Modify: `tests/test_extract_skill_data.py`

**Interfaces:**
- Consumes: `estimate_tokens`, `FIRST_MSG_LIMIT`, `EXAMPLES_CAP`, `INDEX_TOKEN_BUDGET`, `NDC_RESERVE_FRAC`.
- Produces: `intent_key(msg: str) -> str` — cheap dedup key: lowercased alnum/CJK tokens, stopwords removed, first 8 sorted-unique tokens joined by spaces.
- Produces: `build_intent_groups(sessions, token_budget=INDEX_TOKEN_BUDGET, examples_cap=EXAMPLES_CAP) -> tuple[list[dict], int, int]` returning `(groups, selected, omitted)`. Groups deduped by `intent_key(first_user_msg)`; each group: `{"representative_msg","similar_sessions","projects","examples","no_distinctive_cmd"}`. `no_distinctive_cmd` is True only if *every* session in the group had empty `cmd_sig`. Fill order: by `similar_sessions` desc, but reserve `NDC_RESERVE_FRAC` of the budget so `no_distinctive_cmd` groups are not starved. `selected` = sum of `similar_sessions` of emitted groups; `omitted` = total sessions − selected.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_extract_skill_data.py`:

```python
from extract_skill_data import intent_key, build_intent_groups


def test_intent_key_dedups_near_identical():
    assert intent_key("Write a PRD for billing") == intent_key("write a prd for billing please")
    assert intent_key("Write a PRD for billing") != intent_key("summarize this meeting doc")


def _sess(msg, cmd_sig, project="p", file="f"):
    return {"first_user_msg": msg, "cmd_sig": cmd_sig, "project": project, "file": file}


def test_build_intent_groups_groups_and_counts():
    sessions = [
        _sess("write a prd for X", [], file="a"),
        _sess("Write a PRD for X", [], file="b"),
        _sess("rebase my branch", ["git rebase"], file="c"),
    ]
    groups, selected, omitted = build_intent_groups(sessions, token_budget=INDEX_TOKEN_BUDGET)
    by_msg = {g["representative_msg"].lower()[:11]: g for g in groups}
    prd = next(g for g in groups if "prd" in g["representative_msg"].lower())
    assert prd["similar_sessions"] == 2
    assert prd["no_distinctive_cmd"] is True
    rb = next(g for g in groups if "rebase" in g["representative_msg"].lower())
    assert rb["no_distinctive_cmd"] is False
    assert selected == 3 and omitted == 0


def test_build_intent_groups_budget_clips_and_reports_omitted():
    sessions = [_sess(f"distinct ask number {i}", ["cmd%d" % i], file=f"f{i}")
                for i in range(200)]
    groups, selected, omitted = build_intent_groups(sessions, token_budget=200)
    assert selected + omitted == 200
    assert omitted > 0  # tiny budget must clip


def test_build_intent_groups_reserve_keeps_ndc_group():
    # 50 single-session command groups (big by order) + 1 NDC group of size 1.
    sessions = [_sess(f"cmd task {i}", [f"c{i}"], file=f"c{i}") for i in range(50)]
    sessions.append(_sess("please write documentation", [], file="ndc"))
    # budget large enough for a few entries but not all 51
    groups, selected, omitted = build_intent_groups(sessions, token_budget=400)
    assert any(g["no_distinctive_cmd"] for g in groups), "NDC reserve must protect the doc group"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_extract_skill_data.py -q`
Expected: FAIL with `ImportError: cannot import name 'intent_key'`

- [ ] **Step 3: Implement both functions**

After `build_cmd_census` add:

```python
INTENT_STOP = set(
    "the a an to of for and or in on at is be do my me i you your please can "
    "help with this that it now then so just give make want need".split())


def intent_key(msg):
    toks = re.findall(r"[a-z0-9]+|[一-鿿]+", msg.lower())
    toks = [t for t in toks if t not in INTENT_STOP][:8]
    return " ".join(sorted(set(toks)))


def _group_entry(g, examples_cap):
    return {
        "representative_msg": g["msg"][:FIRST_MSG_LIMIT],
        "similar_sessions": g["sessions"],
        "projects": len(g["projects"]),
        "examples": g["examples"][:examples_cap],
        "no_distinctive_cmd": g["no_distinctive_cmd"],
    }


def build_intent_groups(sessions, token_budget=INDEX_TOKEN_BUDGET,
                        examples_cap=EXAMPLES_CAP):
    groups = {}
    for s in sessions:
        k = intent_key(s["first_user_msg"])
        g = groups.get(k)
        if g is None:
            g = groups[k] = {"msg": s["first_user_msg"], "sessions": 0,
                             "projects": set(), "examples": [],
                             "no_distinctive_cmd": True}
        g["sessions"] += 1
        g["projects"].add(s["project"])
        if len(g["examples"]) < examples_cap:
            g["examples"].append(s["file"])
        if s["cmd_sig"]:
            g["no_distinctive_cmd"] = False

    total = sum(g["sessions"] for g in groups.values())
    ordered = sorted(groups.values(), key=lambda g: -g["sessions"])
    reserve = token_budget * NDC_RESERVE_FRAC

    out, used = [], set()

    def try_add(g, ceiling):
        entry = _group_entry(g, examples_cap)
        if estimate_tokens(out + [entry]) > ceiling:
            return False
        out.append(entry)
        used.add(id(g))
        return True

    # Phase 1: by recurrence up to (budget - reserve)
    for g in ordered:
        if not try_add(g, token_budget - reserve):
            break
    # Phase 2: spend the reserve preferentially on not-yet-included NDC groups
    for g in ordered:
        if id(g) in used:
            continue
        if g["no_distinctive_cmd"]:
            try_add(g, token_budget)
    # Phase 3: backfill any remaining budget by recurrence
    for g in ordered:
        if id(g) in used:
            continue
        if not try_add(g, token_budget):
            break

    selected = sum(e["similar_sessions"] for e in out)
    return out, selected, total - selected
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_extract_skill_data.py -q`
Expected: PASS (16 tests)

- [ ] **Step 5: Commit**

```bash
git add extract_skill_data.py tests/test_extract_skill_data.py
git commit -m "feat(extract): intent_key + budgeted stratified intent_groups"
```

---

### Task 5: `build_no_skill_index` — assemble the index

**Files:**
- Modify: `extract_skill_data.py` (add after `build_intent_groups`)
- Modify: `tests/test_extract_skill_data.py`

**Interfaces:**
- Consumes: `build_cmd_census`, `build_intent_groups`, `estimate_tokens`, `INDEX_TOKEN_BUDGET`.
- Produces: `build_no_skill_index(sessions, token_budget=INDEX_TOKEN_BUDGET) -> dict` with keys `scanned`, `selected`, `omitted`, `estimated_tokens`, `cmd_census`, `intent_groups`. `scanned` = `len(sessions)`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_extract_skill_data.py`:

```python
from extract_skill_data import build_no_skill_index


def test_build_no_skill_index_shape():
    sessions = [
        {"first_user_msg": "make a gif", "cmd_sig": ["ffmpeg"], "project": "p1", "file": "f1"},
        {"first_user_msg": "make a gif too", "cmd_sig": ["ffmpeg"], "project": "p2", "file": "f2"},
    ]
    idx = build_no_skill_index(sessions)
    assert idx["scanned"] == 2
    assert idx["omitted"] == 0
    assert "ffmpeg" in idx["cmd_census"]
    assert idx["cmd_census"]["ffmpeg"]["projects"] == 2
    assert isinstance(idx["intent_groups"], list)
    assert idx["estimated_tokens"] == estimate_tokens({
        "scanned": idx["scanned"], "selected": idx["selected"], "omitted": idx["omitted"],
        "cmd_census": idx["cmd_census"], "intent_groups": idx["intent_groups"]})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_extract_skill_data.py -q`
Expected: FAIL with `ImportError: cannot import name 'build_no_skill_index'`

- [ ] **Step 3: Implement `build_no_skill_index`**

After `build_intent_groups` add:

```python
def build_no_skill_index(sessions, token_budget=INDEX_TOKEN_BUDGET):
    census = build_cmd_census(sessions)
    groups, selected, omitted = build_intent_groups(sessions, token_budget)
    idx = {
        "scanned": len(sessions),
        "selected": selected,
        "omitted": omitted,
        "cmd_census": census,
        "intent_groups": groups,
    }
    idx["estimated_tokens"] = estimate_tokens(idx)
    return idx
```

Note: `estimated_tokens` is computed on the dict *before* the key is inserted, so the test reconstructs the same dict (without `estimated_tokens`) to compare. Keep the assertion above in sync.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_extract_skill_data.py -q`
Expected: PASS (17 tests)

- [ ] **Step 5: Commit**

```bash
git add extract_skill_data.py tests/test_extract_skill_data.py
git commit -m "feat(extract): build_no_skill_index assembler"
```

---

### Task 6: Wire into `main()` + `tool_candidate_files` grep glue

**Files:**
- Modify: `extract_skill_data.py` — add `tool_candidate_files` (after `candidate_files`, ~line 62); wire into `main()` (before the `out = {...}` block, ~line 188); add the new key to `out`.

**Interfaces:**
- Consumes: `parse_session_index`, `build_no_skill_index`, `CLAUDE`, `INDEX_TOKEN_BUDGET`.
- Produces: top-level JSON key `out["no_skill_index"]`.

- [ ] **Step 1: Add `tool_candidate_files`**

After `candidate_files` (~line 62) add:

```python
TOOL_MARK_RE = r'"name":"(Bash|Write|Edit)"'


def tool_candidate_files(window_days):
    """jsonl files in window containing at least one Bash/Write/Edit call."""
    res = subprocess.run(
        ["find", str(CLAUDE / "projects"), "-name", "*.jsonl",
         "-mtime", f"-{window_days}"],
        capture_output=True, text=True)
    files = [f for f in res.stdout.splitlines() if f]
    hits = []
    for i in range(0, len(files), 200):
        batch = files[i:i + 200]
        g = subprocess.run(["grep", "-lE", TOOL_MARK_RE] + batch,
                           capture_output=True, text=True)
        hits.extend(g.stdout.splitlines())
    return hits
```

- [ ] **Step 2: Wire into `main()`**

In `main()`, immediately before the `out = {` line (~line 188 after the `summary = {...}` comprehension), add:

```python
    # missing-skill discovery: tool-using sessions -> bounded navigation index
    no_skill_sessions = []
    for path in tool_candidate_files(args.window):
        payload = parse_session_index(path)
        if payload is None:
            continue
        rel = os.path.relpath(path, CLAUDE / "projects")
        payload["file"] = path
        payload["project"] = rel.split(os.sep)[0]
        no_skill_sessions.append(payload)
    no_skill_index = build_no_skill_index(no_skill_sessions, INDEX_TOKEN_BUDGET)
    if no_skill_index["omitted"]:
        print(f"no_skill_index: scanned {no_skill_index['scanned']}, "
              f"selected {no_skill_index['selected']}, "
              f"omitted {no_skill_index['omitted']} "
              f"(~{no_skill_index['estimated_tokens']} tok, "
              f"budget {INDEX_TOKEN_BUDGET})", file=sys.stderr)
```

- [ ] **Step 3: Add the key to `out`**

In the `out = {` dict literal (~line 189), add a line before the closing `}` (e.g., after `"installed": installed,`):

```python
        "no_skill_index": no_skill_index,
```

- [ ] **Step 4: Verify existing unit tests still pass + module imports**

Run: `python3 -m pytest tests/test_extract_skill_data.py -q && python3 -c "import extract_skill_data"`
Expected: PASS (17 tests), no import error.

- [ ] **Step 5: Smoke-run the extractor against real data**

Run: `python3 extract_skill_data.py --window 28 --out /tmp/skillgap_smoke.json`
Expected stderr: an `extracted N calls ...` line, and (given ~1300 candidate sessions) a `no_skill_index: scanned ... omitted ...` line.
Then verify the new key exists and is bounded:

```bash
python3 - <<'PY'
import json
d = json.load(open("/tmp/skillgap_smoke.json"))
i = d["no_skill_index"]
print("scanned", i["scanned"], "selected", i["selected"], "omitted", i["omitted"],
      "est_tok", i["estimated_tokens"], "census_heads", len(i["cmd_census"]),
      "groups", len(i["intent_groups"]))
assert i["estimated_tokens"] <= 60000 * 1.2, "index materially over budget"
assert set(["scanned","selected","omitted","estimated_tokens","cmd_census","intent_groups"]) <= set(i)
print("OK")
PY
```
Expected: prints counts and `OK`; `est_tok` near or under 60000.

- [ ] **Step 6: Commit**

```bash
git add extract_skill_data.py
git commit -m "feat(extract): emit no_skill_index from main()"
```

---

### Task 7: Prompt + report section in `run_skill_insight.sh`

**Files:**
- Modify: `run_skill_insight.sh` — `PROMPT` heredoc only (data-source bullet ~line 99-103, new analysis step after the line `【第 6 步 · 改进建议（improve）】` block, output structure ~line 135-142).

**Interfaces:** none (prompt text consumed by `pi -p`). No unit test — verified by reading the rendered prompt and the end-to-end run in Task 8.

- [ ] **Step 1: Add the `no_skill_index` data-source bullet**

In the data-source list, find the `* installed：已安装的 user_skills 和 plugin_skills 清单` line and add immediately after it (still inside the `${EXTRACT}` bullet's sub-list):

```
  * no_skill_index：缺失-skill 发现的导航索引（不是结论）。scanned/selected/omitted/estimated_tokens 为覆盖元信息；cmd_census={命令头:{sessions 跨会话数, projects 跨项目数, examples 代表会话路径}}（永不封顶）；intent_groups=[{representative_msg 代表诉求, similar_sessions 近似会话数, projects 跨项目数, examples 代表会话路径, no_distinctive_cmd 是否只有 Write/Edit 无特征命令}]（按 token 预算去重后保留）
```

- [ ] **Step 2: Add the new analysis step**

Find the existing `【第 7 步 · trigger 评测集】` header line. Insert a NEW step block immediately before it, and renumber that trigger step header from `第 7 步` to `第 8 步`:

New block to insert (verbatim):

```
【第 7 步 · 能力缺口（建议新建 skill）】读 no_skill_index——这是索引不是结论。
  - 用 cmd_census（看 sessions/projects）和 intent_groups（看 similar_sessions/projects）找出跨 >=2 会话反复出现的工作流苗头；跨 >=2 项目才更可能是全局 skill，单项目反复优先考虑写进该项目的 CLAUDE.md。
  - 最小取证（防 under-explore）：每个进入报告的候选簇，必须实际打开 examples 里 >=2 个会话读全以坐实工作流，并引用真实用户原话；只凭索引、不开会话，不得下结论。
  - 大文件护栏：读 examples 前先 wc -c / ls -la 看大小，偏大的用 grep/head 定点看，不要整文件读入。
  - 对每个簇做路由判断：该新建 skill / 该进 CLAUDE.md（全局或项目级偏好）/ 该扩展现有 skill（那归第 6 步触发类）——只有真·无人覆盖的反复工作流进本节；逐个与 installed 去重，已存在的不提。
  - 若 no_skill_index.omitted > 0（有实质截断），在本节开头一句声明覆盖范围（scanned/selected/omitted）。
```

Then change the following line from:

```
【第 7 步 · trigger 评测集】把漏触发用户原话
```
to:
```
【第 8 步 · trigger 评测集】把漏触发用户原话
```

- [ ] **Step 3: Add the report section and renumber the appendix**

In the `输出要求` structure block, find:

```
⑥ 附录：按 skill 分组的 trigger 评测集 JSON
```

Replace it with:

```
⑥ 建议新建的 Skill（能力缺口）：每个候选给出——候选名+一句话职责；命中证据（出现 N 次·跨 M 项目·2-3 条用户原话截短不润色·典型命令/脚本签名）；一句路由判断（为什么是 skill 而非 CLAUDE.md/扩展现有）；description 草稿（pushy 原则：做什么+何时用+枚举触发语境）；SKILL.md 主体要点+可能的 scripts/ 草图；置信度（高/中）。节首若 omitted>0 先声明覆盖范围。
⑦ 附录：按 skill 分组的 trigger 评测集 JSON
```

And update the final emphasis line from `篇幅向 ② 和 ④ 倾斜。` to:

```
篇幅向 ②、④、⑥ 倾斜。
```

- [ ] **Step 4: Verify the heredoc renders (no syntax break)**

Run: `zsh -n run_skill_insight.sh && echo "syntax OK"`
Expected: `syntax OK`

- [ ] **Step 5: Commit**

```bash
git add run_skill_insight.sh
git commit -m "feat(prompt): add capability-gap (new-skill) analysis step + report section"
```

---

### Task 8: README note + end-to-end verification

**Files:**
- Modify: `README.md` (Layout table row for `extract_skill_data.py`)

- [ ] **Step 1: Update the README Layout row**

Find the table row:

```
| `extract_skill_data.py` | Pre-extracts `~/.claude` skill calls into one compact JSON for the agent |
```

Replace with:

```
| `extract_skill_data.py` | Pre-extracts `~/.claude` into one compact JSON: graded skill calls **and** a `no_skill_index` (bounded navigation index of tool-using sessions) for missing-skill discovery |
```

- [ ] **Step 2: Full unit-test pass**

Run: `python3 -m pytest tests/ -q`
Expected: PASS (17 tests).

- [ ] **Step 3: End-to-end preview run**

Run: `./run_skill_insight.sh --force`
Expected: completes; a new `skill-log/skill_usage_report_<today>.md` is written; `skill-log/skill_insight.log` shows `finished OK`. (Requires `pi` on PATH; if unavailable in this environment, skip and note it.)

- [ ] **Step 4: Manual verification of the new report section**

Open the generated report and confirm section ⑥ exists. For each proposed skill, manually verify:
- the quoted user words are findable in the cited transcript (`grep -F "<quote>" <examples path>`);
- the proposal is NOT already in `installed` (cross-check `~/.claude/skills` + plugin skills);
- the route call is sound (single-project recurrence is not proposed as a global skill);
- if `omitted > 0` in `/tmp/skillgap_smoke.json`'s index, the section header declares coverage.

Record findings in the commit message.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: note no_skill_index in README; verify skill-gap report end-to-end"
```

---

## Self-Review

**Spec coverage** (spec → task):
- §4.1 `M = all tool sessions, has_skill flag` → Task 2 (`has_skill`), Task 6 (`tool_candidate_files`, no subtraction of skill files). ✓
- §4.1 thin per-session payload → Task 2. ✓ (`bytes` field intentionally dropped — YAGNI; large-file guard is a prompt instruction in Task 7 Step 2. Deviation from spec, recorded here.)
- §4.1 command-head + subcommand for generic heads → Task 1. ✓
- §4.1 `cmd_census` uncapped + cross-project + examples → Task 3. ✓
- §4.1 `intent_groups` dedup + token budget + NDC/project reserve → Task 4. ✓
- §4.1 index metadata `scanned/selected/omitted/estimated_tokens` + stderr truncation log → Task 5 (metadata) + Task 6 Step 2 (stderr). ✓
- §4.2 prompt: index-not-conclusion, ≥2-session recurrence, ≥2-project route, min drill-in, large-file guard, route+dedup, truncation declaration → Task 7 Step 2. ✓
- §4.3 report section ⑥ + appendix renumber to ⑦ → Task 7 Step 3. ✓
- §6 tests (mixed session, generic-head subcommand, dedup, budget/omitted) → Tasks 1–5; end-to-end + manual evidence check → Task 8. ✓
- §5 boundaries (no auto file creation, harness untouched, no embeddings, no rotating tail, no episode split) → respected; no task adds them. ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code; commands have expected output. ✓

**Type consistency:** `cmd_head→str`; per-session dict keys (`has_skill, first_user_msg, n_turns, cmd_sig, wrote, file, project`) consistent across Tasks 2/3/4/6; `build_cmd_census`/`build_intent_groups`/`build_no_skill_index` signatures match their call sites; index keys (`scanned, selected, omitted, estimated_tokens, cmd_census, intent_groups`) consistent across Tasks 5/6/7. ✓

**Deviations from spec (intentional, recorded):** dropped the per-session `bytes` field (agent checks size via shell on example paths instead) — keeps the index leaner; the large-file guard survives as a prompt instruction.
