"""
KOSPI 저평가 기업 분석 시스템 - 통합 테스트 (v3.0)

모든 모듈의 연동을 검증한다:
  1. 설정값 검증 (v3.0 설계서 일치)
  2. 스코어링 엔진 검증 (경계값, 극단값)
  3. 신호 판정 검증 (4단계 신호)
  4. 손절 라인 검증 (ATR 공식)
  5. DB CRUD 검증
  6. 메시지 포맷 검증
  7. 전체 파이프라인 시뮬레이션 (mock 데이터)
"""

import json
import logging
import os
import random
import sys
import tempfile
from datetime import datetime
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.WARNING)

# ================================================================
# 테스트 유틸
# ================================================================
_pass_count = 0
_fail_count = 0


def assert_eq(actual: Any, expected: Any, msg: str) -> None:
    global _pass_count, _fail_count
    if actual == expected:
        _pass_count += 1
    else:
        _fail_count += 1
        print(f"  ❌ FAIL: {msg} → 기대={expected}, 실제={actual}")


def assert_true(condition: bool, msg: str) -> None:
    global _pass_count, _fail_count
    if condition:
        _pass_count += 1
    else:
        _fail_count += 1
        print(f"  ❌ FAIL: {msg}")


def section(name: str) -> None:
    print(f"\n{'='*50}")
    print(f"  {name}")
    print(f"{'='*50}")


# ================================================================
# 1. 설정값 검증 (v3.0)
# ================================================================
def test_settings():
    section("1. 설정값 검증 (v3.0 설계서 일치)")
    from config.settings import (
        ScoringConfig as SC,
        SignalConfig as SIG,
        StopLossConfig as SL,
        SchedulerConfig as SCH,
    )

    # 스코어 배분 합계 = 100 (5개 카테고리)
    total_weight = (SC.WEIGHT_VALUE + SC.WEIGHT_FINANCIAL + SC.WEIGHT_GROWTH
                    + SC.WEIGHT_MOMENTUM + SC.WEIGHT_QUALITY)
    assert_eq(total_weight, 100, "총점 100점")
    assert_eq(SC.WEIGHT_VALUE, 30, "가치투자 30점")
    assert_eq(SC.WEIGHT_FINANCIAL, 20, "재무건전성 20점")
    assert_eq(SC.WEIGHT_GROWTH, 20, "성장성 20점")
    assert_eq(SC.WEIGHT_MOMENTUM, 20, "모멘텀 20점")
    assert_eq(SC.WEIGHT_QUALITY, 10, "퀄리티 10점")

    # 가치 세부합계: PER(5)+PBR(4)+배당(3)+업종PER(5)+PEG(5)+EV/EBITDA(5)+PSR(3)=30
    value_sum = (SC.PER_MAX_SCORE + SC.PBR_MAX_SCORE + SC.DIVIDEND_MAX_SCORE
                 + SC.SECTOR_PER_MAX_SCORE + SC.PEG_MAX_SCORE
                 + SC.EV_EBITDA_MAX_SCORE + SC.PSR_MAX_SCORE)
    assert_eq(value_sum, 30, "가치투자 세부합계 30")

    # 재무 세부합계: ROE(5)+영업이익률(5)+부채비율(5)+유동비율(5)=20
    fin_sum = (SC.ROE_MAX_SCORE + SC.OPR_MARGIN_MAX_SCORE
               + SC.DEBT_RATIO_MAX_SCORE + SC.CURRENT_RATIO_MAX_SCORE)
    assert_eq(fin_sum, 20, "재무건전성 세부합계 20")

    # 성장성 세부합계: 매출성장(7)+영업이익성장(7)+이익건전성(6)=20
    growth_sum = (SC.REVENUE_GROWTH_MAX_SCORE + SC.OP_INCOME_GROWTH_MAX_SCORE
                  + SC.PROFIT_HEALTH_BASE_SCORE)
    assert_eq(growth_sum, 20, "성장성 세부합계 20")

    # 모멘텀 세부합계: MA20(4)+MA60(3)+거래량(3)+RSI(3)+MACD(2)+수급(3)+52주(2)=20
    mom_sum = (max(SC.MA20_SCORES.values()) + max(SC.MA60_SCORES.values())
               + max(SC.VOLUME_SCORES.values()) + max(SC.RSI_SCORES.values())
               + max(SC.MACD_SCORES.values()) + max(SC.SUPPLY_DEMAND_SCORES.values())
               + max(SC.WEEK52_SCORES.values()))
    assert_eq(mom_sum, 20, "모멘텀 세부합계 20")

    # 퀄리티 세부합계: FCF수익률(5)+FCF마진(5)=10
    qual_sum = SC.FCF_YIELD_MAX_SCORE + SC.FCF_MARGIN_MAX_SCORE
    assert_eq(qual_sum, 10, "퀄리티 세부합계 10")

    # 신호 기준 (v3.0)
    assert_eq(SIG.STRONG_BUY_SCORE, 75, "강력매수 75점")
    assert_eq(SIG.BUY_SCORE_MIN, 60, "매수 최소 60점")
    assert_eq(SIG.SELL_SCORE, 45, "매도 45점 미만")
    assert_eq(SIG.MIN_MARKET_CAP, 100_000_000_000, "최소 시총 1000억")
    assert_eq(SIG.MIN_TRADING_VALUE, 1_000_000_000, "최소 거래대금 10억")
    assert_eq(SIG.TOP_N, 10, "TOP 10")

    # 손절
    assert_eq(SL.ATR_PERIOD, 14, "ATR 14일")
    assert_eq(SL.ATR_MULTIPLIER, 2.0, "기본 ATR 2.0배")
    assert_eq(SL.HARD_STOP_LOSS_PCT, -7.0, "하드스톱 -7%")

    # 스케줄러
    assert_eq(SCH.DATA_COLLECT_HOUR, 15, "수집 15시")
    assert_eq(SCH.DATA_COLLECT_MINUTE, 40, "수집 40분")

    print(f"  ✅ 설정값 검증 완료")


