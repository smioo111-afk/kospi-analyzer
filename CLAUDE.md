# CLAUDE.md — Claude 작업 가이드

이 저장소에서 Claude (채팅 또는 Claude Code)가 작업할 때 참조할 기본 정보.

## 프로젝트 개요

KOSPI 저평가 기업 분석 시스템.
- DB: SQLite (`data/kospi_analyzer.db`, WAL mode)
- 봇: Telegram (15:40 평일 정기 분석 + /command 인터랙티브)
- 외부 API: KIS (가격/차트/수급), DART (재무/공시)

운영 명령:
- 봇 재시작: `nohup ../venv/bin/python main.py --bot </dev/null >> logs/bot.stdout.log 2>&1 & disown`
- 회귀: `/root/kospianal/venv/bin/pytest tests/`
- DB 백업: `python3 -c "import sqlite3; c=sqlite3.connect('data/kospi_analyzer.db'); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.close()"` 후 `cp`

## 프로젝트 요약 관리 워크플로우

채팅 Claude의 토큰 소모 절감을 위해 `docs/project_summary.md`에 요약본을 유지한다.

- 위치: `/root/kospianal/kospi-analyzer/docs/project_summary.md`
- 크기: ~22.5KB (476 라인)
- 내용: 모듈별 시그니처 + 핵심 로직 + 임계값 + 운영 흐름 + 알려진 한계

### 원격 저장소

raw URL: `https://raw.githubusercontent.com/smioo111-afk/kospi-analyzer/main/docs/project_summary.md`

채팅 Claude 사용 시: 위 URL을 web_fetch로 호출하면 최신 요약 확인.

### 코드 변경 후 워크플로우

1. 코드 변경 + 테스트 + (squash) 머지 (기존 절차)
2. **`docs/project_summary.md` 해당 섹션 갱신**
   - 새 모듈 추가 시: 신규 섹션 추가 (`§3.X` 또는 적절한 위치)
   - 함수 시그니처 변경 시: 시그니처 라인 수정
   - 핵심 로직 변경 시: 분기/계산 요약 수정
   - 임계값/상수 변경 시: `§3.3 buy_state 임계 상수` 또는 `§8 config/settings.py 임계값` 수정
3. `git add docs/project_summary.md && git commit -m "docs: update project_summary for {모듈}"`
4. `git push origin main` (원격 등록 후)

raw URL은 동일 유지되므로 채팅 Claude에서는 자동으로 최신 fetch.

### 갱신 누락 방지

큰 머지 후에는 본인이 만지지 않은 모듈도 영향이 있을 수 있다 (예: scorer 변경이 buy_state 입력 dict 키에 영향). 갱신 시 §1 데이터 흐름 + §2 main + §3 analysis 섹션을 한 번씩 훑어 cross-reference 확인.

## 작업 원칙

- **DB 스키마 변경 금지** (별도 마이그레이션 PR 필요)
- **파일 추가는 원본 파일 우선 편집** (새 파일 만들지 말 것; 단 명시적으로 신규 모듈일 때 예외)
- **봇 재시작은 사용자 지시 후만** (자동 재시작 금지)
- **commit은 사용자 명시적 지시 시만** (작업 끝나도 commit 자동 X)
- 회귀 테스트 통과 확인 후 머지
- T1-9 health check 1건은 무관 pre-existing 실패 (날짜 하드코딩 이슈, 무시 OK)

## 알려진 환경

- Python: `/root/kospianal/venv/bin/python3` (venv는 부모 폴더)
- pytest: `/root/kospianal/venv/bin/pytest`
- 작업 디렉토리: `/root/kospianal/kospi-analyzer/`
- 봇 로그: `logs/kospi_analyzer.log`
- 봇 stdout: `logs/bot.stdout.log`

## 자주 참조하는 파일

| 의도 | 파일 |
|---|---|
| 점수 계산 로직 | `analysis/scorer.py` |
| 매수 분류 | `analysis/buy_state.py` |
| 임계값 변경 | `config/settings.py` |
| DB 스키마 | `database/models.py:_init_tables` (L91~) |
| 봇 명령 추가 | `bot/telegram_bot.py:build_app` (L86~) |
| 메시지 형식 | `bot/formatter.py` |
| 자정 모니터 | `analysis/disclosure_impact.py` |
| 운영 흐름 전체 | `main.py:run_daily_analysis` (~600줄) |
