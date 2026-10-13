<a href="admin_commands.md"><kbd>English</kbd></a> <a href="admin_commands.ko.md"><kbd>한국어</kbd></a>

# CommunityNoticeBot administrator command reference

This guide documents the arguments accepted by every `/admin` command. Discord
may display localized command names (for example, `/관리` on a Korean client),
but the command structure and argument meanings are the same.

The server owner, members with **Manage Server**, and members with the configured
administrator role may use administrator commands. Queue and relay moderation
can also be delegated through the moderation settings. Permission failures and
most command responses are visible only to the person who ran the command.

## Setup

### `/admin setup start`

No arguments. Starts or resumes setup and selects the initial server language
from the server's Discord preferred locale (Korean when it starts with `ko`,
English otherwise). It does not create queues or enable optional features.

### `/admin setup status`

No arguments. Shows the saved setup state, server language, moderation policy,
game enablement, and whether an administrator role or audit channel is missing.

### `/admin setup set-language`

| Argument | Required | Accepted values and effect |
| --- | --- | --- |
| `locale` | Yes | Choose `English` (`en`) or `한국어` (`ko`). Sets the server's default language for bot responses. |

### `/admin setup set-admin-role`

| Argument | Required | Accepted values and effect |
| --- | --- | --- |
| `role` | No | Select a role in this server to grant bot-administrator access. Omit it to remove the configured role. The server owner and members with Manage Server remain administrators. |

### `/admin setup set-audit-channel`

| Argument | Required | Accepted values and effect |
| --- | --- | --- |
| `channel` | No | Select a text channel or thread in this server for audit notices. Omit it to clear the configured destination. Bot permissions may prevent delivery even when the destination is saved. |

### `/admin setup set-moderators`

| Argument | Required | Accepted values and effect |
| --- | --- | --- |
| `policy` | Yes | `Disabled` (`disabled`), `Manage Messages permission` (`manage_messages`), or `One role` (`role`). Controls who can moderate queue submissions and relay contributions. |
| `role` | No | Required when `policy` is `One role`; choose the server role to delegate moderation to. It is cleared for the other policies. |

## Notice queues

Queue IDs are shown by `/admin queue list`. Use the ID for the queue you want to
change. Channels must belong to this server, must be different submission and
publication destinations, and cannot already be assigned to another queue or
relay story. The bot needs View Channel, Send Messages, Read Message History,
and Create Public Threads in queue channels. Relay story channels need View
Channel, Send Messages, and Read Message History.

### `/admin queue create`

Creates an active queue and opens submissions only when you later run
`/admin queue open-submissions`.

| Argument | Required | Accepted values and effect |
| --- | --- | --- |
| `name` | Yes | Queue display name. It is also used to derive an internal queue key. |
| `submission_channel` | Yes | Text channel where members submit entries and where submission threads are created. |
| `publication_channel` | Yes | Different text channel where selected entries are published. |
| `cadence` | No | `Daily` (`daily`), `Weekly` (`weekly`), or `Manual only` (`manual`). Defaults to daily. Manual queues are published with `/admin queue publish-now`, not by the scheduler. |
| `schedule_time` | No | Local 24-hour time in `HH:MM`, such as `09:30`. Defaults to `09:00`. Used by daily and weekly queues. |
| `timezone_name` | No | IANA time-zone name, such as `Asia/Seoul` or `America/New_York`. Defaults to `UTC`. |
| `weekday` | No | For a weekly queue, an integer from `0` (Monday) through `6` (Sunday). Required for weekly cadence. |
| `description` | No | Optional explanation shown with queue information. |

### `/admin queue list`

No arguments. Lists queue IDs, display names, lifecycle state, whether
submissions are open, and cadence.

### `/admin queue view`

| Argument | Required | Accepted values and effect |
| --- | --- | --- |
| `queue_id` | Yes | Integer ID from `/admin queue list`. Shows schedule, channels, submission window, selection policy, reward policy, templates, eligible-entry count, and most recent publication status. |

### `/admin queue edit`

Provide one or more optional fields. Omitted fields keep their existing values.

| Argument | Required | Accepted values and effect |
| --- | --- | --- |
| `queue_id` | Yes | Integer ID from `/admin queue list`. |
| `name` | No | New display name. |
| `description` | No | New description. |
| `accepting` | No | `true` to accept submissions in the current open thread; `false` to stop accepting them. This does not create a new thread. |

### `/admin queue configure`

Provide only the settings to change; omitted settings retain their current
values. A weekly queue's `weekday` uses Monday `0` through Sunday `6`.

