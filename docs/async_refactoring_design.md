# async 리팩토링 설계

작성일: 2026-04-22
대상 파일: `collectors/kis_api.py` (전면), `main.py` (부분), `database/models.py` (무수정 목표)
관련 TODO: `[P1][PERF] 데이터 수집 async 전환`
상태: **설계만. 코드 변경 없음. 승인 후 구현 착수.**

---

## 0. 선행 조사 결과 (2026-04-22 기준 실측)

### 0.1 동기 호출 지점 전수 (non-test, 11개)

| 파일 | 라인 | 호출 | 비고 |
|------|-----|------|------|
| main.py | 99 | `kis = KISClient()` | test 모드 진입점 |
| main.py | 104 | `kis.get_stock_price("005930")` | test 모드 |
| main.py | 130 | `self.kis = KISClient()` | `AnalysisPipeline.__init__` |
| main.py | 206 | `self.kis.check_token()` | 파이프라인 실행 전 검증 |
| main.py | 320 | `self.kis.get_stock_price(code)` | 포트폴리오 개별 보강 |
| main.py | 321 | `self.kis.get_daily_chart(code, days=60)` | 포트폴리오 개별 보강 |
| main.py | 400 | `self.kis.get_kospi_stock_list()` | 월요일 전종목 스캔 |
| main.py | 443 | `self.kis.get_stock_price(...)` | **PER/PBR 보강 루프 (핵심 병목)** |
| main.py | 455 | `self.kis.get_all_stock_prices(target_codes)` | 화~금 배치 |
| main.py | 485 | `self.kis.get_all_daily_charts(stock_codes, days=60)` | 배치 |
| main.py | 489 | `self.kis.get_all_investor_trading(stock_codes, days=25)` | 배치 |
| database/models.py | 1227 | `kis_client.get_stock_price(code)` | `update_performance_tracking` |

추가로 `collectors/kis_api.py:893`에 `__main__` 테스트 블록 `KISClient()` 1건 (무시 가능).

### 0.2 현재 파이프라인 소요 시간 (실측, `logs/kospi_analyzer.log` 근거)

| 시나리오 | 시각 근거 | 소요 |
|---------|----------|------|
| 월요일 전종목 스캔 일부 (198 종목, charts+investor) | 2026-04-06 17:14:44 → 17:18:03 | 3m 19s |
| 203 종목 full `_collect_data` (chart+investor+financial) | 2026-04-07 17:23:31 → 17:42:06 | **18m 35s** |
| 198 종목 일봉 차트 | 2026-04-06 17:14:44 → 17:16:23 | 1m 39s (≒ 0.5s/종목 ✓) |
| 198 종목 수급 | 2026-04-06 17:16:23 → 17:18:03 | 1m 40s (≒ 0.5s/종목 ✓) |
| 203 종목 재무 (DART) | 2026-04-07 17:31:05 → 17:42:06 | ~11m (out-of-scope) |
| `get_kospi_stock_list()` 932종목 페이지네이션 | 2026-04-06 17:12:15 → 17:14:44 | 2m 29s |

**핵심 병목** (async 전환 대상): 18분 중 재무(DART) 11분을 제외한 **7분**. 그중 상당 부분이 `PER/PBR 보강 루프` (월요일 전종목에서 수백 종목을 0.5s 간격으로 단일 호출) + 차트 + 수급.

### 0.3 Rate limit 현재 준수 상태

- `collectors/kis_api.py:171` `_rate_limit_wait()`가 `RATE_LIMIT_INTERVAL=0.5s`를 `time.time()` 기반으로 강제.
- 실측 `50/198 완료마다 25초` → 정확히 0.5s/호출. **준수 중.**
- KIS 공식 REST 한도: **초당 20건** (실전). 현재 활용률 ≒ **10%**.

### 0.4 의존성/환경 현황

| 항목 | 상태 |
|------|------|
| Python | 3.10.12 |
| aiohttp | **3.13.5 (설치됨)** |
| aiolimiter | ✘ 미설치 |
| pytest-asyncio | ✘ 미설치 |
| aioresponses | ✘ 미설치 |
| APScheduler AsyncIOScheduler | 이미 사용 중 (`main.py:19`) |
| python-telegram-bot v20+ | 설치됨, async 기반 |
| `_collect_data` | 이미 `async def` (line 380) — 그런데 내부에서 sync 호출이라 **이벤트 루프 블로킹 상태** |

