# 리포트 발송 시간

- 매일 06:00 KST: 글로벌 증시 중심, 미국 3대 지수·환율·금리·원자재·해외 경제 뉴스.
- 매일 18:00 KST: 한국 증시 중심, KOSPI·KOSDAQ·국내 기업·산업 뉴스.
- 주말·휴일도 발송하며, 시세 기준 시각과 실제 거래일을 표시한다.

GitHub Actions 작업을 최대 3시간 먼저 시작해 대기하고, 목표 시각 3분 전 수집·작성을 시작한다. 준비된 리포트는 목표 시각까지 기다렸다가 전송한다. 미국 겨울철 정규장이 06:00 KST에 끝나는 경우 사전 수집 수치는 확정 종가가 아니므로 잠정 시세로 표시한다.

GitHub 예약은 매시 7·27·47분에 실행을 요청한다. 시작이 늦어지면 다음 회차 직전까지 미발송 리포트를 복구한다(오전 회차는 당일 18시 전, 오후 회차는 다음 날 06시 전). 자정을 넘겨도 전날 오후 회차의 발송 이력을 사용한다. 오래된 과거 리포트를 한꺼번에 보내지는 않는다. 모든 사전 예약이 누락되거나 데이터 수집·모델 생성·Telegram 요청이 지연되면 정시 도착을 보장할 수 없다.

Windows 보조 예약은 `scripts/install_report_scheduler.ps1`로 설치한다. 매일 한국시간 05:45·06:05·06:25·17:45·18:05·18:25에 GitHub CLI의 기존 로그인으로 workflow_dispatch를 요청한다. 실행 중인 리포트 작업이 있으면 생략한다. PC가 켜져 있고 해당 사용자가 로그인되어 있어야 하며, PC가 꺼져 있어도 GitHub 자체 예약은 유지된다. 로그는 `%LOCALAPPDATA%/MarketBriefingScheduler/dispatch.log`에 저장한다. 제거는 `Unregister-ScheduledTask -TaskName MarketBriefing-ReportBackup -Confirm:$false`로 한다.

`data/report_delivery.json`에서 발행일과 회차별 상태를 관리한다. 전송 전에 `sending`을 원격 저장하고 Telegram API가 모든 메시지의 성공을 확인한 후 `sent`로 바꾼다. 전송 결과가 불명확한 회차는 자동 재발송하지 않는다. 최근 이틀의 상태와 다음 발송 대상은 Actions 실행 요약에 표시한다. 불명확한 전송은 오류로, 복구 기한이 지난 미발송은 경고로 표시한다. 실패 시 Actions 로그와 실제 채널 수신 여부를 먼저 확인한다. 수동 workflow 실행도 시간대와 중복 방지 규칙을 따른다.

검증: `python -m unittest discover -p 'test_*.py'` (requests, beautifulsoup4, anthropic, PyYAML 필요).
