---
name: account-specific-b2b-outreach
description: Research AFT prospect accounts and write natural, account-specific cold-email sequences for the selected offer, including managed outreach or opportunity research, monitoring and handoffs. Use for research-to-copy preparation and copy review, not standalone lead discovery, fulfillment packets, mailbox verification or campaign launch.
---

# Account-Specific B2B Outreach

Build a message Aria could naturally say aloud to this person on first meeting them. The customer type, result, opening and next question should belong to this account, not a website summary with a swapped first line. Research supplies a reason to reach out; it is not a requirement to recite facts. Reuse company evidence and completed work rather than restarting research for a tone correction.

For new Email 1 copy, use this default conversational flow: personal observation or researched direct question, a clear connection to the proposed help, plain offer, short approved guarantee/payment condition, then a final permission-aware CTA. These are message functions, not fixed sentence counts. Do not open with a bare company-description sentence such as `Antero works across Texas`. Explain what the sender noticed and how it relates to the help, without inventing firsthand experience. Explaining the offer does not establish the stranger's interest. Include an approved guarantee rather than silently dropping it; if its terms are unknown, flag the missing slot in local review and do not invent send-ready wording. Read the concrete acceptance tests in copy-and-review.md (private reference, not included).

## Scope and authority

Use this skill for AFT work. Resolve AFT business-context paths against the private AFT Lead Ops repository (not included), even when invoked from another project; do not substitute that project's offers or client records. Read the AFT repository's `AGENTS.md`. For an actual batch, use the exact selected `config/offers/<niche>/<offer-id>.json` or immutable run snapshot. For a new-offer planning or local copy request, the user's explicit offer brief can govern a draft; missing commercial terms remain unknown, not invented. A missing profile is not a reason to force managed outreach or reject an otherwise feasible company. A canonical batch still needs its versioned offer and workflow checks.

Apply the newest relevant user decision within its stated scope. A recent industrial approval does not silently rewrite every niche; an old PM example does not approve new payment terms. If a new commercial decision conflicts with a run snapshot, expose the conflict and follow the versioned-offer workflow rather than changing history. Do not select an offer merely because it is the newest filename.

This skill coordinates existing capabilities. For account research, read `$b2b-intent-prospecting` at `skills/b2b-intent-prospecting`. For sequence writing or copy review, read `$cold-email-b2b` at `skills/cold-email-b2b`. Follow their relevant reference routing. If unavailable, report the missing dependency and use this skill's references for a local draft only; do not invent business context or certify campaign readiness.

Use those dependencies for methods, not as approval of template claims or a different product. For AFT voice, commercial disclosure and sequence choices, apply the user's relevant decisions and this skill's copy reference rather than a generic template's numbers, guarantee or paragraph structure.

Choose only the requested stage. Building a skill, reviewing examples, or drafting a handoff does not require starting a lead run, buying research, verifying mailboxes, messaging other tasks, or mutating Instantly.

## Working path

Before selecting discovery routes or copy angles, read AFT sourcing and learning (private reference, not included). Retrieve only matching client/niche/offer feedback and source ideas. Save newly supplied feedback with attribution and scope. Distinguish an opportunity-led opening for the same service from a change to the paid product; neither a saved idea nor a promising signal changes approved terms.

1. **Establish the brief.** Read [offer-modes.md](references/offer-modes.md). Identify the paid product, downstream buyer, provider-owned work, client-owned work, actual deliverable, approved payment condition and any preview asset. Do not confuse a short-list CTA with the paid service. Resolve only material gaps that prevent the requested claim; other useful draft work can continue.
2. **Research the account.** Read signal-research.md (private reference, not included). Reuse inherited sources, check current identity/role and offer-critical facts, then select useful discovery routes. Save observed fact separately from inference. Missing a timing signal is not a fit rejection.
3. **Write the whole sequence.** Read copy-and-review.md (private reference, not included). For calibration or substantial voice corrections, also read voice-examples.md (private reference, not included). Adapt the whole message, not only its personalization. Choose a supported signal or a researched direct opening. Neither an `I saw` opening nor an assumed-pain line is compulsory.
4. **Persist and hand off.** Read aft-workflow.md (private reference, not included) before any batch operation or operational handoff. Use canonical task packets and their exact schema. Save each completed company/contact result durably, preserve source associations and resume pending work only.
5. **Review before scale.** For a new niche/motion, calibrate roughly 5-10 representative contacts, including a same-company pair and a direct opening where available. Research these accounts and write their full selected sequence first, rather than researching the whole cohort before testing the writing approach. Review rendered copy for both truthful offer meaning and spoken cold-recipient context. Show every complete email in the selected sequence together. Once the user approves scaling, carry the framework through the remaining contacts without repeatedly asking for the same approval. Agent QA is not human approval; stop at the requested boundary.


## Quality bar

- Support the actual sentence with retrievable evidence. Preserve authorship, dates, uncertainty and whether the material is a transcript, description, post or other source.
- Explain why the observation changes this offer or question. A public event is not proof of private pain, buying intent or budget.
- Do not claim Aria personally listened, attended, read an entire source, or previously emailed a colleague without evidence of that action.
- Do not disqualify an otherwise suitable company because public content is sparse. A direct message still needs confirmed identity, fit and an account-specific proposition.
- Keep payment meaning, proof permissions, billable results and client obligations faithful to the selected offer. Do not transfer PM terms to unrelated niches.
- Make the offer understandable before asking the stranger to choose a pilot or campaign focus. A true company fact does not supply missing conversational context.
- Test the opening as an approach to the owner in person: is the sender making a relevant observation, or merely telling the owner what their company does? Adding `I noticed` alone does not rescue an irrelevant fact. Test the CTA separately: does it invite interest, or assume an accepted plan? Check the guarantee slot against the actual approved offer before calling copy complete.
- Use concise buyer language and the simplest truthful risk reversal. Do not publish internal prices, delivery quotas, deadlines or remediation formulas by default. Do not impose a hard word limit or rewrite good copy merely to meet a quota.
- Preserve verification evidence and records. Research does not make an email valid; gateway policy is separate from mailbox validity. Respect prior outreach and DNC holds.
- Use no em dashes in AFT deliverables. Preserve paragraph breaks and the standard two-email signature/threading rules in the copy reference.

Return the requested artifact, real coverage and remaining issues. Never call a batch send-ready solely because the writing is complete. Skill instructions do not override canonical validation or authorize external actions.
