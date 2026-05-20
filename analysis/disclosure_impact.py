"""DART 공시 영향 분석 + 단일 종목 점수 재계산 (A1 Phase 2).

매일 00:00 공시 모니터(Phase 4)가 본 모듈의 process_disclosures를
호출한다. needs_data_refresh가 True인 공시(PERIODIC/AMENDMENT/MA)에
대해 financial_metrics를 강제 재수집하고, 저장된 price_data + 새
financial_metrics + 기존 momentum_score로 점수를 재계산해 변화량을
산출한다.

momentum_score를 보존하는 이유:
  scorer는 chart_data(60일 일봉)가 있어야 정확한 모멘텀 점수를
  산출하지만, 자정 모니터 시점에는 chart 데이터가 별도로 축적되어
  있지 않다. 빈 chart로 호출하면 momentum이 0이 되어 비교가 왜곡된다.
  대신 직전 사이클의 momentum_score를 그대로 유지하고, 가치/재무/
  성장/퀄리티만 fresh metrics로 갱신한다. 다음 일일 풀 스캔(15:40)이
  chart까지 포함한 완전한 재계산을 다시 수행한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from analysis.scorer import ScoringEngine
from analysis.signals import Signal, SignalGenerator
from collectors.dart_disclosure import (
    Disclosure,
    needs_data_refresh,
)

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# 데이터 모델
# ----------------------------------------------------------------------
@dataclass
class ScoreSnapshot:
    """점수 스냅샷 (before/after 비교용). DB row 또는 scorer 결과에서 생성."""

    stock_code: str
    stock_name: str
    total_score: int
    value_score: int
    financial_score: int
    growth_score: int
    momentum_score: int
    quality_score: int
    signal: str = ""

    @classmethod
    def from_db_row(cls, row: dict[str, Any]) -> "ScoreSnapshot":
        return cls(
            stock_code=str(row.get("stock_code") or ""),
            stock_name=str(row.get("stock_name") or ""),
            total_score=int(row.get("total_score") or 0),
            value_score=int(row.get("value_score") or 0),
            financial_score=int(row.get("financial_score") or 0),
            growth_score=int(row.get("growth_score") or 0),
            momentum_score=int(row.get("momentum_score") or 0),
            quality_score=int(row.get("quality_score") or 0),
            signal=str(row.get("signal") or ""),
        )

    @classmethod
    def from_score_result(cls, result: dict[str, Any]) -> "ScoreSnapshot":
        return cls(
            stock_code=str(result.get("stock_code") or ""),
            stock_name=str(result.get("stock_name") or ""),
            total_score=int(result.get("total_score") or 0),
            value_score=int(result.get("value_score") or 0),
            financial_score=int(result.get("financial_score") or 0),
            growth_score=int(result.get("growth_score") or 0),
            momentum_score=int(result.get("momentum_score") or 0),
            quality_score=int(result.get("quality_score") or 0),
            signal=str(result.get("signal") or ""),
        )


@dataclass
class DisclosureImpact:
    """공시 1건의 영향 — before/after 점수 + 변화량."""

    disclosure: Disclosure
    stock_code: str
    before: Optional[ScoreSnapshot]
    after: Optional[ScoreSnapshot]
    total_diff: int = 0
    value_diff: int = 0
    financial_diff: int = 0
    growth_diff: int = 0
    momentum_diff: int = 0
    quality_diff: int = 0
    signal_changed: bool = False
    metric_changes: dict[str, Any] = field(default_factory=dict)

    @property
    def is_significant(self) -> bool:
        """5점 이상 변동 또는 신호 변경 시 유의미."""
        return abs(self.total_diff) >= 5 or self.signal_changed


# ----------------------------------------------------------------------
# 비교
# ----------------------------------------------------------------------
def compare_scores(
    before: ScoreSnapshot,
    after: ScoreSnapshot,
    disclosure: Disclosure,
) -> DisclosureImpact:
    """두 ScoreSnapshot을 받아 DisclosureImpact를 만든다."""
    return DisclosureImpact(
        disclosure=disclosure,
        stock_code=after.stock_code or before.stock_code,
        before=before,
        after=after,
        total_diff=after.total_score - before.total_score,
        value_diff=after.value_score - before.value_score,
        financial_diff=after.financial_score - before.financial_score,
        growth_diff=after.growth_score - before.growth_score,
        momentum_diff=after.momentum_score - before.momentum_score,
        quality_diff=after.quality_score - before.quality_score,
        signal_changed=(after.signal != before.signal),
    )


def get_score_snapshot(db: Any, stock_code: str) -> Optional[ScoreSnapshot]:
    row = db.get_stock_score(stock_code)
    if row is None:
        return None
    return ScoreSnapshot.from_db_row(row)


# ----------------------------------------------------------------------
# 재계산
# ----------------------------------------------------------------------
def _build_price_data_from_score_row(row: dict[str, Any]) -> dict[str, Any]:
    """저장된 stock_scores row의 가격 컬럼을 scorer가 받는 price_data dict
    형태로 변환. KIS 시세는 매일 갱신되므로 직전 값 그대로 사용해도 무방.
    """
    return {
        "stock_code": row.get("stock_code", ""),
        "stock_name": row.get("stock_name", ""),
        "current_price": int(row.get("current_price") or 0),
        "market_cap": int(row.get("market_cap") or 0),
        "per": float(row.get("per") or 0.0),
        "pbr": float(row.get("pbr") or 0.0),
        "dividend_yield": float(row.get("dividend_yield") or 0.0),
        "volume": 0,
        "trading_value": 0,
    }


def _invalidate_dart_cache(
    cache_dir: Any, stock_code: str, year: int,
    report_type: str = "annual",
) -> bool:
    """dart_cache parquet 파일 삭제. 없으면 False."""
    from pathlib import Path
    path = Path(cache_dir) / f"{stock_code}_{year}_{report_type}.parquet"
    if path.exists():
        path.unlink()
        return True
    return False


def trigger_score_recalculation(
    db: Any,
    dart_client: Any,
    scorer: ScoringEngine,
    stock_code: str,
    year: int = 2025,
    cache_dir: Optional[Any] = None,
    save_to_db: bool = True,
) -> Optional[ScoreSnapshot]:
    """단일 종목 점수 재계산. 실패 시 None.

    순서:
      1. dart_cache invalidate (cache_dir 주어졌을 때만)
      2. dart_client.extract_financial_metrics(code, year) — fresh 재무
      3. financial_metrics UPSERT
      4. 직전 stock_scores 행에서 price_data + momentum_score 재사용
      5. scorer.calculate_score(price, fresh_fin, []) — chart 없음
      6. result의 momentum_score를 직전 값으로 복원, total 재합산
      7. save_to_db=True면 stock_scores에 오늘자로 INSERT/REPLACE
      8. ScoreSnapshot 반환
    """
    # 1) DART 캐시 무효화
    if cache_dir is not None:
        try:
            _invalidate_dart_cache(cache_dir, stock_code, year)
        except Exception as e:
            logger.warning("캐시 무효화 실패 %s: %s", stock_code, e)

    # 2) 재수집
    # 2026-04-30: sector 누락 시 _calc_financial_revenue가 None을 반환해
    # 일반 매출 라벨로 fallback → 금융주(증권/은행지주) revenue=0 silent
    # regression. 기존 financial_metrics 행의 sector 컬럼을 참조해 전달.
    sector = None
    try:
        existing = db.get_financial_metrics(stock_code, year)
        if existing:
            sector = existing.get("sector") or None
    except Exception as e:
        logger.warning("기존 sector 조회 실패 %s: %s", stock_code, e)
    try:
        metrics = dart_client.extract_financial_metrics(
            stock_code, year=year, sector=sector,
        )
    except Exception as e:
        logger.warning("DART 재수집 실패 %s: %s", stock_code, e)
        return None
    if not metrics or not metrics.get("rcept_no"):
        logger.warning("DART 재수집 결과 비정상 %s (rcept_no 없음)", stock_code)
        return None

    # 3) financial_metrics 갱신
    try:
        db.save_financial_metrics(metrics)
    except Exception as e:
        logger.error("financial_metrics 저장 실패 %s: %s", stock_code, e)
        return None

    # 4) 직전 score row 조회
    prev = db.get_stock_score(stock_code)
    if not prev:
        logger.warning("직전 stock_scores 없음 %s — 재계산 skip", stock_code)
        return None
    price_data = _build_price_data_from_score_row(prev)
    prev_momentum = int(prev.get("momentum_score") or 0)
    prev_stoploss = int(prev.get("stoploss_price") or 0)
    prev_stoploss_pct = float(prev.get("stoploss_pct") or 0)
    prev_atr = float(prev.get("atr") or 0)

    # 5) scorer 호출 (chart 없음 → momentum=0 산출)
    try:
        result = scorer.calculate_score(price_data, metrics, [])
    except Exception as e:
        logger.error("scorer 실패 %s: %s", stock_code, e)
        return None

    # 6) momentum 복원 + total 재합산 (페널티는 scorer가 이미 반영)
    result["momentum_score"] = prev_momentum
    cat_sum = (
        int(result.get("value_score", 0))
        + int(result.get("financial_score", 0))
        + int(result.get("growth_score", 0))
        + prev_momentum
        + int(result.get("quality_score", 0))
    )
    penalty = int(result.get("penalties", 0))
    result["total_score"] = max(0, min(100, cat_sum + penalty))

    # 6.5) 손절가/ATR 보존 — chart 없는 자정 모니터에서 scorer가 0을 반환하면
    # save_stock_scores가 0으로 덮어쓰는 silent regression이 발생. 직전 행의
    # 값이 양수일 때만 보존(momentum 보존 패턴과 동일). 신규 값이 양수면 우선.
    new_stoploss = int(result.get("stoploss_price", 0) or 0)
    new_stoploss_pct = float(result.get("stoploss_pct", 0) or 0)
    new_atr = float(result.get("atr", 0) or 0)
    eff_stoploss = new_stoploss if new_stoploss > 0 else prev_stoploss
    eff_stoploss_pct = new_stoploss_pct if new_stoploss_pct != 0 else prev_stoploss_pct
    eff_atr = new_atr if new_atr > 0 else prev_atr

    # 6.6) 신호 재판정 — total/financial/growth 가 갱신됐는데도 직전 signal을
    # 그대로 저장하면 total<SELL_SCORE 인 종목이 hold로 남는 silent regression
    # (T2-2 health check 위반). 자정 모니터는 fresh price가 없으므로
    # stoploss_hit는 직전 가격 기준으로만 평가; 직전 사이클이 이미 잡아낸 케이스.
    current_price = int(price_data.get("current_price") or 0)
    stoploss_hit = (
        current_price > 0
        and eff_stoploss > 0
        and current_price <= eff_stoploss
    )
    signal_result = SignalGenerator().determine_signal(result, stoploss_hit=stoploss_hit)
    new_signal = signal_result.get("signal", prev.get("signal", ""))
    new_signal_label = signal_result.get("signal_label", Signal.label(new_signal))
    new_reason = signal_result.get("reason", "")

    # 7) DB 저장 (옵션) — save_stock_scores는 stoploss_map에서 손절/ATR을
    # 읽으므로, 보존 결정한 값을 stoploss_map으로 전달한다.
    if save_to_db:
        today = datetime.now().strftime("%Y-%m-%d")
        sl_map = {}
        if eff_stoploss > 0 or eff_atr > 0 or eff_stoploss_pct != 0:
            sl_map[stock_code] = {
                "effective_stoploss": eff_stoploss,
                "effective_stoploss_pct": eff_stoploss_pct,
                "atr": eff_atr,
            }
        try:
            db.save_stock_scores(
                analysis_date=today,
                signals=[{
                    **result,
                    "stock_code": stock_code,
                    "stock_name": prev.get("stock_name", ""),
                    "signal": new_signal,
                    "signal_label": new_signal_label,
                    "reason": new_reason,
                }],
                stoploss_map=sl_map or None,
            )
        except Exception as e:
            logger.warning("stock_scores 저장 실패 %s: %s", stock_code, e)

    # 8) Snapshot
    snap = ScoreSnapshot.from_score_result({
        **result,
        "stock_code": stock_code,
        "stock_name": prev.get("stock_name", ""),
        "signal": new_signal,
    })
    return snap


# ----------------------------------------------------------------------
# 배치 처리
# ----------------------------------------------------------------------
def process_disclosures(
    db: Any,
    dart_client: Any,
    scorer: ScoringEngine,
    disclosures: list[Disclosure],
    year: int = 2025,
    cache_dir: Optional[Any] = None,
    save_to_db: bool = True,
) -> list[DisclosureImpact]:
    """다수 공시를 일괄 처리해 우선순위 순서로 DisclosureImpact 반환.

    - needs_data_refresh가 False인 공시는 skip (DIVIDEND/BUYBACK/MAJOR/OTHER)
    - 같은 종목 여러 공시는 첫 공시만 사용 (재수집은 1회면 충분)
    - 우선순위: 신호 변경 우선, 그 다음 |total_diff| 큰 순
    """
    refresh_targets: list[Disclosure] = [
        d for d in disclosures if needs_data_refresh(d)
    ]
    seen: dict[str, Disclosure] = {}
    for d in refresh_targets:
        if d.stock_code and d.stock_code not in seen:
            seen[d.stock_code] = d

    impacts: list[DisclosureImpact] = []
    for code, disc in seen.items():
        before = get_score_snapshot(db, code)
        if before is None:
            logger.info("직전 점수 없음 — skip %s", code)
            continue
        after = trigger_score_recalculation(
            db=db, dart_client=dart_client, scorer=scorer,
            stock_code=code, year=year,
            cache_dir=cache_dir, save_to_db=save_to_db,
        )
        if after is None:
            continue
        impacts.append(compare_scores(before, after, disc))

    # 우선순위 정렬: signal_changed 우선, |total_diff| 큰 순
    impacts.sort(
        key=lambda i: (not i.signal_changed, -abs(i.total_diff)),
    )
    return impacts
