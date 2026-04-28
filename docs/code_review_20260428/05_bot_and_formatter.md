# Stage 6 — Bot + Formatter

**대상**: `bot/telegram_bot.py` (669) + `bot/formatter.py` (921)
**날짜**: 2026-04-28

---

## 1. 핵심 발견 요약

| ID | 심각도 | 항목 |
|----|--------|------|
| **B-AUTH** | **HIGH** | 봇 명령어 핸들러에 사용자 권한 검증 0건 |
| **B-FAIR** | **HIGH** | 적정주가 괴리율 계산 결함 (fair_low 단일 기준) |
| B-MERGE | LOW (해소) | merge 순서 — 의도대로 동작, 현재 결함 X |
| B-W52 | LOW | week52_position=0이 결손인지 정상인지 구분 불가 |
| B-FMT | LOW | `_fmt_pct(0)` → "—" 처리 (0%인지 결손인지 모호) |

**CRIT 0**.

---

## 2. B-AUTH (HIGH): 봇 권한 검증 부재

### 발견
`_cmd_start`, `_cmd_help`, `_cmd_report`, `_cmd_stock`, `_cmd_history`, `_cmd_watchlist`, `_cmd_stoploss`, `_cmd_buy`, `_cmd_sell`, `_cmd_portfolio`, `_cmd_performance` 11개 핸들러 모두에서 **사용자 검증 0건**:

```python
async def _cmd_stock(self, update: Update, context):
    # update.effective_chat.id 또는 update.message.from_user.id를
    # TelegramConfig.CHAT_ID와 비교하는 코드 없음
    ...
```

### 영향
- 봇 토큰이 노출되면 (chat ID는 검색 가능) 외부 사용자가:
  - `/stock`, `/portfolio`, `/performance` — 사용자 데이터 열람
  - `/buy`, `/sell`, `/watchlist add/del` — 포트폴리오·관심종목 위변조
  - `/stoploss N` — 손절 멀티플라이어 변경

- 발송(send_*) 측은 `chat_id=self.cfg.CHAT_ID`로 사용자에게만 가지만, **수신은 누구나 가능**.

### 권고 (별도 PR)
```python
def _is_authorized(self, update: Update) -> bool:
    chat_id = update.effective_chat.id if update.effective_chat else None
    return str(chat_id) == str(self.cfg.CHAT_ID)
```
모든 `_cmd_*` 진입에서 `if not self._is_authorized(update): return`.

---

## 3. B-FAIR (HIGH): 적정주가 괴리율 계산 결함

### 위치
`analysis/scorer.py:_calc_fair_value` line 322-326:
```python
if fair_low > 0:
    gap_pct = round(((current_price - fair_low) / fair_low) * 100, 1)
else:
    gap_pct = 0.0
```

### 결함
**fair_low 단일 기준** 계산. 현재가가 적정 범위 내([fair_low, fair_high]) 안에 있어도 양수가 나옴.

### 4-28 사용자 발견 사례 검증
- 효성: 표시 "+84.9% 고평가"
  - 현재가가 fair_low보다 84.9% 높다는 의미일 뿐
  - fair_high는 보통 fair_low의 1.7배 수준 (0.7×PER_avg ~ 1.2×PER_avg) → 적정 범위 안일 수도
- 현대글로비스 "+98.7%"도 동일 패턴

### 정정 권고 (별도 PR)
```python
if current_price <= fair_low:
    # 저평가 (fair_low 대비 음수)
    gap_pct = round(((current_price - fair_low) / fair_low) * 100, 1)
elif current_price >= fair_high:
    # 고평가 (fair_high 대비 양수)
    gap_pct = round(((current_price - fair_high) / fair_high) * 100, 1)
else:
    # 적정 범위 내
    gap_pct = 0.0
```

또는 fair_mid = (fair_low + fair_high) / 2 기준으로 단일 비교 후 절대값 작으면 "적정"으로 분류.

formatter는 정상 (line 251-258 — gap < 0/= 0/> 0 분기). 계산만 수정하면 됨.

---

## 4. B-MERGE (LOW, 해소): merge 순서 검증