`_collect_data`가 async임에도 내부가 전부 블로킹이므로, 데이터 수집 중 텔레그램 봇 polling이 stall. 최근 반복된 `telegram.error.NetworkError: httpx.ReadError`와 일부 연관 가능 (확증 못함, 개연성).

---

## 1. 배경과 목표

### 배경
- 현재 `kis_api.py`는 `requests` 동기. `RATE_LIMIT_INTERVAL=0.5s` → 초당 2콜.
- KIS 공식 REST 한도는 **초당 20콜**. 현재 활용률 **10%**.
- 932종목 × 2 API = 일봉 1.7분 + 수급 1.7분 ≒ 3~4분 + 월요일 PER/PBR 보강 루프 수분 = **네트워크 대기 7분+**.
- `_collect_data`는 async인데 내부 블로킹 → 텔레그램 봇 polling 간섭.

### 목표
- **초당 15콜 (공식 한도의 75%, 안전 마진 25%)**.
- 932종목 × 2 API = **목표 약 2분** (현재 3~4분 대비 1.5~2배 개선).
- 월요일 전종목 PER/PBR 보강 루프도 동일하게 단축 (현재 가장 큰 단일 블록).
- 인터페이스 하위 호환 유지. `database/models.py` 무수정.
- 장애 격리: 한 종목 실패가 전체 파이프라인 중단 유발 않음.
- 이벤트 루프 비블로킹 → 텔레그램 봇 polling 안정화.

---

## 2. 범위

### In-scope
- `collectors/kis_api.py` 전면 async 전환 (aiohttp + aiolimiter).
- `main.py` `AnalysisPipeline._collect_data` 호출부를 `await`로 전환.
- `database/models.py` `update_performance_tracking`의 `kis_client.get_stock_price()`는 sync 래퍼 경유로 **무수정**.

### Out-of-scope (이번 사이클 아님)
- `collectors/dart_api.py` async 전환 (11분 병목이지만 별도 TODO).
- 텔레그램 봇 자체 변경 (이미 async).
- 스케줄러 변경 (이미 AsyncIOScheduler).
- DB async (sqlite3는 sync 유지. aiosqlite 도입 금지).

---

## 3. 설계 결정 (선택지 비교 후 권고)

### 결정 1 — 전면 async vs 부분 async

| 대안 | 장점 | 단점 |
|------|-----|------|
| (A) 전면 | 일관성·성능 최대 | 호출부 모두 수정. `models.py`까지 async 강제 |
| (B) 배치만 async | 호출부 변경 최소 | 두 패러다임 혼재. 장기 유지보수 부담 |
| (C) async 구현 + sync 래퍼 | 하위 호환, 점진 전환 | 래퍼 오버헤드(미미) |

**권고: (C) async 구현 + sync 래퍼**
  - `main.py._collect_data`는 이미 async → 배치 호출은 `await`로 자연스럽게 전환.
  - `models.py.update_performance_tracking`은 sync → sync 래퍼로 **무수정**.
  - `asyncio.run(...)`가 이벤트 루프가 이미 돌고 있는 컨텍스트에서 호출되면 에러. 따라서 sync 래퍼는 `asyncio.get_event_loop()` 상태 확인 후 분기 (아래 4.3 참조).

### 결정 2 — Rate Limiter 구현 방식

| 대안 | 장점 | 단점 |
|------|-----|------|
| (A) `asyncio.Semaphore(N)` | 표준 라이브러리만 | 동시성 제한만 가능. "초당 N개" 보장 X |
| (B) `aiolimiter.AsyncLimiter(N, 1)` | 토큰 버킷, 엄격 보장 | 외부 의존성 추가 |
| (C) 자체 구현 (sleep + lock) | 의존성 없음 | 구현/테스트 비용 |

**권고: (B) `aiolimiter`**
  - KIS는 시간 윈도우 기반 제한 → 토큰 버킷이 자연 적합.
  - 라이브러리 크기 작음, 유지보수 활발, MIT.

### 결정 3 — 에러 처리 정책

