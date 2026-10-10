# Opportunity handoff workflow

## 1. Define the client lane

Start with a context check, even when the user provides only a short prompt. Extract context from the conversation and supplied files before asking anything. The required context section means "provide what is known and identify what is unknown," not that every client must answer every possible question.

Create a short client brief before discovery:

- client, contact person, services/offer, geography, and service capacity;
- the exact outreach promise, prospect response, requested stage, and intended use of the deliverable;
- account, property, project, or transaction types, including size/value minimums and maximums;
- accepted and excluded signal families;
- likely decision-makers and legitimate intermediaries;
- current-client, relationship, and territory suppressions;
- prior feedback, rejected examples, preferred examples, and desired opportunity count if specified;
- what the client is expected to do after receiving the handoff.

Separate client-confirmed constraints, public company facts, working assumptions, and unresolved questions. Resolve public facts through company research; ask the user about missing private preferences only when they change the work. Use the exact niche profile as a starting hypothesis. Client facts override profile defaults.

Bundle the smallest useful set of clarification questions, ordinarily one to three. Examples of material gaps include whether "18 units" is a maximum or minimum, which area an ambiguous territory describes, and whether a fire-protection client wants extinguisher service or also installs suppression systems. These change selection. Do not ask for details already in the email exchange, demand a budget unrelated to selection, or require the user to invent a list of signal sources.

While a material clarification is pending, continue independent company research and source assessment; avoid an expensive full search built on an unresolved assumption. If the user says the answer is unknown or asks to proceed, use a conservative working hypothesis and clearly identify what still needs client confirmation. Elapsed time is not an answer. Minor gaps may remain labeled without blocking progress. Do not require approval of a completed brief when the scope is already clear.

## 2. Discover candidates broadly

Select sources from the relevant section of `docs/aft-strategy/tweet-source-library.md` and the niche recipes routed by `docs/aft-strategy/sourcing-and-learning.md` (repository-root paths). Read matching client/offer feedback first. Save the sources considered and why the chosen ones fit this buyer and decision. Library entries are candidate methods, not verified access or proven yield.

Search sources appropriate to the niche. Preserve the source URL, event date, observation date, entity match, factual excerpt, and source class. Syndicated copies of the same announcement count as one event.

Keep a candidate ledger that includes rejects and holds. Useful dispositions are:

- `selected`
- `hold_route`
- `hold_timing`
- `monitor`
- `duplicate`
- `suppressed`
- `rejected_fit`
- `rejected_evidence`

## 3. Gate opportunities before contact work

Review candidates in this order:

1. **Evidence:** current, source-backed, and matched to the correct entity.
2. **Fit:** inside territory and above the agreed value or scope floor.
3. **Timing:** a real reason to discuss the work now or at a stated future milestone.
4. **Decision state:** open, plausibly unresolved, fixed, already awarded, or unknown.
5. **Route:** direct buyer, owner, developer, operator, broker, procurement portal, project contact, or another defensible path.

Do not spend verifier credits on candidates that fail the first three gates.

## 4. Choose the artifact stage

The user's stated stage takes priority. Either stage can be requested directly; there is no required progression or feedback gate. Read `example-library.md` and inspect the relevant stage/niche example before designing the output.

### Initial sample (also called calibration sample)

Default to a table with one opportunity per row. Usually include 5 to 10 or more opportunities according to the request and available evidence; do not pad the count. Select columns using the niche example and client context. Include enough information to assess each opportunity:

- account/property/project, location, and relevant scale;
- what happened, date, and current status;
- why it fits this client and likely service scope;
- named contact people, roles, and why they are the appropriate approach;
- available business contact routes, accurately labeled when included;
- current operator or vendor when relevant and known;
- next step, important uncertainty, and clickable evidence sources.

These can be combined into fewer readable columns, as in earlier client examples, or separated for technical reviews. A sample is concise, not shallow. Research names during this stage; verified emails, a complete contact waterfall, long analysis, and full outreach copy are not normally needed. Clearly label an unresolved person or route; never substitute an invented buyer. An authoritative procurement contact/portal can be the correct route for that opportunity type.

Use multiple pages or an appropriate spreadsheet when warranted. Do not silently substitute cards or a one-page-per-opportunity packet. Cards are allowed when the user selects that format or a matching reference calls for them.

### Detailed handoff

Use when the user requests this depth, either directly or following prospect feedback. If feedback exists, apply it to selection and research; otherwise use the provided client brief. Each page should usually include:

- current signal;
- client-specific fit;
- signal date, current status, and decision window;
- a niche-specific fact row;
- evidence links;
- primary and secondary contact routes when available;
- best first move;
- lead, offer, and guardrail;
- one useful bottom module, such as questions, steps, economics, or suggested notes.

The detailed packet should make the next action obvious without presenting an inference as a fact. Deepen contact research, aim for two useful contacts where possible, and include actual email verification results when verification is part of the authorized work. A detailed layout alone does not mean every email is verified. Adapt secondary sections to the niche rather than filling every possible module.

## 5. Research contacts and verify selectively

Identify the person or route that can advance the relevant decision. Authority and route relevance matter more than title seniority alone. Record every contact candidate and why it was accepted, held, or rejected.

If strict email verification is needed, run a bounded candidate batch after opportunity selection. Do not call guessed addresses verified. Preserve useful non-email routes in the final packet when they are the proper route.

## 6. Render from structured data

Preserve one evidence record per opportunity, with stable IDs. Record the requested stage, chosen example, layout choices, and prospect feedback (if any) in the task brief. Reuse and enrich these records for later stages rather than researching from scratch. Preserve earlier delivery versions.

The packet JSON in [packet-schema.md](packet-schema.md) documents the existing helper's supported layouts. Use it when that renderer fits. For the initial table or a different niche layout, create the export from the same records using an appropriate task-local builder. The helper's legacy card layout is not the default sample. QA custom outputs against the same evidence/contact standards; no universal column count or fact-cell count is required.

Optional modules should reflect the niche:

- management economics for property management;
- likely fabrication scope and ownership of the package for signage;
- responsibility boundaries and system scope for fire protection;
- eligibility and submission steps for procurements;
- buyer underwriting and incumbent status for transaction-driven opportunities.

## 7. QA the client-facing claim

Before delivery, confirm:

- every material fact has a live source;
- dates distinguish event date from research date;
- the account matches the client's territory and size rules;
- the current operator or incumbent is named when known;
- the document says when the decision state is unknown;
- contact statuses are accurate;
- initial lists include named people and roles, with any unresolved names/authoritative institutional-route exceptions explicit;
- the delivered stage and layout match the user's request and selected example;
- the best first move matches the route;
- the guardrail names the main unsupported inference;
- every detailed opportunity fits on one readable page;
- rejected and replaced candidates remain in the internal ledger.

## 8. Learn from feedback

When the user supplies feedback, follow the shared sourcing-and-learning guide: save a new dated note with source attribution, client/niche/offer scope, opportunity IDs where available, what was said versus interpretation, and the change justified for the next delivery. Use dispositions such as accepted for pursuit, already known, wrong fit, too late/already assigned, route insufficient, or monitor. Preserve prior notes; corrections reference the earlier note. An explicit client preference can immediately change that client's next sample; cross-client profile changes need broader support.

Track feedback separately for signal quality, client fit, timing, route, and contactability. A prospect rejecting one opportunity does not invalidate the entire signal family. A repeated pattern of rejection or acceptance should change the niche profile, discovery sources, or qualification gates.
