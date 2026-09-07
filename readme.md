# CommunityNoticeBot

CommunityNoticeBot helps a Discord community run scheduled, member-submitted
notices. Notice queues, relay stories, and the optional **Essence Foundry** game
are configured independently for each server.

Existing `비밀 방` deployments retain their established names and behaviour during
the migration period. Those names are configuration, not product defaults.

## First 15 minutes

1. Create a Discord bot application, invite it with the `applications.commands`
   scope, and give it the channel permissions required by the features you enable.
2. Configure only the deployment secrets: `DISCORD_TOKEN` and `DATABASE_URL`.
   No Discord server, channel, thread, or role ID is required for a fresh setup.
3. Start the bot, then run `/admin setup start` in the target server.
4. Use `/admin setup set-audit-channel` (recommended),
   `/admin setup set-admin-role` (optional), and
   `/admin setup set-moderators` as needed.
5. Check `/admin setup status`. Community features and Essence Foundry begin
   disabled; enable/configure only the features your community wants.
6. Create the first basic queue with `/admin queue create`, then run
   `/admin queue open-submissions` for that queue. Creating the first queue
   completes setup; an audit channel and the optional game can stay disabled.

The server owner, members with **Manage Server**, and the configured
administrator role can use `/admin`. Permission denials are private.

## Administrator commands

Administrative workflows use slash commands except reply-based moderation.
Discord does not provide the replied-to message to slash commands, so moderators
may reply to an eligible queue submission or relay contribution with
`!moderate reject|disqualify|skip [reason]`. The bot checks the same moderator
policy, deletes the moderator command after processing, and sends the result to
the audit channel (or DMs eligible moderators when no audit channel is set).

| Command | Purpose |
| --- | --- |
| `/admin setup start` | Start or resume guided configuration. |
| `/admin setup status` | See setup state, language, moderation policy, and optional features. |
| `/admin setup set-language` | Set English or Korean as the server default. |
| `/admin setup set-admin-role` | Set or remove the additional administrator role. |
| `/admin setup set-audit-channel` | Set or remove the optional audit destination. |
| `/admin setup set-moderators` | Set disabled, Manage Messages, or one-role moderation. |
| `/admin queue create` | Create a daily, weekly, or manual notice queue. |
| `/admin queue list` / `view` | Inspect queue configuration, eligible entries, and last result. |
| `/admin queue configure` / `set-channels` | Change policy/schedule or redirect channels. |
| `/admin queue configure-submissions` | Configure content rules, submission-window duration, edits, links, attachments, and empty-queue handling. |
| `/admin queue open-submissions` | Create a submission thread for a queue. |
| `/admin queue pause` / `resume` / `archive` | Control a queue without erasing its history. |
| `/admin queue publish-now` / `retry` | Safely publish an entry or make an interrupted entry eligible again. |
| `/admin queue moderate` | Reject, disqualify, or skip an eligible entry. |
| `/admin relay create` | Create an optional, dedicated relay-story channel or thread. |
| `/admin relay list` / `view` | Inspect configured stories, contribution state, and rules. |
| `/admin relay edit` / `configure-posting` | Change a story's name/channel/rules or contribution policy. |
| `/admin relay pin-rules` | Pin administrator-supplied story rules. |
| `/admin relay pause` / `resume` / `remove` | Control a story without deleting its contribution history. |
| `/admin relay moderate` | Delete and disqualify a contribution. |
| `/admin game status` | Inspect game enablement, name, and configured reward sources. |
| `/admin game enable` / `disable` | Enable or disable the optional game without deleting progress. |
| `/admin game set-name` | Set the server-facing game name; fresh servers default to Essence Foundry. |
| `/admin game reward-policy` | Set none, essence/secret/production multiplier, or fixed-essence policy; optional weekly cap is 1–7 grants. |
| `/admin game reward-rollback` | Restore an eligible reward recipient to its pre-reward snapshot. |
| `/admin game reward-review` | Inspect a configured reward, its snapshot-retention state, and repair options. |
| `/admin game reward-reconciliations` / `resolve-reconciliation` | Review and record the outcome of deletion/disqualification reward cases. |
| `/admin diagnostics reward` / `migration` | Inspect a reward record or compare imported legacy configuration and evidence. |

Consequential queue actions require a confirmation button and are recorded in the
database audit log. When an audit destination is configured, a short audit notice
is posted there as well.

## Development

Install dependencies from `requirements.txt`, set `DISCORD_TOKEN` and
`DATABASE_URL`, then run `main.py`. The legacy Incremental-game tutorial is kept
at [document/secretae-incremental.md](document/secretae-incremental.md).

## Migration note

The repository identity is **CommunityNoticeBot**. Rename the remote repository
and add redirects for any public documentation paths when deploying this branch.
The migration keeps existing `비밀 방`, `오늘의 토막상식`, `이 주의 교훈`, and
`Secretae Incremental` labels as imported server configuration.
See [the legacy migration recovery procedure](document/migration-recovery.md)
before cutover.

## Troubleshooting

- **`/admin` is not visible:** reinvite the bot with the
  `applications.commands` scope, then allow Discord time to synchronize command
  registrations. Confirm that the bot can view the channel.
- **Setup or a queue cannot use a channel:** choose a channel in the same
  server and grant the bot View Channel, Send Messages, Read Message History,
  and Create Public Threads where threads are used.
- **A scheduled publication is waiting for attention:** use `/admin queue view`
  to inspect the last result, correct the channel or permission issue, then use
  `/admin queue retry`. The durable job record prevents a retry from posting a
  second notice.
- **A reward is disputed or a contribution is removed:** inspect `/admin game
  reward-review` and `/admin game reward-reconciliations`. A snapshot rollback
  restores the recipient's entire state to before the reward, including later
  progress; shared-world changes instead require the recorded manual
  reconciliation procedure.

## Upgrades, disabling, and removal

Take a database backup before upgrading. Deploy the new application version,
run it once so additive schema migrations finish, and check `/admin diagnostics
migration` and `/admin setup status` before enabling changed schedules.

Disable a queue or relay story with its `/admin` lifecycle command; its history
is retained. Disable Essence Foundry with `/admin game disable`; player progress
is retained and no new game commands or community rewards are issued. Before
removing the bot, archive/disable active features, export the audit and
configuration records you need, and revoke the bot token. The legacy cutover
and restoration steps are in [the migration recovery procedure](document/migration-recovery.md).

## Languages and copy review

The server language is chosen with `/admin setup set-language`. Runtime replies,
DMs, and member-specific help use the configured language; before a guild has a
saved choice, Discord's locale is used when available and English is the safe
fallback. Korean and English catalogs are validated for matching message keys
and placeholders by `tests/test_localization.py`. Release owners must review and
approve both catalogs and both versions of the administrator/game guides before
release.

Korean onboarding is available in [readme.ko.md](readme.ko.md); the game guides
are available in [Korean](document/secretae-incremental.md) and
[English](document/secretae-incremental.en.md).

Use the [release checklist](document/release-checklist.md) before deployment.