| Argument | Required | Accepted values and effect |
| --- | --- | --- |
| `queue_id` | Yes | Integer ID from `/admin queue list`. |
| `selection` | No | `Oldest eligible` (`oldest`), `Fair rotation` (`fair`), or `Random` (`random`). Determines which eligible member entry is selected for publication. |
| `per_member_limit` | No | Positive integer, at least `1`. Maximum number of eligible submissions per member in a submission period. |
| `schedule_time` | No | Local 24-hour time `HH:MM`, such as `18:05`. |
| `timezone_name` | No | Valid IANA time-zone name, such as `Asia/Seoul`. |
| `weekday` | No | Integer `0`–`6`, Monday–Sunday. Relevant to weekly queues. |
| `attribution` | No | `Display name` (`display_name`) to show the contributor's display name, or `No attribution` (`none`). |
| `title_template` | No | Non-empty publication title, at most 100 characters. New daily/manual queues default to `{{YYYY}}년 {{MM}}월 {{DD}}일 {{queue_name}}, {{author}}`; weekly queues default to `{{MM}}월 {{week}}주차, {{author}}`. Date tokens use the publication date in the queue's `timezone`. `{{YYYY}}` is the four-digit year, `{{YY}}` the two-digit year, `{{MM}}` the zero-padded month, `{{M}}` the month without padding, `{{DD}}` the zero-padded day, `{{D}}` the day without padding, and `{{week}}` the Sunday number in the month (1–5; weekly queues publish on their configured weekday). `{{queue_name}}` inserts the queue's display name, `{{author}}` the contributor's display name (blank when attribution is disabled), and `{{content}}` the submitted text. |
| `content_template` | No | Non-empty publication body, at most 1,900 characters. Supports all the same date and text placeholders as `title_template`. |
| `submission_title_template` | No | New submission-thread title, at most 100 characters. Supports `{{YYYY}}`, `{{YY}}`, `{{MM}}`, `{{M}}`, `{{DD}}`, `{{D}}`, `{{week}}` (Sunday week of month), and `{{queue_name}}`, using the queue's timezone. |
| `submission_description_template` | No | New submission-thread opening message, at most 1,900 characters. Supports the same date and queue-name placeholders as the submission title. |
| `mention_role` | No | Server role to mention in a separate message after creating the thread. |
| `mention_everyone` | No | Whether to send a separate `@everyone` message after creating the thread. A selected role takes precedence when both are configured. |
| `new_thread_threshold` | No | Integer `0` or greater. After publication, automatically create a new submission thread only when the number of eligible queued submissions is below this value. Set `0` to disable automatic opening. Manual `/admin queue open-submissions` is unaffected. |

Imported legacy defaults preserve the old behavior: 토막상식 uses a date-based
title, its legacy Korean opening text, `@everyone`, and threshold `5`; 교훈 uses
a month/week title, its legacy opening text, `@everyone`, and threshold `1`.

### `/admin queue set-channels`

| Argument | Required | Accepted values and effect |
| --- | --- | --- |
| `queue_id` | Yes | Integer ID from `/admin queue list`. |
| `submission_channel` | Yes | Text channel in this server for submissions. Must differ from the publication channel and be unused by another queue or relay story. |
| `publication_channel` | Yes | Different text channel in this server for published entries. Must be unused by another queue or relay story. |

The change requires confirmation. On confirmation, the old submission thread is
detached and submissions are closed until a new thread is opened.

### `/admin queue configure-submissions`

Omitted numeric and boolean settings keep their current values. Use the clear
switches to remove limits; clearing takes precedence over values supplied in
the same command.

| Argument | Required | Accepted values and effect |
| --- | --- | --- |
| `queue_id` | Yes | Integer ID from `/admin queue list`. |
| `minimum_characters` | No | Integer `0` or greater. Minimum submitted text length. |
| `maximum_characters` | No | Integer `0` or greater, and not below the effective minimum. Values above `2,000` require confirmation. |
| `submission_period_hours` | No | Positive integer. Number of hours a submission window remains open. |
| `allow_attachments` | No | `true` to allow attachments, `false` to disallow them. |
| `allow_links` | No | `true` to allow links, `false` to disallow them. |
| `allow_edits` | No | `true` to allow edits after submission, `false` to reject edited submissions. |
| `no_entry_action` | No | `alert_and_reopen` to alert administrators and open another submission thread, `alert_only` to alert without reopening, or `skip` to skip the scheduled publication. |
| `clear_content_limits` | No | Boolean; defaults to `false`. Set `true` to remove both minimum and maximum character limits. |
| `clear_submission_period` | No | Boolean; defaults to `false`. Set `true` to remove the time limit on a submission window. |

### `/admin queue open-submissions`

