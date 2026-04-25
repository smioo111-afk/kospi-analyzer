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

[P1][FEATURE] KOSPI 지수 수집 구현
  배경: analysis_results.kospi_index 전건 0/NULL. 초과수익 계산 불가.
  내용: KIS FHPUP02100000 또는 pykrx 사용. 매일 분석 파이프라인 마지막에 저장.
  공수: S
  등록일: 2026-04-24
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

[P2][BUG] _calc_growth_score 페널티 결손 식별 (MED-5)
  배경: 감사 보고서 §MED-5. consecutive_loss_years=0이 무손실인지 결손인지
        구분 안 됨. PL 결손 종목에서 페널티 우회 발생.
  내용: revenue/total_assets 등 다른 PL/BS 필드 결손 여부와 결합해
        '결손' 신호를 명시적으로 식별 후 페널티 적용.
  공수: S
  등록일: 2026-04-26
  상태: 열림

[P2][BUG] dividend_yield 전년도 폴백 (HIGH-2)
  배경: financial_metrics 67/233 (28.8%) 결손. 2026-04-26 직접 호출 검증
        결과 alotMatter API status=000 정상 응답이지만 결손 종목 응답에
        "현금배당수익률(%)='-'" (DART 미공시). 보험·증권·일부 적자기업이
        결산기일 기준 사업보고서 공시 시점에 배당 의사결정 미완료.
  내용: _get_dividend_yield에서 '-' 또는 0 응답 시 전년도(year-1) 사업
        보고서로 폴백 호출. 회복 추정 50%+. account_id 기반 매칭도 검토.
  공수: S
  등록일: 2026-04-26
  상태: 열림

[P2][FEATURE] DART 캐시 백필 도구 확장 (HIGH-4)
  배경: consecutive_*_years, prev_revenue, prev_op 등 추가 필드도
        캐시(2024 parquet 포함) 기반 백필 가능.
  내용: tools/backfill_dart_pl.py에 prev 캐시 활용 로직 추가.
        성장률 재계산 + 연속 적자/감소 연수 보강.
  공수: S
  등록일: 2026-04-26
  상태: 열림

[P2][BUG] 금융주 매출 합산 로직
  배경: 사업보고서 공시 후 backfill 결과 sector=금융 32종목 중 9건 revenue=0.
        2026-04-26 시뮬레이션 결과: 단순 후보 추가(이자수익/보험수익)는
        9건 회복하지만 보험사 5건(한화생명/삼성화재/DB손보/서울보증/삼성증권)
        매출이 합산 라인에서 단일 라인으로 잘못 좁아져 회귀 발생.
        본질적으로 보험사 매출 = 보험수익 + 투자영업수익 + 이자수익 합산,
        은행 매출 = 이자수익 + 수수료수익 합산. 단순 후보 추가로는 정확 불가.
  내용: sector별 분기 (은행/보험/증권/지주) + 후보별 합산 로직 또는
        account_id(ifrs-full_InterestIncome 등) 매핑 도입.
  공수: M
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
