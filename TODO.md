# KOSPI Analyzer TODO

작성 규칙:
  [우선순위][카테고리] 제목
    배경: 왜 필요한가
    내용: 무엇을 할 것인가
    전제조건: 먼저 해결되어야 할 것
    공수: S / M / L / L+
    등록일: YYYY-MM-DD
    상태: 열림 / 진행중 / 완료 / 보류

---

## P0 (즉시)

(없음. 모두 완료 처리됨 — 아래 완료됨 섹션 참조.)

---

## P1 (다음 단계 진입 전)

[P1][BUG] performance_tracking 100% 미수집 (CRIT-3)
  배경: docs/data_integrity_audit_20260426.md §6 참조. 82건 전수에서
        price_after_1w/1m/3m/6m/1y 모두 0. last_updated 빈 문자열 80건,
        2건만 fails 카운터 갱신. 19일 경과한 행도 1주 가격 미수집.
        백테스트·적중률 분석 자료 자체 부재.
  내용: update_performance_tracking 호출 경로·실행 이력 재조사.
        KIS sync 래퍼 호출 결과가 DB에 저장되지 않는 원인 추적.
        가설: kis_client.get_stock_price가 0 반환하는 케이스 다수, 또는
        elapsed_days 분기에서 수익률 INSERT 경로 미실행.
  공수: M
  등록일: 2026-04-26
  상태: 열림

[P1][DATA] performance_tracking 실제 적중률 분석
  배경: 백테스트 이전에 지금까지의 신호가 실제로 먹혔는지부터 확인 필요.
  내용: tools/analyze_performance.py 스크립트로 신호별 1m/3m/6m 수익률과 KOSPI 대비 초과수익 계산.
  전제조건: P0 완료.
  공수: M
  등록일: 2026-04-22
  상태: 열림

[P1][DATA] 룩어헤드 편향 감사
  배경: DART 재무제표는 분기 종료 후 45일 공시.
  내용: 재무 데이터에 rcept_dt 컬럼 보관. 백테스트에서 해당 날짜 이후에만 사용.
  공수: M
  등록일: 2026-04-22
  상태: 열림


[P1][FEATURE] 금융주 섹터 매핑 완성
  배경: signals.py _is_financial_sector가 placeholder로 항상 False.
  내용: 실제 업종코드 또는 KRX 공시 기반 섹터 분류 반영.
  공수: M
  등록일: 2026-04-22
  상태: 열림

[P1][FEATURE] 관리종목·거래정지·투자주의 플래그 필터
  배경: 시총 필터 완화(A-2) 선결 조건. 상폐 직전, 관리종목,
        거래정지, 투자경고/위험 종목 자동 배제.
  내용:
    1. KIS API 또는 KRX 공시에서 수집:
       - 관리종목 / 투자주의환기 / 투자경고 / 투자위험
       - 거래정지 / 불성실공시법인
    2. signals.py 필터에 is_warning_stock 항목 추가
    3. Telegram 리포트 경고 섹션에 해당 종목 표시
  전제조건: 생존편향 제거 로직 (P1) 완료 — 이미 완료됨
  공수: M
  등록일: 2026-04-22
  상태: 열림

[P1][PERF] 데이터 수집 async 전환
  배경: requests 동기 + 순차. RATE_LIMIT 0.5s. KIS 공식 한도 대비 10%만 활용 중.
  내용: aiohttp + 세마포 기반 async RateLimiter (목표 15 calls/sec)로 리팩토링.
  전제조건: 현재 성공률 측정으로 기준선 확보.
  공수: L
  등록일: 2026-04-22
  상태: 완료 예정 (2026-04-22 c7ad346 main 머지, 재시작 완료. 내일 15:40 첫 스케줄 모니터 후 완료 이동)

[P1][PERF] dart_api.py async 전환
  배경: KIS async 완료 후 전체 수집 병목은 DART 11분.
        전체 효과 위해 필수 후속.
  내용: aiohttp + 기존 parquet 캐시 유지.
        DART는 일일 10,000콜 제한이라 rate보다 캐시가 먼저.
  공수: M
  등록일: 2026-04-22
  상태: 열림

---

## P2 (여유 시)

