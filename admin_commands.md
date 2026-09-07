# CommunityNoticeBot administrator command reference

Administrative actions are slash commands under `/admin`, apart from the
reply-only `!moderate` workflow described below. The server owner, members with **Manage Server**, or a role set with
`/admin setup set-admin-role` may run them. Failed authorization checks are
private.

| Command | Description |
| --- | --- |
| `/admin setup start` | Begin guided setup. Uses the server locale when Discord provides it. |
| `/admin setup status` | Show setup status and disabled optional features. |
| `/admin setup set-language` | Choose the default English or Korean locale. |
| `/admin setup set-admin-role` | Configure an extra administrator role. |
| `/admin setup set-audit-channel` | Configure the optional audit destination. |
| `/admin setup set-moderators` | Configure disabled, Manage Messages, or role-based moderation. |
| `/admin queue create` | Create a daily, weekly, or manual notice queue. |
| `/admin queue list`, `view` | List queues or inspect one queue's health and configuration. |
| `/admin queue edit`, `configure`, `set-channels` | Change display, policy/schedule, or channel settings. |
| `/admin queue configure-submissions` | Set submission windows and content/edit/link/attachment policy. |
| `/admin queue open-submissions` | Create a submission thread. |
| `/admin queue pause`, `resume`, `archive` | Change lifecycle state while retaining records. |
| `/admin queue publish-now`, `retry` | Manually publish or retry an interrupted submission. |
| `/admin queue moderate` | Reject, disqualify, or skip an eligible entry. |
| `/admin relay create`, `list`, `view` | Create and inspect independent relay stories. |
| `/admin relay edit`, `configure-posting` | Change a story's channel, display, rules, and participation policy. |
| `/admin relay pin-rules` | Pin administrator-supplied rules in the story channel. |
| `/admin relay pause`, `resume`, `remove` | Change lifecycle state while retaining contribution history. |
| `/admin relay moderate` | Delete and disqualify an eligible contribution. |
| `/admin game status` | Inspect optional-game state and reward sources. |
| `/admin game enable`, `disable` | Enable/disable the game without removing saved progress. |
| `/admin game set-name` | Change the server-facing game name. |
| `/admin game reward-policy` | Configure a typed policy and optional 1–7 weekly grant cap for a notice queue or relay story. |
| `/admin game reward-rollback` | Confirm and restore a supported reward snapshot; later player progress is also restored. |
| `/admin game reward-review` | Inspect a configured reward and whether its rollback snapshot remains available. |
| `/admin game reward-reconciliations`, `resolve-reconciliation` | Review and record the outcome of reward cases created by deleted/disqualified contributions. |
| `/admin diagnostics reward` | Find a reward record by submission message ID. |
| `/admin diagnostics migration` | Compare imported legacy configuration and evidence with the deployment manifest. |

Schedule changes, immediate publication, and reward retries require confirmation.
Every configuration and operational change writes an audit-log entry; the bot also
notifies the configured audit destination when it can post there.

## Reply-based moderation

Discord slash commands cannot inspect a replied-to message. To moderate without
copying a message ID, reply to an eligible queue submission or relay contribution:

```text
!moderate reject [reason]
!moderate disqualify [reason]
!moderate skip [reason]
```

The command is limited to configured moderators and administrators. The bot
deletes the moderation command after a successful action, records the result,
and reports it to the audit destination; if no audit destination is configured,
it DMs the configured/permission-based moderators instead.
