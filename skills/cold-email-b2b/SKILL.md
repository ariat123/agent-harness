---
name: cold-email-b2b
description: Write, review, and diagnose B2B cold email, follow-ups, prospect replies, and email campaign performance. Use for copy, reply handling, and deliverability decisions; use intent prospecting for account research and selection.
---

# Cold Email B2B

Use this as the controller for the cold email knowledge base. Keep this file focused on decisions and copy rules. Load the reference files only when the task needs them.

## Business Context And Output Contract

Use for the business named in the request, whether AFT Labs or a client. Establish whose offer is being marketed, who the customer is, and the desired outcome. For client work, use the client's facts, proof, voice, and operating constraints; do not inherit AFT offers, signatures, commercial terms, or campaign defaults. Load AFT business context only when AFT is the subject or its role in the requested work makes that context relevant.

For AFT work, read aft-context.md (private reference, not included) to locate current offers, proof permissions, and execution authorities. Establish seller, buyer, offer, source-backed relevance, and desired next step. Do not run a lead batch merely to write copy.

For the standard AFT two-email sequence, Email 1 has a truthful subject and signs `Aria Tabatabai` followed by `AFT Labs` on its own line. Email 2 has a blank subject and signs only `Aria`. Preserve intentional paragraph breaks and use no em dashes. Render variables against the actual approved schema before campaign use. A pattern's placeholder signature, fictional proof, or guarantee is not approved copy.

Return the requested draft or diagnosis, plus only material assumptions, missing proof, and next checks. Keep unsupported business facts out of send-ready text. For a campaign diagnosis, state what evidence supports the conclusion and what remains unresolved.

Use B2B outreach for the named seller across industries, including a client's business-facing offer. This is not a consumer bulk-email or lifecycle-email playbook. References include AFT service-campaign examples and operating rules; transfer the relevant method, not AFT-specific identities, guarantees, terms, tooling requirements, or historical facts, into client work.

## Reference Routing

Read the relevant reference before answering or editing a campaign:

- infrastructure-deliverability.md (private reference, not included) for domains, inboxes, authentication, ramping, mailbox providers, DNS, validation, catch-alls, waterfall enrichment, reputation, domain monitoring, replacement decisions, or deliverability troubleshooting.
- campaign-operations.md (private reference, not included) for list construction, AI/MCP lead QA, signal-driven campaign angles, local nurture, testing methodology, campaign economics, reply handling operations, or campaign-level diagnosis.
- template-library.md (private reference, not included) when the user wants examples, architectures, patterns, or multiple copy directions.
- customer-offer-patterns.md (private reference, not included) when evaluating or adapting a potential customer's product, use case, price-comparison offer, or other buyer-native commercial angle for cold email. Treat operator examples as hypotheses until first-party evidence supports them.

If the working repo also contains `offer-strategy-principles.md` or `six-test-offer-framework.md`, use those as the deeper authority for materially changing the offer. Use `client-acquisition-and-sales-operations.md` for cross-channel acquisition, manual booking, sales, CRM, acquisition math, funnel design, or sales-cycle decisions. Do not duplicate general agency strategy into this skill.

## Knowledge Intake Protocol

When the user supplies a tweet, thread, newsletter, template, or operator claim, classify it before adding it:

- **[CORE]** durable rule supported by provider requirements, verified campaign evidence, or repeated strong operating logic.
- **[CONDITIONAL]** useful default that depends on market, provider, stage, volume, buyer, or campaign design.
- **[EXPERIMENT]** plausible tactic worth testing, but not strong enough to become doctrine.
- **[VENDOR]** tool-specific workflow that must be rechecked before implementation.
- **[REJECTED]** unsupported, contradicted, misleadingly absolute, or unsafe guidance.

Prefer current provider requirements and applicable law over operator anecdotes. Prefer verified first-party campaign data over promotional claims. Never turn one influencer's numeric benchmark into a universal threshold without evidence.

