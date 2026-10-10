# Data rules

Based on GitLab's public [Data Classification Standard](https://handbook.gitlab.com/handbook/security/policies_and_standards/data-classification-standard/) and the Claude guidance in the [Sales Development handbook](https://handbook.gitlab.com/handbook/sales/sales-development/#claude-sales-dev-bdrsdr-user-guide). Both accessed 2026-10-10.

| Tier | What it covers here | What the skill does |
|---|---|---|
| **Green** (public) | SEC filings, press releases, company websites, public GitLab customer stories, the GitLab handbook | The default. A profile built only from these is Green. |
| **Yellow** (internal) | GitLab internal notes, internal process docs | Not needed. Leave it out. |
| **Orange** (personal and confidential) | Any personal data, **including personal data from public sources**; customer contracts with GitLab; non-public financial information | No personal names in the profile. If the AE adds spend or contract facts to question 8, label the profile Orange. |
| **Red** (restricted) | Customer's Data: anything a customer created or uploaded inside the GitLab platform | Never. Stop and tell the AE if it shows up. |

## Why these choices

- **No names, even public ones.** The standard says the source of personal data "should not change its classification to a level below Orange." It also says a data set that includes personal data is Orange as a whole. One executive's name would turn a public profile into an Orange document. Relationship mapping is a separate part of the account plan anyway.
- **Question 8 stays with the AE.** Spend, contract length, and stage adoption come from GitLab's own systems. They are non-public financial information and customer contract data, which is Orange. The handbook notes that Claude can't reach GitLab systems or CRM data, so the AE adds these by hand if at all.
- **Claude is approved up to Orange.** The Sales Development handbook says "Sharing up to Orange-level data has been approved" for Claude. This skill still defaults to Green so the draft can be shared freely inside the account team.
- **New tools and connectors go through review.** GitLab's [Third Party Risk Management page](https://handbook.gitlab.com/handbook/security/security-assurance/security-risk/third-party-risk-management/) says new vendors or systems accessing Orange or Red data need a TPRM review before approval, and new functionality from an existing vendor can trigger one too. Adding a paid data source or connector to this skill means a review, not a settings change.
