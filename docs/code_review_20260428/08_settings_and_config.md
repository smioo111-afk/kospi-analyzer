# Stage 9 — Settings + Config + 보안

**대상**: `config/settings.py` (435) + `.env` + `.gitignore` + 권한
**날짜**: 2026-04-28

---

## 1. 핵심 발견 요약

| ID | 심각도 | 내용 |
|----|--------|------|
| **SEC-1** | **HIGH** | `config/.env` 권한 644 (그룹/세계 읽기 가능) — APP_KEY/SECRET/BOT_TOKEN 평문 노출 |
| **SEC-2** | **HIGH** | `token_cache/kis_token.json` 권한 644 — access_token 평문 노출 |
| SEC-3 | INFO | numpy 1.26 → 2.2 메이저 업데이트 보류 (회귀 위험) |

**CRIT 0**. 하드코드 시크릿 / git history 노출 / 로그 노출 모두 **0건**.

---

## 2. 하드코드 시크릿 검증

### 전수 grep (CRIT 후보)
```bash
grep -rEn "(API_KEY|SECRET|PASSWORD|BOT_TOKEN)\s*=\s*['\"]" \
  --include="*.py" --exclude-dir=venv . | grep -v "os.getenv\|settings\."
```
**결과: 0건** ✓

모든 시크릿이 `os.getenv()` 또는 `TelegramConfig/KISConfig/DARTConfig` 참조. 코드 내 평문 0.

---

## 3. .gitignore 검증

### 현황 (양호)
```
.env
config/.env
*.db
*.sqlite3
data/
__pycache__/
*.py[cod]
venv/
.venv/
.vscode/
.idea/
.DS_Store
logs/
*.log
token_cache/
```

### 평가 ✓
- `.env` (루트 + `config/.env`) 모두 ignore
- `data/` 전체 ignore → DB·dart_cache·auto_backup 모두 보호
- `logs/` ignore (로그 시크릿 노출 방지)
- `token_cache/` ignore (KIS 토큰 보호)
- `__pycache__/`, `venv/`, IDE 메타 모두 ignore

### git history 검증
```bash
git log --all --full-history -- config/.env .env
```
**결과: 0건** ✓ — 시크릿 git에 들어간 적 없음.

---

## 4. SEC-1 (HIGH): `.env` 파일 권한 644

### 실측
```
-rw-r--r-- 1 root root 1640 Apr  6 09:41 config/.env
```

### 의미
- `rw-` (owner) + `r--` (group) + `r--` (other)
- 같은 머신에서 다른 사용자가 읽을 수 있음
- 단일 사용자 root 환경이면 즉각 위험은 낮지만, **defense in depth** 위반
- 시크릿 평문 12개 (`KIS_APP_KEY`, `KIS_APP_SECRET`, `DART_API_KEY`, `TELEGRAM_BOT_TOKEN` 등)

### 권고 (사용자 수동 작업)
```bash
chmod 600 config/.env
```

---

## 5. SEC-2 (HIGH): `token_cache/kis_token.json` 권한 644

### 실측
```
-rw-r--r-- 1 root root 403 Apr 28 15:40 token_cache/kis_token.json
```

### 의미
- `access_token` (KIS API 인증) 평문 저장
- 토큰 유효기간 24시간이지만 그 안에 도용 시 KIS API 임의 호출 가능
- `_save_token_to_cache` (kis_api.py:131-141) 시 권한 미설정

### 권고 (코드 수정 + 사용자)
- 코드: `_save_token_to_cache`에서 `os.chmod(self._token_cache_path, 0o600)` 추가
- 사용자: `chmod 600 token_cache/kis_token.json` (즉시)

→ **별도 PR 후보** (한 줄 패치).

---

## 6. 로그 시크릿 노출 검증

```bash
grep -rEn "PS[A-Za-z0-9]{20,}|access_token.*[A-Za-z0-9]{20,}" logs/
```
**결과: 0건** ✓

`_issue_new_token` (kis_api.py:114-129) 로그는 `만료: %s`만 출력, 토큰 본문 미노출 ✓.

---

## 7. settings.py 임계값 합리성

### 비즈니스 임계 (`SignalConfig`)
| 임계 | 값 | 평가 |
|------|-----|------|
| MIN_MARKET_CAP | 5000억 | 보수적, KOSPI 중대형주 한정. **단계적 완화** 후속 작업 (사용자 메모) |
| MIN_TRADING_VALUE | 50억 | 유동성 컷, 적정 |
| EXCLUDE_CONSECUTIVE_LOSS_YEARS | 3 | 적자 3년 제외 |
| STRONG_BUY_SCORE | 75 | 상위 5% 가정 |
| BUY_SCORE_MIN/MAX | 60/74 | 상위 25% |
| HOLD_SCORE_MIN/MAX | 45/59 | 중위 |
| SELL_SCORE | 45 | 하위 차단 |
| TOP_N | 10 | 표시 종목 |