## Core Operating Principles

1. **Write person to person.** Use plain, conversational language. Remove corporate filler, jargon, and unnecessary biography.
2. **One decision per email.** The first email should ask for one next step: reply, receive an asset, or take a call.
3. **Subject lines create curiosity, not the full pitch.** They should plausibly resemble a normal internal or person-to-person subject when truthful.
4. **The campaign needs a real offer.** Copy cannot rescue an unwanted outcome, weak economics, or an undefined result.
5. **Personalization is optional; relevance is not.** Use personalization only when it materially improves why this person should care.
6. **Calibrate to stage.** Unknown senders usually optimize for replies. Trusted senders with reply flow can optimize more directly for booked calls.
7. **Calibrate to the buyer.** Lead with the outcome and use buyer-readable fulfillment detail when it explains credibility, responsibilities, or workload. Do not conceal material terms or misrepresent how the service works.
8. **Optimize for instant comprehension.** Short sentences, familiar words, one idea per sentence, buyer-native terminology.
9. **Truth beats cleverness.** Do not invent triggers, intent, proof, inventory, proximity, diagnoses, or qualification.
10. **Test architectures before wordsmithing.** Spend scarce testing budget on list quality, offer, commercial angle, and body architecture before subject-line or wording micro-tests. When volume supports it, compare meaningfully different messages before tiny wording changes.
11. **Match effort to evidence density.** Do not spend Dream 100 effort on every row. Multiple independent, relevant signals can justify deeper research and a custom asset; a strong ICP with little timing evidence usually deserves a more scalable control. Signals raise confidence, not certainty.
12. **State the offer explicitly.** A campaign cannot validate an offer the prospect was never told. The reader should understand what result or useful asset is being proposed.
13. **Read it out loud.** Remove sentences the sender would feel unnatural saying face to face. AI can create a starting point; the final message must sound like the sender.

## Cold Email Offer Preflight

Before writing copy, pressure-test the offer in this order:

1. **Ask what already exists.** Understand the current go-to-market motion, assets, proof, customer base, and constraints.
2. **Find the smallest useful free or inspectable value, if needed.** A sample, shortlist, teardown, prototype, rewritten asset, or other proof of capability can reduce the trust gap. Do not force free value when a direct offer already earns responses.
3. **Choose a comfortable risk reversal.** Use a guarantee, performance structure, pilot, or other de-risking mechanism only around outcomes the provider can define and control. Add stipulations where client actions or starting conditions matter.
4. **Pressure-test the angle.** Use AI or another reviewer to challenge assumptions, identify unsupported claims, and ask why the buyer would care. AI is a critic, not the source of truth.
5. **Centralize the message.** Pick the strongest reason to care, such as the result, proof, free asset, risk structure, trigger, or pain, and build the email around one dominant idea.

## Four-Part Copy Framework

Use this as a flexible structure, not a requirement to make every email four visible paragraphs:

1. **Relevance / personalization**: why this person, company, moment, or situation.
2. **Credibility**: who the sender is, matched proof, or a tangible pre-proof asset.
3. **Offer**: the buyer-understood result and relevant risk/payment logic.
4. **CTA**: one low-friction next decision.

### Relevance and First Lines

Use a sourced first line only when it earns its space. Good first lines are short, factual, and tied to the offer.

Useful sources include a service page, public case study, verified role change, relevant hiring post, company news, website flow, location, technology, public program date, or genuine content engagement.

Avoid generic compliments, long service lists, fake local references, fuzzy company matches, and AI-written flattery. False specificity is worse than broad truthful relevance.

When using AI for first lines, preserve the source and match on hard identifiers such as exact email, domain, URL, or saved research path. Preview a meaningful sample before launch.

### Evidence-Led Diagnosis

When public evidence supports the pitch, separate fact from inference:

1. sourced observation
2. careful implication
3. matched verified proof
4. de-risked next step

