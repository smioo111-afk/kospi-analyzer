# async 벤치마크 결과

측정 시각: 2026-04-22 12:32:28
환경: aioresponses mock (실 네트워크 없음), Python 3.10.12
도구: tools/benchmark_collect.py

## 목적

rate_limit_per_sec 설정이 실효 처리량에 미치는 영향과
aiolimiter 토큰 버킷의 이론값 대비 실측 괴리를 확인한다.
실 KIS API에 의존하지 않으므로 네트워크 지터·KIS 서버 응답
시간은 제외된 "클라이언트 한계" 측정이다.

## 결과

### 단일 배치 쌍 (prices + charts, 순차 실행)

| N codes | rate | elapsed | theory min | eff calls/s | price OK | chart OK |
|--------:|-----:|--------:|-----------:|------------:|:--------:|:--------:|
|     10 |    2 |     9.00s |    9.00s |     2.22 | 10/10 | 10/10 |
|     50 |    2 |    49.00s |   49.00s |     2.04 | 50/50 | 50/50 |
|     10 |   15 |     0.34s |    0.33s |    59.57 | 10/10 | 10/10 |
|     50 |   15 |     5.67s |    5.67s |    17.64 | 50/50 | 50/50 |
|    200 |   15 |    25.67s |   25.67s |    15.58 | 200/200 | 200/200 |

### 운영 시뮬레이션: 3 배치 concurrent gather (prices + charts + investors)

main.py `_collect_data`의 실제 패턴. 같은 KISClient 인스턴스의
`_limiter`를 3 배치가 공유하므로 총 rate는 rate_limit_per_sec로 강제된다.

| N codes | rate | elapsed | theory min | eff calls/s | success |
|--------:|-----:|--------:|-----------:|------------:|:-------:|
|    932 |   15 |   185.49s |  185.40s |    15.07 | 932/932 (p) + 932/932 (c) + 932/932 (i) |

### 필드 정의

- **elapsed**: `aget_all_stock_prices + aget_all_daily_charts` 총 소요 시간
- **theory min**: 토큰 버킷 이론 최소 시간
  - 초기 버킷 크기 = rate (burst 허용)
  - 첫 rate개는 즉시 소비, 이후 (N-rate)/rate 초
  - 2N콜 (prices + charts) 기준
- **eff calls/s**: `(2 × N) / elapsed`, 실측 처리량
- **OK 비율**: 성공 건수 / 요청 건수. mock이라 항상 100%.

### 해석 가이드

1. N이 rate보다 크면 총 소요 ≈ theory min. 오버헤드는
   (elapsed - theory_min) / N 로 계산되는 콜당 상수.
2. rate=2 vs rate=15의 배율은 이론상 7.5배. 실측도 비슷해야 정상.
3. 실제 KIS 환경에서는 네트워크 응답 시간이 더해지지만, 현재
   파이프라인 병목은 rate limit이지 네트워크 지연이 아니라는
   것이 주요 가정 (초당 2콜은 명백히 rate-bound).

## 운영 환경 예상 (932 종목 기준, 월요일 전종목 스캔)

PER/PBR 보강 + 차트 + 수급 ≒ **3N 콜 = 2796 콜**.
- rate=2  (롤백): 이론 (2796-2)/2 ≈ 1397s ≒ 23분
- rate=15 (기본): 이론 (2796-15)/15 ≈ 185s ≒ 3분
- 실제 KIS는 네트워크 응답 평균 100-200ms 가산. 3개 배치가
  gather로 병렬 실행되면 네트워크 지연은 rate 기다림과 겹쳐
  대부분 숨겨짐. 최종 예상 3~4분.

현 운영 로그(2026-04-07): 203 종목 `_collect_data` 18분 중 KIS
네트워크 대기 ~7분. async 전환 후 **7분 → ~2분** 예측.
DART 재무 11분은 변동 없음 (out-of-scope).

## 롤백 경로 검증

`KIS_RATE_LIMIT_PER_SEC=2` 환경변수로 기존 sync 속도 수준을
재현할 수 있음을 벤치에서 확인(rate=2 case).
