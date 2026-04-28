# 액션 아이템 (2026-04-28 기준)

전수 코드 리뷰(Stage 1~10) 결과 통합. 우선순위별 분류 + PR 의존성.

---

## 🔴 사용자 즉시 작업 (1회)

오늘 패치 적용 + SEC-1 해소를 위해:

```bash
# 1. 시크릿 파일 권한 (defense in depth)
chmod 600 config/.env
chmod 600 token_cache/kis_token.json

# 2. 봇 종료
ps -ef | grep "main.py --bot" | grep -v grep
kill <PID>

# 3. (선택) 다중 사용자 화이트리스트
# echo 'TELEGRAM_ALLOWED_CHAT_IDS=<chat_id1>,<chat_id2>' >> config/.env

# 4. 봇 재시작
cd /root/kospianal/kospi-analyzer
nohup ../venv/bin/python main.py --bot </dev/null >> logs/bot.stdout.log 2>&1 &
disown
```

재시작 후 자동 적용 패치:
- KOSPI change_rate 정상 표시 (다음 분석 사이클부터)
- 적정주가 괴리율 정상 (효성/현대글로비스 등 "+85% 고평가" 사라짐)
- 봇 명령어 화이트리스트 (외부 사용자 차단)
- 토큰 캐시 0o600 (다음 토큰 갱신 시 자동 적용)

---

## ✅ 오늘 즉시 패치 완료 (5건)

| ID | 심각도 | 머지 커밋 | 변경 라인 |
|----|--------|----------|----------|
| N4 KOSPI change_rate | HIGH | `6930fbf` + `491fe81` | 92 |
| B-FAIR 적정주가 괴리율 | HIGH | `2cbf0be` | 137 |
| B-AUTH 봇 권한 | HIGH | `05cd7cb` | 171 |
| SEC-2 token 권한 | HIGH | `2c2a62e` | 93 |
| Phase 0 test_signals 픽스처 | LOW | `d6531cb` | 4 |

---

## 🟡 이번 주 (P1) — 회귀 위험 낮은 마무리

### M-HC1 — health check에 KOSPI change_rate 검증 추가
**Why**: N4 같은 silent fail 회귀 차단. T1-2를 보강해 change_rate=0 + change!=0 조합 잡음.
**공수**: S (1줄+테스트)
**의존성**: 없음

### D-1 — `update_portfolio_stock_names_from_master` 데드 메소드 제거
**Why**: 외부 호출 0건, 31줄.
**공수**: S
**의존성**: 없음

### L1 — `_is_financial_sector` dead flag 제거
**Why**: 참조 0건, 항상 False. 코드 인지 부담만.
**공수**: S (함수 + 할당 라인 제거)
**의존성**: 없음

---

## 🔵 이번 달 (P2) — 가치 큰 개선

### N1 — IFRS account_id 매칭을 매출/영업이익/순이익까지 확장
**Why**: 현재 FCF에만 적용. 매출=0 잔존(보험 1, 증권 1, 금융 1)에서 결손율 추가 개선 가능.
**공수**: M
**의존성**: 없음
**회귀 위험**: 낮음 (4단계 매칭 fallback 유지)

### T8-1 + T8-5 — main.py 종단 통합 테스트
**Why**: pipeline.run() 12% 커버리지. 어제 KOSPI async 회귀 같은 silent fail 차단.
**공수**: M
**의존성**: 없음
**범위**: KIS aioresponses + DART parquet mock으로 1 사이클 종단 실행

### T8-2 — bot 명령어 통합 테스트
**Why**: 11개 명령어 핸들러 본문 미커버. send_health_alert 등 신규 메소드.
**공수**: M
**의존성**: 없음

### M1 — `ta` 미사용 의존성 정리
**Why**: requirements.txt 선언, import 0건.
**공수**: S (requirements.txt 1줄 + pip uninstall)
**의존성**: 없음

### B-MERGE-PROC — 안전 merge 패턴 (`_merge_keep_nonempty`)
**Why**: 향후 컬럼 추가 시 stock_scores ↔ daily_report_log 충돌 재발 방지.
**공수**: S
**의존성**: 없음

### SEC-4 — 필수 환경변수 fail-fast 검증
**Why**: DART_API_KEY/TELEGRAM_CHAT_ID 등 누락 시 silent → 401에서 발견.
**공수**: S
**의존성**: 없음

### SEC-5 — `.env.example` 추가
**Why**: 신규 deploy 가이드.
**공수**: S
**의존성**: 없음

---

## 🟢 장기 (P3) — 데이터 누적 / 큰 변경

### MED1 — `AnalysisPipeline.run()` 247줄 분리
**Why**: 단일 책임 위반. 가독성·테스트 용이성.
**공수**: L (회귀 위험 큼)
**의존성**: T8-1 통합 테스트 선행 권고

### D-MIG — 마이그레이션 버전 관리
**Why**: 5건 ALTER 하드코드, 향후 추적 어려움.
**공수**: M (가벼운 schema_version 테이블)
**의존성**: 없음

