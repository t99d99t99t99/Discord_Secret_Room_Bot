# CommunityNoticeBot 관리자 명령어 참고

관리 작업은 답장 전용 `!moderate`를 제외하고 `/admin` 슬래시 명령으로 수행합니다.
서버 소유자, **서버 관리** 권한 보유자, 또는 `/admin setup set-admin-role`로 지정한 역할이
실행할 수 있습니다. 권한 거부는 비공개로 표시됩니다.

| 명령 | 설명 |
| --- | --- |
| `/admin setup start`, `status` | 안내형 설정을 시작하거나 현재 구성과 비활성 선택 기능을 확인합니다. |
| `/admin setup set-language` | 서버 기본 언어를 영어 또는 한국어로 선택합니다. |
| `/admin setup set-admin-role`, `set-audit-channel`, `set-moderators` | 관리자 역할, 감사 대상, 검수 권한 정책을 설정합니다. |
| `/admin queue create`, `list`, `view` | 일일·주간·수동 공지 대기열을 만들고 상태를 확인합니다. |
| `/admin queue edit`, `configure`, `set-channels`, `configure-submissions` | 표시 이름, 일정, 채널, 신청 규칙을 변경합니다. |
| `/admin queue open-submissions`, `pause`, `resume`, `archive` | 신청 스레드를 열거나 대기열 수명주기를 변경합니다. |
| `/admin queue publish-now`, `retry`, `moderate` | 즉시 게시, 중단 작업 재시도, 신청 검수를 수행합니다. |
| `/admin relay create`, `list`, `view`, `edit`, `configure-posting` | 독립 릴레이 이야기를 만들고 채널·규칙을 관리합니다. |
| `/admin relay pin-rules`, `pause`, `resume`, `remove`, `moderate` | 규칙 고정, 수명주기 관리, 기여 검수를 수행합니다. |
| `/admin game status`, `enable`, `disable`, `set-name` | 선택 게임의 상태·이름을 관리하며 진행도는 유지합니다. |
| `/admin game reward-policy`, `reward-review`, `reward-rollback` | 기능별 보상 정책을 설정·검토하고 지원되는 스냅샷을 복구합니다. |
| `/admin game reward-reconciliations`, `resolve-reconciliation` | 삭제·부적격 기여의 보상 조정 기록을 검토·완료합니다. |
| `/admin diagnostics migration`, `reward` | 마이그레이션 증적 또는 제출 메시지의 보상 기록을 확인합니다. |

일정 변경, 즉시 게시, 보상 재시도, 전체 복구에는 확인 버튼이 필요합니다. 모든 구성 및 운영 변경은
감사 로그에 남고, 감사 대상이 설정되어 있으면 그곳에도 알림을 보냅니다.

## 답장 기반 검수

슬래시 명령은 답장한 메시지를 읽을 수 없으므로, 적격 공지 신청 또는 릴레이 기여에 답장하여 다음을 보냅니다.

```text
!moderate reject [사유]
!moderate disqualify [사유]
!moderate skip [사유]
```

이 명령은 설정된 검수자와 관리자만 사용할 수 있습니다. 성공 시 봇은 검수 명령을 삭제하고 결과를
감사 대상으로 보냅니다. 감사 대상이 없으면 설정·권한 기반 검수자에게 DM으로 알립니다.