# ================================================================
# 2. 스코어링 엔진 검증 (v3.0)
# ================================================================
def test_scoring():
    section("2. 스코어링 엔진 검증")
    from analysis.scorer import ScoringEngine
    from config.settings import ScoringConfig as SC

    engine = ScoringEngine()

    # --- PER 경계값 (v3.0: max 5점) ---
    # thresholds: <5→5, <10→4, <15→3, <25→1, else→0
    assert_eq(engine._threshold_below(4.99, SC.PER_THRESHOLDS, SC.PER_DEFAULT_SCORE, True), 5, "PER 4.99 → 5점")
    assert_eq(engine._threshold_below(5.0, SC.PER_THRESHOLDS, SC.PER_DEFAULT_SCORE, True), 4, "PER 5.0 → 4점")
    assert_eq(engine._threshold_below(9.99, SC.PER_THRESHOLDS, SC.PER_DEFAULT_SCORE, True), 4, "PER 9.99 → 4점")
    assert_eq(engine._threshold_below(10.0, SC.PER_THRESHOLDS, SC.PER_DEFAULT_SCORE, True), 3, "PER 10.0 → 3점")
    assert_eq(engine._threshold_below(14.99, SC.PER_THRESHOLDS, SC.PER_DEFAULT_SCORE, True), 3, "PER 14.99 → 3점")
    assert_eq(engine._threshold_below(15.0, SC.PER_THRESHOLDS, SC.PER_DEFAULT_SCORE, True), 1, "PER 15.0 → 1점")
    assert_eq(engine._threshold_below(24.99, SC.PER_THRESHOLDS, SC.PER_DEFAULT_SCORE, True), 1, "PER 24.99 → 1점")
    assert_eq(engine._threshold_below(25.0, SC.PER_THRESHOLDS, SC.PER_DEFAULT_SCORE, True), 0, "PER 25.0 → 0점")
    assert_eq(engine._threshold_below(0, SC.PER_THRESHOLDS, SC.PER_DEFAULT_SCORE, True), 0, "PER 0 → 0점 (적자)")
    assert_eq(engine._threshold_below(-10, SC.PER_THRESHOLDS, SC.PER_DEFAULT_SCORE, True), 0, "PER -10 → 0점 (적자)")

    # --- PBR 경계값 (v3.0: max 4점) ---
    # thresholds: <0.5→4, <0.8→3, <1.0→2, <1.5→1, else→0
    assert_eq(engine._threshold_below(0.49, SC.PBR_THRESHOLDS, SC.PBR_DEFAULT_SCORE, True), 4, "PBR 0.49 → 4점")
    assert_eq(engine._threshold_below(0.5, SC.PBR_THRESHOLDS, SC.PBR_DEFAULT_SCORE, True), 3, "PBR 0.5 → 3점")
    assert_eq(engine._threshold_below(0.79, SC.PBR_THRESHOLDS, SC.PBR_DEFAULT_SCORE, True), 3, "PBR 0.79 → 3점")
    assert_eq(engine._threshold_below(0.8, SC.PBR_THRESHOLDS, SC.PBR_DEFAULT_SCORE, True), 2, "PBR 0.8 → 2점")
    assert_eq(engine._threshold_below(1.0, SC.PBR_THRESHOLDS, SC.PBR_DEFAULT_SCORE, True), 1, "PBR 1.0 → 1점")
    assert_eq(engine._threshold_below(1.5, SC.PBR_THRESHOLDS, SC.PBR_DEFAULT_SCORE, True), 0, "PBR 1.5 → 0점")

    # --- 부채비율 (역산, max 5점) ---
    # thresholds: <50→5, <100→3, <200→1, else→0
    assert_eq(engine._score_debt(0), 5, "부채 0% → 5점 (무부채)")
    assert_eq(engine._score_debt(49.9), 5, "부채 49.9% → 5점")
    assert_eq(engine._score_debt(50), 3, "부채 50% → 3점")
    assert_eq(engine._score_debt(100), 1, "부채 100% → 1점")
    assert_eq(engine._score_debt(200), 0, "부채 200% → 0점")

    # --- 배당수익률 (max 3점) ---
    # thresholds: ≥5→3, ≥3→2, ≥1→1, else→0
    assert_eq(engine._threshold_above(6.0, SC.DIVIDEND_THRESHOLDS, SC.DIVIDEND_DEFAULT_SCORE), 3, "배당 6% → 3점")
    assert_eq(engine._threshold_above(5.0, SC.DIVIDEND_THRESHOLDS, SC.DIVIDEND_DEFAULT_SCORE), 3, "배당 5% → 3점")
    assert_eq(engine._threshold_above(4.9, SC.DIVIDEND_THRESHOLDS, SC.DIVIDEND_DEFAULT_SCORE), 2, "배당 4.9% → 2점")
    assert_eq(engine._threshold_above(3.0, SC.DIVIDEND_THRESHOLDS, SC.DIVIDEND_DEFAULT_SCORE), 2, "배당 3% → 2점")
    assert_eq(engine._threshold_above(1.0, SC.DIVIDEND_THRESHOLDS, SC.DIVIDEND_DEFAULT_SCORE), 1, "배당 1% → 1점")
    assert_eq(engine._threshold_above(0.5, SC.DIVIDEND_THRESHOLDS, SC.DIVIDEND_DEFAULT_SCORE), 0, "배당 0.5% → 0점")

    # --- ROE (max 5점) ---
    assert_eq(engine._threshold_above(20.0, SC.ROE_THRESHOLDS, SC.ROE_DEFAULT_SCORE), 5, "ROE 20% → 5점")
    assert_eq(engine._threshold_above(10.0, SC.ROE_THRESHOLDS, SC.ROE_DEFAULT_SCORE), 4, "ROE 10% → 4점")
    assert_eq(engine._threshold_above(5.0, SC.ROE_THRESHOLDS, SC.ROE_DEFAULT_SCORE), 2, "ROE 5% → 2점")
    assert_eq(engine._threshold_above(0.0, SC.ROE_THRESHOLDS, SC.ROE_DEFAULT_SCORE), 0, "ROE 0% → 0점")

    # --- PEG (max 5점) ---
    peg_s, peg_v = engine._score_peg(4.0, 35.0)
    assert_eq(peg_s, 5, "PEG 0.11 → 5점")
    peg_s2, _ = engine._score_peg(10.0, 10.0)
    assert_eq(peg_s2, 2, "PEG 1.0 → 2점 (1.0 < 1.5 구간)")
    peg_s3, _ = engine._score_peg(0, 10.0)
    assert_eq(peg_s3, 0, "PER 0 → PEG 0점")

    # --- EV/EBITDA (max 5점) ---
    ev_s, ev_v = engine._score_ev_ebitda(int(1e12), int(2e11), int(1e11), int(3e11))
    assert_eq(ev_s, 5, "EV/EBITDA 3.67 → 5점")
    ev_s2, _ = engine._score_ev_ebitda(int(1e12), 0, 0, int(1e11))
    assert_eq(ev_s2, 2, "EV/EBITDA 10.0 → 2점")

    # --- 만점 테스트 (v3.0) ---
    price = {"stock_code": "TEST", "stock_name": "만점", "current_price": 50000,
             "per": 4.0, "pbr": 0.4, "volume": 15000000, "trading_value": 500e9,
             "market_cap": int(1e12),
             "foreign_net_buy_days": 5, "institutional_net_buy_days": 5}
    fin = {"stock_code": "TEST", "roe": 20.0, "operating_margin": 20.0,
           "debt_ratio": 30.0, "current_ratio": 250.0, "dividend_yield": 6.0,
           "sector": "기타", "op_income_growth_yoy": 35.0, "revenue_growth_yoy": 25.0,
           "operating_income": 100, "prev_operating_income": 50,
           "net_income": 100, "prev_net_income": 80,
           "consecutive_loss_years": 0, "consecutive_op_decline_years": 0,
           "consecutive_revenue_decline_years": 0,
           "revenue": int(5e12), "ebitda": int(3e11), "total_liabilities": int(2e11),
           "cash_equivalents": int(1e11), "free_cash_flow": int(1e12)}

    # 상승 추세 차트 (최신순)
    chart = []
    for i in range(60):
        p = 50900 - i * 100  # 최신(50900)→과거(45000)
        chart.append({"date": f"d{i}", "open": p - 50, "high": p + 500, "low": p - 500,
                       "close": p, "volume": 15000000 + (59 - i) * 10000})

    result = engine.calculate_score(price, fin, chart)
    assert_eq(result["value_score"], 30, "만점기업 가치=30")
    assert_eq(result["financial_score"], 20, "만점기업 재무=20")
    assert_eq(result["growth_score"], 20, "만점기업 성장=20")
    assert_eq(result["quality_score"], 10, "만점기업 퀄리티=10")
    assert_true(result["total_score"] >= 80, f"만점기업 총점 ≥80 (실제={result['total_score']})")

    print(f"  ✅ 스코어링 검증 완료")