| 대안 | 장점 | 단점 |
|------|-----|------|
| (A) 실패 시 전체 중단 | 문제 즉시 노출 | 단일 장애 종목이 전체 죽임 |
| (B) 실패 종목만 스킵 (현재 동작) | 견고 | 시스템 이상 감지 늦음 |
| (C) 재시도 후 스킵 + 실패율 임계치 초과 시 중단 | 안전 + 조기 감지 | 임계치 튜닝 필요 |

**권고: (C). 임계치 20% (실패율)**
  - 실패 종목만 개별 스킵 + WARNING 로그.
  - 배치 종료 시 `failure_rate = 실패수 / 전체` 계산.
  - 20% 초과 시 `KISBatchFailureError` 예외 발생 → 호출부 (pipeline)가 캐치해서 해당 파이프라인 실행만 abort, 스케줄러는 유지.

---

## 4. 아키텍처

### 4.1 KISClient 변경 구조

```python
# collectors/kis_api.py

import asyncio
from typing import Optional
import aiohttp
from aiolimiter import AsyncLimiter

class KISClient:
    def __init__(
        self,
        rate_limit_per_sec: int = 15,
        fail_threshold: float = 0.2,
    ) -> None:
        # 토큰 매니저: 동기 유지 (파일 I/O 1회, 블로킹 무시 가능)
        self._token_manager = KISTokenManager()
        self._limiter = AsyncLimiter(rate_limit_per_sec, time_period=1)
        self._session: Optional[aiohttp.ClientSession] = None
        self._fail_threshold = fail_threshold
        # 토큰 동시 갱신 방지
        self._token_lock = asyncio.Lock()

    # ---------- Lifecycle ----------
    async def __aenter__(self) -> "KISClient":
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10),
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    # ---------- 단건 async 메서드 (접두사 a-) ----------
    async def aget_stock_price(self, code: str) -> dict: ...
    async def aget_daily_chart(self, code: str, days: int = 60) -> list: ...
    async def aget_investor_trading(self, code: str, days: int = 10) -> dict: ...
    async def aget_kospi_stock_list(self) -> list: ...
    async def acheck_token(self) -> bool: ...

    # ---------- 배치 async 메서드 ----------
    async def aget_all_stock_prices(self, codes: list) -> list:
        results, failures = [], 0
        tasks = [self.aget_stock_price(c) for c in codes]
        for coro in asyncio.as_completed(tasks):
            try:
                results.append(await coro)
            except KISAPIError:
                failures += 1
        self._check_failure_rate(failures, len(codes), "stock_prices")
        return results

    async def aget_all_daily_charts(self, codes: list, days: int = 60) -> dict: ...
    async def aget_all_investor_trading(self, codes: list, days: int = 10) -> dict: ...

    # ---------- Sync 래퍼 (하위 호환) ----------
    def get_stock_price(self, code: str) -> dict:
        return _run_sync(self._with_session(self.aget_stock_price, code))

    def get_daily_chart(self, code: str, days: int = 60) -> list:
        return _run_sync(self._with_session(self.aget_daily_chart, code, days))

    def get_all_stock_prices(self, codes: list) -> list:
        return _run_sync(self._with_session(self.aget_all_stock_prices, codes))

    def get_all_daily_charts(self, codes: list, days: int = 60) -> dict:
        return _run_sync(
            self._with_session(self.aget_all_daily_charts, codes, days))

    def get_all_investor_trading(self, codes: list, days: int = 10) -> dict:
        return _run_sync(
            self._with_session(self.aget_all_investor_trading, codes, days))

    def get_kospi_stock_list(self) -> list:
        return _run_sync(self._with_session(self.aget_kospi_stock_list))

    def check_token(self) -> bool:
        return self._token_manager.check()  # 기존 sync 경로 유지

    # ---------- 내부 ----------
    async def _with_session(self, coro_fn, *args, **kwargs):
        """sync 래퍼용: 세션 컨텍스트를 매 호출마다 관리."""
        if self._session is not None:
            return await coro_fn(*args, **kwargs)
        async with self:
            return await coro_fn(*args, **kwargs)

    async def _request_get(
        self, path: str, tr_id: str, params: dict,
        extra_headers: Optional[dict] = None,
    ) -> dict:
        """aiohttp 기반 GET + 재시도 + rate limit."""
        url = f"{KISConfig.BASE_URL}{path}"
        headers = await self._get_headers(tr_id)
        if extra_headers:
            headers.update(extra_headers)
        for attempt in range(1, KISConfig.MAX_RETRIES + 1):
            async with self._limiter:
                try:
                    async with self._session.get(
                        url, headers=headers, params=params,
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if data.get("rt_cd") == "0":
                                return data
                            # API 레벨 에러 → 재시도
                        # else: HTTP 에러 → 재시도
                except aiohttp.ClientError as e:
                    logger.warning(
                        "네트워크 에러 (%d/%d): %s",
                        attempt, KISConfig.MAX_RETRIES, e,
                    )
            if attempt < KISConfig.MAX_RETRIES:
                await asyncio.sleep(KISConfig.RETRY_BACKOFF_BASE ** attempt)
        raise KISAPIError(f"{path} 최대 재시도 초과")

    def _check_failure_rate(
        self, failures: int, total: int, label: str,
    ) -> None:
        if total == 0:
            return
        rate = failures / total
        if rate > self._fail_threshold:
            raise KISBatchFailureError(
                f"{label} 실패율 {rate:.1%} > 임계치 "
                f"{self._fail_threshold:.0%} ({failures}/{total})")


def _run_sync(coro):
    """이벤트 루프 상태에 따라 안전하게 async를 sync에서 호출.

    - 루프 없음 (예: update_performance_tracking 내 sync 경로): asyncio.run
    - 루프 실행 중 (예: 이미 async 컨텍스트): 에러 (호출부가 await해야 함)
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError(
        "이미 실행 중인 이벤트 루프에서 sync 래퍼를 호출할 수 없다. "
        "async 컨텍스트에서는 a-prefixed 메서드를 직접 await하라.")
```