A visible signal may justify a hypothesis. It does not prove internal spend, dissatisfaction, urgency, buying intent, or operational failure.

### Trigger -> Implication -> Proof/Offer -> Ask

Use this as a high-value architecture when a sourced trigger is the strongest reason for the email. The trigger is optional; do not force one onto a strong direct offer merely to make the message look personalized.

1. **Trigger:** a verified fact, event, observation, or account-specific detail that creates real relevance.
2. **Implication:** a careful statement or question about the priority, constraint, opportunity, or operating condition the trigger may create. Treat this as a hypothesis unless the source proves it.
3. **Proof or pre-proof offer:**
   - when matched proof exists, use a verified result from a genuinely comparable company or situation;
   - when matched proof does not exist, do not fake a case study. Use the clear buyer-understood offer plus a visible risk/payment structure, or a small inspectable asset that proves capability.
4. **Ask:** one small next decision. This can be a simple yes/no, an asset request, or a diagnostic choice where either answer helps scope the opportunity.

Pre-proof shape:

```text
Saw {{verified_trigger}}. {{careful_implication_or_question}}.

We {{buyer_understood_offer}} on {{risk_or_payment_structure}}.

Would {{option_a}} or {{option_b}} be more useful first?
```

Proof-led shape:

```text
Saw {{verified_trigger}}. {{careful_implication_or_question}}.

We helped {{comparable_company_type}} {{verified_result}} in {{timeframe}}.

Worth me sending {{small_asset_or_breakdown}}?
```

Recency improves timing when it is commercially relevant, but do not impose an arbitrary age limit such as `90 days`. An older fact can still be useful when its implication is still current. Preserve a working pre-proof control until first-party campaign evidence justifies replacing it.

### Signal Conjunction And Evidence Density

One signal is usually a reason to investigate. Multiple independent signals can justify more confidence and more outreach effort. Useful evidence dimensions include:

- ICP and economic fit
- direct evidence of the problem or opportunity
- a recent timing event or trigger
- evidence of budget, capacity, or ability to act
- a reachable person who plausibly owns the outcome

Treat the conjunction as a prioritization tool, not proof that the prospect is `ready to buy`. A company with strong ICP fit plus a relevant job post, a recent change, and a sourced problem can deserve manual research and a custom asset. A qualified account with no timing evidence may be better served by a direct offer or segment-level asset. Read campaign-operations.md (private reference, not included) for tiering.

### Credibility When Proof Is Weak

Use verified proof when it exists. When it does not, do not fake it. Move credibility into something the prospect can inspect: a custom plan, shortlist, sample, prototype, rewritten asset, audit finding, or other small proof of capability.

A sample should prove that the sender understands the situation, can perform the relevant work, and has a credible paid next step. Build the smallest useful slice that makes the rest believable. A sample proves capability, not client outcomes.

**Standalone-value test:** mentally remove the seller's brand, product pitch, and CTA. If the remaining asset would not still help the prospect understand, decide, diagnose, or do something useful, it is probably a brochure wearing a lead-magnet label. Prefer assets that deliver their main value quickly; `under 10 minutes` can be a useful design target, not a universal rule.

### Offer Rules

Lead with a buyer-understood result. Make the complete campaign offer as concrete as the evidence allows: result, timeframe, qualification, attribution, client obligations, and risk/payment structure.

Do not invent precision merely because exact numbers sound persuasive. Do not guarantee revenue, contracts, or downstream outcomes the provider does not control.

Treat the payment structure itself as risk reversal when it genuinely removes risk. Do not stack a vague guarantee on top of a clear pay-on-result structure just to sound safer.

### Progressive Fulfillment Disclosure

Do not confuse outcome-first copy with hiding the operating model at every stage. Fulfillment detail can strengthen the sale when it demonstrates that the provider absorbs meaningful work and makes the promised result more believable.