# ================================================================
# 3. 신호 판정 검증 (v3.0)
# ================================================================
def test_signals():
    section("3. 신호 판정 검증")
    from analysis.signals import SignalGenerator, Signal

    gen = SignalGenerator()

    def make(total, momentum, financial, growth=0):
        return {"total_score": total, "momentum_score": momentum,
                "financial_score": financial, "growth_score": growth,
                "stock_code": "T", "stock_name": "T",
                "value_score": max(0, total - momentum - financial - growth)}

    # 강력 매수: ≥75 AND 모멘텀≥10 AND 재무≥12 AND 성장≥10
    r = gen.determine_signal(make(75, 10, 12, 10))
    assert_eq(r["signal"], Signal.STRONG_BUY, "75/10/12/10 → 강력매수")

    r = gen.determine_signal(make(90, 15, 18, 15))
    assert_eq(r["signal"], Signal.STRONG_BUY, "90/15/18/15 → 강력매수")

    # 75점이지만 성장 부족
    r = gen.determine_signal(make(75, 10, 12, 9))
    assert_true(r["signal"] != Signal.STRONG_BUY, "75/10/12/9 → 강력매수 아님 (성장 미달)")

    # 75점이지만 모멘텀 부족
    r = gen.determine_signal(make(75, 9, 12, 10))
    assert_true(r["signal"] != Signal.STRONG_BUY, "75/9/12/10 → 강력매수 아님 (모멘텀 미달)")

    # 매수: 60~74 AND 재무≥10
    r = gen.determine_signal(make(60, 10, 10, 5))
    assert_eq(r["signal"], Signal.BUY, "60/10/10/5 → 매수")

    r = gen.determine_signal(make(74, 15, 15, 10))
    assert_eq(r["signal"], Signal.BUY, "74/15/15/10 → 매수")

    # 매수 점수이지만 재무 부족
    r = gen.determine_signal(make(65, 15, 9, 10))
    assert_eq(r["signal"], Signal.HOLD, "65/15/9/10 → 보유 (재무 미달)")

    # 보유: 45~59
    r = gen.determine_signal(make(45, 10, 10, 5))
    assert_eq(r["signal"], Signal.HOLD, "45/10/10/5 → 보유")

    r = gen.determine_signal(make(59, 10, 15, 10))
    assert_eq(r["signal"], Signal.HOLD, "59/10/15/10 → 보유")

    # 매도: <45
    r = gen.determine_signal(make(44, 10, 15, 10))
    assert_eq(r["signal"], Signal.SELL, "44점 → 매도")

    # 매도: 모멘텀 <4
    r = gen.determine_signal(make(70, 3, 15, 10))
    assert_eq(r["signal"], Signal.SELL, "모멘텀 3 → 매도")

    # 매도: 손절 도달
    r = gen.determine_signal(make(90, 15, 18, 15), stoploss_hit=True)
    assert_eq(r["signal"], Signal.SELL, "손절 도달 → 매도 (점수 무관)")

    # 필터링 테스트 (SignalConfig: 시총 5000억↑, 거래대금 50억↑)
    stocks = [
        {"stock_code": "A", "market_cap": 600e9, "trading_value": 100e9, "total_score": 80},
        {"stock_code": "B", "market_cap": 100e9, "trading_value": 100e9, "total_score": 90},  # 시총 미달
        {"stock_code": "C", "market_cap": 600e9, "trading_value": 1e9, "total_score": 85},   # 거래 미달
    ]
    fins = [{"stock_code": "A", "consecutive_loss_years": 0},
            {"stock_code": "B", "consecutive_loss_years": 0},
            {"stock_code": "C", "consecutive_loss_years": 0}]

    filtered = gen.filter_stocks(stocks, fins)
    assert_eq(len(filtered), 1, "필터 후 1종목 (A만)")
    assert_eq(filtered[0]["stock_code"], "A", "A만 통과")

    print(f"  ✅ 신호 판정 검증 완료")