### ATR / 손절 (`StopLossConfig`)
| 임계 | 값 | 평가 |
|------|-----|------|
| ATR_PERIOD | 14 | 표준 |
| ATR_MULTIPLIER | 2.0 | 보수적 (1.0~3.0 클램프) |
| HARD_STOP_LOSS_PCT | -7.0 | 표준 |
| MA60_BREAK_WARNING | True | 추세 보조 |

### 가중치 합 100 ✓
```
WEIGHT_VALUE 30 + FINANCIAL 20 + GROWTH 20 + MOMENTUM 20 + QUALITY 10 = 100
```
Stage 4에서 sub-score max 합계도 정합 (각 카테고리 max sum = WEIGHT) 검증됨.

---

## 8. API 설정

### KIS (`KISConfig`)
| 변수 | 값 | 평가 |
|------|-----|------|
| RATE_LIMIT_PER_SEC | 15 (env override 가능) | 한도 20 대비 25% 마진 ✓ |
| TOKEN_REFRESH_BUFFER | 3600 (1시간) | 만료 1시간 전 갱신 ✓ |
| MAX_RETRIES | 3 | 적정 |
| RETRY_BACKOFF_BASE | 2.0 | 1→2→4초 백오프 |
| BASE_URL | 모의투자 default | env override 가능 ✓ |
| IS_PAPER_TRADING | true (default) | 안전 default ✓ |

### DART (`DARTConfig`)
| 변수 | 값 | 평가 |
|------|-----|------|
| DAILY_CALL_LIMIT | 9000 | 한도 10000 대비 1000 마진 ✓ |
| CACHE_DAYS | 90 | 분기 갱신 주기 적정 |

---

## 9. DEPRECATED 상수 검증

### `RATE_LIMIT_INTERVAL = 0.5` (line 34)
- DEPRECATED 코멘트 명시 (line 32-33)
- 코드 참조 **0건** (전수 grep) ✓
- 의도: 비상 rollback 경로 보존
- → **진짜 dead**, 그러나 의도적 보존 (코멘트로 명시). 정리 시 별도 PR 검토.

---

## 10. 환경변수 정합성

### `.env` 변수 12개 (값 마스킹 확인)
| 변수 | 필수 | 코드 default |
|------|------|-------------|
| KIS_APP_KEY | 필수 | "" |
| KIS_APP_SECRET | 필수 | "" |
| KIS_ACCOUNT_NO | 선택 | "" |
| KIS_BASE_URL | 선택 | 모의투자 |
| KIS_IS_PAPER_TRADING | 선택 | true |
| DART_API_KEY | 필수 | "" |
| TELEGRAM_BOT_TOKEN | 필수 | "" |
| TELEGRAM_CHAT_ID | 필수 (B-AUTH 후) | "" |
| TELEGRAM_ERROR_CHAT_ID | 선택 | "" |
| LOG_LEVEL | 선택 | INFO |
| DB_PATH | 선택 | data/kospi_analyzer.db |
| TIMEZONE | 선택 | Asia/Seoul |

### 신규 변수 (오늘 B-AUTH 패치)
- `TELEGRAM_ALLOWED_CHAT_IDS`: 선택, 미설정 시 `TELEGRAM_CHAT_ID` 단독 사용

### 발견
**SEC-4 [INFO]**: 필수 변수 미설정 시 fail-fast 부재. `KISConfig.APP_KEY=""` 빈 문자열로 silent 사용 시 401에서 실패. main.py:802에 부분 검증(`if not KISConfig.APP_KEY: sys.exit(1)`)있지만 `DART_API_KEY` 등 다른 필수 변수 검증 부재.

**SEC-5 [INFO]**: `.env.example` 파일 부재. 신규 deploy 시 변수 목록 안내 가이드 없음. 권고: `.env.example` 추가 (값은 placeholder).

---

## 11. 로그 로테이션

### 설정 (`LogConfig`)
- `MAX_FILE_SIZE_MB = 10` × `BACKUP_COUNT = 5` = **최대 50MB**
- `RotatingFileHandler` (main.py:52) 적용 ✓
- 현재 logs/ 720K — 안전 여유

### 발견
없음.

