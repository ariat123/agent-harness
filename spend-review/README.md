# Spend-to-shipped review

A weekly review of agent spend for one person or a team: where the tokens went, what they cost at list price, and what shipped because of them. I run it on my own Codex and Claude Code logs. The page: https://spend-to-shipped-review.vercel.app

It answers two questions:

- **Who's burning tokens without output?** Threads with high spend and no shipped artifact, or far more spend than the output needed.
- **Who isn't using enough?** Work with cheap, steady output and little spend, and cheap model tiers that barely run.

## What's here

| File | What it does |
|---|---|
| `spend_review.py` | Reads local Codex and Claude Code logs, prices every request, rolls helper agents and approval checks into the thread that started them, and writes the summary. Standard library only, no network. |
| `prices.json` | Public list prices with sources and the date checked. |
| `hooks/outcome_tag.py` | Claude Code `SessionEnd` hook. Adds one ledger line per session: tokens, list-price cost, and an outcome tag. |
| `build_page.py`, `templates/` | Builds the one-page review from the summary. |
| `tests/` | Offline tests on synthetic logs. |

## Run it

```bash
python3 spend_review.py --start 2026-09-10 --end 2026-10-09 --cutoff 2026-10-09T22:56:00-07:00 \
  --exclude-workstream "Personal"
python3 -m unittest discover -s tests
```

`--cutoff` pins a snapshot so the numbers don't move while sessions are still running. `--exclude-workstream "<label>"` leaves a workstream out of every figure (repeatable).

Outputs go to `out/`:

- `summary.json`: aggregates by workstream, model, kind of work and day. No IDs, folders, titles or message text. This is the only output meant to leave the machine.
- `threads.local.csv`: one row per top-level thread with its folder and title, for labeling. Keep it local.

Two optional local files steer the review. Both stay out of git (see `.gitignore`):

- `workstreams.local.json`: rules that map folders or thread titles to plain workstream labels.
- `outcomes.local.json`: `shipped`, `partial` or `none` for threads the hook didn't tag.

## The hook

Add to `~/.claude/settings.json`:

```json
{"hooks": {"SessionEnd": [{"hooks": [
  {"type": "command", "command": "python3 /path/to/spend-review/hooks/outcome_tag.py"}]}]}}
```

Before closing a session, the person or the agent writes one line:

```
OUTCOME: shipped - pricing page copy merged
```

The hook reads only that tag line, never the conversation. Untagged sessions are logged as `untagged`, so the review can show how much spend has no outcome yet. The ledger lives at `~/.agent-ledger/sessions.jsonl`, and `spend_review.py` reads it automatically.

## How it counts

- **Codex** keeps a running token total per thread. The review counts each increase, treats a drop as a restart, and merges threads that span several files. Summing request by request lands within 3% of the running totals on my logs.
- **Claude Code** logs a response once per content block, so responses are counted once by message ID.
- **Prices** are public Standard-tier list rates. The dollar figures are list-price estimates, not a bill: I pay through flat subscriptions. Models without a public price (Codex's built-in approval reviewer) are counted in tokens and left out of dollars.
- **Outcomes** come from the hook's tags, then from `outcomes.local.json`. Anything else counts as "no output on record."

## For a team

Swap the local logs for each AI tool's admin usage export, keyed by person, and swap the outcome file for what each team already calls shipped: merged PRs, meetings booked, contracts turned. The summary and the questions stay the same.