# ================================================================
# 4. 손절 라인 검증
# ================================================================
def test_stoploss():
    section("4. 손절 라인 검증")
    from analysis.stoploss import StopLossCalculator
    import numpy as np

    calc = StopLossCalculator(multiplier=2.0)

    # 고정 차트 (True Range = 항상 2000)
    chart = []
    for i in range(20):
        chart.append({
            "date": f"d{i}", "open": 50000,
            "high": 51000, "low": 49000, "close": 50000, "volume": 100000
        })

    atr = calc.calculate_atr(chart)
    assert_eq(atr, 2000.0, "ATR = 2000 (고정 TR)")

    sl = calc.calculate_stoploss(50000, chart, multiplier=2.0)
    assert_eq(sl["stoploss_price"], 46000, "손절가 = 50000 - 4000 = 46000")
    assert_eq(sl["hard_stop_price"], 46500, "하드스톱 = 50000 * 0.93 = 46500")
    assert_eq(sl["effective_stoploss"], 46500, "유효손절 = max(46000, 46500) = 46500")

    # 배수별 테스트
    sl15 = calc.calculate_stoploss(50000, chart, multiplier=1.5)
    assert_eq(sl15["stoploss_price"], 47000, "ATR 1.5배: 50000-3000=47000")

    sl30 = calc.calculate_stoploss(50000, chart, multiplier=3.0)
    assert_eq(sl30["stoploss_price"], 44000, "ATR 3.0배: 50000-6000=44000")

    # 손절 도달 확인
    assert_true(calc.check_stoploss_hit(46000, 46500), "46000 ≤ 46500 → 도달")
    assert_true(calc.check_stoploss_hit(46500, 46500), "46500 = 46500 → 도달")
    assert_true(not calc.check_stoploss_hit(47000, 46500), "47000 > 46500 → 미도달")

    # 데이터 부족 시
    sl_empty = calc.calculate_stoploss(50000, [])
    assert_eq(sl_empty["atr"], 0.0, "빈 차트 ATR = 0")
    assert_true("ATR 계산 불가" in sl_empty["warnings"][0], "경고 메시지")

    # 배수 범위 제한
    sl_over = calc.calculate_stoploss(50000, chart, multiplier=5.0)
    assert_eq(sl_over["multiplier"], 3.0, "배수 3.0 초과 → 3.0 제한")

    print(f"  ✅ 손절 라인 검증 완료")


