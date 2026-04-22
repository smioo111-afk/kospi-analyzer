# async rate limit 동작 분석

작성일: 2026-04-22
배경: Phase 5 드라이런에서 실효 rate 31 calls/s 관측. rate_limit_per_sec=15보다
높은 값이라 burst인지 batch 독립인지 확인 필요.

## 1. `_limiter` 공유 검증

### 코드상 구조

```python
class KISClient:
    def __init__(self, rate_limit_per_sec=None, ...):
        self._limiter: Optional[AsyncLimiter] = None  # 인스턴스 속성

    async def __aenter__(self):
        self._limiter = AsyncLimiter(self._rate, time_period=1.0)
        return self

    async def _request_get(self, ...):
        async with self._limiter:       # self.의 limiter 사용
            ...
```

### 결론

같은 `KISClient` 인스턴스의 모든 요청이 **`self._limiter` 하나**를 공유한다.
main.py에서:

```python
async with self.kis:              # __aenter__ 1회 → limiter 1개 생성
    prices_t = self.kis.aget_all_stock_prices(codes)
    charts_t = self.kis.aget_all_daily_charts(codes, days=60)
    inv_t    = self.kis.aget_all_investor_trading(codes, days=25)
    await asyncio.gather(prices_t, charts_t, inv_t)
                                  # 세 배치가 같은 self._limiter로 rate 분배
```

배치 3개가 각각 내부적으로 `asyncio.gather(*N tasks)`를 쓰지만,
모든 태스크가 결국 `self._limiter`를 통과하므로 총 합산 rate가
`rate_limit_per_sec`로 강제된다.

코드 인스펙션 검증:
```
_request_get가 self._limiter 사용: True
aget_all_stock_prices → self.aget_*: True
aget_all_daily_charts → self.aget_*: True
aget_all_investor_trading → self.aget_*: True
```

## 2. 드라이런의 31 calls/s 해석

### 측정값

- 5 종목 × 3 API = 15 콜
- 소요: 0.48s
- 실효: 15 / 0.48 ≒ 31.5 calls/s
- `rate_limit_per_sec` 설정: 15

### 원인: aiolimiter 토큰 버킷의 burst 허용

`AsyncLimiter(15, time_period=1.0)`의 동작:

- **용량(capacity) = 15 토큰**. 생성 시점에 **가득 찬 상태**로 시작.
- **리필 속도**: 1초마다 15 토큰 (= 15/sec).
- **획득**: `async with limiter:` 시 1 토큰 소비. 빈 버킷이면 대기.

드라이런은 15 콜 = burst 용량과 정확히 일치.
- 시점 0: 버킷 15/15 → 15개 동시 시도 → 전부 통과
- 이후 HTTP 응답 대기만 남음 (실 KIS 응답 시간 ≒ 400ms)
- 15 / 0.48 = 31 calls/s는 **버킷 용량의 순간 소진 속도**

즉 `rate_limit_per_sec=15`는 **"1초 윈도우 안에 15 이하"**를 보장하지,
**"매 콜마다 1/15초 대기"**를 보장하지 않는다. 첫 15콜은 즉시, 16번째부터
리필 속도에 묶인다.

### 공식

```
총 N 콜, rate R (burst = R)
이론 최소 시간 = max(0, (N - R) / R) + 네트워크_지연
실효 rate    = N / 이론_최소_시간
```

| N | R | (N-R)/R | 실효 상한 | 비고 |
|---|---|---------|-----------|------|
| 15 | 15 | 0 | 무한대 (네트워크 한계) | 버스트 |
| 30 | 15 | 1s | 30 / 1.4s = ~21 | 버스트+1초 |
| 100 | 15 | 5.67s | 100 / ~6s = ~17 | 수렴 |
| 2796 | 15 | 185.4s | 2796 / 185.4 = 15.1 | 거의 이론 rate |

### 실측 증거 (벤치마크)

| N | rate | 실측 elapsed | 실효 rate | 수렴? |
|---|------|-------------|----------|------|
| 20 콜 | 15 | 0.34s | 59.6 | 버스트 구간 |
| 100 콜 | 15 | 5.67s | 17.6 | 수렴 중 |
| 400 콜 | 15 | 25.67s | 15.6 | 거의 수렴 |
| **2796 콜** | 15 | **185.49s** | **15.07** | **완전 수렴** |