[P2][ANALYSIS] 가치 TOP 10 vs 모멘텀 TOP 10 적중률 비교
  배경: 종합 TOP 10(가치+모멘텀 하이브리드) vs 모멘텀 TOP 10(순수
        모멘텀) 동시 운용. 4-28부터 5월~7월 1m 수익률 누적 후 비교.
  내용: tools/analyze_performance.py에 두 그룹 비교 분석 추가.
        모멘텀 TOP 10 스냅샷은 현재 DB 미저장 (formatter에서만 즉석 생성)
        — 비교 위해 daily_report_log 또는 신규 컬럼 검토.
  공수: M
  등록일: 2026-04-27
  상태: 열림

[P2][TEST] KIS 통합 테스트 갭 점검
  배경: 묶음 D KOSPI 테스트 3개가 모두 자체 `async with KISClient(...) as kis:`
        컨텍스트로 호출해서 main.py 실제 사용 패턴(인스턴스 미리 생성 후
        호출 시점 진입)을 검증 못 함. 4-27 회귀가 통과했음.
  내용: 다른 KIS aget_* 메서드도 같은 갭 있는지 점검. 인스턴스 재사용
        패턴 통합 테스트 추가 (aget_stock_price, aget_daily_chart,
        aget_investor_trading 등).
  공수: M
  등록일: 2026-04-27
  상태: 열림

[P2][FEATURE] 백테스트 엔진 구축
  배경: 백테스트 없이 전략 검증 불가.
  내용: docs/backtest_tech_comparison.md의 결정에 따라 엔진 구축.
  전제조건: P1 데이터 품질 3건 (적중률/생존편향/룩어헤드) 완료.
  공수: L
  등록일: 2026-04-22
  상태: 열림

[P2][FEATURE] 거래대금 기준 동적 필터
  배경: 시총 완화 시 유동성 낮은 종목 대비. 현재 10억 하한은 대형주 기준.
  내용:
    시총 구간별 차등:
      - 시총 2,000억+: 거래대금 10억+
      - 시총 500~2,000억: 거래대금 5억+
      - 시총 500억 미만: 거래대금 3억+
    20일 평균 거래대금 사용 (당일 급등 노이즈 제거)
  전제조건: 관리종목 필터
  공수: S
  등록일: 2026-04-22
  상태: 열림

[P2][FEATURE] 시총 필터 단계적 완화
  배경: A-2 방향 확정 (2026-04-22). KOSPI 내 소형주 포함.
  내용:
    단계 1: 1,000억 → 500억 (1개월 관찰)
    단계 2: 500억 → 200억 (1개월 관찰)
    단계 3: 200억 → 유지 또는 50억 (백테스트 결과 따라)
  전제조건: 관리종목 필터, 거래대금 동적 필터, 백테스트 엔진,
            생존편향 제거(완료)
  주의: 각 단계 성과 데이터 별도 보존
  공수: M
  등록일: 2026-04-22
  상태: 열림

[P2][FEATURE] 시총 구간 모드 분리 (선택)
  배경: 대형주와 소형주 팩터 효과 다름. 동일 가중치는 비최적.
  내용:
    config에 STRATEGY_MODE: "large_cap" / "mixed" / "small_cap_tilt"
    소형주 틸트: 퀄리티·모멘텀 상향, 수급 하향
  전제조건: 시총 완화 단계 1 완료 후 실적 비교
  공수: M
  등록일: 2026-04-22
  상태: 열림

[P2][RISK] 시장 레벨 리스크 엔진
  배경: 시장 급락 시 자동 대응 없음.
  내용: KOSPI -3% 시 신호 억제. 포트폴리오 -15% 시 청산 알림.
  전제조건: 백테스트 엔진.
  공수: M
  등록일: 2026-04-22
  상태: 열림

[P2][FEATURE] Paper trading 연동
  배경: 실전 전 모의투자 환경 3개월 검증 필요.
  내용: KIS 모의투자 API로 주문 자동 실행. 실제 신호와 괴리 측정.
  전제조건: 백테스트 통과.
  공수: M
  등록일: 2026-04-22
  상태: 열림

[P2][OPS] 관측성 강화
  배경: 파이프라인 1회 실행을 로그에서 추적 어려움.
  내용: 파이프라인 UUID trace_id 발급 후 모든 로그에 포함. 수집 성공률 80% 미만 시 Telegram 알림.
  공수: S
  등록일: 2026-04-22
  상태: 열림

