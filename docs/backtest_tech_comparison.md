# 백테스트 엔진 기술 비교 (2026-04-22)

## 결론 미리보기

1. **bt** — 월/주 단위 리밸런싱, 유지보수 저비용, 한국 데이터 연동 용이.
   현재 프로젝트 맥락에 가장 적합.
2. **직접 구현 (pandas)** — 학습 곡선 사실상 0, 완전한 통제. 단 "생존편향/룩어헤드
   자체 방어"를 스스로 구현해야 한다는 부담.
3. **vectorbt** — 파라미터 스윕/대규모 실험 단계에서 유용. 지금은 과한 무기.

(backtrader는 사실상 유지보수 중단, zipline-reloaded는 과잉 스택이므로
후보에서 제외 권장.)

## 비교표

| 항목 | pandas 직구현 | vectorbt | backtrader | bt | zipline-reloaded |
|------|---------------|----------|-----------|-----|------------------|
| 유지보수 | N/A (core) | 활발 (2026-03 릴리스) | 사실상 중단 (2023-04 마지막 commit) | 활발 (2026-03 릴리스) | 활발 (2025-07 릴리스, 2026-01 push) |
| 스타 수 (대략) | pandas 45k | 7.3k | 21k | 2.9k | 1.7k |
| 라이선스 | BSD-3 | Apache-2.0 + Commons Clause | GPL-3.0 | MIT | Apache-2.0 |
| 한국 시장 First-party 지원 | 없음 | 없음 | 없음 | 없음 | 없음 (XKRX 캘린더는 exchange_calendars에 있음) |
| pykrx / KIS 연동 난이도 | 가장 쉬움 (그냥 DataFrame) | 쉬움 | 중간 (data feed 래퍼 필요) | 쉬움 | 어려움 (custom bundle 필요) |
| 학습 곡선 | 없음 | 높음 (자체 API/Numba) | 높음 (이벤트 드리븐 프레임워크) | 낮음~중간 (tree-of-algos 개념) | 높음 (Pipeline/bundle/Commission) |
| 벡터화 / 속도 | 부분 (직접 구현) | 최상위 (Numba JIT, 수천 파라미터) | 느림 (이벤트 루프) | 중간 (rebalance 단위) | 중간~느림 (이벤트 드리븐) |
| 포트폴리오 단위 | 직접 구현 | 강함 | 중간 | 매우 강함 (트리/nested strategy) | 강함 |
| 거래비용 유연성 | 완전 자유 | 유연 (per-order 파라미터) | 매우 유연 (CommInfoBase) | 유연 (commissions callable) | 유연 (PerShare/PerTrade/...) |
| 룩어헤드 방지 | 스스로 (.shift 등) | 스스로 | 이벤트 구조로 내장 | 없음 (rebalance 시점 기반) | 최상위 (Pipeline 강제) |
| 생존편향 방지 | 스스로 | 없음 | 없음 | 없음 | 데이터 번들 의존 (Norgate/Sharadar 등) |
| 리스크 엔진 부착 | 자유 | 부착 가능 (커스텀 Portfolio) | 자유 (Cerebro) | 자유 (Algo 추가) | 자유 (Pipeline factor) |
| 실전매매 연동 | 자체 (KIS SDK 직결) | 공식 연동 없음 | IB/Oanda/VisualChart 내장, 한국 브로커는 KOAPY 커뮤니티 | 없음 (백테스트 전용) | 공식 없음 (zipline-trader 등 3rd-party) |
| 한국 브로커 연동 | 100% 수동 (자체 KIS 연동) | 수동 | 커뮤니티 KOAPY | 불가 | 수동 |
| 개인 프로젝트 적합도 (1~5) | 4 | 3 | 2 | 5 | 2 |

## 각 후보 상세

### 1. 직접 구현 (pandas + numpy)

장점:
  - 학습 곡선 없음. 현재 코드베이스와 그대로 연속.
  - 완전한 투명성. 룰 기반 전략이 무엇을 하는지 한 줄씩 확인 가능.
  - 한국 시장 거래세/수수료/가격제한폭/시간대를 그대로 쓸 수 있음 (파이썬 함수 작성 뿐).
  - pykrx로 가격, DART로 재무, 모두 이미 연동 완료.
  - 실전매매 전환 시 이질감 없음 (KIS API 이미 사용 중).

