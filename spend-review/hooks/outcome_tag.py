#!/usr/bin/env python3
"""Claude Code SessionEnd hook: append one ledger line per session.

Claude Code passes JSON on stdin (session_id, transcript_path, cwd, reason).
The hook sums the session's token usage, prices it at list rates, and looks
for one outcome tag that the person or the agent wrote during the session:

    OUTCOME: shipped - resume and application submitted
    OUTCOME: partial - draft packet, waiting on review
    OUTCOME: none - dropped, wrong data source

Only that tag line is read from message text. Prompts and replies are not
stored. Untagged sessions are recorded as "untagged" so the weekly review can
show how much spend has no outcome yet.

Ledger: ~/.agent-ledger/sessions.jsonl (override with AGENT_LEDGER).
spend_review.py reads it with --outcomes.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PRICES = os.path.join(HERE, "..", "prices.json")
TAG = re.compile(r"^\s*OUTCOME:\s*(shipped|partial|none)\b[\s:-]*(.{0,80})", re.I | re.M)


def text_blocks(content):
    if isinstance(content, str):
        yield content
    elif isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                yield b.get("text", "")


def summarize_transcript(path, prices):
    seen, usd, tokens, outcome, artifact = set(), 0.0, 0, "untagged", ""
    with open(path) as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            msg = d.get("message")
            if not isinstance(msg, dict):
                continue
            if d.get("type") in ("user", "assistant"):
                for t in text_blocks(msg.get("content")):
                    for m in TAG.finditer(t):  # last tag in the session wins
                        outcome, artifact = m.group(1).lower(), m.group(2).strip()
            u = msg.get("usage")
            if d.get("type") != "assistant" or not u or msg.get("id") in seen:
                continue
            seen.add(msg.get("id"))
            cc = u.get("cache_creation") or {}
            cw1h = cc.get("ephemeral_1h_input_tokens", 0)
            cw5m = cc.get("ephemeral_5m_input_tokens", u.get("cache_creation_input_tokens", 0) - cw1h)
            parts = (u.get("input_tokens", 0), cw5m, cw1h, u.get("cache_read_input_tokens", 0), u.get("output_tokens", 0))
            tokens += sum(parts)
            rate = prices.get(msg.get("model"))
            if rate:
                if u.get("speed") == "fast":
                    rate = rate["fast"]
                usd += (parts[0] * rate["input"] + parts[1] * rate["cache_write_5m"] + parts[2] * rate["cache_write_1h"]
                        + parts[3] * rate["cache_read"] + parts[4] * rate["output"]) / 1e6
    return {"tokens": tokens, "usd": round(usd, 4), "outcome": outcome, "artifact": artifact}


def main():
    event = json.load(sys.stdin)
    with open(PRICES) as fh:
        prices = json.load(fh)["anthropic"]
    row = {
        "session_id": event.get("session_id"),
        "ended_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reason": event.get("reason", ""),
        "cwd": event.get("cwd", ""),
        **summarize_transcript(event["transcript_path"], prices),
    }
    ledger = os.environ.get("AGENT_LEDGER", os.path.expanduser("~/.agent-ledger/sessions.jsonl"))
    os.makedirs(os.path.dirname(ledger), exist_ok=True)
    with open(ledger, "a") as fh:
        fh.write(json.dumps(row) + "\n")


if __name__ == "__main__":
    main()