[P2][OPS] cascade skip 알림
  배경: 안전장치로 cascade 스킵 시 로그만 남김. 관측성 부족.
  내용: cascade skip 발생 시 Telegram WARN 메시지 (종목코드, 예외타입, 실패횟수).
  공수: S
  등록일: 2026-04-24
  상태: 열림

[P2][OPS] WAL 자동 체크포인트
  배경: 2026-04-22 백업 전 WAL 2.1M 누적 발견. main DB는 2주 전 상태.
  내용:
    (a) 파이프라인 끝에서 conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    (b) 또는 PRAGMA wal_autocheckpoint=1000 명시 설정
    TRUNCATE는 다른 연결 있으면 실패 가능. PASSIVE 권장.
  공수: S
  등록일: 2026-04-22
  상태: 열림

[P2][OPS] 자동 백업 스케줄
  배경: 수동 백업은 잊기 쉬움.
  내용: 매일 자정 sqlite3 .backup 일관 스냅샷. 최근 7일 유지.
        cron 또는 APScheduler.
  전제조건: WAL 자동 체크포인트
  공수: S
  등록일: 2026-04-22
  상태: 열림

[P2][REFACTOR] 휴장일 판정 경량화
  배경: 매 스케줄 트리거마다 KIS 토큰 + 삼성전자 조회.
  내용: pykrx 공휴일 테이블 1차 체크 후 실패 시 KIS 폴백.
  공수: S
  등록일: 2026-04-22
  상태: 열림

[P2][OPS] 샘플 30개 알림 코드 제거
  배경: 일회성 알림. 발송 후 코드 불필요.
  내용: docs/sample_notifier_cleanup.md 절차대로 제거.
  전제조건: 텔레그램 알림 수신 확인.
  공수: S
  등록일: 2026-04-22
  상태: 열림

[P2][FEATURE] DART 캐시 백필 도구 확장 (HIGH-4)
  배경: consecutive_*_years, prev_revenue, prev_op 등 추가 필드도
        캐시(2024 parquet 포함) 기반 백필 가능.
  내용: tools/backfill_dart_pl.py에 prev 캐시 활용 로직 추가.
        성장률 재계산 + 연속 적자/감소 연수 보강.
  공수: S
  등록일: 2026-04-26
  상태: 열림

[P2][BUG] signals.py 판정 기준 문서/코드 불일치
  배경: docstring은 종합 >=80 이나 settings.py는 STRONG_BUY_SCORE=75.
  내용: 실제 운영 값 기준으로 docstring 수정.
  공수: S
  등록일: 2026-04-22
  상태: 열림

---

## P3 (장기 아이디어)

[P3][BUG] FCF NULL/0 구분 (silent fail 해소)
  배경: 현재는 매칭 실패 시 0 저장 → 진짜 0과 구분 불가.
        FCF account_id 매칭 보강 후에도 캐시 누락 11개(우선주)는 여전히 0.
        scorer 가드(fcf<=0 → quality 0점)가 silent fail을 가리고 있음.
  내용: financial_metrics.free_cash_flow NULL 허용 마이그레이션 +
        scorer에서 None과 음수를 분리 처리.
  공수: M (마이그레이션 동반)
  등록일: 2026-04-27
  상태: 열림

[P3][DATA] 4-27 analysis_results.kospi_index 결손 (1행)
  배경: 4-27 첫 정상 사이클에서 async with 컨텍스트 버그로 결손.
        실제 종가 6615.03 확정 (aget_kospi_daily_index + 격리 호출
        교차 확인). 본 머지로 4-28부터는 정상 수집.
  내용: 단일 UPDATE로 백필 가능. tools/backfill_kospi_index.py 활용.
        SQL: UPDATE analysis_results SET kospi_index=6615.03
             WHERE analysis_date='2026-04-27' AND kospi_index=0
        데이터 영향 미미 (101개 중 1개)이므로 우선순위 낮음.
  공수: S
  등록일: 2026-04-27
  상태: 보류 (사용자 결정으로 백필 미실시)

[P3][BUG] KOSPI 지수 응답 change_rate=0 의심
  배경: 4-27 격리 호출 결과 index=6615.03, change=139.4인데
        change_rate=0.0. change에 비해 change_rate 모순.
        응답 파싱 또는 KIS 응답 자체 문제 가능.
  내용: aget_kospi_index 응답 파싱 로직 view + 다른 거래일 표본으로
        검증. 분석에 직접 사용 안 하지만 데이터 정확성 의심.
  공수: S
  등록일: 2026-04-27
  상태: 열림

