<a href="getting-started.md"><kbd>English</kbd></a> <a href="getting-started.ko.md"><kbd>한국어</kbd></a>

# Getting started with CommunityNoticeBot

CommunityNoticeBot helps a Discord community run scheduled, member-submitted
notices. Notice queues, relay stories, and the optional **Essence Foundry** game
are configured independently for each server.

Existing `비밀 방` deployments retain their established names and behaviour during
the migration period. Those names are configuration, not product defaults.

## Initial setup

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

See the [administrator command reference](../admin_commands.md) for setup,
queue, relay, game, and diagnostic commands, including reply-based moderation
and confirmation/audit behavior.

## Running and developing the bot

The [codebase guide](codebase.md) explains installation, startup, module
responsibilities, data flows, and development checks.

## Migration note

The repository identity is **CommunityNoticeBot**. Rename the remote repository
and add redirects for any public documentation paths when deploying this branch.
The migration keeps existing `비밀 방`, `오늘의 토막상식`, `이 주의 교훈`, and
`Secretae Incremental` labels as imported server configuration.
See [the legacy migration recovery procedure](migration-recovery.md)
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
and restoration steps are in [the migration recovery procedure](migration-recovery.md).

## Languages and copy review

The server language is chosen with `/admin setup set-language`. Runtime replies,
DMs, and member-specific help use the configured language; before a guild has a
saved choice, Discord's locale is used when available and English is the safe
fallback. Korean and English catalogs are validated for matching message keys
and placeholders by `tests/test_localization.py`. Release owners must review and
approve both catalogs and both versions of the administrator/game guides before
release.

Use the [release checklist](release-checklist.md) before deployment.
