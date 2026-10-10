#!/usr/bin/env python3
"""Spend-to-shipped review for one person's local agent logs.

Reads Codex rollouts (~/.codex/sessions, ~/.codex/archived_sessions) and
Claude Code transcripts (~/.claude/projects), prices every request at public
list rates (prices.json), rolls helper agents and approval checks up into the
thread that started them, and writes:

  out/threads.local.csv   one row per root thread (private: has folder paths)
  out/summary.json        aggregates by workstream, model, kind and day (publishable)

Only token counters, model names, timestamps and working folders are read.
Prompts, messages and tool output are never parsed.
Standard library only. No network calls.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
PT = ZoneInfo("America/Los_Angeles")

# Codex thread_source values -> kind of work
KIND = {
    "user": "main",
    None: "main",
    "guardian_review": "review",
    "subagent": "helper",
    "agent_created_thread": "helper",
    "agent_forked_thread": "helper",
}


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def pt_day(ts: datetime) -> str:
    return ts.astimezone(PT).date().isoformat()


# ---------------------------------------------------------------- pricing

class Pricer:
    def __init__(self, path: str):
        with open(path) as fh:
            self.p = json.load(fh)
        self.long_at = self.p["openai_long_context_threshold"]

    def openai(self, model, uncached, cached, out, req_input):
        """Return USD, or None when the model has no public price."""
        rate = self.p["openai"].get(model)
        if rate is None:
            return None
        if req_input > self.long_at:
            rate = rate["long"]
        return (uncached * rate["input"] + cached * rate["cached"] + out * rate["output"]) / 1e6

    def anthropic(self, model, u, fast=False):
        rate = self.p["anthropic"].get(model)
        if rate is None:
            return None
        if fast:
            rate = rate["fast"]
        return (u["input"] * rate["input"] + u["cw5m"] * rate["cache_write_5m"]
                + u["cw1h"] * rate["cache_write_1h"] + u["cache_read"] * rate["cache_read"]
                + u["output"] * rate["output"]) / 1e6


# ---------------------------------------------------------------- codex

def codex_files(home: str):
    for sub in ("sessions/**/*.jsonl", "archived_sessions/*.jsonl"):
        yield from glob.glob(os.path.join(home, sub), recursive=True)


def read_codex_file(path: str) -> dict:
    """Meta plus a list of cumulative-counter events, in file order."""
    meta, events, model = {}, [], None
    with open(path, "rb") as fh:
        for line in fh:
            head = line[:300]
            if b'"session_meta"' in head:
                d = json.loads(line)
                if d.get("type") == "session_meta" and not meta:
                    p = d["payload"]
                    meta = {
                        "id": p.get("id"),
                        "root": p.get("session_id") or p.get("id"),
                        "cwd": p.get("cwd") or "",
                        "kind": KIND.get(p.get("thread_source"), "helper"),
                    }
            elif b'"turn_context"' in head:
                d = json.loads(line)
                if d.get("type") == "turn_context":
                    model = d["payload"].get("model") or model
            elif b'"token_count"' in head:
                d = json.loads(line)
                info = d.get("payload", {}).get("info")
                if not info:
                    continue
                events.append((d["timestamp"], model, info["total_token_usage"],
                               (info.get("last_token_usage") or {}).get("input_tokens", 0)))
    return {"meta": meta, "events": events}


def codex_deltas(events):
    """Turn a thread's cumulative counters into per-request deltas.

    Counters usually grow. When a thread is resumed or compacted the counter
    can restart near zero; a drop means a new count started, so the new value
    is itself the delta. Repeated identical events add nothing.
    """
    prev = None
    for ts, model, cur, req_input in sorted(events, key=lambda e: e[0]):
        if prev is not None and cur["total_tokens"] == prev["total_tokens"]:
            continue
        if prev is None or cur["total_tokens"] < prev["total_tokens"]:
            base = {k: 0 for k in cur}
        else:
            base = prev
        d = {k: cur.get(k, 0) - base.get(k, 0) for k in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")}
        prev = cur
        yield ts, model, d, req_input


def load_codex(home, pricer, start, end, rows):
    threads = defaultdict(lambda: {"meta": None, "events": []})
    for f in codex_files(home):
        r = read_codex_file(f)
        if not r["meta"].get("id"):
            continue
        t = threads[r["meta"]["id"]]
        t["meta"] = t["meta"] or r["meta"]
        t["events"].extend(r["events"])
    for tid, t in threads.items():
        m = t["meta"]
        for ts, model, d, req_input in codex_deltas(t["events"]):
            when = parse_ts(ts)
            if not (start <= when < end):
                continue
            cached = d["cached_input_tokens"]
            uncached = max(d["input_tokens"] - cached, 0)
            out = d["output_tokens"]
            usd = pricer.openai(model, uncached, cached, out, req_input)
            rows.append({
                "tool": "codex", "root": m["root"], "thread": tid, "kind": m["kind"], "cwd": m["cwd"],
                "ts": when, "model": model or "unknown", "input": uncached, "cache_read": cached,
                "cache_write": 0, "output": out, "reasoning": d["reasoning_output_tokens"], "usd": usd,
            })


# ---------------------------------------------------------------- claude code

def load_claude(home, pricer, start, end, rows):
    seen = set()
    for f in glob.glob(os.path.join(home, "projects", "**", "*.jsonl"), recursive=True):
        helper = f"{os.sep}subagents{os.sep}" in f
        with open(f) as fh:
            for line in fh:
                if '"usage"' not in line:
                    continue
                d = json.loads(line)
                msg = d.get("message")
                if d.get("type") != "assistant" or not isinstance(msg, dict) or "usage" not in msg:
                    continue
                key = msg.get("id") or d.get("requestId") or d.get("uuid")
                if key in seen:  # one API response is logged once per content block
                    continue
                seen.add(key)
                when = parse_ts(d["timestamp"])
                if not (start <= when < end):
                    continue
                u = msg["usage"]
                cc = u.get("cache_creation") or {}
                cw1h = cc.get("ephemeral_1h_input_tokens", 0)
                cw5m = cc.get("ephemeral_5m_input_tokens", u.get("cache_creation_input_tokens", 0) - cw1h)
                parts = {"input": u.get("input_tokens", 0), "cw5m": cw5m, "cw1h": cw1h,
                         "cache_read": u.get("cache_read_input_tokens", 0), "output": u.get("output_tokens", 0)}
                model = msg.get("model") or "unknown"
                if model == "<synthetic>":
                    continue
                usd = pricer.anthropic(model, parts, fast=u.get("speed") == "fast")
                rows.append({
                    "tool": "claude", "root": d.get("sessionId") or f, "thread": d.get("sessionId") or f,
                    "kind": "helper" if (helper or d.get("isSidechain")) else "main", "cwd": d.get("cwd") or "",
                    "ts": when, "model": model, "input": parts["input"], "cache_read": parts["cache_read"],
                    "cache_write": cw5m + cw1h, "output": parts["output"],
                    "reasoning": (u.get("output_tokens_details") or {}).get("thinking_tokens", 0), "usd": usd,
                })


# ---------------------------------------------------------------- workstreams and outcomes

def load_rules(path):
    """workstreams.json: ordered rules, first match wins.

    {"rules": [{"folder": "<substring>", "label": "..."},
               {"title": "<substring, any case>", "label": "..."},
               {"title_regex": "<regex>", "label": "..."}]}
    Keep the real file local: folder names and titles can name clients.
    """
    if not path or not os.path.exists(path):
        return []
    with open(path) as fh:
        return json.load(fh)["rules"]


def workstream(cwd, title, rules):
    for r in rules:
        if "folder" in r and r["folder"] in cwd:
            return r["label"]
        if "title" in r and r["title"].lower() in title.lower():
            return r["label"]
        if "title_regex" in r and re.search(r["title_regex"], title):
            return r["label"]
    return "Other"


def load_titles(codex_home, claude_home):
    """Thread titles, used only to apply workstream rules. Never written to summary.json."""
    titles = {}
    idx = os.path.join(codex_home, "session_index.jsonl")
    if os.path.exists(idx):
        with open(idx) as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                titles[d.get("id")] = d.get("thread_name") or ""
    for f in glob.glob(os.path.join(claude_home, "projects", "*", "*.jsonl")):
        with open(f) as fh:
            for line in fh:
                if '"custom-title"' in line:
                    d = json.loads(line)
                    titles[d.get("sessionId")] = d.get("customTitle") or ""
    return titles


def load_outcomes(*paths):
    """Outcome per root thread, from either source (later paths win):

    outcomes.json      {root_thread_id: {"outcome": shipped|partial|none, "artifact": "..."}}
    sessions.jsonl     ledger lines written by hooks/outcome_tag.py
    """
    out = {}
    for path in paths:
        if not path or not os.path.exists(path):
            continue
        with open(path) as fh:
            if path.endswith(".jsonl"):
                for line in fh:
                    d = json.loads(line)
                    if d.get("outcome") in ("shipped", "partial", "none"):
                        out[d["session_id"]] = {"outcome": d["outcome"], "artifact": d.get("artifact", "")}
            else:
                out.update({k: v for k, v in json.load(fh).items() if isinstance(v, dict)})
    return out


# ---------------------------------------------------------------- summarize

TOKEN_FIELDS = ("input", "cache_read", "cache_write", "output", "reasoning")


def blank():
    return {**{k: 0 for k in TOKEN_FIELDS}, "usd": 0.0, "unpriced_tokens": 0, "requests": 0}


def add(acc, r):
    for k in TOKEN_FIELDS:
        acc[k] += r[k]
    acc["requests"] += 1
    if r["usd"] is None:
        acc["unpriced_tokens"] += r["input"] + r["cache_read"] + r["cache_write"] + r["output"]
    else:
        acc["usd"] += r["usd"]


def total_tokens(a):
    return a["input"] + a["cache_read"] + a["cache_write"] + a["output"]


def summarize(rows, rules, titles, outcomes, start, end, exclude=()):
    roots = defaultdict(lambda: {**blank(), "first": None, "last": None, "cwd": "", "tool": "",
                                 "kinds": defaultdict(float), "models": defaultdict(float)})
    by = {k: defaultdict(blank) for k in ("workstream", "model", "kind", "tool", "day")}
    day_model = defaultdict(lambda: defaultdict(float))
    overall = blank()
    root_cwd = {}  # a root thread's folder is the folder of its main thread
    for r in rows:
        if r["kind"] == "main":
            root_cwd[r["root"]] = r["cwd"]
    for r in rows:
        root_cwd.setdefault(r["root"], r["cwd"])
    kept = []
    for r in rows:
        r["workstream"] = workstream(root_cwd.get(r["root"], r["cwd"]), titles.get(r["root"], ""), rules)
        if r["workstream"] not in exclude:
            kept.append(r)
    for r in kept:
        ws = r["workstream"]
        add(overall, r)
        add(by["workstream"][ws], r)
        add(by["model"][r["model"]], r)
        add(by["kind"][r["kind"]], r)
        add(by["tool"][r["tool"]], r)
        add(by["day"][pt_day(r["ts"])], r)
        day_model[pt_day(r["ts"])][r["model"]] += r["usd"] or 0
        t = roots[r["root"]]
        add(t, r)
        t["kinds"][r["kind"]] += r["usd"] or 0
        t["models"][r["model"]] += r["usd"] or 0
        t["cwd"], t["tool"], t["workstream"] = root_cwd.get(r["root"], r["cwd"]), r["tool"], ws
        t["title"] = titles.get(r["root"], "")
        t["first"] = min(t["first"] or r["ts"], r["ts"])
        t["last"] = max(t["last"] or r["ts"], r["ts"])

    # outcome join: labeled root threads carry shipped / partial / none
    ws_out = defaultdict(lambda: {"shipped": 0, "partial": 0, "none": 0, "unlabeled_usd": 0.0,
                                  "shipped_usd": 0.0, "partial_usd": 0.0, "none_usd": 0.0})
    for rid, t in roots.items():
        o = outcomes.get(rid, {}).get("outcome")
        w = ws_out[t["workstream"]]
        if o in ("shipped", "partial", "none"):
            w[o] += 1
            w[o + "_usd"] += t["usd"]
        else:
            w["unlabeled_usd"] += t["usd"]
        t["outcome"] = o or ""
        t["artifact"] = outcomes.get(rid, {}).get("artifact", "")

    return {"overall": overall, "by": {k: dict(v) for k, v in by.items()}, "roots": roots, "outcomes": dict(ws_out),
            "day_model": {d: {m: round(v, 2) for m, v in mv.items()} for d, mv in sorted(day_model.items())},
            "window": {"start": start.astimezone(PT).date().isoformat(),
                       "end_inclusive": (end.astimezone(PT) - timedelta(seconds=1)).date().isoformat()}}


def top_model(t):
    if not t["models"] or not t["usd"]:
        return ("", 0.0)
    m = max(t["models"], key=t["models"].get)
    return (m, t["models"][m] / t["usd"])


def write_outputs(s, outdir):
    os.makedirs(outdir, exist_ok=True)
    ordered = sorted(s["roots"].items(), key=lambda kv: -kv[1]["usd"])
    with open(os.path.join(outdir, "threads.local.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["root", "tool", "workstream", "cwd", "first_pt", "last_pt", "active_days", "requests",
                    "tokens", "cache_read_share", "usd", "usd_main", "usd_helper", "usd_review",
                    "top_model", "top_model_usd_share", "outcome", "title"])
        for rid, t in ordered:
            days = (t["last"].astimezone(PT).date() - t["first"].astimezone(PT).date()).days + 1
            tok = total_tokens(t)
            w.writerow([rid, t["tool"], t["workstream"], t["cwd"], t["first"].astimezone(PT).isoformat(timespec="minutes"),
                        t["last"].astimezone(PT).isoformat(timespec="minutes"), days, t["requests"], tok,
                        round(t["cache_read"] / tok, 4) if tok else 0, round(t["usd"], 2),
                        round(t["kinds"]["main"], 2), round(t["kinds"]["helper"], 2), round(t["kinds"]["review"], 2),
                        top_model(t)[0], round(top_model(t)[1], 3), t["outcome"], t.get("title", "")])
    public = {
        "generated_at": datetime.now(PT).isoformat(timespec="minutes"),
        "window": s["window"],
        "usd_by_day_and_model": s["day_model"],
        "overall": s["overall"],
        "by": s["by"],
        "outcomes_by_workstream": s["outcomes"],
        "thread_count": len(s["roots"]),
        "top_threads": [  # no ids or folders: rank, workstream, size, outcome only
            {"rank": i + 1, "workstream": t["workstream"], "tool": t["tool"], "usd": round(t["usd"], 2),
             "tokens": total_tokens(t), "requests": t["requests"], "outcome": t["outcome"],
             "cache_read_share": round(t["cache_read"] / total_tokens(t), 3) if total_tokens(t) else 0,
             "helper_usd_share": round(t["kinds"]["helper"] / t["usd"], 3) if t["usd"] else 0,
             "top_model": top_model(t)[0], "top_model_usd_share": round(top_model(t)[1], 3),
             "active_days": (t["last"].astimezone(PT).date() - t["first"].astimezone(PT).date()).days + 1}
            for i, (_, t) in enumerate(ordered[:40])
        ],
    }
    with open(os.path.join(outdir, "summary.json"), "w") as fh:
        json.dump(public, fh, indent=1, default=str)
    return public


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--start", default="2026-09-10", help="first day, Pacific time")
    ap.add_argument("--end", default="2026-10-09", help="last day, inclusive, Pacific time")
    ap.add_argument("--cutoff", help="stop at this ISO timestamp, for a reproducible snapshot")
    ap.add_argument("--codex-home", default=os.path.expanduser("~/.codex"))
    ap.add_argument("--claude-home", default=os.path.expanduser("~/.claude"))
    ap.add_argument("--prices", default=os.path.join(HERE, "prices.json"))
    ap.add_argument("--workstreams", default=os.path.join(HERE, "workstreams.local.json"))
    ap.add_argument("--outcomes", default=os.path.join(HERE, "outcomes.local.json"))
    ap.add_argument("--ledger", default=os.environ.get("AGENT_LEDGER", os.path.expanduser("~/.agent-ledger/sessions.jsonl")))
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    ap.add_argument("--exclude-workstream", action="append", default=[],
                    help="leave a workstream out of every figure (repeatable)")
    a = ap.parse_args(argv)

    start = datetime.combine(date.fromisoformat(a.start), datetime.min.time(), PT)
    end = datetime.combine(date.fromisoformat(a.end) + timedelta(days=1), datetime.min.time(), PT)
    if a.cutoff:
        end = min(end, datetime.fromisoformat(a.cutoff))
    pricer = Pricer(a.prices)
    rows = []
    load_codex(a.codex_home, pricer, start, end, rows)
    load_claude(a.claude_home, pricer, start, end, rows)
    titles = load_titles(a.codex_home, a.claude_home)
    s = summarize(rows, load_rules(a.workstreams), titles, load_outcomes(a.outcomes, a.ledger), start, end,
                  exclude=set(a.exclude_workstream))
    pub = write_outputs(s, a.out)
    o = pub["overall"]
    print(f"{s['window']['start']} to {s['window']['end_inclusive']}: {pub['thread_count']} root threads, "
          f"{total_tokens(o)/1e9:.2f}B tokens, ${o['usd']:,.0f} at list price, "
          f"{o['unpriced_tokens']/1e6:.0f}M unpriced tokens", file=sys.stderr)


if __name__ == "__main__":
    main()
