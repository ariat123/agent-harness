# Output template

Keep the headings exactly as written. `evals/check_profile.py` looks for them.

```markdown
# Account Profile: <Company> (<Exchange>: <Ticker>)

Draft for AE review. Built <YYYY-MM-DD> from public sources only. Data tier: Green.

**Snapshot:** <one or two lines: what the company does, latest fiscal-year revenue and headcount, fiscal year end> [S1]

## 1. Goals and strategic initiatives
- <claim> [S1]
- Read: <your reading of the facts> [S1, S3]

**Value drivers most likely to matter**
1. <exact value driver name>: <one-line reason> [S#]
2. <exact value driver name>: <one-line reason> [S#]

## 2. Macro factors
- <claim> [S#]

## 3. Competitors and share
- <claim> [S#]

## 4. Biggest risks
- Short term: <claim> [S#]
- Medium term: <claim> [S#]
- Long term: <claim> [S#]

## 5. How IT supports or hinders the strategy
- <claim> [S#]

## 6. Digital transformation strategy
- <claim> [S#]

## 7. How IT is perceived
- Not in public sources.
- Ask: <one discovery question>

## 8. Current GitLab relationship
- <from a public GitLab customer story, with its source ID> OR AE to fill from Salesforce.

## Gaps to close with the account team
- <what the team still needs to learn>

## Questions to confirm with your champion
1. <question that tests the draft's biggest assumption>

## Held
- None. OR <claim A> [S#] vs <claim B> [S#]

## Sources
| ID | Source | Published | URL | Accessed |
|---|---|---|---|---|
| S1 | <Company> Form 10-K, fiscal <year> | YYYY-MM-DD | https://... | YYYY-MM-DD |
```

## Style

- Plain words an AE would say out loud on a call.
- One claim per line. Two or three lines per question. About 750 words before Sources.
- Numbers with their period ("fiscal 2025 revenue of $X").
- No personal names. Use roles.