# ================================================================
# 5. DB CRUD 검증
# ================================================================
def test_database():
    section("5. DB CRUD 검증")
    from database.models import Database
    from database.history import AnalysisHistory

    # 임시 DB
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        db = Database(db_path=db_path)

        # 분석 결과 저장
        rid = db.save_analysis_result(
            "2026-04-01",
            [{"stock_code": "005930", "total_score": 87}],
            [{"stock_code": "000660", "warning": "손절 접근"}],
            {"total_analyzed": 800},
            kospi_index=2680.5,
            foreign_net_buy=1200,
        )
        assert_true(rid > 0, "분석 결과 저장 성공")

        # 분석 결과 조회
        latest = db.get_latest_result()
        assert_eq(latest["analysis_date"], "2026-04-01", "최신 날짜")
        assert_eq(latest["kospi_index"], 2680.5, "KOSPI 지수")
        assert_eq(len(latest["top_10"]), 1, "TOP 10 개수")

        # 종목 스코어 저장 (growth/quality 포함)
        count = db.save_stock_scores("2026-04-01", [
            {"stock_code": "005930", "stock_name": "삼성전자", "total_score": 87,
             "value_score": 35, "financial_score": 28,
             "growth_score": 18, "momentum_score": 24, "quality_score": 7,
             "signal": "strong_buy", "signal_label": "🟢 강력매수", "reason": "test",
             "current_price": 72300, "market_cap": 432e12, "per": 8.2, "pbr": 0.9,
             "roe": 12.5, "operating_margin": 14.3, "debt_ratio": 45.0, "dividend_yield": 2.5},
        ])
        assert_eq(count, 1, "스코어 1건 저장")

        # 종목 스코어 조회 (growth/quality 영구 보존 검증)
        score = db.get_stock_score("005930", "2026-04-01")
        assert_eq(score["total_score"], 87, "삼성전자 87점")
        assert_eq(score["signal"], "strong_buy", "강력매수 신호")
        assert_true("growth_score" in score, "growth_score 컬럼 존재")
        assert_true("quality_score" in score, "quality_score 컬럼 존재")
        assert_eq(score["growth_score"], 18, "성장 18점 영구 보존")
        assert_eq(score["quality_score"], 7, "퀄리티 7점 영구 보존")

        # UPSERT 테스트 (같은 날짜 + 종목 → 업데이트)
        db.save_stock_scores("2026-04-01", [
            {"stock_code": "005930", "stock_name": "삼성전자", "total_score": 85,
             "value_score": 33, "financial_score": 28, "momentum_score": 24,
             "signal": "strong_buy", "signal_label": "🟢 강력매수", "reason": "updated",
             "current_price": 72000, "market_cap": 430e12, "per": 8.3, "pbr": 0.9,
             "roe": 12.5, "operating_margin": 14.3, "debt_ratio": 45.0, "dividend_yield": 2.5},
        ])
        score2 = db.get_stock_score("005930", "2026-04-01")
        assert_eq(score2["total_score"], 85, "UPSERT 후 85점")

        # 관심종목
        assert_true(db.add_watchlist("005930", "삼성전자", "반도체"), "관심종목 추가")
        assert_true(db.add_watchlist("000660", "SK하이닉스"), "관심종목 추가2")
        wl = db.get_watchlist()
        assert_eq(len(wl), 2, "관심종목 2개")

        assert_true(db.remove_watchlist("000660"), "관심종목 삭제")
        wl2 = db.get_watchlist()
        assert_eq(len(wl2), 1, "관심종목 1개 남음")

        # 이력
        hist = AnalysisHistory(db)
        reports = hist.get_recent_reports(7)
        assert_true(len(reports) >= 1, "이력 조회 성공")

        # 신호 변경 감지
        db.save_stock_scores("2026-04-02", [
            {"stock_code": "005930", "stock_name": "삼성전자", "total_score": 60,
             "value_score": 25, "financial_score": 20, "momentum_score": 15,
             "signal": "hold", "signal_label": "⭐ 보유", "reason": "점수하락",
             "current_price": 68000, "market_cap": 400e12, "per": 9.0, "pbr": 0.95,
             "roe": 12.5, "operating_margin": 14.3, "debt_ratio": 45.0, "dividend_yield": 2.5},
        ])
        changes = hist.detect_signal_changes([
            {"stock_code": "005930", "stock_name": "삼성전자", "total_score": 60,
             "signal": "hold", "signal_label": "⭐ 보유"}
        ])
        # 최신 저장이 hold이므로 변경 없음
        assert_true(len(changes) == 0, "동일 신호 → 변경 없음")

        db.close()
        print(f"  ✅ DB CRUD 검증 완료")
    finally:
        os.unlink(db_path)