단점:
  - 룩어헤드 편향/생존편향 방지를 스스로 만들어야 한다 (이미 TODO에 있음).
  - 테스트 커버리지, 수익 곡선 지표(Sharpe, MDD 등)를 직접 구현해야 한다.
  - 파라미터 스윕/워크포워드 검증 인프라가 없어 대규모 실험은 느림.

추천 상황:
  - "지금까지의 신호가 실제로 먹혔는가"를 빠르게 확인하는 1~2주 단기 실험.
  - 이미 축적된 `performance_tracking` / `daily_report_log` 데이터로
    간단한 기간 수익률 재현 수준의 백테스트.

출처: https://github.com/sharebook-kr/pykrx , https://github.com/flowtide/pykrx-backtesting

### 2. vectorbt (polakowo/vectorbt)

장점:
  - Numba JIT. 수천 개 파라미터 조합을 분 단위에 스윕.
  - `Portfolio.from_signals` 등 간결한 API. multi-asset 포트폴리오 내장.
  - OHLCV가 pandas DataFrame이면 그대로 사용 가능 → pykrx와 잘 맞는다.

단점:
  - 학습 곡선. 자체 idx/index 체계, Numba 제약 때문에 커스텀 인디케이터 작성이 거칠다.
  - 룩어헤드/생존편향 방지 자동화 없음.
  - OSS 버전의 라이선스가 Apache-2.0 + Commons Clause (GitHub "Other"로 표기).
    상업 배포 제약 존재. 저자는 PRO 유료판에 중점.
  - 실전매매 연동 없음 (CCXT 정도).

추천 상황:
  - 룰 기반 전략이 안정된 뒤, 수십 개 파라미터 조합을 탐색하는 단계.
  - 팩터 리서치 단계.

출처: https://github.com/polakowo/vectorbt , https://vectorbt.dev/

### 3. backtrader (mementum/backtrader)

장점:
  - 이벤트 드리븐 구조 → 룩어헤드 편향이 구조적으로 차단.
  - Commission/Slippage 모델이 매우 유연.
  - Cerebro + Strategy 패턴이 친숙 (Quantopian 출신 자료 많음).

단점:
  - **유지보수 사실상 중단** (마지막 커밋 2023-04). 커뮤니티 포크 논의 중.
  - GPL-3.0 → 자체 전략 배포 시 바이러스성 라이선스 경계.
  - 벡터화 없음. 대규모 유니버스에서 느림.
  - 한국 브로커 연동은 KOAPY 같은 3rd-party 조합 필요.

추천 상황:
  - (추천하지 않음. 살아있는 대안이 있다.)
  - 기존 자산이 있을 때만 유지.

출처: https://github.com/mementum/backtrader , https://community.backtrader.com/topic/3702/is-backtrader-dead

### 4. bt (pmorissette/bt)

장점:
  - 트리 기반 Strategy/Algo 조합 (RunMonthly + WeighEqually + RebalanceIfOutOfBounds 등).
    현재 프로젝트의 "월/주 단위 리밸런싱" 요구와 일대일 대응.
  - 코드량 최소. 학습 곡선 낮음.
  - pandas DataFrame 그대로 입력.
  - MIT 라이선스.
  - 2026-03 최신 릴리스. 유지보수 유지.

단점:
  - 백테스트 전용. 실전매매 연동 없음.
  - 룩어헤드 보호가 약하다 (rebalance 시점 기반이라 실수 여지 있음).
  - 이벤트 틱 단위 시뮬레이션이 필요한 HFT에는 부적합 (현재 프로젝트는 아님).

추천 상황:
  - 월/주 리밸런싱 전략 백테스트의 표준.
  - 여러 서브 포트폴리오/업종 분산 제약 실험.

출처: https://github.com/pmorissette/bt , https://pmorissette.github.io/bt/

### 5. zipline-reloaded (stefan-jansen/zipline-reloaded)

장점:
  - 룩어헤드 방어가 강함 (Pipeline API).
  - 생존편향 방지도 데이터 번들 차원에서 지원 (Norgate/Sharadar 유료).
  - 학술 자료가 풍부. 《Machine Learning for Algorithmic Trading》 교과서와 직결.

단점:
  - 한국 시장을 쓰려면 **커스텀 번들**을 직접 구현해야 함 (XKRX 캘린더는 있음).
    진입 장벽이 가장 높다.
  - 실전매매 공식 연동 없음.
  - 스택 무거움. 개인 운영 부담 큼.
  - ML 통합/Pipeline 없이 쓰면 장점 대부분 소실.

