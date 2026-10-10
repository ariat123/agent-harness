# Google Maps Lead Research

A generic Google Maps business-discovery and lightweight public company/contact-research pipeline. RapidAPI Maps Data discovers locations; a persistent registry groups companies and suppresses historical matches; bounded HTTP and Firecrawl research collects service evidence, current-role evidence, and public or defensibly inferred work-email candidates. It exports research CSVs. It does not send outreach or verify mailboxes.

The supported entry point is **`python -m maps_lead_research`** (campaign schema v2). This public copy contains the current pipeline only. Older layer scripts, run data, and lead databases stay private.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Store `RAPIDAPI_KEY` and `FIRECRAWL_API_KEY` in the ignored `.env` file or environment. Never put credentials in campaign JSON, code, or command arguments. New domain matching uses the bundled public suffix list offline. This CLI supports macOS/Linux filesystem locking.

## Start with a campaign

Copy [the v2 example](campaigns/maps-lead-research-v2.example.json), choose a unique output directory, and adjust the category, queries, geography, review limits, company/contact/email targets, and hard provider budgets. The example is configuration only: validating it spends nothing. Defaults and all fields are in [campaign-defaults-v2.json](docs/campaign-defaults-v2.json).

```bash
python -m maps_lead_research validate campaigns/maps-lead-research-v2.example.json
python -m maps_lead_research plan campaigns/maps-lead-research-v2.example.json
python -m maps_lead_research keys
```

`validate`, `plan`, `status`, `export`, `rebuild`, and `import-history` never use the network. `keys` reports presence booleans. An explicitly requested `balance --live` reads Firecrawl account usage without scraping; RapidAPI remaining quota is available through its subscription dashboard.

Before your first campaign, import previous work into one shared registry with `import-history`. Imports are additive and idempotent, read source databases without modifying them, preserve provenance, and quarantine insufficient or conflicting identity evidence. In my own use, every new run is checked against a registry of about 27,000 companies and 95,000 locations built from earlier runs.

## Execute only after authorizing the budget

The following commands **spend provider credits**. `--allow-paid` is an execution gate and must reflect the operator's explicit authorization. `--allow-http` permits bounded direct website requests. Firecrawl additionally requires a positive account-credit delta budget; its default is zero.

```bash
# Discovery only
python -m maps_lead_research discover campaign.json --allow-paid

# Research known companies without new Maps calls
python -m maps_lead_research enrich campaign.json --allow-paid --allow-http

# Complete pipeline
python -m maps_lead_research run campaign.json --allow-paid --allow-http

# Resume the immutable configuration and existing receipts
python -m maps_lead_research resume runs/my-campaign --stage run --allow-paid --allow-http

# Multiple discoveries, then one serialized enrichment queue using shared evidence
python -m maps_lead_research batch campaign-a.json campaign-b.json --allow-paid --allow-http
```

Exact-input decision-maker campaigns can suppress optional fallback work while
maximizing Firecrawl Search breadth, then reconcile external research offline:

```bash
python -m maps_lead_research resume-inputs runs/my-input-campaign \
  --allow-paid --search-only --search-workers 1 --search-delay 1
python -m maps_lead_research finalize-input-results runs/my-input-campaign \
  --perplexity-results perplexity.csv \
  --supergrok-results supergrok.csv \
  --output-directory outputs/my-final-results
```

`--search-only` disables direct HTTP and Firecrawl fallback scrapes for that
execution without changing the immutable campaign limits. Finalization makes no
network requests, snapshots both external CSVs, applies conservative evidence
precedence, and preserves the original exact-input rows in the final outputs.
When an explicitly increased budget requires a new immutable ledger, create a
successor that inherits completed research without resending it:

```bash
python -m maps_lead_research continue-input-run runs/source \
  --output-directory runs/source-continuation \
  --max-firecrawl-credit-delta 100
```

Use `enrich campaign.json --backlog` to prepare/rebuild the registry's incomplete-domain backlog that meets the campaign's source filters and has matching niche evidence without spending. Add the authorized network flags to research it. Successfully researched domains are reused across campaigns. Set `refresh_research: true` in a **new run** only when a refresh is explicitly intended.

```bash
python -m maps_lead_research status runs/my-campaign
python -m maps_lead_research export runs/my-campaign
python -m maps_lead_research rebuild runs/my-campaign --output runs/my-campaign-rebuilt
python -m pytest -q
```

An unknown provider outcome is a hard stop for that request; it is not blindly retried. Preserved successful responses recover automatically. See [resume and accounting details](docs/architecture.md) for remaining failure cases. Budgets and per-domain operation caps persist on resume; use a reviewed new campaign for higher limits.

## Results

Each run produces `companies.csv`, `locations.csv`, `company_campaign_memberships.csv`, `people_contacts.csv`, `email_candidates.csv`, `already_found.csv`, `objective_exclusions.csv`, `research_gaps.csv`, `firecrawl_backlog.csv`, `email_verification_upload.csv`, `run_summary.md`, and `validation_report.json`. Additional observation/duplicate CSVs reconcile every Maps result. Raw responses, request receipts, usage ledger, immutable configuration, geography plan/coverage and run manifest are retained. Exact headers: [output-schemas-v2.json](docs/output-schemas-v2.json).

`companies.csv` distinguishes net-new discoveries from explicitly resumed historical backlog through memberships and the summary. Multiple Google Places can belong to one company. Closed businesses, review/geography failures, suppression matches and ambiguous identities are documented. Unconfirmed source relevance becomes a research gap. Company size, ownership, assumed need and offer fit are not qualification gates. Property-management service mix is recorded without excluding SFR unless the actual search requests a narrower niche.

Contacts and emails are best-effort: lightweight deterministic extraction deliberately leaves gaps when role/name evidence is unclear. Public emails have source provenance; inferred emails require a current source-backed person and two public named examples supporting one company pattern. All deliverability fields remain `unverified`. The verifier-upload CSV is optional input for a separately approved verification workflow.

## Documentation

- [Architecture, accounting, limitations and recovery](docs/architecture.md)
- [Fictional offline example](docs/offline-example/README.md) and [acceptance report](docs/acceptance-report.md)
- [Operator skill](../skills/maps-lead-research/SKILL.md)

Example request: “Find up to 1,000 commercial sign companies across the US with at least five Google reviews, excluding companies already processed; research at most two Firecrawl pages per domain within a 2,000-credit account delta and 50-credit reserve, and find up to two relevant decision makers and work-email candidates per company.”

## Routine execution and model overhead

The canonical `run` command executes discovery, shared-registry deduplication, website research, contact/email extraction, CSV export and validation in one Python process. These stages do not call a language-model API or require per-company Codex decisions. RapidAPI supplies Maps results; HTTP/Firecrawl supplies website content; local Python handles normalization, evidence extraction and output formatting.

For ordinary campaigns, the operator should prepare one configuration, use `plan` once (which includes validation), execute `run` with the authorized provider flags, wait for completion, and inspect the final summary. Execution already exports and validates results. Separate export, repeated status/file inspection, repository audits, and manual per-company browsing are unnecessary unless diagnosing a concrete issue or explicitly requested. Existing historical imports and completed website evidence should be reused. Provider page and spending limits still apply; this operating pattern reduces avoidable model work but does not promise a particular Codex usage amount.