| Argument | Required | Accepted values and effect |
| --- | --- | --- |
| `queue_id` | Yes | Integer ID from `/admin queue list`. Creates a new submission thread for the queue and opens it to submissions. |

### `/admin queue pause`, `/admin queue resume`, `/admin queue archive`

Each command takes the same argument:

| Argument | Required | Accepted values and effect |
| --- | --- | --- |
| `queue_id` | Yes | Integer ID from `/admin queue list`. `pause` stops scheduled activity while retaining the queue; `resume` reactivates it; `archive` retires it without deleting its history. |

### `/admin queue publish-now`

| Argument | Required | Accepted values and effect |
| --- | --- | --- |
| `queue_id` | Yes | Integer ID from `/admin queue list`. Publishes the oldest eligible entry now. Requires confirmation. |

### `/admin queue retry`

| Argument | Required | Accepted values and effect |
| --- | --- | --- |
| `message_id` | Yes | Decimal Discord message ID of the original submission, not the publication or thread. The submission must be in `selected` or `failed` state. Makes that entry eligible for another safe attempt. |

### `/admin queue moderate`

| Argument | Required | Accepted values and effect |
| --- | --- | --- |
| `message_id` | Yes | Decimal Discord message ID of an eligible source submission. |
| `action` | Yes | `Reject` (`rejected`), `Disqualify` (`invalid`), or `Skip` (`invalid`). Reject records a rejection. Disqualify and Skip currently have the same stored effect: both mark the entry invalid so it is no longer eligible. |
| `reason` | No | Optional moderation note saved with the action. |

## Relay stories

Story IDs are shown by `/admin relay list`. A relay channel must belong to this
server, be unused by queues and other stories, and allow the bot to view, send,
read history, and create public threads.

### `/admin relay create`

| Argument | Required | Accepted values and effect |
| --- | --- | --- |
| `name` | Yes | Display name for the relay story. |
| `channel` | Yes | Text channel or thread in this server where the story is hosted. |
| `rules` | No | Optional rules text shown to contributors and used by the pin-rules command. |

### `/admin relay list`

No arguments. Lists story IDs, names, channels, and lifecycle states.

### `/admin relay view`

| Argument | Required | Accepted values and effect |
| --- | --- | --- |
| `story_id` | Yes | Integer ID from `/admin relay list`. Shows its channel, state, eligible contribution count, latest contributor, rules, reward policy, and moderator policy. |

### `/admin relay edit`

Provide one or more fields to change. Omitted fields are retained. Changing the
channel asks for confirmation; the new channel must pass the relay-channel checks.

| Argument | Required | Accepted values and effect |
| --- | --- | --- |
| `story_id` | Yes | Integer ID from `/admin relay list`. |
| `name` | No | New display name. |
| `channel` | No | New text channel or thread in this server. It must not be assigned to another queue or story. |
| `rules` | No | Replacement story rules text. |

### `/admin relay pin-rules`

| Argument | Required | Accepted values and effect |
| --- | --- | --- |
| `story_id` | Yes | Integer ID from `/admin relay list`. Pins the configured rules in the story channel. Rules must already be set. |

### `/admin relay configure-posting`

| Argument | Required | Accepted values and effect |
| --- | --- | --- |
| `story_id` | Yes | Integer ID from `/admin relay list`. |
| `require_intervening_contributor` | No | Boolean; defaults to `true`. When true, a contributor must wait until someone else contributes before posting again. `false` allows consecutive contributions by the same person. |
| `maximum_characters` | No | Positive integer maximum contribution length. Omit it to remove the length cap. |

### `/admin relay set-moderators`

| Argument | Required | Accepted values and effect |
| --- | --- | --- |
| `story_id` | Yes | Integer ID from `/admin relay list`. |
| `policy` | Yes | `inherit` to use server-wide moderator settings, `manage_messages` to require Manage Messages permission, or `role` to use one story-specific role. |
| `role` | No | Required for `role`; select the server role allowed to moderate this story. Ignored and cleared for the other policies. |

### `/admin relay pause`, `/admin relay resume`, `/admin relay remove`

Each command takes the same argument:

| Argument | Required | Accepted values and effect |
| --- | --- | --- |
| `story_id` | Yes | Integer ID from `/admin relay list`. `pause` stops new activity; `resume` reactivates the story; `remove` archives it. Removing a story does not delete its contribution history. `remove` requires confirmation. |

### `/admin relay moderate`

| Argument | Required | Accepted values and effect |
| --- | --- | --- |
| `message_id` | Yes | Decimal Discord message ID of an eligible contribution in this server. The bot attempts to delete that message and marks the contribution disqualified. |
| `reason` | No | Optional reason recorded with the moderation and any resulting reward-reconciliation case. |

## Essence Foundry game and community rewards

