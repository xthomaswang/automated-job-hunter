# Changelog

What changes for people who run a feed. Bring a feed repo up to date with
`scripts/hunter.py --repo <feed repo> upgrade` from the skill's copy, then commit.

## Unreleased

### Unmet requirements (assessment rubric `assess-v2`)

- Each assessment now lists the mandatory requirements the profile does not meet, of four kinds:
  a degree, current enrollment, a credential or a license. Each comes with the requirement quoted
  from the posting. A requirement that is only preferred, or that equivalent experience can
  replace, is not listed. The list is `unmet` in `positions.jsonl`, an "Unmet requirements"
  section in the posting's brief, and an "unmet <kind>" flag in the README.
- New setting `thresholds.unmet_requirements` in `profile/criteria.json`, for example
  `["degree", "enrollment"]`. It leaves out postings with an unmet requirement of those kinds, in
  the same way as `max_required_years` and `max_seniority`: they are counted under `blocked`, a
  listed posting it takes out gets a `blocked` event, and removing a kind sends the postings it
  left out back to be assessed. It is off by default and judges nothing again.
- The assessment can only tell from what `profile/profile.md` says, so the profile should state
  the person's degrees and fields, whether they are enrolled now, and any certifications or
  licenses.
- **Nothing is assessed again by upgrading.** Postings assessed before this version have no
  `unmet` field and stay listed, unchecked; `judged.rubric` says which rubric assessed a posting.
  To check them, run `python3 .hunter/engine/hunter.py requeue --assessed` once in the feed repo,
  then commit `state/`. It prints the estimated cost, about $2 for every hundred assessed postings,
  and the work is spread over the next updates. If you are editing `profile/profile.md` anyway,
  skip the requeue: a profile change assesses everything again by itself. Consider leaving
  `unmet_requirements` empty until those assessments have arrived: the unmet requirements are then
  only flagged, so you can check the model's claims against their quotes before any posting is
  left out. Setting the kinds afterwards judges nothing again.
- `hunter apply`'s `blocked` count no longer counts a listed posting twice when it is assessed
  again and left out.
- The `blk` tag in `state/` now records every fact a hard requirement checks, such as
  `blk:years=7+seniority=senior+unmet=degree`. Relaxing one requirement then sends back only the
  postings that no other requirement still leaves out. Tags written by earlier versions still work.