추천 상황:
  - 본격 ML 백테스트 파이프라인 구축. 현재 단계에선 과잉.

출처: https://github.com/stefan-jansen/zipline-reloaded , https://zipline.ml4trading.io/bundles.html

## 이 프로젝트 맥락에서의 판단

우리 시스템 특징:
  - 리밸런싱 주기: 월 1회 또는 주 1회 수준 (HFT 아님).
  - 종목 수: 932개 이상 스캔.
  - 데이터 소스: KIS API + DART + pykrx (모두 pandas DataFrame 친화).
  - 자본 규모: 개인 (소규모).
  - 운영자: 개인 개발자 1명. 유지보수 비용 최소화 선호.
  - 최종 목표: 실전매매 연동.

이 맥락에서 각 후보를 재평가:

| 기준 | 가중 | pandas 직구현 | vectorbt | backtrader | bt | zipline-reloaded |
|------|------|--------------|----------|-----------|-----|------------------|
| 유지보수 저비용 | 높음 | 최상 | 중 | 하 | 상 | 하 |
| 한국 데이터 연동 | 높음 | 최상 | 상 | 중 | 상 | 하 |
| 월/주 리밸런싱 적합 | 높음 | 상 | 상 | 상 | 최상 | 상 |
| 생존편향/룩어헤드 지원 | 중 | 하 (자구) | 하 | 구조적으로 상 | 중 | 최상 |
| 실전매매 로드맵 호환 | 중 | 최상 (KIS 이미 사용) | 중 | 중 (KOAPY) | 하 | 하 |
| 파라미터 스윕 필요 | 낮음 | 하 | 최상 | 하 | 중 | 중 |

**1순위 후보: bt**
  - 월/주 단위 리밸런싱이 주력이면 bt의 트리/알고 조합이 그대로 들어맞는다.
  - 코드량이 가장 적게 나오면서 요구 기능 대부분을 커버.
  - MIT라 분산 공유/수정 자유도 높음.
  - 단, 생존편향/룩어헤드 방지를 프로젝트 코드 쪽에서 (TODO P1 작업으로)
    명시적으로 막아야 한다.

**2순위 후보: pandas 직구현**
  - "지금까지의 신호가 실제 먹혔는가"만 먼저 확인하는 단계라면
    직접 DataFrame 산술이 가장 빠르고 정확.
  - `tools/analyze_performance.py` 설계가 이미 pandas 기반이므로,
    이 스크립트를 발전시키면 자연스럽게 백테스트 레이어가 된다.

**3순위 후보: vectorbt**
  - 룰 기반 전략 안정 후, 팩터별 기여도를 정량화하거나
    점수 임계값 스윕을 돌리는 단계에 도입 고려.

**탈락: backtrader, zipline-reloaded**
  - backtrader: 유지보수 중단이 결정적.
  - zipline-reloaded: 한국 번들 제작 비용이 이득을 상회.

## 결정 시 고려할 질문

사용자가 선택 전에 스스로에게 물어볼 질문:
  1. 월 단위 리밸런싱이면 벡터화 속도가 정말 중요한가?
     → 중요하지 않다면 vectorbt는 과한 투자.
  2. 한국 거래세(0.18%, 매도 시)·수수료·호가 단위를 직접 모델링할 각오가 있는가?
     → 어느 프레임워크를 쓰든 결국 본인이 작성.
  3. 훗날 실시간 매매를 붙일 때, 백테스트 결과와 실전 주문 로직이
     같은 코드에서 나오길 원하는가 (= pandas 직구현에 유리)?
     아니면 백테스트는 검증 용도로 별도 스택이어도 괜찮은가?
  4. ML 모델(XGBoost/LightGBM) 통합 계획이 단기(6개월 이내)에 있는가?
     → 있다면 zipline 검토 의미 있음. 없다면 bt/pandas.
  5. 룩어헤드 편향 방지를 프레임워크에 위임하고 싶은가,
     코드 리뷰로 막고 싶은가?

## 다음 단계

1. 사용자가 이 문서를 읽고 1~3순위 중 하나 선택.
2. 선택 후 `TODO.md`의 `[P2][FEATURE] 백테스트 엔진 구축` 항목에
   `선택 기술: XXX` 노트 추가.
3. 착수 전 반드시 P1의 세 항목 (적중률 분석, 생존편향 제거,
   룩어헤드 편향 감사) 중 최소 생존편향+적중률은 완료 상태여야 한다.
   그래야 백테스트 결과의 베이스라인이 생긴다.