### F-trail — 트레일링 스탑
**Why**: 보유 중 가격 상승 시 동적 손절선 갱신 부재.
**공수**: M
**의존성**: 없음

### N-NULL — FCF NULL/0 구분 인프라
**Why**: 현재 0이 결손인지 실제 0인지 구분 못함. health check가 외부 노출하지만 근본 해결 X.
**공수**: M (스키마 변경 + 다운스트림)
**의존성**: D-MIG (선행 권고)

### 시총 필터 단계적 완화 (5000 → 3000 → 1000)
**Why**: 현재 5000억 컷으로 KOSPI 중대형주만. 중소형 가치주 발견 기회 증가.
**공수**: S (settings 변경)
**의존성**: admin filter (오늘 추가) 운영 검증 (4-29 풀스캔 후)

### 거래대금 동적 필터 (시총별)
**Why**: 일률 50억은 대형주에 약함, 중소형주에 강함.
**공수**: M
**의존성**: 시총 완화 후

### DART async 전환
**Why**: 현재 sync requests, 분기 보고서 도입 시 병목.
**공수**: L
**의존성**: 분기 데이터 도입 결정

### F-1 (룩어헤드 감사 후속) — `financial_metrics.rcept_dt` 컬럼
**Why**: 백테스트 시점 필터 가능. 정정공시 추적.
**공수**: S
**의존성**: D-MIG (선행 권고)

### F-2 (룩어헤드 후속) — annual-only 정책 명문화
**Why**: 분기 데이터 도입 시 룩어헤드 위험 사전 차단.
**공수**: S (docs)

### performance_analyzer 커버리지 보강
**Why**: 16% 커버리지. 월간/분기/반기/연간 리포트 로직.
**공수**: M

### backfill 도구 dry-run 통합 테스트
**Why**: 현재 0% 커버리지. dry-run 출력 형식 silent 변경 가능.
**공수**: M

---

## ⏳ 데이터 누적 대기 (보류)

| 항목 | 시점 | 비고 |
|------|------|------|
| 가치 vs 모멘텀 적중률 비교 | 2026-05 ~ 07 | performance_tracking 30+ 샘플 |
| 백테스트 엔진 구축 | 3~6개월 | F-1 (rcept_dt) 선행 권고 |
| ML 랭킹 모델 | 1년+ | 1년 분 데이터 누적 후 |
| 자동매매 (paper 6개월+) | 1년+ | KIS 모의투자 검증 |

---

## 🛡️ 운영 메모 (관찰만)

| ID | 내용 |
|----|------|
| O1 | sample_threshold_notifier 등록 스킵 로그 매번 |
| O2 | KOSPI fallback 0.0 → T1-2가 잡음 (안전망) |
| O3 | 토큰 sync 발급 1초 블록 (하루 1-2회) |
| O4 | (해소) 토큰 권한 — 오늘 SEC-2 패치 |
| M-HC2 | 휴장일 다음 T1-7 false positive 가능 |
| M-HC3 | circuit-breaker 로그 전체 스캔 (큰 로그 시) |
| T-1 | backup_db.py `--dry-run` 부재 |
| T-2 | backup_db.py argparse 부재 |
| T-3 | backfill 도구 docstring "백업 권고" 보강 |
| T-4 | sample_threshold_notifier 정리 (docs 절차) |
| T-5 | mutating 도구 "봇 종료 권고" docstring |
| SEC-3 | numpy 메이저 업데이트 보류 |
| SEC-6 | 수동 백업 16개 누적 정리 |

---

## PR 의존성 그래프 (P1~P2 핵심)

```
M-HC1 (health check 보강)  ──┐
D-1 (dead method)          ──┤
L1 (dead flag)             ──┤── 모두 독립적 (P1, 같은 주에 처리 가능)
M1 (ta 의존성)             ──┘

T8-1 + T8-5 (main 통합)    ──→ MED1 (run 분리) [P3]
T8-2 (bot 통합)            ──┘

N1 (IFRS 확장)             ──→ N-NULL (NULL/0 구분) [P3]

D-MIG (버전 관리)          ──→ F-1 (rcept_dt 컬럼) [P3]
                             ──→ N-NULL [P3]

시총 완화                  ──→ admin filter 운영 검증 (4-29 후)
                             ──→ 거래대금 동적 필터
```

---

**우선순위 PR 권고 순서** (회귀 위험·가치 가중):
1. **M-HC1** (S, P1) — N4 회귀 차단
2. **D-1 + L1** (S, P1) — dead 정리
3. **T8-1 + T8-5** (M, P2) — main 통합 테스트 (안전망)
4. **N1** (M, P2) — IFRS 매출/영업이익 확장
5. **B-MERGE-PROC** (S, P2) — 안전 merge 패턴
6. **SEC-4 + SEC-5** (S, P2) — fail-fast + .env.example

---

**문서 버전**: 1.0
**작성**: 2026-04-28 (Stage 10 종합)
