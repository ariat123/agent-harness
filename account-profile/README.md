# Account Profile skill

A Claude skill that drafts the **Account Profile** page of a GitLab account plan from public sources. An AE gives it a company name. It answers the eight Account Profile questions from GitLab's public [Account Planning](https://handbook.gitlab.com/handbook/sales/account-planning/#account-profile) page, maps the findings to GitLab's four [customer value drivers](https://handbook.gitlab.com/handbook/sales/command-of-the-message/#customer-value-drivers), and cites a dated source on every line. The AE reviews every line before any of it goes into the plan.

**[Read the samples and pilot plan (PDF, 5 pages)](account-profile-samples.pdf)**

## Three real runs

| Company | GitLab relationship (question 8) | Sample |
|---|---|---|
| Lockheed Martin | Filled from GitLab's public customer story | [lockheed-martin.md](samples/lockheed-martin.md) |
| Deere & Company | No public story, so "AE to fill from Salesforce" | [deere.md](samples/deere.md) |
| Capital One | No public story, so "AE to fill from Salesforce" | [capital-one.md](samples/capital-one.md) |

Every line in the samples was checked against its source on 2026-10-10. That check corrected 21 of about 140 lines (none was an invented fact), and all three pass `evals/check_profile.py`. Details in [evals/RESULTS.md](evals/RESULTS.md).

## Every rule comes from a handbook page

| Rule | Handbook basis |
|---|---|
| Draft only the Account Profile, using the handbook's eight questions in order | [Account Planning](https://handbook.gitlab.com/handbook/sales/account-planning/#components-of-an-account-plan): the plan's four parts and the Account Profile questions |
| About the customer, with no GitLab pitch | The Account Profile "is all about the customer (not GitLab)" |
| At most two of the four named value drivers | [Command of the Message](https://handbook.gitlab.com/handbook/sales/command-of-the-message/#customer-value-drivers) |
| Filings first, then news and hiring | [Sales Development](https://handbook.gitlab.com/handbook/sales/sales-development/): 10-Ks give "strategic priorities directly from leadership" |
| Mark gaps, end with questions for the champion | Account Planning, Steps 2 and 6 |
| No personal names, even public ones | [Data Classification](https://handbook.gitlab.com/handbook/security/policies_and_standards/data-classification-standard/): public personal data stays Orange |
| GitLab relationship only from public customer stories or the AE | Data Classification: contracts and non-public financials are Orange |
| The AE reviews every line | Sales Development: agent output is "an action aid, not a replacement for rep judgment" |

The full mapping is in [references/handbook-basis.md](references/handbook-basis.md).

## What's here

```
SKILL.md                         the skill
references/
  account-profile-questions.md   the eight questions and four value drivers
  source-rules.md                which sources, in what order
  data-rules.md                  Green, Yellow, Orange, Red, and what the skill does with each
  output-template.md             the page layout
  handbook-basis.md              where each design choice comes from
evals/
  check_profile.py               structural checks: citations, value drivers, no names, question 8
  rerun-checklist.md             what to re-check after a model or skill change
  RESULTS.md                     checker results and a second-model re-run
samples/                         three real runs on public companies
PILOT_PLAN.md                    five field champions, four weeks, Field Enablement's four measures
```

## Use it

Load the folder as a skill in Claude (or Claude Code), then ask: "Draft the Account Profile for <company>." Check a draft with:

```bash
python3 evals/check_profile.py samples/*.md
```

## Limits

- Public sources only. It doesn't see Salesforce, contracts, or anything inside a customer's GitLab instance.
- It stops before relationship mapping, whitespace mapping, and the action plan.
- The pilot plan is a proposal. No GitLab rep has used this skill, and no time savings are claimed.

Built on GitLab's public handbook (accessed 2026-10-10). Not affiliated with or endorsed by GitLab.
