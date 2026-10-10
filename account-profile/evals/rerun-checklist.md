# Re-run checklist

Use after any change to the skill, its references, or the model behind it. Re-run all three samples in `samples/` and compare each new draft with the saved one.

## Automatic (run `python3 evals/check_profile.py <new drafts>`)

1. All eight question sections present, in handbook order.
2. Every line in questions 1 to 6 cites a source ID, and every ID is in the Sources table.
3. Every source has an https URL and an accessed date.
4. One or two value drivers, using only the four handbook names, each with a citation.
5. Question 8 claims a GitLab relationship only when it cites a public GitLab customer story.
6. No personal names (role-plus-name patterns).
7. Three to five questions for the champion.

## By hand (about 10 minutes per sample)

8. **Spot-check five fact lines** against their sources. Any wrong line fails the run.
9. **Same company, same story.** The snapshot, top goals, and value drivers should match the saved sample unless a newer filing explains the change.
10. **Gaps stay gaps.** Question 7 still says "Not in public sources" unless a source says it directly, and nothing new appears in question 8 without a customer story.
11. **No new invented precision.** New numbers, dates, or percentages must each trace to a source.
12. **Read lines stay labeled.** Inference hasn't crept into fact lines.

## Record

Write the date, the model, pass or fail per item, and anything that changed in `evals/RESULTS.md`.
