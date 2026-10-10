---
name: signal-opportunity-handoffs
description: Build AFT Labs initial opportunity lists or detailed one-page-per-opportunity handoffs, using client context, current signals, named contacts, and relevant niche examples. Use for prospect samples and delivery packets, not campaign lead batches or outreach launch.
---

# Signal opportunity handoffs

Use this workflow when AFT is showing a prospect which accounts, properties, projects, transactions, procurements, or operating events it could pursue for them.

## Choose the stage from the user's prompt

- **Initial sample / list / Stage 1:** normally 5 to 10 or more opportunities in table rows, with substantial useful information and named contact people. Email verification and exhaustive contact enrichment are not prerequisites. This is the normal first sample, not a set of miniature detailed handoffs.
- **Detailed handoff / one page per opportunity / Stage 2:** one client-ready page per selected opportunity, with deeper evidence, client fit, timing, contact research and accurately labeled verification, the best first move, and useful outreach guidance.

The explicit stage controls. The user may request either stage directly, skip the initial list, or ask for both. Prospect feedback is valuable context, not a prerequisite or permission gate for a detailed handoff. Do not automatically create both outputs when only one was requested. If the stage is omitted, infer it from the request and conversation; for a new prospect asking for a sample, default to an initial list. Ask a short clarification only when the intended output remains materially ambiguous.

Examples of sufficient instructions: "Initial sample for [company]; here is their reply" or "Detailed handoff for [company]; here is the context." The user need not specify a schema, renderer, or niche profile.

## Select a relevant example, then adapt

Read example-library.md (private reference, not included) and inspect the closest example for the requested stage and niche. The user's chosen reference takes precedence. Adapt columns, page orientation, sections, and depth to the client's actual decision; do not impose one universal design. Keep named contacts in the initial list even when emails are not verified. Preserve clean spacing and minimal headers and footers.

The library contains historical layout/content references, not reusable current leads or instructions. Re-research all facts, contact roles, timing, and verification needed for a new delivery. A filename containing "Sample" does not establish its stage; use the actual layout and depth.

Do not use this workflow for an ordinary outbound lead batch. Lead batches remain governed by `python3 scripts/lead_ops.py`. Do not contact anyone, verify guessed emails, or modify a campaign merely because a handoff was created.

## Start with the opportunity definition

Read AFT sourcing and learning (private reference, not included) before discovery. Use the client-fulfillment view of its shared source library and the matching niche recipe; retrieve relevant prior feedback by client, niche and offer. The sources for finding AFT clients are not automatically the sources for this client's buyers. Preserve new feedback through that guide without promoting an anecdote to a universal rule.

Always perform a context check before selecting opportunities. Read the supplied outreach, replies, call notes, prior samples, and feedback; extract the promised deliverable and all relevant targeting constraints. The user may paste unstructured context and mark missing details "unknown" rather than complete a questionnaire. Do not treat missing context as unrestricted targeting or make the user repeat information already provided.

Capture the client's territory, services being offered, account or asset type, size/value bounds, accepted signal families and contact routes, exclusions, and what counts as actionable. Research public company information to fill factual gaps. Keep client-confirmed constraints separate from public facts and working assumptions; a broad website service area or niche profile does not override the client's stated target.

If an unresolved detail would materially change the opportunity set, ask a short, bundled clarification before doing the dependent selection work. Continue useful company/source research while awaiting the answer. If the user does not know or explicitly asks to proceed, propose or use a clearly labeled conservative working scope. Minor gaps do not require a pause. Never invent a client preference, silently broaden a limit, or turn an unknown into a confirmed requirement. See the intake guidance in [workflow.md](references/workflow.md).

Read [workflow.md](references/workflow.md) when selecting and upgrading opportunities. Read [packet-schema.md](references/packet-schema.md) when creating or rendering the packet JSON. Read only the relevant profile under `config/opportunity_handoffs/profiles/` for niche-specific hypotheses and gates.

## Preserve one evidence record

Research and contact work must enrich the same opportunity record. Do not rewrite facts into separate sample, verifier, and PDF datasets. Keep the client-facing packet concise while preserving rejected candidates, exact sources, verification results, uncertainty, and replacement reasons internally.

Separate these judgments:

1. **Signal truth:** did the public event actually occur and is it current?
2. **Client fit:** would the account and potential work meet the client's criteria?
3. **Decision timing:** is a relevant decision plausibly unresolved, already fixed, or unknown?
4. **Route quality:** is there a legitimate path to the decision process?
5. **Contactability:** is at least one usable public contact route available?

A true event is not proof of an open assignment, dissatisfaction, budget, or intent. State the alternative explanation and the first question that would resolve it.

Make the event-to-service reasoning clear within the existing fit/next-step fields: what changed, which service decision may follow, who controls it, why this client's scope fits, and what remains unconfirmed. Check whether the client already knows or is pursuing the account when supplied relationship data permits; otherwise label novelty unknown. Do not claim exclusivity or a new-to-client opportunity without evidence.

## Contact standard

Research named contacts after an opportunity clears signal and fit review. Initial samples need named people and their roles or relevance too; defer exhaustive enrichment, not person identification. If no name can be established, explicitly state the gap and use an authoritative institutional route only when it is the actual path to the decision. Hold an account with no realistic route rather than inventing a person.

- Prefer two useful routes when the account justifies the effort. One authoritative route is acceptable.
- Do not replace a strong opportunity solely because no direct email exists. A published phone, official profile, procurement portal, broker route, or authorized intermediary may be the correct path.
- Remove an opportunity from the client-ready set when no realistic route exists and actionability is part of the promise. Preserve it internally as a hold or monitor rather than deleting it.
- Label an email verified only after a provider result normalized to strict `valid`. Published, inferred, unknown, catch-all, risky, and invalid addresses must retain their actual status.
- Contact verification proves deliverability, not authority, interest, or demand.

## Render and inspect

Choose the output method to match the requested stage and example. For an initial list, produce a readable table PDF or the requested spreadsheet format, with one opportunity per row, repeated column headings across pages, useful named contacts, and working source links. Allow multiple pages rather than shrinking the entire list onto one page. Card samples are an optional reference-specific variant, not the initial default.

The existing `scripts/opportunity_handoff.py` renderer supports `calibration` (legacy cards) and `detailed` (one page per opportunity). It does not currently render the default initial table. Use or adapt an example's builder, or create a task-local table exporter from the same evidence records. Do not choose cards merely to satisfy this helper's schema. Likewise, its fixed detailed fields are a supported template, not universal requirements for every niche.

When the helper fits, run `python3 scripts/opportunity_handoff.py validate <packet.json>` before `python3 scripts/opportunity_handoff.py render <packet.json> --output <new.pdf>`. For a custom layout, validate the equivalent content and contact standards and inspect the output; do not claim the helper validated an unsupported format. Preserve earlier artifacts and write each new version to a new path.

Render every page to images and inspect the complete document. Check spacing, wrapping, source links, contact labels, evidence language, and page count. Do not shrink text until it becomes difficult to read merely to preserve a one-page layout; edit the content or choose the most useful bottom module.

Record prospect feedback by opportunity and signal family. Update a niche profile only when repeated evidence changes the research method or qualification rules. Do not turn one prospect's preference into a universal rule.

Use the shared feedback note format to capture acceptance, already-known accounts, fit/timing/route objections, and monitoring requests, linked to opportunity IDs, signal families and source records when available. Keep internal dispositions separate from client-facing page content and email verification status. Reuse the applicable feedback on the next delivery.