### `/admin game status`

No arguments. Shows whether the game is enabled, its display name, and queue or
relay reward policies currently configured in this server.

### `/admin game enable`, `/admin game disable`

No arguments. Each action requires confirmation. Enable allows the game to be
used in this server; disable stops new game actions and community rewards but
retains player progress.

### `/admin game set-name`

| Argument | Required | Accepted values and effect |
| --- | --- | --- |
| `name` | Yes | Non-blank display name, 1–100 characters. Leading/trailing whitespace is trimmed. |

### `/admin game reward-policy`

This configures the reward for one queue or relay story. Target IDs are the IDs
from `/admin queue list` or `/admin relay list`. A confirmation button applies
the new policy.

| Argument | Required | Accepted values and effect |
| --- | --- | --- |
| `target_type` | Yes | `notice_queue` or `relay_story`; identifies which feature type `target_id` refers to. |
| `target_id` | Yes | Integer queue or story ID in this server. |
| `policy_type` | Yes | `none`, `essence_multiplier`, `each_secret_multiplier`, `production_multiplier`, or `fixed_essence`. |
| `amount` | Conditional | Decimal number, entered as text. Required except for `none`. Essence and each-secret multipliers must be `1.1`–`2`; production multiplier must be `1`–`3`; fixed essence must be greater than `0`. |
| `first_grant_amount` | No | Optional positive decimal amount for the recipient's first grant. Must be greater than `0`. |
| `weekly_cap` | No | Optional integer `1`–`7`; maximum total grants from this queue or story per Korean calendar week. It is a per-feature cap across all recipients, not a per-member cap. |

`essence_multiplier` multiplies the recipient's current Essence; if they have
zero Essence and `first_grant_amount` is set, that value is used instead.
`fixed_essence` adds `amount` to current Essence, except that a zero-Essence
recipient receives `first_grant_amount` when configured. `each_secret_multiplier`
multiplies every secret amount; `production_multiplier` multiplies every organic
production value. `first_grant_amount` applies only to the two Essence policies.
When `policy_type` is `none`, the other amount/cap fields are not used. A
multiplier of `1` is therefore not a substitute for `none` except for the
production multiplier, where `1` is accepted and leaves production unchanged.

### `/admin game reward-review`

| Argument | Required | Accepted values and effect |
| --- | --- | --- |
| `ledger_id` | Yes | Integer reward-ledger ID from a pending reconciliation record or the deployment's reward/audit records. Shows recipient, source feature, contribution message, policy, state, and snapshot availability. |

### `/admin game reward-rollback`

| Argument | Required | Accepted values and effect |
| --- | --- | --- |
| `ledger_id` | Yes | Integer reward-ledger ID for this server, found in the deployment's reward/audit records or a pending reconciliation. Requires confirmation. Restores the recipient's full saved game state from immediately before that reward; later progress is also rolled back. |

### `/admin game reward-reconciliations`

No arguments. Lists up to 25 pending cases caused by deleted or disqualified
rewarded contributions. Each case includes the case ID needed by
`resolve-reconciliation` and may include a configured reward-ledger ID.

### `/admin game resolve-reconciliation`

| Argument | Required | Accepted values and effect |
| --- | --- | --- |
| `case_id` | Yes | Integer ID of a pending case from `/admin game reward-reconciliations`. |
| `note` | Yes | Non-blank resolution record, at most 500 characters. State what was reviewed or how the case was resolved. Requires confirmation. |

## Diagnostics

### `/admin diagnostics reward`

| Argument | Required | Accepted values and effect |
| --- | --- | --- |
| `message_id` | Yes | Decimal Discord message ID of the original notice submission. Reports whether a legacy reward record exists and, if found, its type, recipient, publication thread, and recorded time. |

### `/admin diagnostics migration`

No arguments. Compares imported notice-queue and relay-story configuration with
the legacy deployment settings. It does **not** verify game-player migration.

## Reply-based moderation

Slash commands cannot identify the message being replied to. Reply to an
eligible queue submission or relay contribution with one of:

```text
!moderate reject [reason]
!moderate disqualify [reason]
!moderate skip [reason]
```

The text after the action is an optional free-form reason. The bot checks the
same server/feature moderator policy, deletes the moderation command after
success, records the decision, and reports to the audit channel. If no audit
channel is configured, it DMs eligible moderators where possible.

## Confirmation and audit behavior

Queue channel changes, scheduled configuration changes, immediate publication,
queue archival, relay channel changes/removal, game enable/disable, reward-policy
changes, reward rollback, and reconciliation resolution ask for confirmation.
Configuration and moderation actions are recorded in the database audit log.
When an audit channel is configured and available to the bot, a notice is also
sent there.