N이 커질수록 실효 rate → rate_limit_per_sec로 수렴.

### 실 KIS 입장

KIS 공식 한도는 "초당 20콜" 즉 1초 윈도우 기반. 우리 버스트는 15콜을
**첫 1초 안에** 소진. KIS 쪽도 같은 윈도우 기준이면 safe.
KIS가 더 엄격한 "매 50ms 이상 간격" 같은 규칙을 적용한다면 burst가 문제될 수
있으나, 공개 문서에는 그런 제약이 명시되어 있지 않다. 본 설계는 **초당 합계**
기준으로 설계되어 있으며 2796콜 시뮬레이션에서 장기 rate 15.07 (설정 15의
100.5%) 수준으로 수렴해 문제 없음을 검증.

## 3. 실전 932 종목 × 3 API 시뮬레이션

### 조건

- 932 종목
- 3 endpoints: 현재가 + 일봉 + 수급 = 2796 콜
- 운영 패턴과 동일: `asyncio.gather(prices_task, charts_task, inv_task)`
- 각 배치 내부도 gather로 병렬
- 같은 KISClient 인스턴스의 공유 `_limiter`
- aioresponses mock으로 네트워크 지연 없음 (순수 limiter 동작)

### 결과

| 항목 | 값 |
|------|-----|
| 설정 rate | 15/sec |
| 총 콜 | 2796 |
| 소요 시간 | **185.49s** (3분 5초) |
| 이론 최소 | 185.40s |
| 괴리 | +0.09s (0.05%) |
| 실효 rate | 15.07 calls/s |
| 성공률 | prices 932/932, charts 932/932, investors 932/932 |

### 해석

- **이론과 거의 완벽하게 일치**. aiolimiter 구현이 정확.
- 3 배치가 같은 `_limiter`를 공유하므로 독립적으로 rate를 초과하지 않음.
- 실 KIS에서는 네트워크 응답 시간이 가산되지만 초당 15콜 페이스 안에서
  병렬 대기로 대부분 숨겨진다 (응답 대기 중에도 다음 토큰 리필은 진행).
- **예상 운영 소요**: 2796콜 × 15 rate = 3분 + α(네트워크) ≒ **3~4분**.

현재 운영 로그(2026-04-07) 203 종목 `_collect_data` 18분 중 KIS 네트워크
부분 ~7분을 async 전환으로 **~3분으로 단축** 가능. 절대 수치로는 4분 개선,
DART(11분 out-of-scope) 때문에 전체 파이프라인은 18분 → ~14분.

## 4. 주의 사항 (실전 반영 시)

1. **burst를 줄이고 싶다면** `AsyncLimiter(rate, time_period=1.0)`에서
   `time_period`를 늘리지 말고 `rate`를 줄이는 게 의미론적으로 명확하다.
   예: `rate=10`으로 낮추면 버스트도 10, 지속도 10/sec.
2. **rate를 올리고 싶다면** KIS 공식 한도 20/sec를 넘지 말 것. 현재 75%
   활용 중. 80% 상한 권장.
3. **KIS 일시 장애** 시 실패율이 올라가면 `KISBatchFailureError`(>20%) 발동.
   `_collect_data` 상위에서 catch → 해당 파이프라인 실행만 실패, 스케줄러
   유지. 다음 실행에서 자동 복구.
4. **burst는 초기 진입 시에만** 발생. 연속 실행에서는 리필 중이라
   burst 용량이 줄어든 상태에서 시작할 수 있음. 장기 서비스라면
   수렴 rate = `rate_limit_per_sec`.

## 결론

- `_limiter`는 KISClient 인스턴스 속성이라 같은 세션의 모든 호출이 공유. ✓
- 31 calls/s는 **burst 소진 구간의 순간 rate**. 장기 rate는 설정값에 수렴. ✓
- 2796콜 시뮬레이션에서 실측 = 이론 (오차 0.05%). ✓

운영 반영 시 리스크 없음.
