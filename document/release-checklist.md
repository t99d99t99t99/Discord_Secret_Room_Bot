# CommunityNoticeBot release checklist

Complete this checklist for each release that changes configuration, notices,
relay stories, game rewards, localization, or migration behavior. Record the
release version, reviewer, date, test-guild ID, and evidence links in the
deployment record; do not add tokens, database URLs, or Discord IDs to this
repository.

## Automated gate

- [ ] Run `python3 -m unittest discover -s tests -v` with the project
  dependencies installed.
- [ ] Confirm the locale-catalog test passes: Korean and English have exactly
  the same keys and template placeholders.
- [ ] Run `git diff --check`.

## Copy review

- [ ] An owner has reviewed and approved the Korean catalogs and Korean guides.
- [ ] An owner has reviewed and approved the English catalogs and English guides.
- [ ] Follow the [localization contribution and review process](localization-review.md)
  and record the Korean/English copy reviewer and approval date.
- [ ] Review new command metadata in Discord in both languages after command
  synchronization.

## Clean-server exercise

- [ ] Invite the bot to a new test server with no server-specific environment
  variables beyond token/database configuration.
- [ ] Run `/admin setup start`, choose each language in turn, and confirm
  `/admin setup status` plus member game help have no missing/internal keys.
- [ ] Create a queue, open submissions, submit, publish, and inspect the audit
  record. Repeat a failed/retried publication and verify no duplicate post or
  reward.
- [ ] Create two relay stories and confirm contribution turns and rewards do not
  cross story boundaries.
- [ ] Remove a required channel permission, verify the recoverable diagnostic,
  restore it, and retry safely.

## Existing-server migration and recovery

- [ ] Take and retain a restorable PostgreSQL backup before cutover.
- [ ] Compare `/admin diagnostics migration` with the private manifest; resolve
  every mismatch before enabling the generalized scheduler.
- [ ] Observe one Tomak daily cycle and one Kyohoon weekly cycle, recording one
  publication, one final reward outcome, contributor DM, and public-thread
  notification for each.
- [ ] Rehearse recovery from a backup and an interrupted job in staging.
- [ ] Verify game migration row counts/world JSON against migration evidence;
  never overwrite `si_*` records.

## Retirement decision

Legacy names, command handling, scheduler paths, and environment configuration
may be removed only after every applicable item above has recorded evidence and
the deployment owner approves retirement. Keep the migration recovery procedure
available until that decision is complete.