# ================================================================
# 6. 메시지 포맷 검증
# ================================================================
def test_formatter():
    section("6. 메시지 포맷 검증")
    from bot.formatter import MessageFormatter

    fmt = MessageFormatter()

    # 리포트 생성
    top10 = [{"stock_code": f"00{i}000", "stock_name": f"종목{i}", "total_score": 90-i*3,
              "signal_label": "🟢 강력매수", "per": 8+i, "pbr": 0.8+i*0.1, "roe": 15-i,
              "current_price": 50000+i*1000, "reason": "테스트"} for i in range(10)]

    stoploss = {f"00{i}000": {"effective_stoploss": 45000+i*1000,
                               "effective_stoploss_pct": -5.0, "warnings": []}
                for i in range(10)}

    msgs = fmt.format_daily_report(
        top_10=top10, warnings=[], stats={"total_analyzed": 800, "after_filter": 650},
        stoploss_map=stoploss, kospi_index=2680.5, kospi_change=0.8, foreign_net_buy=1200,
    )

    assert_true(len(msgs) >= 1, f"메시지 {len(msgs)}개 생성")
    for i, m in enumerate(msgs):
        assert_true(len(m) <= 4000, f"메시지{i+1} 길이 {len(m)} ≤ 4000")
    assert_true("1️⃣" in msgs[0], "번호 이모지 포함")
    assert_true("KOSPI" in msgs[-1] or "KOSPI" in msgs[0], "시장 요약 포함")

    # 시총 포맷
    assert_eq(fmt._format_market_cap(1_500_000_000_000), "1.5조원", "1.5조")
    assert_eq(fmt._format_market_cap(500_000_000_000), "5,000억원", "5000억")
    assert_eq(fmt._format_market_cap(80_000_000_000), "800억원", "800억")

    # 에러 메시지
    err = fmt.format_error_message("테스트 에러", "kis_api")
    assert_true("에러" in err, "에러 포함")
    assert_true("kis_api" in err, "모듈명 포함")

    print(f"  ✅ 메시지 포맷 검증 완료")


