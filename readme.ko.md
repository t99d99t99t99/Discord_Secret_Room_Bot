# CommunityNoticeBot 관리자 시작 안내

CommunityNoticeBot은 구성원이 제출한 글을 정해진 시간에 게시하는 Discord 봇입니다.
공지 대기열, 릴레이 이야기, 선택 기능인 **Essence Foundry**는 서버별로 독립 구성합니다.

기존 `비밀 방` 배포는 전환 기간에 현재 이름과 동작을 유지합니다. 이 이름들은 제품의
기본값이 아니라 기존 서버에서 가져오는 구성입니다.

## 처음 15분

1. Discord 개발자 포털에서 봇 애플리케이션을 만들고 `applications.commands` 범위로 초대합니다.
2. 배포에는 `DISCORD_TOKEN`과 `DATABASE_URL`만 설정합니다. 새 서버에서는 채널·스레드·역할 ID가 필요 없습니다.
3. 봇을 시작한 뒤 대상 서버에서 `/admin setup start`를 실행합니다.
4. 필요에 따라 `/admin setup set-audit-channel`, `/admin setup set-admin-role`,
   `/admin setup set-moderators`를 실행합니다.
5. `/admin setup status`를 확인한 뒤, 필요한 공지 대기열·릴레이 이야기·게임만 구성하거나 활성화합니다.
6. `/admin queue create`로 첫 기본 대기열을 만든 뒤 해당 대기열에
   `/admin queue open-submissions`를 실행합니다. 첫 대기열을 만들면 설정이
   완료되며, 감사 채널과 선택 사항인 게임은 비활성으로 두어도 됩니다.

서버 소유자, **서버 관리** 권한 보유자, 또는 지정한 관리자 역할만 `/admin`을 사용할 수 있습니다.
권한 거부는 호출자에게만 표시됩니다.

## 관리자 명령

관리 작업은 슬래시 명령을 사용합니다. 단, Discord 슬래시 명령은 답장한 메시지를 읽을 수
없으므로 검수자는 적격 공지 신청 또는 릴레이 기여에 답장하여 다음 명령을 사용할 수 있습니다.

```text
!moderate reject|disqualify|skip [사유]
```

봇은 같은 검수 권한 정책을 확인하고, 처리 후 검수 명령을 삭제합니다. 결과는 감사 채널로
보내며 감사 채널이 없으면 권한이 있는 검수자에게 DM으로 알립니다.

| 명령 | 용도 |
| --- | --- |
| `/admin setup start`, `status` | 안내형 설정을 시작하고 언어·검수 정책·선택 기능 상태를 확인합니다. |
| `/admin queue create`, `list`, `view` | 일일·주간·수동 공지 대기열을 만들고 상태를 확인합니다. |
| `/admin queue configure`, `configure-submissions`, `set-channels` | 일정, 게시 서식, 신청 기간·규칙, 채널을 설정합니다. |
| `/admin queue open-submissions`, `pause`, `resume`, `archive` | 신청 스레드와 대기열 수명주기를 관리합니다. |
| `/admin queue publish-now`, `retry`, `moderate` | 즉시 게시, 중단 작업 재시도, 적격 신청 검수를 수행합니다. |
| `/admin relay create`, `list`, `view`, `edit`, `configure-posting` | 독립 릴레이 이야기의 채널과 규칙을 관리합니다. |
| `/admin relay pin-rules`, `pause`, `resume`, `remove`, `moderate` | 규칙 고정, 이야기 수명주기, 기여 검수를 수행합니다. |
| `/admin game status`, `enable`, `disable`, `set-name` | 선택 게임의 상태·이름을 관리하며 진행도는 보존합니다. |
| `/admin game reward-policy`, `reward-review`, `reward-rollback`, `reward-reconciliations` | 기능별 보상과 보상 조정 기록을 관리합니다. |
| `/admin diagnostics migration`, `reward` | 마이그레이션 상태와 제출 메시지의 보상 기록을 확인합니다. |

중요한 대기열 작업은 확인 버튼을 요구하고 데이터베이스 감사 로그에 남습니다. 감사 대상이
설정되어 있으면 짧은 감사 알림도 보냅니다.

## 기본 대기열 만들기

`/admin queue create`에서 일일 또는 주간 일정을 고르고, 같은 서버의 신청 채널과 게시 채널을 선택합니다.
그다음 `/admin queue open-submissions`로 신청 스레드를 열고, `/admin queue view`로 다음 게시 시각과
적격 신청 수를 확인합니다. 게시 실패나 권한 오류는 `/admin queue retry`로 복구할 수 있습니다.