### 4.2 main.py `_collect_data` 변경

핵심은 기존 동기 배치 호출을 **단일 `async with KISClient()`와 `asyncio.gather`로 묶는** 것.

```python
async def _collect_data(self, target_codes=None):
    async with self.kis:  # 세션 컨텍스트
        if target_codes is None:
            # 월요일: 전종목 스캔
            price_list = await self.kis.aget_kospi_stock_list()
            # 1차 필터 (변경 없음)
            price_list = [...]

            # PER/PBR 보강: 현재 수백 종목 sync loop → 병렬화
            codes = [p["stock_code"] for p in price_list]
            details = await self.kis.aget_all_stock_prices(codes)
            detail_map = {d["stock_code"]: d for d in details}
            for p in price_list:
                d = detail_map.get(p["stock_code"], {})
                p["per"] = d.get("per", 0.0)
                p["pbr"] = d.get("pbr", 0.0)
                # ...
        else:
            price_list = await self.kis.aget_all_stock_prices(target_codes)
            # ... 종목명 보강 (변경 없음, DB 조회만)

        stock_codes = [p["stock_code"] for p in price_list]

        # 일봉 + 수급 동시 실행 (limiter가 전체 rate 제어)
        chart_task = self.kis.aget_all_daily_charts(stock_codes, days=60)
        investor_task = self.kis.aget_all_investor_trading(stock_codes, days=25)
        chart_dict, investor_dict = await asyncio.gather(
            chart_task, investor_task,
        )
        # 병합 로직은 기존 그대로
```

**주의 — 동시 실행 시 limiter가 공유되는지**: `self._limiter`는 인스턴스 변수라 같은 `KISClient` 인스턴스의 모든 코루틴이 **같은 버킷**을 공유한다. 따라서 `gather`로 두 배치를 동시 실행해도 초당 총 15콜을 넘지 않는다.

### 4.3 database/models.py — 무수정 검증

`update_performance_tracking`에서 `kis_client.get_stock_price(code)` 호출.
- sync 컨텍스트(루프 없음) → `_run_sync` 경로 → `asyncio.run(...)` 실행 → OK.
- 단, 이 함수는 스케줄러 내에서는 호출되지 않는다. 수동 트리거 또는 별도 CLI 경로에서만 호출된다는 전제 확인 필요 (현재 그러함).

만약 향후 이 메서드가 async 컨텍스트에서 호출되면 `_run_sync`가 RuntimeError를 던진다. 이때는 호출부를 `await kis_client.aget_stock_price(code)`로 수정.

### 4.4 3개 배치 동시 실행 시 Rate 계산