[P3][BUG] test_integration.py::test_signals 사전 결함
  배경: 필터 임계값 IndexError. 여러 PR(묶음 D/E/F + 본 KOSPI 수정)에서
        무관하다고 넘김. main에 잠복 중.
  내용: 정확한 원인 규명 (filter_stocks 임계값 vs 테스트 입력) +
        수정 또는 deprecated 처리.
  공수: S
  등록일: 2026-04-27
  상태: 열림

[P3][REFACTOR] 미호출 public Database 메서드 정리 또는 활용
  배경: 묶음 D-4 조사에서 발견. get_results_by_date, get_delisted_stocks,
        get_fetch_failure_candidates, update_portfolio_stock_names_from_master,
        mark_stock_delisted (운영자 도구) 등 5개.
  내용: 사용처 결정 후 텔레그램 명령/대시보드/백테스트 entry로 활용 또는
        deprecated 처리.
  공수: S
  등록일: 2026-04-26
  상태: 열림

[P3][OPS] BANK_HOLDING_CODES 신규 종목 추가 워크플로
  배경: 묶음 F의 sector='금융' 안에서 은행지주를 식별하기 위한 화이트리스트.
        신규 상장/지정 시 collectors/dart_api.py:_BANK_HOLDING_CODES 갱신 필요.
  내용: 운영 메모. 검토 트리거(분기 또는 신규 IPO 알림)와 매뉴얼 정의.
  공수: S
  등록일: 2026-04-26
  상태: 열림

[P3][FEATURE] consecutive_*_years 1년 비교 백필
  배경: 묶음 F 조사에서 확인. 캐시 2024 97.7%로 1년 비교 가능하지만
        효과 5건 수준. scorer 페널티 임계값(>=3년)에 대부분 못 미침.
        2023 캐시 15.7%만이라 3년 페널티는 보류.
  내용: backfill_dart_pl 확장으로 prev 캐시 비교 → 흑자→적자 전환
        페널티 정확 판정. 4-27 정상 사이클 결과 본 후 재평가.
  공수: S
  등록일: 2026-04-26
  상태: 열림

[P3][FEATURE] 포트폴리오 최적화
  배경: 현재 TOP 10 equal weight. 업종 분산, 상관관계 고려 부재.
  내용: risk parity 또는 업종 분산 제약 있는 mean-variance. 단, Markowitz 순정은 과최적화 심하므로 단순 제약 기반이 안전.
  공수: L
  등록일: 2026-04-22
  상태: 열림

[P3][FEATURE] ML 랭킹 모델
  배경: 룰 기반 한계 도달 시 고려.
  내용: XGBoost/LightGBM으로 미래 수익률 예측.
  전제조건: 3년 이상 자체 축적 데이터. walk-forward validation 체계. 룰 기반 vs ML A/B 비교.
  주의: 금융 시계열 과적합 위험 높음. 섣부른 도입 금지.
  공수: L+
  등록일: 2026-04-22
  상태: 열림

[P3][FEATURE] 뉴스 감성 분석
  배경: 이벤트성 급등락 미반영.
  내용: Naver 뉴스 + Claude API로 TOP 10 종목 주 1회 감성 점수화.
  주의: Claude API 별도 과금. 소규모 선검증.
  공수: L
  등록일: 2026-04-22
  상태: 열림

[P3][FEATURE] 자동매매 연동
  배경: Phase 3 최종 목표.
  내용: KIS 실거래 API로 시가 분할매수, 손절 자동 실행.
  전제조건: 백테스트 + paper trading 6개월 검증 통과.
  주의: 자본 5%부터 시작해 단계적 확대.
  공수: L+
  등록일: 2026-04-22
  상태: 열림

[P3][REFACTOR] DB 마이그레이션 도구
  배경: CREATE TABLE IF NOT EXISTS만 있고 ALTER 경로 없음.
  내용: alembic 또는 자체 버전 테이블 도입.
  공수: M
  등록일: 2026-04-22
  상태: 열림

[P3][FEATURE] 섹터 사이클 지표
  배경: 반도체 DRAM, 조선 BDI, 자동차 환율 등 섹터 고유 모멘텀 미반영.
  내용: 섹터별 핵심 지표 수집 후 섹터 모멘텀 스코어 추가.
  공수: L
  등록일: 2026-04-22
  상태: 열림

