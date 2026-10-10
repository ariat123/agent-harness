# Source rules

GitLab's Sales Development handbook lists "Financial disclosures and 10-K reports" as the source of "strategic priorities directly from leadership," next to company news and strategic hires ([Sales Development, account research](https://handbook.gitlab.com/handbook/sales/sales-development/), accessed 2026-10-10). This skill uses the same families, in this order.

## Priority order

1. **Latest 10-K** (SEC EDGAR). Item 1 (business, strategy, competition), Item 1A (risk factors), Item 1C (cybersecurity), Item 7 (management's discussion).
2. **Latest 10-Q and earnings release.** For anything that changed since the 10-K.
3. **8-Ks from the last 12 months.** Acquisitions, divestitures, restructurings, leadership changes. Cite the event, not the person.
4. **Investor relations and newsroom.** Investor day material, shareholder letters, press releases.
5. **Current job posts on the company's careers site.** Only for technology direction (for example, a platform engineering or DevSecOps team being built). Cite the post and its date. A job post is a hint, not proof of a program.
6. **GitLab customer stories** at about.gitlab.com/customers. Only for question 8.
7. **Press coverage.** Only for events the company hasn't filed on, and the line must say it's press.

## How to fetch

- SEC EDGAR needs a User-Agent header with a contact. The submissions index is `https://data.sec.gov/submissions/CIK##########.json` (10-digit CIK).
- Cite the filing's EDGAR document URL, its form type, and its filing date.
- Record the date you accessed every source.

## What counts as support

- The source says it in substance. A paraphrase is fine; a stretch is not.
- Numbers match the source exactly, with the period they cover.
- If the latest filing is more than 12 months old, say so in the snapshot line.
- If two sources disagree, hold the line under "Held" and show both.