- 만약 월요일 전종목 PER/PBR 보강 + 차트 + 수급을 모두 gather:
  - 보강 932콜 + 차트 932콜 + 수급 932콜 = **2796콜**.
  - 초당 15콜 → 2796 / 15 = **186초 ≒ 3분**.
  - 현재 추정 7분 (PER/PBR 보강 ≒ 4분 + 차트 1.7분 + 수급 1.7분) 대비 **~55% 단축**.
- 화~금 (이미 필터된 target_codes, 200~300건):
  - 현재 차트+수급+재무 = 18분 중 DART(out-of-scope) 11분 제외하면 네트워크 대기 ~7분.
  - async 전환 후 네트워크 대기 부분은 ~1분 미만으로 떨어지지만, **DART 11분이 잔존** → 전체 소요는 ~12분으로 소폭 개선.
  - DART도 async하면 큰 개선. 별도 TODO로 이미 있음.

---

## 5. 하위 호환성 매트릭스

| 호출부 | 기존 API | 변경 필요 | 비고 |
|-------|---------|---------|------|
| main.py `_collect_data` | sync 배치 | **async로 수정** | 이미 async 함수라 await만 추가 |
| main.py 포트폴리오 보강 loop (L320-321) | sync 단건 | **async로 수정 권장** | 또는 sync 래퍼 유지 (블로킹 감수) |
| main.py `check_token` | sync | 무수정 | 토큰 매니저는 sync 유지 |
| main.py test 모드 (L99-104) | sync | 무수정 (sync 래퍼 경유) | |
| models.py `update_performance_tracking` | sync 단건 | **무수정** | sync 래퍼 경유 |

---

## 6. 리스크와 완화

| 리스크 | 확률 | 영향 | 완화 |
|--------|-----|-----|------|
| aiohttp 세션 누수 | 중 | 중 | `async with` 필수. sync 래퍼는 `_with_session`이 세션 생성/종료 책임 |
| 토큰 동시 갱신 경합 | 중 | 높 | `self._token_lock = asyncio.Lock()`로 `_get_headers` 내부 보호 |
| Rate limit 초과 | 낮 | 중 (KIS IP 차단 가능) | aiolimiter 엄격 + 마진 25% + 배치 시작 시 토큰 버킷 재초기화 |
| 재시도 중복 요청 부작용 | 낮 | 저 | KIS 조회는 GET만 사용 → 멱등. 문제 없음 |
| sync 래퍼가 async 컨텍스트에서 호출 | 중 | 중 | `_run_sync`에서 RuntimeError. 실행 시 즉시 발견됨 |
| `_collect_data` 내부 블로킹 잔재 | 중 | 중 (텔레그램 poll stall) | 구현 후 CPU 프로파일로 블로킹 지점 검출 |
| asyncio.gather에서 한 태스크 예외 | 중 | 높 | `return_exceptions=True` + 개별 검사로 `KISBatchFailureError` 일관 처리 |
| DART sync 호출이 여전히 blocking | 높 | 중 | 별도 TODO. 단기엔 감수 |

---

## 7. 테스트 전략

### 7.1 의존성 추가

`requirements.txt`에:
```
aiolimiter>=1.1.0
```
`requirements-dev.txt` (신규 파일 권장):
```
pytest>=8.0
pytest-asyncio>=0.23
aioresponses>=0.7
```

### 7.2 단위 테스트

- `tests/test_kis_async.py` 신규:
  - `aioresponses`로 HTTP mock.
  - `aget_stock_price` 단건.
  - `aget_all_stock_prices` 배치 + 일부 실패 시나리오.
  - 실패율 20% 초과 시 `KISBatchFailureError` 발생.
  - Rate limiter 실제 시간 확인 (100콜 / 15콜 초 ≒ 7초 이상 보장).
- 기존 `tests/test_survivorship.py`는 sync 래퍼 경로로 **변경 없이 통과해야 함**. 이게 하위 호환의 리트머스 테스트.
- 기존 `tests/test_integration.py`는 kis_api mock 상태라 그대로.

### 7.3 통합 테스트

- KIS 모의투자 환경 10종목 조회. 실제 토큰·rate 확인.

### 7.4 부하 테스트

- 100종목 배치 3회 연속 호출 → rate 초과 없음 확인.
- 로그에서 `[2/6] 데이터 수집` 소요 시간 기록.

---

## 8. 롤아웃 계획