### 위치
`bot/telegram_bot.py:_cmd_stock` line 233-235:
```python
v3_log = self.db.get_latest_report_log_for_stock(stock_code)
if v3_log:
    score = {**v3_log, **score}
```

### 분석
- score = 최신 stock_scores (당일 또는 가장 최근), v3_log = TOP 10 진입 시점 daily_report_log
- merge 결과: **score 우선** → 겹치는 키(13개)는 stock_scores 최신값
- v3_log only 키 (fair_value_*, revenue_growth, op_income_growth, foreign_net_buy_days, rank): v3_log 값 보존

### P2 결함 시나리오 (이전)
- stock_scores에 growth_score/quality_score 컬럼 추가 전에는 score dict에 해당 키 부재 → v3_log의 정상값 표시
- 컬럼 추가 후 백필 안 된 종목: stock_scores.growth_score=0이 v3_log의 정상값 덮어씀 ⚠️

### 현 상태
- 어제 백필로 stock_scores 모든 row의 growth/quality 컬럼 정상값 채워짐
- 4-27 (월요일 풀스캔) + 4-28 (화) 사이클 모두 정상 → **현재 결함 발현 없음**

### 권고
**B-MERGE-PROC [LOW]**: 향후 컬럼 추가 시 같은 패턴 재발 위험. 안전 패턴:
```python
# 0/None을 결손으로 보고 v3_log 우선
def _merge_keep_nonempty(base, overlay):
    out = dict(base)
    for k, v in overlay.items():
        if v not in (0, None, "", 0.0):
            out[k] = v
    return out
```
별도 PR 후보 (선택).

---

## 5. B-W52 (LOW): week52_position 결손 처리 모호

### 위치
`bot/formatter.py` line 124:
```python
w52 = s.get("week52_position", 0)
w52_str = f"{w52:.0f}%" if w52 else "—"
```

### 문제
- `w52=0`은 정상값 가능 (52주 최저점에 위치 = 0%)
- 그러나 `if w52`는 0이면 falsy → "—"로 표시
- 모멘텀 TOP 10에서 52주 최저점 종목이 결손으로 표시될 수 있음

### 영향
표시 오해. 분석 영향 없음.

### 권고
`if w52 is not None:` 또는 정상값 sentinel(-1) 도입 (별도 PR).

---

## 6. B-FMT (LOW): _fmt_pct(0) 처리

### 위치
`bot/formatter.py:_fmt_pct` line 478-480:
```python
if f == 0:
    return "—"
```

### 문제
- `_fmt_pct(0.0)` → "—" (결손으로 표시)
- 그러나 실제 0%인 경우도 있음 (예: ROE=0인 적자 기업, 배당=0)
- 결손과 0%를 구분 못함

### 영향
- 적자 기업 ROE=0 → "—" → 사용자가 "결손인 줄 알고 신뢰 X"
- 적자 기업 분류는 다른 컬럼(consecutive_loss_years)으로 외부 가능 → 영향 미미

### 권고
NULL/0 구분 인프라 도입 후 같이 수정 (P3 후속).

---

## 7. 모멘텀 TOP 10 보조 섹션 (오늘 추가, 정상 작동)

### 검증 완료
- 시총 1,000억 + 거래대금 10억 필터 (종합 TOP 10보다 완화) ✓
- `momentum_score` 내림 → `foreign_net_buy_5d` 내림 정렬 ✓
- 결손 처리: `f5/i5_str = f"{f5:+,}" if f5 else "0"` (수급은 0 표시) ✓
- 종합 TOP 10과 중복 허용 (별도 시각 제공 의도) ✓
- 테스트 커버리지: tests/test_formatter.py 6+건 (line 184-303 추정)

### 발견
B-W52 외 없음.

---

## 8. 텔레그램 메시지 분할 (`_split_messages` line 852-871)

### 평가
- `MAX_MESSAGE_LENGTH=4000` (settings line 51) — 텔레그램 한계 4096보다 96자 여유
- 라인 단위 분할, 줄바꿈 포함 길이 계산 ✓
- 단일 line이 4000자 초과 시 처리 없음 (드물지만 가능 — TOP 10 reason이 매우 길면)

### 발견
**B-SPLIT [INFO]**: 단일 line 4000자 초과 시 분할 안 됨. 실측 문제 없지만 방어 추가 권고 (선택).