---

## 12. 디스크 사용

```
logs/  720K
data/  33M
```
- DB 1.1MB
- dart_cache (245+ parquet 파일) ~30MB
- 백업 (수동 .bak_* 다수) — 정리 후보

### 발견
**SEC-6 [INFO]**: 수동 백업 (`.bak_before_*`) 16개 누적. 디스크 압박 단계는 아니지만, 자동 백업(오늘 추가, 30일 retain) 외 수동 백업 정리 정책 부재. 권고: 30일+ 수동 백업도 별도 정리 (사용자 결정).

---

## 13. 의존성 최신성

### pip outdated (10건)
| 패키지 | 현재 | 최신 | 평가 |
|--------|------|------|------|
| numpy | 1.26.4 | 2.2.6 | **메이저** — 회귀 위험 큼, 보류 |
| pyarrow | 23.0.1 | 24.0.0 | 메이저 |
| pandas | 2.3.3 | (현재 최신) | ✓ |
| matplotlib | 3.10.8 | 3.10.9 | 패치 |
| certifi | 2026.2.25 | 2026.4.22 | CA 번들 — 보안 권고 |
| idna | 3.11 | 3.13 | 보안 권고 |
| pip/setuptools | (오래됨) | - | 환경 도구 |

### 발견
**SEC-3 [INFO]** numpy 메이저 업데이트(1.26 → 2.2)는 회귀 위험 큰 변경. 보안 영향 0이라 미루기 OK. certifi/idna는 보안 패치라 별도 PR 권고 (선택).

---

## 14. KIS 토큰 보안 흐름 (Stage 3 보강)

### 캐시 라이프사이클
1. 메모리 → 디스크 → API 발급 순 fallback
2. 만료 1시간 전 사전 갱신
3. **세이브 시 권한 미설정 (SEC-2)** ⚠️

### `_load_token_from_cache` 검증
- JSON 디코드 + KeyError 처리 ✓
- 만료 datetime 검증 ✓
- 파일 권한 검증 부재 (다른 사용자가 변조한 토큰 사용 시 401만 발생)

---

## 15. 텔레그램 보안 (Stage 6 패치 후)

### 현재 보호 ✓
- BOT_TOKEN 환경변수만, 하드코드 0
- B-AUTH 패치 적용: 11 명령어 모두 `filters.Chat(chat_id=allowed_ids)` 일괄 적용
- fail-closed (allowed_ids 비어있으면 dummy {-1}로 모든 명령 차단)

### 잔존 위험
- BOT_TOKEN이 노출되면 외부에서 발신은 가능 (수신만 차단). 단, 발신은 정해진 chat_id로만 가니 사용자 시야 안에 있음.
- chat_id 자체 노출 시 spam 발송 가능 — 텔레그램 자체 차단으로 보호.

---

## 16. DART API 한도 보호

### 현황 (`dart_api.py:710`)
```python
if self._api_call_count >= DARTConfig.DAILY_CALL_LIMIT:
    raise DARTAPILimitError(...)
```
- 한도 9000 도달 시 명시적 예외
- 캐시 우선 → 캐시 hit 시 호출 카운트 안 증가

평가 ✓.

---

## 17. Stage 9 누적 발견

| ID | 심각도 | 내용 | 권고 |
|----|--------|------|------|
| **SEC-1** | **HIGH** | `config/.env` 권한 644 | 사용자: `chmod 600 config/.env` |
| **SEC-2** | **HIGH** | `token_cache/kis_token.json` 권한 644 | 코드 패치 (`_save_token_to_cache`에 chmod) + 즉시 chmod |
| SEC-3 | INFO | numpy 메이저 업데이트 보류 | 운영 안정 후 별도 PR |
| SEC-4 | INFO | 필수 환경변수 fail-fast 부재 (DART_API_KEY 등) | 별도 PR (main.py 검증 보강) |
| SEC-5 | INFO | `.env.example` 부재 | 신규 deploy 가이드용 |
| SEC-6 | INFO | 수동 백업 16개 누적, 정리 정책 부재 | 사용자 결정 |
| O3 (Stage 3 재확인) | INFO | 토큰 발급 sync requests 1초 블록 | 영향 미미 |

---

## 18. CRIT 즉시 항목

**0건**. SEC-1/SEC-2 HIGH는 단일 root 사용자 환경에서 즉각 위험 낮지만 defense in depth 권고. **별도 PR + 사용자 chmod 1회**로 해소 가능.

---

**다음**: Stage 10 (종합 + 액션 아이템) 진행 승인 요청.