[P3][OPS] 로컬 대시보드
  배경: Telegram 외 시각화 없음.
  내용: Streamlit으로 누적 수익률, 팩터 기여도, KOSPI 비교.
  주의: FastAPI/AWS 같은 무거운 스택 금지.
  공수: M
  등록일: 2026-04-22
  상태: 열림

---

## 완료됨

[P1][BUG] FCF 수집 결손 64.5% — account_id/공백 매칭 보강
  배경: 256개 중 165개(64.5%) free_cash_flow=0 강제로 quality_score=0.
        부수: 정상 91개도 CAPEX 매칭 실패로 FCF=OCF 그대로 과대평가
        (삼성전자 ≈ 2.25×).
  완료일: 2026-04-27
  결과:
    - 커밋 37db2e3 main 머지 (squash)
    - _get_account_value: account_id 우선 매칭 + 공백 정규화 단계 추가
    - tools/backfill_fcf.py 신규 (캐시 재파싱으로 DB만 갱신)
    - tests/test_dart_api 34/34 pass (신규 11건: 8개 표본 골든 + 회귀 방지)
    - 백필 결과: zero 64.5% → 4.3% (256개 중 11개만 잔존, 캐시 누락 우선주)
    - 87 회복 / 50 진짜 음수 / 88 CAPEX 차감 정정 / 회귀 0건
    - 봇 PID 320709 → 321610 (20:16 재시작)
    - 후속(P3): FCF NULL/0 구분 (silent fail 해소)

[P2][FEATURE] 모멘텀 TOP 10 보조 섹션 (저평가 괴리율 교체)
  배경: 사용자 직관 + 학술 모멘텀 가설(Jegadeesh 1993, AQR). 종합
        TOP 10(가치+모멘텀 하이브리드)과 별도로 순수 모멘텀 시각 제공.
  내용:
    1) bot/formatter.py format_daily_report 보조 섹션 교체
       (저평가 괴리율 → 모멘텀 TOP 10)
    2) 정렬: momentum_score(0~20) 내림차순, 동점 시 foreign_net_buy_5d
    3) 필터: 시총 1,000억+, 거래대금 10억+ (가치 필터 없음)
    4) 표시: 모멘텀 점수, 52주 위치, 외국인/기관 5일 수급, 현재가
    5) 종합 TOP 10 / DB 스키마 / 점수 가중치 변경 없음
  완료일: 2026-04-27
  결과:
    - 커밋 046e611 main 머지 (squash)
    - tests/test_formatter.py 13/13 pass (신규 5건 포함), 회귀 101/102
    - 격리 검증: 4-27 데이터로 종합 TOP 10 손상 0건 확인
    - 봇 재시작 (PID 318564 → 319113), 4-28 사이클부터 새 형식
    - 후속 [P2][ANALYSIS] 가치 vs 모멘텀 적중률 비교 등록

[P1][BUG] KOSPI 지수 수집 async with 컨텍스트 누락
  배경: 4-27 첫 정상 사이클 로그에서 발견. main.py:298 호출만 유일하게
        `async with self.kis:` 블록 밖. KISClient 세션 미초기화로
        RuntimeError → 분석은 통과했으나 analysis_results.kospi_index=0.0
        저장 (4-21~24는 6388~6475 정상 범위).
  내용:
    1) main.py:297-302 호출을 `async with self.kis:` 블록으로 감쌈
    2) 회귀 차단 테스트 2건 추가 (test_kis_async.py)
       - test_kospi_index_outside_context_raises: 컨텍스트 밖 호출 가드 검증
       - test_kospi_index_main_py_pattern: 인스턴스 재사용 + 시점별 진입 검증
    3) 묶음 D 테스트 갭 발견 → P2 [TEST] KIS 통합 테스트 갭 점검 등록
  완료일: 2026-04-27
  결과:
    - 커밋 66677f3 main 머지 (squash)
    - tests/test_kis_async.py 14/14 pass, 회귀 97/98 pass (잔여 1건 무관)
    - 라이브 호출 6615.03 정상 수신
    - 봇 재시작 (PID 313961 → 318564), 4-28 사이클부터 정상 수집 예정
    - 4-27 결손 1행은 P3 [DATA] 보류 (사용자 결정)

