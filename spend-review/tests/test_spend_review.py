"""Offline tests on synthetic fixtures. Run: python3 -m unittest discover -s tests"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import spend_review as sr  # noqa: E402

PRICES = os.path.join(ROOT, "prices.json")


def usage(inp, cached, out, reasoning=0):
    return {"input_tokens": inp, "cached_input_tokens": cached, "output_tokens": out,
            "reasoning_output_tokens": reasoning, "total_tokens": inp + out}


def codex_lines(tid, root, source, cwd, events, model="gpt-6.1-sol"):
    yield {"timestamp": events[0][0], "type": "session_meta",
           "payload": {"id": tid, "session_id": root, "cwd": cwd, "thread_source": source}}
    yield {"timestamp": events[0][0], "type": "turn_context", "payload": {"model": model}}
    for ts, u in events:
        yield {"timestamp": ts, "type": "event_msg",
               "payload": {"type": "token_count", "info": {"total_token_usage": u, "last_token_usage": {"input_tokens": 1000}}}}


def read(path):
    with open(path) as fh:
        return fh.read()


def write_jsonl(path, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        for x in lines:
            fh.write(json.dumps(x) + "\n")


class Window:
    start = datetime.fromisoformat("2026-09-10T00:00:00-07:00")
    end = datetime.fromisoformat("2026-10-10T00:00:00-07:00")


class CodexCounters(unittest.TestCase):
    def test_growing_counter_is_differenced(self):
        ev = [("2026-09-20T10:00:00Z", None, usage(100, 50, 10), 0),
              ("2026-09-20T10:01:00Z", None, usage(300, 200, 30), 0)]
        d = [x[2] for x in sr.codex_deltas(ev)]
        self.assertEqual(sum(x["input_tokens"] for x in d), 300)
        self.assertEqual(sum(x["output_tokens"] for x in d), 30)

    def test_reset_counts_new_value(self):
        ev = [("2026-09-20T10:00:00Z", None, usage(1000, 900, 10), 0),
              ("2026-09-20T11:00:00Z", None, usage(200, 100, 5), 0)]  # counter restarted
        d = [x[2] for x in sr.codex_deltas(ev)]
        self.assertEqual(sum(x["input_tokens"] for x in d), 1200)

    def test_duplicate_event_adds_nothing(self):
        u = usage(100, 50, 10)
        ev = [("2026-09-20T10:00:00Z", None, u, 0), ("2026-09-20T10:00:01Z", None, u, 0)]
        self.assertEqual(len(list(sr.codex_deltas(ev))), 1)


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        codex = os.path.join(self.tmp, "codex")
        # one thread resumed into a second file: the counter continues, so it must not double count
        write_jsonl(os.path.join(codex, "sessions/2026/09/20/a.jsonl"),
                    codex_lines("T1", "T1", "user", "/work/client-alpha", [
                        ("2026-09-20T17:00:00Z", usage(1_000_000, 900_000, 10_000)),
                        ("2026-09-20T17:05:00Z", usage(2_000_000, 1_800_000, 20_000))]))
        write_jsonl(os.path.join(codex, "sessions/2026/09/21/b.jsonl"),
                    codex_lines("T1", "T1", "user", "/work/client-alpha", [
                        ("2026-09-21T17:00:00Z", usage(3_000_000, 2_700_000, 30_000))]))
        # a review sub-thread rolls up to T1 and has no public price
        write_jsonl(os.path.join(codex, "sessions/2026/09/21/c.jsonl"),
                    codex_lines("R1", "T1", "guardian_review", "/work/client-alpha", [
                        ("2026-09-21T17:01:00Z", usage(50_000, 0, 500))], model="codex-auto-review"))
        # outside the window
        write_jsonl(os.path.join(codex, "sessions/2026/08/01/d.jsonl"),
                    codex_lines("T2", "T2", "user", "/work/old", [("2026-08-01T17:00:00Z", usage(9_999, 0, 9))]))
        with open(os.path.join(codex, "session_index.jsonl"), "w") as fh:
            fh.write(json.dumps({"id": "T1", "thread_name": "Alpha Corp - secret prospect"}) + "\n")

        claude = os.path.join(self.tmp, "claude")
        msg = {"type": "assistant", "sessionId": "S1", "cwd": "/work/personal-notes", "timestamp": "2026-10-01T18:00:00Z",
               "message": {"id": "m1", "model": "claude-opus-5-5", "content": [{"type": "text", "text": "private reply"}],
                           "usage": {"input_tokens": 1000, "cache_creation_input_tokens": 2000, "cache_read_input_tokens": 100_000,
                                     "output_tokens": 500, "cache_creation": {"ephemeral_1h_input_tokens": 2000, "ephemeral_5m_input_tokens": 0}}}}
        write_jsonl(os.path.join(claude, "projects/p/S1.jsonl"), [msg, msg])  # same response logged twice

        self.rules = os.path.join(self.tmp, "rules.json")
        with open(self.rules, "w") as fh:
            json.dump({"rules": [{"title_regex": "^[A-Z][a-z]+ Corp - ", "label": "Prospect packets"},
                                 {"folder": "personal-notes", "label": "Personal"}]}, fh)
        self.outcomes = os.path.join(self.tmp, "outcomes.json")
        with open(self.outcomes, "w") as fh:
            json.dump({"T1": {"outcome": "shipped", "artifact": "packet"}}, fh)
        self.out = os.path.join(self.tmp, "out")
        sr.main(["--codex-home", codex, "--claude-home", claude, "--workstreams", self.rules,
                 "--outcomes", self.outcomes, "--ledger", os.path.join(self.tmp, "none.jsonl"), "--out", self.out])
        with open(os.path.join(self.out, "summary.json")) as fh:
            self.s = json.load(fh)

    def test_totals_reconcile_without_double_count(self):
        o = self.s["overall"]
        # codex T1: 3.0M input (2.7M cached) + 30K output; review: 50K input + 500 output
        self.assertEqual(o["cache_read"], 2_700_000 + 100_000)
        self.assertEqual(o["input"], 300_000 + 50_000 + 1000)
        self.assertEqual(o["output"], 30_000 + 500 + 500)

    def test_pricing(self):
        o = self.s["overall"]
        codex = (300_000 * 2.00 + 2_700_000 * 0.10 + 30_000 * 10.00) / 1e6
        claude = (1000 * 4.00 + 2000 * 8.00 + 100_000 * 0.20 + 500 * 20.00) / 1e6
        self.assertAlmostEqual(o["usd"], codex + claude, places=6)
        self.assertEqual(o["unpriced_tokens"], 50_500)  # auto-review counted, not priced

    def test_rollup_workstreams_and_outcomes(self):
        self.assertEqual(self.s["thread_count"], 2)
        self.assertIn("Prospect packets", self.s["by"]["workstream"])
        self.assertIn("Personal", self.s["by"]["workstream"])
        self.assertEqual(self.s["outcomes_by_workstream"]["Prospect packets"]["shipped"], 1)
        self.assertEqual(self.s["by"]["kind"]["review"]["requests"], 1)

    def test_public_summary_has_no_paths_titles_or_text(self):
        raw = read(os.path.join(self.out, "summary.json"))
        for secret in ("client-alpha", "Alpha Corp", "secret prospect", "/work/", "private reply", "T1", "S1"):
            self.assertNotIn(secret, raw)

    def test_excluded_workstream_leaves_every_figure(self):
        out = os.path.join(self.tmp, "out-ex")
        sr.main(["--codex-home", os.path.join(self.tmp, "codex"), "--claude-home", os.path.join(self.tmp, "claude"),
                 "--workstreams", self.rules, "--outcomes", self.outcomes, "--ledger", os.path.join(self.tmp, "none.jsonl"),
                 "--out", out, "--exclude-workstream", "Personal"])
        s = json.loads(read(os.path.join(out, "summary.json")))
        self.assertNotIn("Personal", s["by"]["workstream"])
        self.assertEqual(s["thread_count"], 1)
        self.assertEqual(s["overall"]["cache_read"], 2_700_000)

    def test_long_context_tier(self):
        p = sr.Pricer(PRICES)
        short = p.openai("gpt-6.1-sol", 1_000_000, 0, 0, req_input=100_000)
        long_ = p.openai("gpt-6.1-sol", 1_000_000, 0, 0, req_input=300_000)
        self.assertEqual((short, long_), (2.0, 4.0))


class Hook(unittest.TestCase):
    def test_hook_writes_tag_and_usage_only(self):
        tmp = tempfile.mkdtemp()
        tr = os.path.join(tmp, "t.jsonl")
        write_jsonl(tr, [
            {"type": "user", "message": {"role": "user", "content": "please draft the packet for Alpha Corp"}},
            {"type": "assistant", "message": {"id": "a1", "model": "claude-opus-5-5",
                                              "content": [{"type": "text", "text": "Done.\nOUTCOME: shipped - packet sent"}],
                                              "usage": {"input_tokens": 10, "cache_read_input_tokens": 1000, "output_tokens": 100}}},
        ])
        ledger = os.path.join(tmp, "ledger.jsonl")
        event = json.dumps({"session_id": "S9", "transcript_path": tr, "cwd": "/x", "reason": "exit"})
        subprocess.run([sys.executable, os.path.join(ROOT, "hooks", "outcome_tag.py")], input=event, text=True,
                       env={**os.environ, "AGENT_LEDGER": ledger}, check=True)
        row = json.loads(read(ledger))
        self.assertEqual((row["outcome"], row["artifact"], row["tokens"]), ("shipped", "packet sent", 1110))
        self.assertNotIn("Alpha", json.dumps(row))
        self.assertEqual(sr.load_outcomes(ledger)["S9"]["outcome"], "shipped")

    def test_untagged_session(self):
        tmp = tempfile.mkdtemp()
        tr = os.path.join(tmp, "t.jsonl")
        write_jsonl(tr, [{"type": "assistant", "message": {"id": "a1", "model": "claude-opus-5-5", "content": [],
                                                            "usage": {"input_tokens": 1, "output_tokens": 1}}}])
        ledger = os.path.join(tmp, "ledger.jsonl")
        subprocess.run([sys.executable, os.path.join(ROOT, "hooks", "outcome_tag.py")],
                       input=json.dumps({"session_id": "S8", "transcript_path": tr}), text=True,
                       env={**os.environ, "AGENT_LEDGER": ledger}, check=True)
        self.assertEqual(json.loads(read(ledger))["outcome"], "untagged")


if __name__ == "__main__":
    unittest.main()
