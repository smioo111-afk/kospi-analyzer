# `save_analysis_result` 호출 누락 추적 (D-4, 2026-04-26)

## 결론 (정정)

**`save_analysis_result`는 정상 호출되고 있다**. `analysis_results.kospi_index`가
100% 0인 진짜 원인은 `main.py:298`의 `self.history.save_daily_result(...)` 호출
시 `kospi_index`/`foreign_net_buy` **인자가 빠진 채 default 0이 사용**되기 때문.

이번 묶음 D 작업으로 KOSPI 지수 수집 추가 + 두 인자 전달이 해결책.

## 추적 경로

```
main.py:298  self.history.save_daily_result(
                  analysis_date=...,
                  top_10=...,
                  warnings=...,
                  all_signals=...,
                  stats=...,
                  stoploss_map=...,
                  # ← kospi_index, foreign_net_buy 누락
              )
   ↓
database/history.py:105  save_daily_result(
                            ..., kospi_index: float = 0.0,
                                 foreign_net_buy: int = 0)
   ↓ (line 129)
database/models.py:291  save_analysis_result(..., kospi_index=0.0, ...)
   ↓
INSERT INTO analysis_results (..., kospi_index=0, foreign_net_buy=0)
```

매일 호출은 정상이나 두 필드만 default 0으로 입력됨.

## git log 점검

- baseline 커밋 `dc67092 chore: initial baseline before async refactoring` 시점부터 main.py에 KOSPI 지수 수집 코드 자체가 없음. 즉 누락 시점이 아니라 **처음부터 미구현** 상태.
- async refactor (`c7ad346`)와 무관. KIS 지수 endpoint 호출이 단순히 작성된 적이 없음.

## 다른 미호출 public 메서드 점검

`Database` 클래스 35개 public 메서드 중 main/bot/tools/analysis/collectors에서 직접 호출 안 되는 것:

| 메서드 | 실제 사용 | 비고 |
|---|---|---|
| `save_analysis_result` | ✓ HistoryService 경유 | 이번 작업 영향 받음 |
| `save_stock_scores` | ✓ HistoryService 경유 | 정상 |
| `cleanup_old_data` | ✓ HistoryService 경유 | 정상 |
| `update_performance_tracking` | ✓ main.py:397 (to_thread) | grep 누락 |
| `save_financial_metrics` | ✓ batch 변종 사용 | 정상 |
| `get_results_by_date` | ✗ 미호출 | 향후 백테스트용 (P3) |
| `update_portfolio_stock_names_from_master` | ✗ 미호출 | 운영 도구 (P3) |
| `mark_stock_delisted` | ✗ 미호출 | 운영자 도구 (의도) |
| `get_delisted_stocks` | ✗ 미호출 | 텔레그램 명령용 후보 (P3) |
| `get_fetch_failure_candidates` | ✗ 미호출 | 디버깅 도구 (P3) |

운영자 도구 의도 메서드 외 진짜 미사용 5개. 이번 작업 직접 영향 없음.

## 권고

- 즉시: 묶음 D 그대로 진행 — KIS 지수 수집 + main.py:298 인자 전달.
- 별도 P3: `get_results_by_date`(백테스트), `get_delisted_stocks`/`get_fetch_failure_candidates`(텔레그램 명령 또는 운영 대시보드) 활용 검토.
