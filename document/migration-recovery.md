# Legacy notice and relay migration recovery

This procedure applies only to the existing `비밀 방` deployment. New servers do
not use legacy environment IDs or legacy scheduler cogs.

## Before cutover

1. Take a PostgreSQL backup and keep the deployment version that still loads the
   legacy scheduler cogs available for rollback. Do not run both versions at the
   same time.
2. Record the legacy environment values for `SECRET_ROOM_SERVER_ID`,
   `TOMAK_SUBMISSION_ID`, `TOMAK_NOTIFY_ID`, `KYOHOON_SUBMISSION_ID`,
   `KYOHOON_NOTIFY_ID`, and `RELAY_STORY_ID` in the deployment manifest.
   Treat these as deployment secrets; do not paste them into public Discord
   messages or this repository.
3. Start the generalized bot once with those values. It imports records
   idempotently and the old scheduler cogs remain unloaded.
4. Run `/admin diagnostics migration` in the legacy guild. It must report
   `일치`; investigate any channel, schedule, or imported relay-reward count
   mismatch before the next scheduled cycle.

## Acceptance window

Observe at least one normal Tomak daily cycle and one Kyohoon weekly cycle.
For each, confirm exactly one publication thread, one legacy reward record, the
legacy-format contributor DM, and the legacy-format public thread notification.
The queue job's idempotency key and the legacy reward tables prevent duplicate
publication/reward when a scheduled task is retried.

## Recovery

If verification fails, pause the affected queue with `/admin queue pause`, keep
the generalized bot as the only running scheduler, and preserve the audit/job
records. Correct channel/schedule configuration with `/admin queue set-channels`
or `/admin queue configure`, then use `/admin queue retry` only for the selected
unpublished source message. Never manually re-run a reward for an already
published message.

If a code rollback is necessary, first stop the generalized bot, restore the
database backup if its generalized records must be removed, then start only the
known legacy deployment. Running both schedulers concurrently is unsafe.

Deleted or disqualified rewarded relay contributions create a pending entry in
`/admin game reward-reconciliations`. Review its linked configured reward ledger
and use `/admin game reward-rollback` only when restoring the recorded snapshot
is acceptable; it also restores later progress for that player. Legacy and
shared-world reward changes require an administrator's documented manual
adjustment, followed by `/admin game resolve-reconciliation` to preserve the
audit trail.

## Guild-scoped game verification

Before enabling the migrated game for normal use, retain the backup reference
and compare the legacy guild's `guild_game_*` player, secret, organic, alert,
relay-reward, and world-state rows with the immutable `si_*` source and
`game_migration_evidence` checksum. Recovery may remove only newly created
guild-scoped rows or restore the backup; it must never overwrite `si_*` data.