---

## 9. 봇 명령어 검증

### 핸들러 11개
| 명령 | 핸들러 | 평가 |
|------|--------|------|
| `/start` | `_cmd_start` | 단순 환영 — 권한 X (B-AUTH) |
| `/help` | `_cmd_help` | 명령 안내 — 권한 X |
| `/report` | `_cmd_report` | DB 조회 — 권한 X |
| `/stock` | `_cmd_stock` | 종목 상세 — **권한 X** |
| `/history` | `_cmd_history` | 7일 이력 — 권한 X |
| `/watchlist` | `_cmd_watchlist` | 추가/삭제 — **권한 X (위변조 가능)** |
| `/stoploss` | `_cmd_stoploss` | 멀티플라이어 — **권한 X** |
| `/buy` | `_cmd_buy` | 포트폴리오 매수 기록 — **권한 X (위변조 가능)** |
| `/sell` | `_cmd_sell` | 매도 기록 — **권한 X** |
| `/portfolio` | `_cmd_portfolio` | 보유 조회/clear — **권한 X (전체 삭제 가능)** |
| `/performance` | `_cmd_performance` | 성과 조회 — 권한 X |

→ B-AUTH 영역. 모든 명령에 동일 검증 추가 필요.

### 외부 명령 실행 위험
- `eval/exec/subprocess` 사용 0건 (전수 grep) ✓
- SQL은 모두 parameterized (Stage 5 검증) ✓

---

## 10. 폴링 안전성

`run_bot()` (main.py:850) → `app.run_polling(drop_pending_updates=True)` ✓
- python-telegram-bot v20+ 자체 이벤트 루프
- post_init에서 스케줄러 시작 (이벤트 루프 충돌 방지)
- timeout/network 에러는 라이브러리 내부 재시도

### 발견
없음.

---

## 11. send_health_alert (오늘 추가, 정상)

`bot/telegram_bot.py` line 627-647:
- chat_id = ERROR_CHAT_ID or CHAT_ID
- BOT_TOKEN 없으면 silent return
- 포맷 실패 시 logger.error
- 발송 실패 시 logger.error

평가 ✓.

---

## 12. 보안 점검

### 토큰 처리
- `TelegramConfig.BOT_TOKEN` 환경변수 (config/.env via python-dotenv)
- 코드 내 하드코드 0건
- logger.info에 token 노출 0건 (전수 grep `BOT_TOKEN.*log`)

### .env / .gitignore (Stage 9에서 정밀)
- 추정 OK, Stage 9에서 확정.

### 발견
B-AUTH 외 없음.

---

## 13. Stage 6 누적 발견

| ID | 심각도 | 내용 | 권고 |
|----|--------|------|------|
| **B-AUTH** | **HIGH** | 봇 11 핸들러 모두 권한 검증 0건 | **이번 주 별도 PR 필수** |
| **B-FAIR** | **HIGH** | 적정주가 괴리율 fair_low 단일 기준 (현재가 적정 범위 내에서도 양수) | **이번 주 별도 PR** (계산만 수정) |
| B-MERGE-PROC | LOW | 향후 컬럼 추가 시 merge 순서 재발 위험 (현재 결함 없음) | 안전 패턴 별도 PR (선택) |
| B-W52 | LOW | week52_position=0 결손/정상 모호 | NULL 인프라 후 수정 |
| B-FMT | LOW | _fmt_pct(0) → "—" (0%와 결손 모호) | NULL 인프라 후 수정 |
| B-SPLIT | INFO | 단일 line 4000자 초과 시 분할 안 됨 | 방어 추가 (선택) |

---

## 14. CRIT 즉시 항목

**0건**. B-AUTH/B-FAIR는 HIGH지만:
- B-AUTH: 봇 토큰 미노출 한 영향 없음. 운영 환경 검증 후 우선순위 결정.
- B-FAIR: 분석/매매 영향 0 (표시 전용). 사용자 신뢰 영향만.

→ **이번 주 PR 후보 2건** (B-AUTH, B-FAIR).

---

**다음**: Stage 7 (monitoring + tools) 진행 승인 요청.