[P2][BUG] 금융주 매출 합산 로직 + 호텔신라 부분 결손 (묶음 F)
  배경: sector=금융 32 중 9건 revenue=0, 보험 6 정확하지 않은 단일 라인,
        증권 NH 결손. 호텔신라(008770)도 부분 결손 (op만 0).
  내용:
    1) collectors/dart_api.py에 _BANK_HOLDING_CODES(11종) +
       _calc_financial_revenue(df, sector, code) 헬퍼
    2) extract_financial_metrics에 sector 인자 + main.py가 sector_map 전달
    3) 보험: IFRS4(보험수익+투자영업+이자) / IFRS17(보험서비스+이자+수수료)
    4) 증권: 영업수익 우선 → 결손 시 수수료+이자+외환거래 합산
    5) 은행지주: 이자+수수료+영업수익 합산
    6) tools/backfill_dart_pl.py에 --include-financial 옵션 (WHERE 완화)
  완료일: 2026-04-26
  결과:
    - PR 804c1f5 main 머지 (squash)
    - 백필 21행 적용. 보험 6 + 증권 2 + 은행지주 11 + 부분 결손 2
    - 한화생명 15.29조→26.80조, KB금융 0→47.31조, 신한지주 0→35.92조 등
    - 회귀 0건 검증 (비금융 30 + 일반 지주 21 + 정상 증권 3 표본)
    - 신규 단위 테스트 8건. 회귀 89 passed.
    - 호텔신라(P3 후속) 자동 회복 (부분 결손 WHERE 완화 부수효과)
  관련 문서: docs/financial_sector_audit_20260426.md

[P1][FEATURE] KOSPI 지수 수집 구현 (묶음 D)
  배경: analysis_results.kospi_index 100% 0/NULL. 초과수익 계산 불가.
  내용:
    1) KISClient.aget_kospi_index() (TR FHPUP02100000) + sync 래퍼
    2) aget_kospi_daily_index() 백필용 (TR FHKUP03500100)
    3) main.py:296 분석 사이클 종료부에 호출 + save_daily_result 인자 전달
    4) tools/backfill_kospi_index.py — KIS 기반 (pykrx KRX 호환 깨짐)
  완료일: 2026-04-26
  결과:
    - PR de3dd26 main 머지 (squash)
    - 백필 26/26 행 갱신 (5,450~6,475 범위, 결손 0)
    - 다음 사이클부터 자동 수집 + foreign_net_buy 누적 함께 기록
    - baseline부터 호출 인자 누락 상태였음 (save_analysis_result 자체는
      HistoryService 경유 정상 호출)

[P2][OPS] WAL 자동 체크포인트 (묶음 E-1)
  배경: 4-22 백업 직전 WAL 2.1M 누적 발견. main DB 2주 stale 상태.
  내용:
    1) PRAGMA wal_autocheckpoint=1000 (~4MB) — 연결 초기화 시 설정
    2) Database.checkpoint_wal(PASSIVE) 메서드 신설
    3) main.py 분석 사이클 종료 직전 명시적 PASSIVE 호출
  완료일: 2026-04-26
  결과: PR 73b1223. autocheckpoint 자동 + 사이클 종료 PASSIVE 보강.

[P2][OPS] cascade circuit-breaker (묶음 E-2)
  배경: 4-22~24 cascade 80건 폭주 사건의 다층 방어 추가.
  내용: _CASCADE_PER_CYCLE_LIMIT=5 상수 + 사이클당 카운터 +
        임계 도달 시 logger.error + 잔여 cascade 차단.
  완료일: 2026-04-26
  결과: 기존 안전장치(RuntimeError skip, 대형주 whitelist)와 직교.
        단위 테스트로 7개 후보 → 5건 발화 + 차단 검증.

[P2][BUG] 011170 영업이익 매칭 오류 (묶음 C-3)
  배경: 적자기업 "영업손실" 라벨이 후보 리스트에 없음. 부분 매칭이
        "기본및희석주당중단영업이익"에 잘못 걸림.
  내용: 후보에 "영업손실" 추가. 정확 일치 우선 + 부분 일치 fallback 분리.
  완료일: 2026-04-26
  결과: 011170 130→-9,431억 등 5종목 정정/회복. 정상 종목 회귀 0건.

[P2][BUG] _calc_growth_score 페널티 결손 식별 (묶음 C-4 / MED-5)
  배경: PL 전체 결손 종목이 페널티 판정 회피. 결손과 무손실 구분 안 됨.
  내용: is_pl_missing 가드 추가. penalty_reasons에 "결손" 사유 표시.
  완료일: 2026-04-26
  결과: 점수 회귀 0 (사유 표시만 강화).

