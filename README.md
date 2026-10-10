# Agent harness

This is how I run my agency's work on AI agents: the skills my agents follow, the research pipeline they run, and the guardrails around both. Everything here comes from real work at AFT Labs, my AI services agency. Client names, lead data, and API keys are left out.

## What's in here

**`skills/`**: the instructions my agents follow. These do the actual work, so they're specific about evidence, limits, and when to stop and ask a person.

| Skill | What it's for |
|---|---|
| `maps-lead-research` | Turns a request like "find 100 property managers in Texas and Arizona" into a planned, budgeted run of the pipeline below. |
| `company-discovery` | The general method for finding companies, researching them, and marking gaps instead of guessing. |
| `b2b-intent-prospecting` | Separates "fits the offer" from "has a real reason to talk now," with a source behind every claim. |
| `signal-opportunity-handoffs` | Builds opportunity packets for clients: cited evidence, named contacts, and a clear pursue, hold, or reject call for each one. |
| `account-specific-b2b-outreach` | Turns account research into natural, source-backed outreach for a specific offer. |
| `cold-email-b2b` | Writes and reviews cold email and diagnoses campaign and reply data. |

**`maps-lead-research/`**: the Python pipeline behind most of my research. It turns a business category and a geography into a researched list of companies, locations, decision makers, and email candidates. Google Maps data (through RapidAPI) handles discovery, Firecrawl handles websites and people search, and SQLite keeps a shared history so nothing gets researched twice. It has 54 tests, and it runs on fake sample data with no API keys.

## How the work is split

- **Codex** is my main builder: the pipelines, repos, tests, and most of the heavy data work.
- **Claude Code** builds too, and handles documents and running skills across repos.
- **Grok Bot** runs scheduled work: sends, reply and bounce checks, and follow-up timing.
- **Research agents** (HyperAgent, Manus, Perplexity) do deep research. **Data tools** (Firecrawl, Treg, Monid, Origami) scrape, find, and verify.

Each tool forgets what the others did. Work moves between them through short handoff files that say what was done, what's verified, and what's next.

## Scale

- Every new run is checked against a shared history of about **27,000 companies and 95,000 locations**, built from about 1,000 earlier run files. Companies I already have don't get paid for twice.
- One September run looked for a real decision maker at **1,077 companies** across three niches using **972 Firecrawl searches**. It found a credible person for 534. The rest were marked unconfirmed or missing, not guessed.

## Guardrails

- **Paid calls need an explicit budget.** Firecrawl's spend limit defaults to zero, and a run won't spend until it's set.
- **An unclear result from a paid call stops that request.** It isn't blindly retried.
- **Every provider response is saved,** so a crashed run picks back up without paying again.
- **What an agent found stays separate from what's confirmed.** Agents sound just as confident when they're wrong.
- **When sources or verifiers disagree, the row is held.** I don't pick the answer I like.
- **Nothing goes out until a person has checked it.**

## How cost and accuracy data change what I do

- **The pipeline doesn't call a language model for every company.** Discovery, deduplication, extraction, and exports are plain Python. Models are saved for the judgment calls, which keeps the cost per useful answer low.
- **A cheaper model screens and a stronger one audits** before I trust the cheaper one on new work. In one niche the stronger model overturned half of the cheaper model's calls, so stronger review became the standard for evidence checks.
- **Research is reused across campaigns.** A company researched once isn't researched again unless I ask for a refresh.
- **When a sourcing provider's credits ran out mid-project,** I stopped paid sourcing and worked the records I already had instead of topping up blind.

## Getting other people onto it

I installed a multi-party maintenance agent for a property manager. It coordinates tenants, contractors, the landlord, and the manager, and the manager keeps the final say. It's still in use, and the manager has since started using AI tools on their own. I haven't rolled agents out across a whole team yet. That's the part I want to do next.

## Run it yourself

No API keys needed.

```bash
cd maps-lead-research
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q
python -m maps_lead_research validate campaigns/maps-lead-research-v2.example.json
python -m maps_lead_research plan campaigns/maps-lead-research-v2.example.json
```

`validate` and `plan` never touch the network. Paid commands need explicit flags and budgets. See `maps-lead-research/README.md`.