Use operational detail progressively:

- **First cold email:** keep the outcome dominant. Add at most a compressed buyer-readable line such as `we handle the research, outreach, qualification, and scheduling` when low workload itself strengthens the offer.
- **Positive reply:** if useful, expand the operating model enough to show what the provider handles and what minimal input the buyer provides.
- **Sales call:** explain the complete result-producing system, including meaningful provider-owned stages, client dependencies, and handoffs.
- **Proposal or agreement:** state responsibilities, prerequisites, qualification, attribution, and boundaries precisely.
- **Follow-up:** use fulfillment detail only when it answers an objection or contributes a new reason to engage; do not send a generic scope dump.

Name buyer-readable responsibilities before technical implementation details. `We handle account research, outreach, replies, qualification, and scheduling` usually communicates more value than a list of sequencers, domains, enrichment tools, models, or workflow software. Name technical details only when the buyer is technical or the implementation itself materially improves credibility.

The purpose of scope detail is to prove low client workload and operating completeness, not to turn the email into a menu of services.

### Offer Compression and the One-Decision Test

Write the shortest truthful version of the offer before adding context. A compressed control can be useful when the list is narrow, the result is obvious, and the payment/risk structure is easy to understand.

Example shape:

```text
Hey {{first_name}}, are you open to {{buyer_understood_result}} where you only {{payment_trigger_or_risk_reversal}}?
```

A failed compressed email does not automatically prove the offer is bad. Targeting, deliverability, list quality, timing, trust, and saturation can also constrain response.

### Inspectable Asset CTAs

When offering a resource, name the concrete thing the prospect can receive or inspect.

Prefer: `Want me to send the first three changes and the short plan?`

Be cautious with vague labels such as `audit`, `consultation`, `analysis`, or `strategy session` when the useful output is not named.

In many markets, **send** and **show** create less friction than **make**, **consult**, or **analyze** because the value feels immediate.

If a prospect asks for the asset, deliver it as soon as practical while the problem and conversation are still salient. Same-day delivery is a strong operating default when the asset is ready; do not make an engaged prospect wait for the next batch merely because the campaign runs on a schedule.

Keep the original terms of an asset offer. A free teardown offered without a call must not become a paid-pilot intake or mandatory meeting. Deliver the promised asset first; make further work or a call a separate, optional next step. If the asset is not ready, state that honestly rather than claiming it already exists.

## CTA Selection

### Cold / low trust

Use reply-based CTAs that make a one-word response easy:

- `Want me to send it over?`
- `Worth a look?`
- `Interested in {{specific_result}}?`

A **diagnostic-choice CTA** can be stronger when two real options both advance qualification, for example `Would transitions or lease-ups be more useful first?` or `Which territory would you want to start with?` Use this only when the choices are genuinely relevant and the answer changes the next step. Do not manufacture a false binary.

Do not imply prior contact that never happened.

### Trusted / active reply flow

Move quickly to a call when trust already exists. Offer specific times or a simple booking path. Minimize unnecessary ping-pong.

### Post-Reply Progression

A positive reply is not the end of the campaign. Respond quickly, answer the actual question, ask only necessary qualification questions, and actively move the prospect toward the next appropriate step. A done-for-you buyer should not hit a long do-it-yourself funnel immediately after replying.

For cold or low-trust responders, active manual booking is often the lowest-friction default. Offer one or two concrete times, agree on a time, and send the invite. Use a calendar link when the prospect requests one or it clearly makes scheduling easier. Do not automatically push every positive reply through an application, VSL, or self-service booking flow.

Use phone or text only when appropriate for the relationship, market, consent context, and jurisdiction.

## Subject Lines

The subject line should not carry the whole sales argument. Useful categories include:

- `quick question`
- `question for {{first_name}}`
- a buyer-native topic
- a real local reference
- a truthful internal-looking relevance cue
- a verified event or function

