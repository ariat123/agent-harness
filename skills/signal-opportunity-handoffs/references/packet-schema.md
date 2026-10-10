# Packet JSON contract

This is the contract for the existing `scripts/opportunity_handoff.py` helper, not a universal restriction on every deliverable. The default initial sample is a table of opportunities with named people. This helper currently renders legacy cards and detailed pages only; use a task-local table/spreadsheet exporter for the initial list. Do not select cards just to fit this schema.

Preserve structured evidence and stable opportunity IDs regardless of exporter. For table samples, keep named contact people and roles, source URLs, dates, client fit, next steps, uncertainty, and any available accurately labeled contact routes in the record. Columns can combine or separate these fields according to the selected niche example. When upgrading to detailed pages, enrich those same records and retain the earlier delivered version.

For helper-supported layouts, the client-facing input is one JSON object with packet metadata and an `opportunities` array. Internal evidence and rejection ledgers may contain more fields, but the PDF should render only reviewed client-facing fields.

## Packet fields

- `schema_version`: currently `1`.
- `layout`: `calibration` (legacy cards, not the default initial list) or `detailed` (one page per opportunity).
- `packet_title`: client-facing document title.
- `client_name`: business receiving the opportunities.
- `prepared_by`: usually `AFT Labs`.
- `subtitle`: optional short context line.
- `research_date`: ISO date.
- `profile_id`: the niche profile used for discovery and qualification.
- `opportunities`: ordered opportunity objects.

## Opportunity fields shared by both layouts

- `id`: stable internal identifier.
- `title`: account, property, project, transaction, or procurement name.
- `meta`: location, scale, and signal-class context.
- `route_type`: direct owner, broker route, procurement, development, expansion, or another accurate route label.
- `signal.summary`: sourced description of the current event.
- `why`: why the event is relevant to this client.
- `sources`: list of objects with `label` and `url`.
- `first_move`: the next practical action.

Calibration records also use `access`, a concise description of the current decision route.

## Fields required by the detailed helper

Initial table records also need named contacts and useful timing information; these fields are not research exclusive to the detailed stage. They are listed here because of what this particular helper validates.

- `signal.date`: event date or date range.
- `signal.status`: current lifecycle status.
- `signal.decision_window`: when the relevant choice is likely to matter.
- `facts_secondary`: exactly three `{label, value}` cells. These labels are niche-specific.
- `contacts`: one or more reviewed contact routes.
- `best_first_move`: a fuller route-specific action.
- `outbound_angle.lead_with`
- `outbound_angle.offer`
- `outbound_angle.guardrail`

Optional bottom modules:

- `questions`: up to four `{label, value}` items.
- `steps`: up to three `{label, value}` items.
- `suggested_notes`: one or two `{label, subject, body}` items.

Use only the modules that improve the decision. A packet does not need suggested copy on every page.

## Contact object

- `name`
- `tier`: primary, secondary, additional, portal, or reference.
- `role_relevance`
- `email.value`: optional.
- `email.status`: `strict_valid`, `published`, `unverified`, `unknown`, `invalid`, or `unavailable`.
- `phone`: optional `{label, url}`.
- `profile`: optional `{label, url}`.

At least one email, phone, profile, portal, or other route must be present for a detailed client-ready opportunity. Two useful routes are preferred, but one authoritative route is acceptable.

## Evidence and language rules

- Do not write that an assignment is open unless a source establishes it.
- Do not equate a sale, permit, opening, or hiring event with buyer intent.
- Name an incumbent or current operator when known.
- Use the guardrail to state the principal uncertainty.
- Preserve exact source URLs and dates in the internal evidence ledger.
- Use ASCII hyphens in client-facing output. Do not use em dashes.