1. **설계 승인** (이 문서).
2. 브랜치 `feat/async-kis` 에서 `aget_*` 구현 + 단위 테스트 통과.
3. sync 래퍼 추가 + `test_survivorship.py` 통과 확인.
4. `main.py._collect_data`만 async 전환. 나머지 호출부는 sync 래퍼.
5. **Dry run**: 평일 스케줄 외 시각에 수동 `python main.py --analyze-now` (혹은 동등 명령)로 실측. 결과물은 버림.
6. 벤치마크 비교 표 작성 (전/후: 소요 시간, 성공률, 실패율).
7. 운영 스위치: 평일 장 마감 후 봇 재시작. 첫 15:40 스케줄 모니터링.
8. 문제 시 즉시 롤백 (9절).

---

## 9. 롤백 절차

- 단일 커밋 단위로 작성해서 **`git revert <hash>` 5분 내 복원**.
- 긴급 완화책: 환경변수 `RATE_LIMIT_PER_SEC=2`로 낮추면 실효 초당 2콜로 기존 동기 속도 수준 유지. async 전환 자체는 유지되지만 부하는 현재와 동일.
- DB 스키마 변경 없음 → DB 롤백 불필요.

---

## 10. 의존성 추가 요약

### 운영
- `aiolimiter>=1.1.0` (신규 / MIT)
- `aiohttp>=3.9.0` (이미 requirements.txt에 명시, 설치됨 3.13.5)

### 개발
- `pytest>=8.0`
- `pytest-asyncio>=0.23`
- `aioresponses>=0.7`

운영 Python 3.10.12에서 위 전부 호환.

---

## 11. 미결정 사항 (사용자 판단 요청)

1. **월요일 PER/PBR 보강 루프 병렬화 여부**
   현재 `for i, price in enumerate(price_list)` 루프로 수백 종목 sync 조회.
   async 전환 시 이를 `aget_all_stock_prices` 호출로 교체하면 가장 큰 이득.
   단, 이 경우 기존 `get_kospi_stock_list`의 반환 항목에 이미 있는 가격과
   `aget_stock_price`의 상세 PER/PBR/EPS/BPS/sector를 **별도 dict로 merge**해야 함.
   → 이 병렬화 포함 여부를 확인 바람. **기본값: 포함 권장**.
2. **포트폴리오 개별 보강 (main.py L320-321) 처리**
   - (a) sync 래퍼 유지 (이벤트 루프 블로킹 감수. 수 종목이라 실사용 영향 작음)
   - (b) async 전환 (완결성 ↑, 수정 범위 ↑)
   → **기본값: (b) async 전환**.
3. **`KISBatchFailureError` 임계치 20% 적정성**
   - 장중 KIS 일부 장애 시 일시 실패율 ↑ 가능성.
   - 10%는 너무 엄격, 30%는 너무 느슨할 수 있음.
   → **기본값: 20% 시작, 3개월 운영 후 조정**.
4. **aiolimiter 실 측정 rate**
   - AsyncLimiter는 이상적 토큰 버킷. 실제 KIS는 초당 20콜 "초과 시 즉시 차단"인지, "일정 시간 평균"인지 공식 문서 확인 필요.
   - 공식 문서 링크 확인 못함. **보수적으로 15콜 유지 권장**.
5. **재시도 실패 시 최종 스킵 vs raise 정책**
   - 현재 동기 코드: 3회 재시도 후 `KISAPIError` raise → 상위에서 종목 단위 스킵.
   - async에서도 동일 유지. 문제 없음.
6. **dart_api.py async 전환 병행 여부**
   - 18분 파이프라인 중 11분이 DART. async 전환의 전체 효과를 느끼려면 함께 해야 함.
   - 단, 이번 사이클 범위는 명시적으로 KIS만이라 제외.
   → **DART는 별도 P1 TODO로 후속 제안 필요**.

---

## 12. 다음 단계

이 문서 승인 시:
1. `feat/async-kis` 브랜치 생성.
2. `collectors/kis_api.py` 구현 (약 L+급 작업, 2주 예상).
3. `requirements.txt` / `requirements-dev.txt` 갱신.
4. 단위 테스트 + 하위 호환 리트머스 (`test_survivorship.py`) 통과.
5. `main.py._collect_data` 수정.
6. Dry run + 벤치마크.
7. 운영 스위치.