Do not fabricate local references, colleague familiarity, internal context, or prior relationship. A pattern interrupt is only good when it remains truthful and relevant.

Short, buyer-native subjects are a useful default, including one-to-three-word subjects when they fit naturally, but not a law. Do not spend early testing capacity trying to crown a subject-line winner while list quality, offer, angle, or body architecture is still uncertain.

## Follow-Ups

Start simple. An initial email plus one follow-up is a safe default when the campaign is unproven. Add further touches only when the sequence earns incremental lift without unacceptable complaints or reputation damage.

Follow-ups should sound like a human ping, not a second brochure. They can confirm delivery, simplify the value, add a new fact or asset, or offer a smaller next step.

A contextual reply opt-out can be used when appropriate, for example: `If this is already handled, reply "handled" and I won't follow up.` Treat any clear stop request as an opt-out, regardless of keyword. Do not use random opt-out words to manipulate reply semantics or conceal required unsubscribe mechanisms.

A new angle or elapsed waiting period is not automatic permission to recycle a silent or suppressed contact. Follow the campaign's approved contact policy, prior replies, and suppression state. Stop requests need no second pitch.

## Saturated-Category Reframing

When the ICP sees the same promise constantly, test a truthful alternate frame such as vendor openness, capacity, territory, eligibility, a buyer-native operating condition, or a useful asset. Test it against a direct outcome-led control. Clever framing does not rescue a generic offer.

## Writing Workflow

Before writing, gather:

1. buyer and ICP
2. buyer-understood result
3. timeframe and qualification rules
4. proof or pre-proof asset
5. risk/payment structure
6. campaign goal
7. relevant sourced signals
8. current campaign learnings
9. provider-owned operational stages and required client inputs
10. the planned reply, qualification, and booking path

Then:

1. choose an entry architecture
2. write the shortest truthful offer
3. add only relevance that earns its space
4. choose the stage-appropriate CTA
5. write the subject and inbox-preview opening together
6. produce meaningfully different variants when the user wants a test
7. run the audit below

Read template-library.md (private reference, not included) when examples would help.

## Audit

Reject or rewrite when any of these fail:

- The main point cannot be understood in one fast scan.
- The prospect cannot identify the result and the single next decision.
- The message uses personalization or vague collaboration language without ever stating the offer.
- Personalization delays the offer without adding relevance.
- A resource CTA does not name the actual useful output.
- Claims about proof, intent, inventory, proximity, problems, or triggers are unsupported.
- The mechanism is foregrounded even though it creates category spam for this buyer.
- Fulfillment detail is either absent when buyer workload is a material concern, or so detailed that it buries the outcome.
- The CTA asks for too much trust relative to stage.
- The email tries to complete the whole sale with biography, mechanism, multiple proof points, a Loom, an application, and a calendar link at once.
- The copy uses corporate filler, fake scarcity, fake familiarity, or AI slop.
- The message relies on a guarantee outside the provider's control.

For deliverability or domain diagnosis, do not guess from copy alone. Read infrastructure-deliverability.md (private reference, not included).

## Platform Translation

Translate the same principles to the channel rather than copying email verbatim:

- **LinkedIn:** shorter, identity-rich, useful for role verification and light familiarity.
- **SMS:** very short; apply applicable consent and messaging rules before use.
- **X / Instagram DMs:** more casual, with a very short visible hook.

## Final Sanity Check

Before shipping cold email copy, verify:

- truthful relevance
- buyer-native result
- proof or honest pre-proof anchor
- visible and supportable risk/payment logic when useful
- one clear next decision
- instant comprehension
- stage-appropriate CTA
- mechanism disclosure fits the buyer
- no unsupported intent, diagnosis, inventory, or results
- no unnecessary links, attachments, formatting, or complexity
- variants differ meaningfully when testing

When the task concerns campaign performance rather than wording, read the appropriate reference instead of forcing a copy rewrite.
