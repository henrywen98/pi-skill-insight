#!/usr/bin/env python3
"""Pre-extract skill-usage data from Claude Code transcripts for the biweekly
insight run. Does the heavy lifting cheaply so the LLM analyst (pi) reads one
compact JSON instead of scanning GBs of jsonl itself.

Memory discipline: files are filtered with grep first (only ~10% contain Skill
calls), then parsed strictly line-by-line; per-file state is dropped after use.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

CLAUDE = Path.home() / ".claude"
SKILL_MARK = '"name":"Skill"'
CMD_RE = re.compile(r"<command-name>([^<]+)</command-name>")
PROMPT_BEFORE_LIMIT = 600
AFTER_TEXT_LIMIT = 800
AFTER_MSG_CAP = 6
FIRST_MSG_LIMIT = 240
CMD_SIG_CAP = 15
EXAMPLES_CAP = 5
INDEX_TOKEN_BUDGET = 60000
NDC_RESERVE_FRAC = 0.25
GENERIC_HEADS = {"git", "docker", "npm", "npx", "pnpm", "yarn", "pip", "pip3",
                 "cargo", "kubectl", "go", "make", "brew", "apt", "systemctl"}
ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def msg_text(message):
    c = message.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(
            i.get("text", "") for i in c
            if isinstance(i, dict) and i.get("type") == "text"
        )
    return ""


def is_noise(text):
    t = text.lstrip()
    return (not t or t.startswith("<system-reminder")
            or t.startswith("<ide_") or t.startswith("<task-notification")
            or t.startswith("Caveat:")
            or t.startswith("Base directory for this skill")
            or t.startswith("Launching skill"))


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


def candidate_files(window_days):
    """jsonl files modified within the window that contain at least one Skill call."""
    res = subprocess.run(
        ["find", str(CLAUDE / "projects"), "-name", "*.jsonl",
         "-mtime", f"-{window_days}"],
        capture_output=True, text=True)
    files = [f for f in res.stdout.splitlines() if f]
    hits = []
    for i in range(0, len(files), 200):
        batch = files[i:i + 200]
        g = subprocess.run(["grep", "-l", "-F", SKILL_MARK] + batch,
                           capture_output=True, text=True)
        hits.extend(g.stdout.splitlines())
    return len(files), hits


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


def parse_file(path):
    """One pass over a transcript: ordered user messages + Skill calls."""
    events = []  # ('user'|'call', payload) in file order
    with open(path, errors="replace") as f:
        for line in f:
            has_call = SKILL_MARK in line
            is_user = '"type":"user"' in line
            if not (has_call or is_user):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = obj.get("message") or {}
            if has_call and obj.get("type") == "assistant":
                for item in (msg.get("content") or []):
                    if isinstance(item, dict) and item.get("type") == "tool_use" \
                            and item.get("name") == "Skill":
                        events.append(("call", {
                            "skill": (item.get("input") or {}).get("skill", "?"),
                            "ts": obj.get("timestamp", ""),
                        }))
            elif is_user and obj.get("type") == "user":
                text = msg_text(msg)
                if is_noise(text):
                    continue
                m = CMD_RE.search(text)
                events.append(("user", {
                    "ts": obj.get("timestamp", ""),
                    "text": text,
                    "cmd": m.group(1) if m else None,
                }))
    return events


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
            is_user = '"type":' in line and '"user"' in line
            is_asst = '"type":' in line and '"assistant"' in line
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


def build_no_skill_index(sessions, token_budget=INDEX_TOKEN_BUDGET):
    census = build_cmd_census(sessions)
    census_tok = estimate_tokens(census)
    groups, selected, omitted = build_intent_groups(
        sessions, max(0, token_budget - census_tok))
    idx = {
        "scanned": len(sessions),
        "selected": selected,
        "omitted": omitted,
        "cmd_census": census,
        "intent_groups": groups,
    }
    idx["estimated_tokens"] = estimate_tokens(idx)
    return idx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=14)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    total_files, files = candidate_files(args.window)
    calls = []
    for path in files:
        events = parse_file(path)
        rel = os.path.relpath(path, CLAUDE / "projects")
        project = rel.split(os.sep)[0]
        in_subagent = "/subagents/" in path
        per_skill_in_file = Counter(p["skill"] for k, p in events if k == "call")
        last_user = None
        for idx, (kind, payload) in enumerate(events):
            if kind == "user":
                last_user = payload
                continue
            after = []
            for k2, p2 in events[idx + 1:]:
                if k2 == "user":
                    after.append({
                        "ts": p2["ts"],
                        "text": p2["text"][:AFTER_TEXT_LIMIT],
                        "is_command": bool(p2["cmd"]),
                    })
                    if len(after) >= AFTER_MSG_CAP:
                        break
            calls.append({
                "skill": payload["skill"],
                "ts": payload["ts"],
                "project": project,
                "file": path,
                "in_subagent": in_subagent,
                "trigger_cmd": last_user["cmd"] if last_user else None,
                "prompt_before": (last_user["text"][:PROMPT_BEFORE_LIMIT]
                                  if last_user else ""),
                "after_user_msgs": after,
                "same_file_repeats": per_skill_in_file[payload["skill"]],
            })

    # explicit slash-command usage from history.jsonl within the window
    cutoff_ms = (time.time() - args.window * 86400) * 1000
    slash = Counter()
    hist = CLAUDE / "history.jsonl"
    if hist.exists():
        with open(hist, errors="replace") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("timestamp", 0) < cutoff_ms:
                    continue
                d = (obj.get("display") or "").strip()
                if d.startswith("/"):
                    slash[d.split()[0].lstrip("/")] += 1

    # installed skill inventory
    installed = {"user_skills": [], "plugin_skills": []}
    user_dir = CLAUDE / "skills"
    if user_dir.is_dir():
        installed["user_skills"] = sorted(
            p.name for p in user_dir.iterdir() if (p / "SKILL.md").exists())
    plug_dir = CLAUDE / "plugins"
    if plug_dir.is_dir():
        names = set()
        for sk in plug_dir.glob("**/skills/*/SKILL.md"):
            names.add(sk.parent.name)
        installed["plugin_skills"] = sorted(names)

    per_skill = defaultdict(lambda: {"calls": 0, "files": set(), "projects": set(),
                                     "subagent_calls": 0, "explicit": 0})
    for c in calls:
        s = per_skill[c["skill"]]
        s["calls"] += 1
        s["files"].add(c["file"])
        s["projects"].add(c["project"])
        if c["in_subagent"]:
            s["subagent_calls"] += 1
        if c["trigger_cmd"]:
            s["explicit"] += 1
    summary = {k: {"calls": v["calls"], "sessions": len(v["files"]),
                   "projects": len(v["projects"]),
                   "subagent_calls": v["subagent_calls"],
                   "explicit_trigger": v["explicit"]}
               for k, v in sorted(per_skill.items(),
                                  key=lambda kv: -kv[1]["calls"])}

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

    out = {
        "window_days": args.window,
        "scanned_files": total_files,
        "files_with_calls": len(files),
        "total_calls": len(calls),
        "per_skill_summary": summary,
        "explicit_slash_counts": dict(slash.most_common()),
        "installed": installed,
        "no_skill_index": no_skill_index,
        "calls": calls,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"extracted {len(calls)} calls from {len(files)}/{total_files} files "
          f"-> {out_path} ({out_path.stat().st_size // 1024} KB)", file=sys.stderr)


if __name__ == "__main__":
    main()
