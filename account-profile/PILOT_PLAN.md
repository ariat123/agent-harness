# Pilot plan: five field champions, four weeks

A proposal for testing this skill with real account teams before anyone rolls it out. Nothing here has been run with GitLab reps. No time savings are claimed.

## The question the pilot answers

Does a sourced first draft of the Account Profile get used in real account plans, and is it accurate enough that AEs trust it after checking it?

## Who

- **Five champions:** three AEs from different segments, one Solutions Architect, and one Customer Success Manager. The handbook asks AEs to build account plans with their SA and CSM, so the pilot includes both roles ([Account Planning](https://handbook.gitlab.com/handbook/sales/account-planning/)).
- **Time:** about two hours a week each, agreed with their manager.
- **Accounts:** each champion picks up to three strategic accounts, matching the handbook's "up to three account plans at one time." That's up to 15 profiles.

## Schedule

| Week | What happens |
|---|---|
| 0 | 30-minute kickoff. Load the skill in Claude. Each champion runs one profile live and logs what they'd change. |
| 1-3 | Champions run the skill on their accounts and use the drafts in real account plans, QBR prep, or AE-SA-CSM account calls. One 20-minute check-in each week. |
| 4 | Readout: the numbers below, the error log, and a go, fix, or stop call. |

## Measures

The four levels Field Enablement already uses ([How We Measure Success](https://handbook.gitlab.com/handbook/sales/field-operations/field-enablement/#how-we-measure-success)):

| Level | What we track | Read it as |
|---|---|---|
| 1. Engagement | Profiles run per champion, and how many reached AE review | Did they use it at all? |
| 2. Sentiment | After each profile: "Would you start your next account plan from this draft?" (1-5) and one line on what they changed | Do they trust it? |
| 3. Behavior | Profiles that ended up in a real account plan, QBR deck, or account call. Share of lines kept, edited, or deleted. Champion questions actually asked of the customer | Did it change how they prepare? |
| 4. Outcome | Self-reported prep time for the Account Profile section, before and after. Treated as a hypothesis only | Too early for pipeline or deal-cycle effects. Name them, don't claim them. |

**Accuracy, tracked separately:** every wrong line goes in an error log with its type (wrong fact, stale fact, unsupported read, missed something important). Each error is checked against its cited source to see whether the skill or the source was wrong.

## Decision at week 4

- **Go to a wider cohort** if at least 10 of 15 profiles were used in real account work, the average rating is 4 or higher, and no profile had an uncited or invented line.
- **Fix and re-run** if usage is good but the error log shows a pattern. Change the skill, re-run the three samples, then continue.
- **Stop** if champions didn't use the drafts. That answer is worth having early.

## Data rules for the pilot

From GitLab's [Data Classification Standard](https://handbook.gitlab.com/handbook/security/policies_and_standards/data-classification-standard/) and the [Claude guidance for Sales Development](https://handbook.gitlab.com/handbook/sales/sales-development/#claude-sales-dev-bdrsdr-user-guide):

- **Green inputs only.** Public filings, news, company sites, and public GitLab customer stories.
- **No personal names** in profiles. Public personal data is still Orange.
- **No Salesforce exports, contracts, or spend** pasted into the skill during the pilot. Question 8 is filled by the AE in the account plan itself.
- **Never Red data.** Nothing from inside a customer's GitLab instance.
- **No new tools or connectors** during the pilot. Adding one starts a Third Party Risk Management review.

## Feedback

One GitLab issue per profile, from a short template: account, rating, lines changed, errors found. The Sales Development team already collects AI agent feedback through an issue template in a GitLab project, so this follows a pattern the field knows.

## After a model change

Re-run the three samples in `samples/` and walk through `evals/rerun-checklist.md` before champions use the new version.

## What this pilot does not test

- Relationship mapping, whitespace mapping, or the action plan.
- Paid data sources or CRM connectors.
- Whether better profiles lead to more pipeline. That needs a longer window and a comparison group.
