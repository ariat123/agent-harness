---
name: account-profile
description: Draft the Account Profile section of a GitLab account plan from public sources. Answers the eight Account Profile questions from GitLab's public Account Planning page, maps the findings to GitLab's four customer value drivers, cites a dated source on every line, marks what public sources can't answer, and ends with questions for the AE to confirm with their champion. Use when an AE, SA or CSM is starting or refreshing an account plan. Not for relationship mapping, contact research, whitespace mapping or the action plan.
---

# Account Profile draft

This skill writes a first draft of the **Account Profile** page of an account plan. An AE reviews every line before any of it goes into the plan. The draft is an aid to the AE's judgment, not a replacement for it.

Every structural choice below comes from a public GitLab handbook page. `references/handbook-basis.md` lists which page each rule comes from.

## Inputs

- **Required:** the company name.
- **Optional:** ticker, the line of business the AE cares about, and public notes from the AE (a news link, an earnings call quote).
- **Never use:** data from inside a customer's GitLab instance (Red), Salesforce exports, opportunity notes, contracts, or spend. If the AE adds GitLab relationship facts to question 8 by hand, the profile becomes Orange. Label it that way. See `references/data-rules.md`.

## Steps

1. **Pin the entity.** Confirm the legal name, ticker, and fiscal year end. If the name could mean a parent or a subsidiary, ask the AE which one before researching.
2. **Gather sources** in the priority order in `references/source-rules.md`: latest 10-K, latest 10-Q and earnings release, 8-Ks from the last 12 months, the investor relations site and newsroom, current technology job posts, and GitLab's public customer stories. Give each source an ID (S1, S2, ...) with its publish date and the date you accessed it.
3. **Answer the eight questions in handbook order** (`references/account-profile-questions.md`). Two or three lines per question. One claim per line, each followed by its source IDs, like `[S2]`.
   - A line that states what a source says is a fact line.
   - A line that is your reading of the facts starts with `Read:`. Keep these few, and make each one easy to argue with.
4. **Pick the value drivers** under question 1. Choose at most two of GitLab's four value drivers, using the exact names in `references/account-profile-questions.md`. Give each a one-line reason tied to cited lines. Never add a fifth driver.
5. **Questions 7 and 8 are usually not public.**
   - Question 7 (how the business sees IT): write `Not in public sources.` unless a source says it directly. Then add one discovery question.
   - Question 8 (the GitLab relationship): fill it only from a public GitLab customer story at about.gitlab.com/customers, or leave `AE to fill from Salesforce.` Never infer a customer relationship from a job post or a tech-stack guess.
6. **List the gaps.** Under "Gaps to close with the account team," say what the team still needs to learn. Under "Questions to confirm with your champion," write three to five questions that would test the draft's biggest assumptions.
7. **Hold conflicts.** If two sources disagree, put both under "Held" with their IDs. Don't pick one.
8. **Check the draft.** Run `python3 evals/check_profile.py <profile.md>` and fix every failure before handing it to the AE.

Use `references/output-template.md` for the layout.

## Rules

- **The profile is about the customer, not GitLab.** No GitLab product pitches in questions 1 to 7.
- **Source and date on every line.** If you can't cite it, don't write it. Write `Not in public sources.` instead of guessing.
- **No personal names.** Public personal data is still Orange under GitLab's data classification standard, and people mapping is a separate part of the account plan. Refer to roles ("the CFO said") instead.
- **Prefer the company's own filings** over press coverage. Use press only for events the company hasn't filed on yet, and say so.
- **Keep dates exact.** Write the filing and its date (for example "fiscal 2025 10-K, filed on its EDGAR date"), not "recently."
- **Forward-looking statements stay attributed.** "The company expects..." with a source, never a bare prediction.
- **One page.** About 750 words before the Sources table. Two or three lines per question (four at most for question 1), up to four gaps. If it runs long, keep the lines an AE would use in a first call.

## Stop and ask the AE

- The entity is ambiguous (parent vs. subsidiary, recent merger, name collision).
- The AE pastes data that looks like customer platform data, contract terms, or spend.
- The AE asks for named contacts, an org chart, or emails. That belongs in relationship mapping, done in approved tools.

## When to re-run

- Every quarter, after the company's next 10-Q or 10-K.
- Right away after a leadership change, a merger or acquisition, or a new competitive threat.
- After the underlying model changes. Re-run the three samples in `samples/` and check them with `evals/rerun-checklist.md` before AEs use the new version.