# ================================================================
# 7. 전체 파이프라인 시뮬레이션
# ================================================================
def test_pipeline_simulation():
    section("7. 전체 파이프라인 시뮬레이션 (mock 데이터)")
    from analysis.scorer import ScoringEngine
    from analysis.signals import SignalGenerator
    from analysis.stoploss import StopLossCalculator
    from bot.formatter import MessageFormatter
    from database.models import Database
    from database.history import AnalysisHistory

    random.seed(42)

    # mock 데이터 생성 (20개 종목)
    stock_codes = [f"{i:06d}" for i in range(1, 21)]
    stock_names = [f"테스트기업{i}" for i in range(1, 21)]

    # 시세 데이터
    price_list = []
    for i, (code, name) in enumerate(zip(stock_codes, stock_names)):
        price_list.append({
            "stock_code": code,
            "stock_name": name,
            "current_price": random.randint(10000, 200000),
            "change_rate": round(random.uniform(-5, 5), 2),
            "volume": random.randint(100000, 20000000),
            "trading_value": random.randint(1_000_000_000, 500_000_000_000),
            "market_cap": random.randint(100_000_000_000, 500_000_000_000_000),
            "per": round(random.uniform(3, 30), 1),
            "pbr": round(random.uniform(0.3, 3.0), 2),
            "foreign_net_buy_days": random.randint(-5, 10),
            "institutional_net_buy_days": random.randint(-3, 5),
        })

    # 재무 데이터 (v3.0 필드 포함)
    financial_list = []
    for code in stock_codes:
        financial_list.append({
            "stock_code": code,
            "roe": round(random.uniform(-5, 25), 1),
            "operating_margin": round(random.uniform(-5, 25), 1),
            "debt_ratio": round(random.uniform(20, 300), 1),
            "current_ratio": round(random.uniform(50, 300), 1),
            "dividend_yield": round(random.uniform(0, 7), 1),
            "consecutive_loss_years": random.choice([0, 0, 0, 0, 1, 2, 3]),
            "revenue_growth_yoy": round(random.uniform(-20, 30), 1),
            "op_income_growth_yoy": round(random.uniform(-30, 40), 1),
            "operating_income": random.randint(-100, 500),
            "prev_operating_income": random.randint(0, 300),
            "net_income": random.randint(-100, 500),
            "prev_net_income": random.randint(0, 300),
            "consecutive_op_decline_years": random.choice([0, 0, 0, 1, 2]),
            "consecutive_revenue_decline_years": random.choice([0, 0, 0, 1]),
            "sector": random.choice(["반도체", "자동차", "화학", "기타"]),
            "revenue": random.randint(int(1e11), int(5e12)),
            "ebitda": random.randint(int(1e10), int(5e11)),
            "total_liabilities": random.randint(int(1e10), int(3e12)),
            "cash_equivalents": random.randint(int(1e9), int(5e11)),
            "free_cash_flow": random.randint(int(-1e11), int(5e11)),
        })

    # 차트 데이터
    chart_dict = {}
    for code in stock_codes:
        base = random.randint(10000, 100000)
        chart = []
        for j in range(60):
            c = base + random.randint(-2000, 2000)
            chart.append({
                "date": f"d{j}", "open": c - 500, "high": c + 1000,
                "low": c - 1000, "close": c, "volume": random.randint(500000, 10000000)
            })
            base = c
        chart_dict[code] = chart

    # 파이프라인 실행
    scorer = ScoringEngine()
    signal_gen = SignalGenerator()
    sl_calc = StopLossCalculator()
    fmt = MessageFormatter()

    # 1. 스코어링
    scored = scorer.score_all_stocks(price_list, financial_list, chart_dict)
    assert_eq(len(scored), 20, "20종목 스코어링")
    assert_true(scored[0]["total_score"] >= scored[-1]["total_score"], "내림차순 정렬")

    # v3.0: 5개 카테고리 필드 확인
    assert_true("growth_score" in scored[0], "성장 점수 필드")
    assert_true("quality_score" in scored[0], "퀄리티 점수 필드")

    # 2. 손절 라인
    stoploss_map = sl_calc.calculate_all_stoploss(price_list, chart_dict)
    assert_eq(len(stoploss_map), 20, "20종목 손절 계산")

    # 3. 신호 생성
    result = signal_gen.generate_signals(scored, financial_list)
    top10 = result["top_10"]
    assert_true(len(top10) <= 10, f"TOP 10 이내: {len(top10)}")
    assert_true("stats" in result, "통계 포함")

    # 4. 리포트 생성
    msgs = fmt.format_daily_report(
        top_10=top10, warnings=result["warnings"],
        stats=result["stats"], stoploss_map=stoploss_map,
    )
    assert_true(len(msgs) >= 1, "리포트 생성")

    # 5. DB 저장
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        db = Database(db_path=db_path)
        hist = AnalysisHistory(db)
        hist.save_daily_result(
            analysis_date="2026-04-05",
            top_10=top10,
            warnings=result["warnings"],
            all_signals=result["all_signals"],
            stats=result["stats"],
            stoploss_map=stoploss_map,
        )

        latest = db.get_latest_result()
        assert_true(latest is not None, "DB 저장/조회 성공")

        db.close()
    finally:
        os.unlink(db_path)

    # 통계 출력
    dist = result["stats"].get("signal_distribution", {})
    print(f"  신호 분포: {dist}")
    print(f"  TOP 1: {top10[0]['stock_name']} ({top10[0]['total_score']}점)" if top10 else "  TOP 없음")
    print(f"  리포트: {len(msgs)}개 메시지, {sum(len(m) for m in msgs)}자")
    print(f"  ✅ 파이프라인 시뮬레이션 완료")


# ================================================================
# 실행
# ================================================================
if __name__ == "__main__":
    print("🧪 KOSPI 분석 시스템 통합 테스트 (v3.0)")
    print(f"   실행 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    test_settings()
    test_scoring()
    test_signals()
    test_stoploss()
    test_database()
    test_formatter()
    test_pipeline_simulation()

    print(f"\n{'='*50}")
    print(f"  📊 테스트 결과: ✅ {_pass_count} 통과 / ❌ {_fail_count} 실패")
    print(f"{'='*50}")

    if _fail_count > 0:
        sys.exit(1)
    else:
        print("  🎉 모든 테스트 통과!")
