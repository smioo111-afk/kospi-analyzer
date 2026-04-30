"""매수 상태 분류 모듈.

외부 표현: 3단계 (BUY / WATCH / AVOID)
내부 정교: buy_score 우선순위 점수 (0~100 정규화)

분류 원칙:
  - TOP 10 자체가 1차 필터 (점수 + 신호 + 가치 통과)
  - 2차 필터는 명시적 위험 차단만 (관대)
  - AVOID 차단 5가지: 매도신호 / 고평가 / 손절근접 /
    52주 고점 / 가치 함정
  - 그 외 모두 BUY
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class BuyState(Enum):
    BUY = "buy"
    WATCH = "watch"
    AVOID = "avoid"


# AVOID 임계치 (모듈 상단 명시 — 튜닝 시 한 곳만 수정)
STOPLOSS_PROXIMITY_PCT = 3.0   # 손절가 대비 3% 이내면 차단
WEEK52_HIGH_THRESHOLD = 85.0   # 52주 위치 85% 초과면 차단
VALUE_TRAP_MOMENTUM_MAX = 5    # 모멘텀이 이보다 낮은 저평가 = 가치 함정

# WATCH 임계치 — TOP 10이지만 적극 매수 보류
BUY_SIGNALS = ("buy", "strong_buy")     # 이 외 신호는 BUY 자격 없음
SUPPLY_STRONG_NEG = -5                  # 외국인/기관 5일 연속 매도 일수
SUPPLY_BOTH_NEG = -3                    # 둘 다 동시 매도일 때 더 엄격
RANK_DROP_THRESHOLD = -4                # 4계단 이상 하락 = 추세 약화


def classify_buy_state(score: dict[str, Any]) -> BuyState:
    """3단계 분류.

    Args:
        score: scorer.calculate_score 결과 또는 daily_report_log dict.
            필수 키: stock_code, current_price, signal,
                     fair_value_low, fair_value_high,
                     stoploss_price, momentum_score, week52_position
    """
    stock_code = score.get("stock_code", "")
    cp = score.get("current_price", 0) or 0
    fair_high = score.get("fair_value_high", 0) or 0

    # 데이터 부족 → AVOID (분석 불가능한 종목은 매수 차단)
    if cp <= 0 or fair_high <= 0:
        logger.warning(
            "분류 시 필수 데이터 부족: %s, cp=%s, fair_high=%s",
            stock_code, cp, fair_high,
        )
        return BuyState.AVOID

    # 1. 매도 신호
    if score.get("signal") == "sell":
        return BuyState.AVOID

    # 2. 고평가 (현재가 > 적정 상단)
    if cp > fair_high:
        return BuyState.AVOID

    # 3. 손절 근접
    sl_price = score.get("stoploss_price", 0) or 0
    if sl_price > 0:
        sl_dist = (cp - sl_price) / cp * 100
        if sl_dist < STOPLOSS_PROXIMITY_PCT:
            return BuyState.AVOID

    # 4. 52주 고점 (week52_position 0~100, 100=고점)
    week52_pos = score.get("week52_position", 50) or 0
    if week52_pos > WEEK52_HIGH_THRESHOLD:
        return BuyState.AVOID

    # 5. 가치 함정 (저평가 + 모멘텀 매우 약함)
    momentum = score.get("momentum_score", 0) or 0
    fair_low = score.get("fair_value_low", 0) or 0
    fair_mid = (fair_low + fair_high) / 2 if fair_low > 0 else fair_high
    is_undervalued = cp <= fair_mid
    if is_undervalued and momentum < VALUE_TRAP_MOMENTUM_MAX:
        return BuyState.AVOID

    # === WATCH: TOP 10이나 적극 매수 보류 ===

    # 6. 신호가 적극 매수가 아니면 WATCH (Hold 상태에서는 적극 매수 X)
    signal = score.get("signal", "")
    if signal not in BUY_SIGNALS:
        return BuyState.WATCH

    # 7. 수급 강한 매도 추세
    foreign_5d = score.get("foreign_net_buy_5d", 0) or 0
    inst_5d = score.get("institutional_net_buy_5d", 0) or 0
    if foreign_5d <= SUPPLY_STRONG_NEG or inst_5d <= SUPPLY_STRONG_NEG:
        return BuyState.WATCH
    if foreign_5d <= SUPPLY_BOTH_NEG and inst_5d <= SUPPLY_BOTH_NEG:
        return BuyState.WATCH

    # 8. 점수 급락 (rank_change는 main.py가 prev_top_10에서 계산해 주입)
    rank_change = score.get("rank_change")
    if rank_change is not None and rank_change <= RANK_DROP_THRESHOLD:
        return BuyState.WATCH

    # TOP 10 + 모든 차단 회피 + 적극 매수 신호 + 수급/추세 안정 → BUY
    return BuyState.BUY


def calculate_buy_score(
    score: dict[str, Any],
    history: Optional[list[dict[str, Any]]] = None,
) -> float:
    """매수 우선순위 점수 (0~100 정규화).

    가중치:
      - momentum (절대값) × 30%
      - momentum 가속도 (clamp ±5) × 25%
      - 종합 점수 × 20%
      - 52주 저점 가산 × 15%
      - 지속성 × 5%
      - 손절 여유 × 5%

    Args:
        score: stock_scores DB row 또는 scorer 결과
        history: 최근 stock_scores (DESC, 가장 최근이 [0])
    """
    momentum_today = score.get("momentum_score", 0) or 0
    total_score = score.get("total_score", 0) or 0
    week52_pos = score.get("week52_position", 50) or 0
    cp = score.get("current_price", 0) or 0
    sl_price = score.get("stoploss_price", 0) or 0

    # 모멘텀 가속도 (직전 3일 평균 대비, ±5 clamp)
    momentum_change = 0.0
    if history and len(history) >= 3:
        avg_3d = sum((h.get("momentum_score", 0) or 0) for h in history[:3]) / 3
        momentum_change = momentum_today - avg_3d
    momentum_change = max(min(momentum_change, 5), -5)

    # 지속성: 가장 최근 history 대비 momentum 유지/상승 여부
    consistency = 0
    if history and len(history) >= 1:
        prev_momentum = history[0].get("momentum_score", 0) or 0
        if momentum_today >= prev_momentum:
            consistency = 1

    # 손절 여유 (%)
    sl_dist = 0.0
    if cp > 0 and sl_price > 0:
        sl_dist = (cp - sl_price) / cp * 100

    buy_score = (
        (momentum_today / 20) * 30
        + (momentum_change / 5) * 25
        + (total_score / 100) * 20
        + ((100 - week52_pos) / 100) * 15
        + consistency * 5
        + (sl_dist / 10) * 5
    )
    return round(buy_score, 2)


def get_state_label(state: BuyState) -> str:
    """텔레그램 표시용 라벨."""
    labels = {
        BuyState.BUY: "🟢 BUY",
        BuyState.WATCH: "🟡 WATCH",
        BuyState.AVOID: "🔴 AVOID",
    }
    return labels.get(state, "")


def get_state_reason(state: BuyState, score: dict[str, Any]) -> str:
    """AVOID/WATCH 사유. BUY는 빈 문자열."""
    if state == BuyState.BUY:
        return ""

    cp = score.get("current_price", 0) or 0
    fair_high = score.get("fair_value_high", 0) or 0
    sl_price = score.get("stoploss_price", 0) or 0
    week52_pos = score.get("week52_position", 50) or 0
    momentum = score.get("momentum_score", 0) or 0
    fair_low = score.get("fair_value_low", 0) or 0
    fair_mid = (fair_low + fair_high) / 2 if fair_low > 0 else fair_high

    if state == BuyState.AVOID:
        if cp <= 0 or fair_high <= 0:
            return "데이터 부족"
        if score.get("signal") == "sell":
            return "매도 신호"
        if cp > fair_high:
            return "고평가"
        if sl_price > 0 and (cp - sl_price) / cp * 100 < STOPLOSS_PROXIMITY_PCT:
            return "손절 근접"
        if week52_pos > WEEK52_HIGH_THRESHOLD:
            return "52주 고점"
        if cp <= fair_mid and momentum < VALUE_TRAP_MOMENTUM_MAX:
            return "가치 함정"
        return ""

    # WATCH 사유
    signal = score.get("signal", "")
    if signal not in BUY_SIGNALS:
        return f"신호 {signal or '미정'}"
    foreign_5d = score.get("foreign_net_buy_5d", 0) or 0
    inst_5d = score.get("institutional_net_buy_5d", 0) or 0
    if foreign_5d <= SUPPLY_STRONG_NEG or inst_5d <= SUPPLY_STRONG_NEG:
        return "수급 매도 추세"
    if foreign_5d <= SUPPLY_BOTH_NEG and inst_5d <= SUPPLY_BOTH_NEG:
        return "수급 동반 매도"
    rank_change = score.get("rank_change")
    if rank_change is not None and rank_change <= RANK_DROP_THRESHOLD:
        return f"순위 {rank_change}계단 하락"
    return ""