명령 전체 목록과 답장 기반 검수 방법은 [관리자 명령어 참고](admin_commands.ko.md)를 참조하세요.

## 문제 해결과 운영

- `/admin`이 보이지 않으면 봇을 `applications.commands` 범위로 다시 초대하고, 봇이 채널을 볼 수 있는지 확인합니다.
- 채널을 선택할 수 없거나 게시가 실패하면 같은 서버의 채널인지, 봇에 채널 보기·메시지 보내기·기록 보기·공개 스레드 만들기 권한이 있는지 확인합니다.
- 예약 게시가 중단되면 `/admin queue view`에서 마지막 결과를 보고 문제를 고친 뒤 `/admin queue retry`를 실행합니다. 재시도는 중복 게시를 만들지 않습니다.
- 보상 분쟁이나 삭제된 기여는 `/admin game reward-review`와
  `/admin game reward-reconciliations`에서 확인합니다. 스냅샷 복구는 보상 이후의 진행도도 함께 되돌립니다.

## 개발

`requirements.txt`의 의존성을 설치하고 `DISCORD_TOKEN`, `DATABASE_URL`을 설정한 뒤
`main.py`를 실행합니다. 기존 게임 상세 안내는
[document/secretae-incremental.md](document/secretae-incremental.md)에 남아 있습니다.

## 마이그레이션 안내

저장소의 제품 이름은 **CommunityNoticeBot**입니다. 이 브랜치를 배포할 때 원격 저장소 이름을
바꾸고 가능한 공개 문서 경로에는 리디렉션을 추가합니다. 기존 `비밀 방`, `오늘의 토막상식`,
`이 주의 교훈`, `Secretae Incremental` 표기는 가져온 서버 구성으로 유지됩니다. 전환 전에는
[레거시 마이그레이션 복구 절차](document/migration-recovery.ko.md)를 따르세요.

## 문제 해결

- `/admin`이 보이지 않으면 봇을 `applications.commands` 범위로 다시 초대하고 Discord 명령
  동기화 시간을 기다립니다. 봇이 해당 채널을 볼 수 있는지도 확인합니다.
- 채널을 선택할 수 없거나 게시가 실패하면 같은 서버의 채널인지, 봇에 채널 보기·메시지 보내기·
  기록 보기·공개 스레드 만들기 권한이 있는지 확인합니다.
- 예약 게시가 중단되면 `/admin queue view`에서 마지막 결과를 보고 문제를 고친 뒤
  `/admin queue retry`를 실행합니다. 영속 작업 기록이 재시도 중 중복 게시를 막습니다.
- 보상 분쟁이나 삭제된 기여는 `/admin game reward-review`와
  `/admin game reward-reconciliations`에서 확인합니다. 스냅샷 복구는 보상 이후의 진행도도
  함께 되돌리며, 공유 월드 변경은 기록된 수동 조정 절차를 따릅니다.

## 업그레이드, 비활성화, 제거

업그레이드 전에는 데이터베이스를 백업합니다. 업데이트 후 봇을 한 번 실행하여 스키마 마이그레이션을 적용하고,
`/admin diagnostics migration` 및 `/admin setup status`를 확인합니다. 대기열·릴레이는 각 수명주기 명령으로
비활성화 또는 보관할 수 있으며 기록은 유지됩니다. `/admin game disable`은 게임 진행도를 지우지 않고 새 게임 명령과
보상만 중지합니다. 봇을 제거하기 전에는 활성 기능을 보관/비활성화하고 필요한 감사·구성 기록을 내보낸 뒤
봇 토큰을 폐기합니다. 운영 전환·복구는 [마이그레이션 복구 절차](document/migration-recovery.ko.md)를 따르세요.

## 언어와 문구 검토

`/admin setup set-language`로 서버 기본 언어를 선택합니다. 저장된 선택이 없으면 Discord 로캘을 사용하고,
확인할 수 없으면 영어로 표시합니다. 한국어와 영어 메시지 카탈로그는 동일 키와 서식을 자동 검증합니다.
릴리스 전에는 소유자가 두 언어의 카탈로그와 관리자·게임 안내를 모두 검토하고 승인해야 합니다.

영문 안내는 [readme.md](readme.md), 게임 안내는
[한국어](document/secretae-incremental.md) 및 [English](document/secretae-incremental.en.md)로 제공합니다.

배포 전에는 [릴리스 점검표](document/release-checklist.ko.md)를 사용하세요.