[P2][BUG] dividend_yield 전년도 폴백 (묶음 C-2 / HIGH-2)
  배경: alotMatter status=000인데 결손 종목 응답이 '-'. DART 미공시.
  내용: _get_dividend_yield에서 year=N 결손 시 year-1로 1회 폴백 호출.
  완료일: 2026-04-26
  결과: 결손율 28.8%(67건) → 25.8%(60건). 7건 회복.
        tools/backfill_dividend.py 동봉.

[P1][BUG] DART PL 데이터 결손 수정 (CIS fallback) + scorer silent fail
  배경: financial_metrics(2025 annual) 233건 중 180건(77%) 매출/영업이익/
        순이익 0. 원인: _get_account_value가 sj_div='IS'만 필터링.
        K-IFRS 단일 포괄손익계산서(CIS)만 제출하는 기업의 PL 미추출.
        부수 발견: _score_growth(0)=2점, _score_debt(0)=MAX 5점 silent fail.
  내용:
    1) IS → CIS fallback (정확 일치 우선, 부분 일치 fallback)
    2) revenue/op_income 후보 리스트에 K-IFRS 변형('매출','영업손익') 추가
    3) _score_growth/debt가 0/None을 명시적 결손 처리 (가산점 차단)
    4) tools/backfill_dart_pl.py: 캐시 기반 PL 백필
    5) 22개 단위 테스트 (dart 9 + scorer 13)
  완료일: 2026-04-26
  결과:
    - PR fe236bc main 머지 (squash)
    - 백필 168행 적용 → 결손율 77% → 4.3% (10건만 잔존, 우선주/잘못된 코드)
    - 표본 검증: 023530 매출 13.7조 / SK하이닉스 ROE 35.59% / 카카오 매출 8.1조
    - silent fail 시뮬레이션: delta>0 0건(회귀 없음), 평균 -3.66점 가산점 회수
    - 봇 재시작 완료. 다음 사이클(15:40 또는 월요일)부터 새 점수 자동 반영
  관련 문서:
    - docs/dart_pl_loss_investigation.md (조사)
    - docs/data_integrity_audit_20260426.md (16개 이슈 분류 + 시뮬레이션)

[P1][DATA] 생존편향 제거
  배경: 현재가 조회 실패 종목이 스킵되어 상장폐지 종목 누락.
  내용: update_performance_tracking에서 조회 실패 시 delisted 플래그 + -100%로 보정.
  완료일: 2026-04-22 최초 구현 / 2026-04-24 안전장치 강화
  결과:
    - 2026-04-22: cascade 로직, mark_stock_delisted, 단위 테스트 도입
    - 2026-04-24: async 리팩터링 이후 _run_sync RuntimeError로 14종목 80건 오탐
      발생. 원인 수정 (asyncio.to_thread로 sync 래퍼 격리) + 안전장치 2종
      추가 (RuntimeError 류 cascade 금지, 시총 ≥500B KRW 대형주 화이트리스트)
      + 복구 도구 (tools/recover_performance_tracking.py) 배포. 오염 80행 전수
      복구 완료.

[P0][DOC] Project Knowledge 재동기화
  배경: 채팅에 업로드된 파일이 서버 실제 코드와 버전 불일치.
        적정주가 3-모델, 수급 8점, 동적 섹터 등이 메모리에는 있는데 업로드 코드에는 없음.
  내용: docs/project_knowledge_sync.md 체크리스트에 따라 최신 파일 업로드.
  완료일: 2026-04-22
  결과: scorer.py _calc_fair_value 3-모델 가중평균 확인. 동기화 성공.

[P0][BUG] 포트폴리오 종목코드 005308 교정
  배경: 현대차 표준 코드는 005380. 005308은 존재하지 않음.
  내용: portfolio 테이블에서 005308 -> 005380 업데이트.
  완료일: 2026-04-22
  완료 메모: 확인 결과 이미 is_sold=1로 처리되어 있었음 (005308은 2026-04-06
            잘못 입력 → 다음 날 005380 현대차로 재입력, 구 행은 매도 플래그).
            잔존 행은 DELETE로 제거 (DELETE ... WHERE stock_code='005308'
            AND is_sold=1, 1행 삭제). 005380 현대차 행은 보존됨.
