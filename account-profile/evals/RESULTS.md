# Results

## 2026-10-10: first three runs (Claude Opus 5.5)

Each sample was produced by running SKILL.md as written, then checked two ways.

**1. Structural checker** (`check_profile.py`): all three passed on the first run with 0 failures. Two things showed up as warnings:
- Undated web sources (the GitLab customer pages and a company tech page). These are live pages with no publish date, so they stay "Undated."
- Length. The first drafts ran 937 to 1,046 words before the Sources table, and none fit on one page. The skill said "one page" but gave no number. So the skill now sets about 750 words and two or three lines per question, and the checker warns above 800.

**2. Line-by-line evidence check.** A separate reviewer opened every cited source and checked every line without relying on the run's own notes.

| Sample | Lines checked | OK | Fixed | Cut | Kind of fixes |
|---|---:|---:|---:|---:|---|
| Lockheed Martin | 50 | 42 | 8 | 0 | A paraphrase that went past the source ("commercial partnerships"), a risk warning written as a requirement, a 15-month-old loss figure standing in for a current risk, a missing "expects to recover" qualifier on tariffs |
| Deere | 44 | 37 | 7 | 0 | Leaving out context that changes the read: guidance had just been raised, adoption had improved since the 10-K, and a retirement notice was not yet confirmed as done |
| Capital One | 48 | 42 | 6 | 0 | A dropped "certain" that overstated how much of an acquired business runs in data centers, hedged statements written as fact, a regulatory status that could only be dated to February |

There were no wrong numbers, wrong dates, or invented facts. Every fix was applied, the lines were trimmed to one page, and the checker passes again.

**What this changed in the skill:** a concrete length rule, a check for drafts over one page, and a reminder that "as of" dates matter. A fact from a December filing can be stale by October.

## 2026-10-10: second-model re-run (Claude Sonnet 5.5), Capital One

A blind run: the model followed SKILL.md and the references without seeing the saved sample.

- **Checker, first run:** pass, 0 failures, 2 warnings (an undated source, and 1,153 words, over one page).
- **Same story:** it chose the same two value drivers (Accelerate Cloud Adoption & Transformation; Reduce Security and Compliance Risk). It found the same seam between the cloud-based core and the acquired network's data centers. It kept question 7 as "Not in public sources" and question 8 as "AE to fill."
- **Differences:**
  - It held a "conflict" that isn't one: the acquisition price at signing vs. at closing, which differ for normal reasons. That's over-holding, which is safer than under-holding but still noise for an AE.
  - Its short-term risk was credit losses rather than integration cost. That's accurate, but less useful for a software conversation.
- **Takeaway:** structure and holds carried over between models; judgment about which facts matter to a GitLab AE varied. That's why the re-run checklist includes a by-hand read of the snapshot, top goals, and value drivers.

The second-model draft is kept for internal comparison only. It was not line-checked for publication.
