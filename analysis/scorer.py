"""
KOSPI 저평가 기업 분석 시스템 - 종합 스코어링 엔진 (v3.0)

100점 만점 (5개 카테고리):
  가치(30): PER + PBR + 배당 + 업종PER + PEG + EV/EBITDA + PSR
  재무(20): ROE + 영업이익률 + 부채비율 + 유동비율
  성장(20): 매출성장률 + 이익성장률 + 이익건전성
  모멘텀(20): MA + 거래량 + RSI + MACD + 수급 + 52주위치
  퀄리티(10): FCF수익률 + FCF마진
"""

import logging
from statistics import median
from typing import Any

import numpy as np

from config.settings import ScoringConfig

logger = logging.getLogger(__name__)


class ScoringEngine:
    """종합 스코어링 엔진 v3.0."""

    def __init__(self) -> None:
        self.cfg = ScoringConfig
        # 동적 업종 평균 (DB 조회 결과). main.py가 set_sector_averages()로 주입.
        # 형식: {sector: {"avg_per": float, "avg_pbr": float, "avg_ev_ebitda": float}}
        self._dynamic_sector_avg: dict[str, dict[str, float]] = {}
        # 턴어라운드 점수에서 2~3년 전 영업이익 조회용 DB 핸들 (선택적).
        # main.py가 set_db()로 주입. None이면 historical 조회 없이 폴백 처리.
        self._db: Any = None

    def set_db(self, db: Any) -> None:
        """턴어라운드 점수 계산용 DB 핸들을 주입한다 (선택적)."""
        self._db = db

    def set_sector_averages(
        self, averages: dict[str, dict[str, float]]
    ) -> None:
        """DB에서 조회한 업종 평균을 주입한다.

        주입된 값은 _calc_fair_value, _score_sector_per에서 settings.py
        고정값보다 우선 사용된다. 누락된 업종은 settings.py 폴백.
        """
        self._dynamic_sector_avg = averages or {}

    def _get_sector_avg_per(self, sector: str) -> float:
        """업종 평균 PER (동적 → 고정 폴백)."""
        dyn = self._dynamic_sector_avg.get(sector, {})
        v = dyn.get("avg_per", 0)
        if v and v > 0:
            return float(v)
        return self.cfg.SECTOR_AVG_PER.get(sector, self.cfg.DEFAULT_SECTOR_PER)

    def _get_sector_avg_pbr(self, sector: str) -> float:
        """업종 평균 PBR (동적 → 고정 폴백)."""
        dyn = self._dynamic_sector_avg.get(sector, {})
        v = dyn.get("avg_pbr", 0)
        if v and v > 0:
            return float(v)
        return self.cfg.SECTOR_AVG_PBR.get(sector, self.cfg.DEFAULT_SECTOR_PBR)

    def _get_sector_avg_ev_ebitda(self, sector: str) -> float:
        """업종 평균 EV/EBITDA (동적 → 고정 폴백)."""
        dyn = self._dynamic_sector_avg.get(sector, {})
        v = dyn.get("avg_ev_ebitda", 0)
        if v and v > 0:
            return float(v)
        return self.cfg.SECTOR_AVG_EV_EBITDA.get(
            sector, self.cfg.DEFAULT_SECTOR_EV_EBITDA)

    # 업종 평균 채택 최소 표본 수 (이하면 단일 종목 outlier 왜곡 위험으로 제외)
    SECTOR_AVG_MIN_SAMPLES: int = 3

    @staticmethod
    def calculate_sector_averages(
        scored_list: list[dict[str, Any]],
    ) -> dict[str, dict[str, float]]:
        """전종목 스코어링 결과로부터 업종별 PER/PBR/EV-EBITDA 중앙값을 계산한다.

        - PER이 0/음수인 종목은 PER 표본에서 제외 (적자기업/이상치)
        - PBR이 0 이하인 종목은 PBR 표본에서 제외
        - EV/EBITDA가 0 이하인 종목은 EV/EBITDA 표본에서 제외
        - PER 표본 수가 SECTOR_AVG_MIN_SAMPLES(3) 미만인 업종은 결과에서 완전 제외
          → settings.py 폴백값이 사용되어 단일 종목 outlier 왜곡 방지

        Args:
            scored_list: ScoringEngine.score_all_stocks 결과

        Returns:
            dict: {업종명: {"avg_per": float, "avg_pbr": float,
                            "avg_ev_ebitda": float, "sample_count": int}}
        """
        groups: dict[str, dict[str, list[float]]] = {}
        for s in scored_list:
            sector = s.get("sector") or "기타"
            per = s.get("per", 0)
            pbr = s.get("pbr", 0)
            ev_ebitda = s.get("ev_ebitda", 0)

            # PER 0/음수는 PER 표본에서 제외 (그루핑 자체 제외)
            if per is None or per <= 0:
                continue

            g = groups.setdefault(sector, {"per": [], "pbr": [], "ev_ebitda": []})
            g["per"].append(float(per))
            if pbr and pbr > 0:
                g["pbr"].append(float(pbr))
            # EV/EBITDA outlier 필터: 0 < x < 50 만 표본에 포함
            # 금융/보험/증권 등에서 EBITDA가 거의 0에 가까운 종목이 ratio를
            # 비대칭적으로 크게 만들어 중앙값을 왜곡시키는 것을 방지
            if ev_ebitda and 0 < ev_ebitda < 50:
                g["ev_ebitda"].append(float(ev_ebitda))

        result: dict[str, dict[str, float]] = {}
        for sector, vals in groups.items():
            per_list = vals["per"]
            pbr_list = vals["pbr"]
            ev_list = vals["ev_ebitda"]
            # 표본 수 N<3 업종은 결과에서 제외 (단일 outlier 왜곡 방지)
            if len(per_list) < ScoringEngine.SECTOR_AVG_MIN_SAMPLES:
                continue
            entry: dict[str, float] = {
                "avg_per": round(median(per_list), 2),
                "avg_pbr": round(median(pbr_list), 3) if pbr_list else 0.0,
                "avg_ev_ebitda": round(median(ev_list), 2) if ev_list else 0.0,
                "sample_count": len(per_list),
            }
            result[sector] = entry
        return result

    def calculate_score(
        self, price_data: dict[str, Any], financial_data: dict[str, Any],
        chart_data: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """종목의 종합 스코어를 계산한다."""
        val = self._calc_value_score(price_data, financial_data)
        fin = self._calc_financial_score(financial_data)
        grw = self._calc_growth_score(financial_data)
        mom = self._calc_momentum_score(price_data, chart_data)
        qlt = self._calc_quality_score(price_data, financial_data)

        raw_total = val["total"] + fin["total"] + grw["total"] + mom["total"] + qlt["total"]
        penalties = grw.get("total_penalties", 0)
        total_score = max(0, min(100, raw_total + penalties))

        # 적정주가 계산
        fair = self._calc_fair_value(price_data, financial_data)

        return {
            "stock_code": price_data.get("stock_code", ""),
            "stock_name": price_data.get("stock_name", ""),
            "sector": financial_data.get("sector", "기타"),
            "total_score": total_score,
            "raw_total": raw_total,
            "value_score": val["total"],
            "financial_score": fin["total"],
            "growth_score": grw["total"],
            "momentum_score": mom["total"],
            "quality_score": qlt["total"],
            "penalties": penalties,
            "penalty_reasons": grw.get("penalty_reasons", []),
            "per": price_data.get("per", 0.0),
            "pbr": price_data.get("pbr", 0.0),
            "roe": financial_data.get("roe", 0.0),
            "operating_margin": financial_data.get("operating_margin", 0.0),
            "debt_ratio": financial_data.get("debt_ratio", 0.0),
            "current_ratio": financial_data.get("current_ratio", 0.0),
            "dividend_yield": financial_data.get("dividend_yield", 0.0),
            "revenue_growth": financial_data.get("revenue_growth_yoy", 0.0),
            "op_income_growth": financial_data.get("op_income_growth_yoy", 0.0),
            "current_price": price_data.get("current_price", 0),
            "market_cap": price_data.get("market_cap", 0),
            "volume": price_data.get("volume", 0),
            "trading_value": price_data.get("trading_value", 0),
            "foreign_net_buy_days": price_data.get("foreign_net_buy_days", 0),
            "institutional_net_buy_days": price_data.get("institutional_net_buy_days", 0),
            "foreign_net_buy_5d": price_data.get("foreign_net_buy_5d", 0),
            "foreign_net_buy_20d": price_data.get("foreign_net_buy_20d", 0),
            "institutional_net_buy_5d": price_data.get("institutional_net_buy_5d", 0),
            "institutional_net_buy_20d": price_data.get("institutional_net_buy_20d", 0),
            "consecutive_op_decline_years": financial_data.get("consecutive_op_decline_years", 0),
            "peg": val.get("peg_value", 0),
            "ev_ebitda": val.get("ev_ebitda_value", 0),
            "psr": val.get("psr_value", 0),
            "fcf_yield": qlt.get("fcf_yield_value", 0),
            "week52_position": mom.get("week52_pct", 0),
            "fair_value_low": fair["low"],
            "fair_value_high": fair["high"],
            "fair_value_gap": fair["gap_pct"],
            "fair_value_method": fair["method"],
            "turnaround_score": grw.get("turnaround_score", 0),
            "turnaround_label": grw.get("turnaround_label", ""),
            "detail": {"value": val, "financial": fin, "growth": grw,
                       "momentum": mom, "quality": qlt, "fair_value": fair},
        }

    # ================================================================
    # 적정주가 계산 (3-모델 가중평균)
    # ================================================================
    def _calc_fair_value(
        self, price: dict[str, Any], fin: dict[str, Any],
    ) -> dict[str, Any]:
        """3개 모델의 가중평균으로 적정주가 범위를 산출한다.

        모델 구성:
          - 모델1 (40%): EPS × 업종 평균 PER 범위
          - 모델2 (30%): BPS × 업종 평균 PBR 범위
          - 모델3 (30%): (EBITDA × 업종 평균 EV/EBITDA - 순차입금) / 발행주식수

        계산 불가능한 모델(EPS·EBITDA·BPS 미산출 등)은 제외하고
        남은 모델끼리 가중치를 재배분한다.

        Returns:
            dict: {low, high, gap_pct, method, eps}
        """
        current_price = price.get("current_price", 0)
        per = price.get("per", 0.0)
        pbr = price.get("pbr", 0.0)
        market_cap = price.get("market_cap", 0)
        sector = fin.get("sector", "기타")
        op_growth = fin.get("op_income_growth_yoy", 0.0)
        ebitda = fin.get("ebitda", 0)
        total_debt = fin.get("total_liabilities", 0)
        cash = fin.get("cash_equivalents", 0)

        if current_price <= 0:
            return {"low": 0, "high": 0, "gap_pct": 0.0,
                    "method": "계산불가", "eps": 0}

        # 발행주식수 근사 (시총 / 현재가)
        shares = market_cap / current_price if market_cap > 0 else 0

        # === PBR 기반 동적 가중치 결정 ===
        # PBR이 높을수록 BPS 모델이 underestimate를 유발하므로 모델2 비중 축소
        # 줄어든 가중치는 모델1(EPS×PER)에 재배분
        #   PBR < 3:  m1=0.40, m2=0.30 (기본)
        #   3 ≤ PBR < 5: m1=0.50, m2=0.20 (10% 이전)
        #   PBR ≥ 5:  m1=0.60, m2=0.10 (20% 이전)
        if pbr >= 5:
            w_model1, w_model2 = 0.60, 0.10
        elif pbr >= 3:
            w_model1, w_model2 = 0.50, 0.20
        else:
            w_model1, w_model2 = 0.40, 0.30

        # 각 모델별 (가중치, low, high) 결과 수집
        models: list[tuple[float, float, float]] = []
        methods_used: list[str] = []
        eps_value = 0

        # ---- 모델 1: EPS × 업종 PER (가중치 40~60% 동적) ----
        if per > 0:
            eps = int(current_price / per)
            if eps > 0:
                eps_value = eps
                avg_per = self._get_sector_avg_per(sector)
                per_low = avg_per * 0.7
                per_high = avg_per * 1.2

                # 성장성 반영: 이익 성장 중이면 PER 상한 확대
                if op_growth > 20:
                    per_high *= 1.3
                elif op_growth > 10:
                    per_high *= 1.15
                elif op_growth < -10:
                    per_high *= 0.8
                    per_low *= 0.8

                m1_low = eps * per_low
                m1_high = eps * per_high
                if m1_low > 0 and m1_high > 0:
                    models.append((w_model1, m1_low, m1_high))
                    methods_used.append(
                        f"EPS×PER({per_low:.1f}~{per_high:.1f})")

        # ---- 모델 2: BPS × 업종 PBR (가중치 10~30% 동적) ----
        if pbr > 0:
            bps = current_price / pbr
            if bps > 0:
                avg_pbr = self._get_sector_avg_pbr(sector)
                pbr_low = avg_pbr * 0.7
                pbr_high = avg_pbr * 1.2
                m2_low = bps * pbr_low
                m2_high = bps * pbr_high
                if m2_low > 0 and m2_high > 0:
                    models.append((w_model2, m2_low, m2_high))
                    methods_used.append(
                        f"BPS×PBR({pbr_low:.2f}~{pbr_high:.2f})")

        # ---- 모델 3: EV/EBITDA → 주당 가치 (가중치 30%) ----
        # 금융/보험/증권 등은 EV/EBITDA 모델 자체를 스킵 (영업이익 구조 차이)
        if (ebitda > 0 and shares > 0
                and sector not in self.cfg.EV_EBITDA_EXCLUDED_SECTORS):
            avg_ev_ebitda = self._get_sector_avg_ev_ebitda(sector)
            ev_low_mult = avg_ev_ebitda * 0.7
            ev_high_mult = avg_ev_ebitda * 1.2
            net_debt = total_debt - cash
            target_ev_low = ebitda * ev_low_mult
            target_ev_high = ebitda * ev_high_mult
            target_mcap_low = target_ev_low - net_debt
            target_mcap_high = target_ev_high - net_debt
            if target_mcap_low > 0 and target_mcap_high > 0:
                m3_low = target_mcap_low / shares
                m3_high = target_mcap_high / shares
                if m3_low > 0 and m3_high > 0:
                    models.append((0.30, m3_low, m3_high))
                    methods_used.append(
                        f"EV/EBITDA({ev_low_mult:.1f}~{ev_high_mult:.1f})")

        if not models:
            return {"low": 0, "high": 0, "gap_pct": 0.0,
                    "method": "계산불가", "eps": eps_value}

        # 가중치 재배분 후 가중평균 계산
        total_weight = sum(w for w, _, _ in models)
        fair_low = int(sum(w * lo for w, lo, _ in models) / total_weight)
        fair_high = int(sum(w * hi for w, _, hi in models) / total_weight)

        # 괴리율: 음수=저평가, 0=적정 범위 내, 양수=고평가
        # 적정 범위 [fair_low, fair_high] 안이면 0% (적정).
        # 미만이면 fair_low 대비 음수, 초과면 fair_high 대비 양수.
        # 과거에는 항상 fair_low 단일 기준이라, 적정 범위 안 종목도
        # 큰 양수 ("+85% 고평가")로 표시되던 결함 수정.
        if fair_low <= 0:
            gap_pct = 0.0
        elif current_price < fair_low:
            gap_pct = round(((current_price - fair_low) / fair_low) * 100, 1)
        elif current_price > fair_high:
            gap_pct = round(((current_price - fair_high) / fair_high) * 100, 1)
        else:
            gap_pct = 0.0

        return {
            "low": fair_low,
            "high": fair_high,
            "gap_pct": gap_pct,
            "method": " + ".join(methods_used),
            "eps": eps_value,
        }

    # ================================================================
    # 가치투자 (20점, v3.1)
    # ================================================================
    def _calc_value_score(self, price: dict, fin: dict) -> dict:
        per = price.get("per", 0.0)
        pbr = price.get("pbr", 0.0)
        div_yield = fin.get("dividend_yield", 0.0)
        if div_yield == 0:
            div_yield = price.get("dividend_yield", 0.0)
        sector = fin.get("sector", "기타")
        op_growth = fin.get("op_income_growth_yoy", 0.0)
        market_cap = price.get("market_cap", 0)
        revenue = fin.get("revenue", 0)
        ebitda = fin.get("ebitda", 0)
        total_debt = fin.get("total_liabilities", 0)
        cash = fin.get("cash_equivalents", 0)

        per_s = self._threshold_below(per, self.cfg.PER_THRESHOLDS, self.cfg.PER_DEFAULT_SCORE, True)
        pbr_s = self._threshold_below(pbr, self.cfg.PBR_THRESHOLDS, self.cfg.PBR_DEFAULT_SCORE, True)
        div_s = self._threshold_above(div_yield, self.cfg.DIVIDEND_THRESHOLDS, self.cfg.DIVIDEND_DEFAULT_SCORE)
        sector_s = self._score_sector_per(per, sector)
        peg_s, peg_val = self._score_peg(per, op_growth)
        ev_ebitda_s, ev_ebitda_val = self._score_ev_ebitda(market_cap, total_debt, cash, ebitda)
        psr_s, psr_val = self._score_psr(market_cap, revenue)

        return {
            "per_score": per_s, "pbr_score": pbr_s, "dividend_score": div_s,
            "sector_per_score": sector_s, "peg_score": peg_s, "peg_value": peg_val,
            "ev_ebitda_score": ev_ebitda_s, "ev_ebitda_value": ev_ebitda_val,
            "psr_score": psr_s, "psr_value": psr_val,
            "total": per_s + pbr_s + div_s + sector_s + peg_s + ev_ebitda_s + psr_s,
        }

    def _score_sector_per(self, per: float, sector: str) -> int:
        if per <= 0:
            return self.cfg.SECTOR_PER_DEFAULT_SCORE
        avg = self._get_sector_avg_per(sector)
        if avg <= 0:
            return self.cfg.SECTOR_PER_DEFAULT_SCORE
        ratio = per / avg
        for t, s in self.cfg.SECTOR_PER_THRESHOLDS:
            if ratio < t:
                return s
        return self.cfg.SECTOR_PER_DEFAULT_SCORE

    def _score_peg(self, per: float, growth: float) -> tuple[int, float]:
        """PEG = PER / 이익성장률. 성장 대비 밸류에이션."""
        if per <= 0 or growth <= 0:
            return self.cfg.PEG_DEFAULT_SCORE, 0.0
        peg = per / growth
        for t, s in self.cfg.PEG_THRESHOLDS:
            if peg < t:
                return s, round(peg, 2)
        return self.cfg.PEG_DEFAULT_SCORE, round(peg, 2)

    def _score_ev_ebitda(self, mcap: int, debt: int, cash: int, ebitda: int) -> tuple[int, float]:
        """EV/EBITDA = (시총+순차입금)/EBITDA.

        ratio가 100을 초과하면 outlier로 간주하고 0점 처리한다 (EBITDA가
        거의 0에 가까운 한계기업이 비대칭적으로 큰 ratio를 만드는 케이스).
        """
        if ebitda <= 0 or mcap <= 0:
            return self.cfg.EV_EBITDA_DEFAULT_SCORE, 0.0
        ev = mcap + debt - cash
        ratio = ev / ebitda
        # outlier 캡: ratio > 100은 0점 (한계기업/EBITDA 0 근접 종목)
        if ratio > 100:
            return self.cfg.EV_EBITDA_DEFAULT_SCORE, round(ratio, 1)
        for t, s in self.cfg.EV_EBITDA_THRESHOLDS:
            if ratio < t:
                return s, round(ratio, 1)
        return self.cfg.EV_EBITDA_DEFAULT_SCORE, round(ratio, 1)

    def _score_psr(self, mcap: int, revenue: int) -> tuple[int, float]:
        """PSR = 시총/매출액."""
        if revenue <= 0 or mcap <= 0:
            return self.cfg.PSR_DEFAULT_SCORE, 0.0
        psr = mcap / revenue
        for t, s in self.cfg.PSR_THRESHOLDS:
            if psr < t:
                return s, round(psr, 2)
        return self.cfg.PSR_DEFAULT_SCORE, round(psr, 2)

    # ================================================================
    # 재무건전성 (20점)
    # ================================================================
    def _calc_financial_score(self, fin: dict) -> dict:
        roe_s = self._threshold_above(fin.get("roe", 0), self.cfg.ROE_THRESHOLDS, self.cfg.ROE_DEFAULT_SCORE)
        opr_s = self._threshold_above(fin.get("operating_margin", 0), self.cfg.OPR_MARGIN_THRESHOLDS, self.cfg.OPR_MARGIN_DEFAULT_SCORE)
        debt_s = self._score_debt(fin.get("debt_ratio", 0))
        cur_s = self._threshold_above(fin.get("current_ratio", 0), self.cfg.CURRENT_RATIO_THRESHOLDS, self.cfg.CURRENT_RATIO_DEFAULT_SCORE)
        return {"roe_score": roe_s, "operating_margin_score": opr_s,
                "debt_ratio_score": debt_s, "current_ratio_score": cur_s,
                "total": roe_s + opr_s + debt_s + cur_s}

    def _score_debt(self, ratio: float) -> int:
        # ratio == 0(또는 음수)은 데이터 결손 신호로 취급. 한국 상장사에서
        # 진짜 부채 0인 사례는 사실상 없음. 결손이 만점을 받지 않도록 default.
        if ratio is None or ratio <= 0:
            return self.cfg.DEBT_RATIO_DEFAULT_SCORE
        for t, s in self.cfg.DEBT_RATIO_THRESHOLDS:
            if ratio < t:
                return s
        return self.cfg.DEBT_RATIO_DEFAULT_SCORE

    # ================================================================
    # 성장성 (20점)
    # 매출성장률(7) + 영업이익성장률(7) + 이익건전성(3) + 턴어라운드(3)
    # ================================================================
    def _calc_growth_score(self, fin: dict) -> dict:
        rev_g = fin.get("revenue_growth_yoy", 0.0)
        op_g = fin.get("op_income_growth_yoy", 0.0)
        rev_s = self._score_growth(rev_g, self.cfg.REVENUE_GROWTH_THRESHOLDS, self.cfg.REVENUE_GROWTH_DEFAULT_SCORE)
        op_s = self._score_growth(op_g, self.cfg.OP_INCOME_GROWTH_THRESHOLDS, self.cfg.OP_INCOME_GROWTH_DEFAULT_SCORE)
        health_s, health_label = self._score_profit_health(
            fin.get("operating_income", 0), fin.get("prev_operating_income", 0),
            fin.get("net_income", 0), fin.get("prev_net_income", 0),
            op_g, fin.get("consecutive_op_decline_years", 0))
        # 이익건전성은 3점으로 캡 (배점 축소)
        health_s = min(health_s, self.cfg.PROFIT_HEALTH_MAX_SCORE)
        turnaround_s, turnaround_label = self._calc_turnaround_score(fin)
        growth_total = rev_s + op_s + health_s + turnaround_s

        total_penalties = 0
        penalty_reasons: list[str] = []
        # MED-5: PL 핵심 필드 모두 0이면 페널티 판정 자체가 불가능. 결손과
        # 무손실(흑자)을 구분해서 사유에 명시. 페널티 점수는 0 유지.
        is_pl_missing = (
            fin.get("revenue", 0) == 0
            and fin.get("operating_income", 0) == 0
            and fin.get("net_income", 0) == 0
        )
        if is_pl_missing:
            penalty_reasons.append("PL 데이터 결손 — 페널티 판정 불가")
        else:
            p = self.cfg.TOTAL_SCORE_PENALTIES
            if fin.get("consecutive_revenue_decline_years", 0) >= 3:
                total_penalties += p["3yr_revenue_decline"]
                penalty_reasons.append(f"3년 연속 매출 감소 ({p['3yr_revenue_decline']})")
            if fin.get("prev_net_income", 0) > 0 and fin.get("net_income", 0) < 0:
                total_penalties += p["profit_to_loss"]
                penalty_reasons.append(f"흑자→적자 전환 ({p['profit_to_loss']})")
            if fin.get("consecutive_loss_years", 0) >= 3:
                total_penalties += p["3yr_consecutive_loss"]
                penalty_reasons.append(f"3년 연속 적자 ({p['3yr_consecutive_loss']})")

        return {"revenue_growth_score": rev_s, "op_income_growth_score": op_s,
                "profit_health_score": health_s, "profit_health_label": health_label,
                "turnaround_score": turnaround_s, "turnaround_label": turnaround_label,
                "total": max(0, growth_total), "total_penalties": total_penalties,
                "penalty_reasons": penalty_reasons}

    def _calc_turnaround_score(self, fin: dict) -> tuple[int, str]:
        """4기간 영업이익 흐름으로 턴어라운드 점수 (max 3점) 산출.

        판정 우선순위:
          1. 적자→흑자 전환 (prev<0, curr>0) → 3점
          2. 감소→증가 전환 (전전기>전기 AND 전기<당기) → 2점
          3. 2년 연속 증가 (전전기<전기<당기) → 1점
          4. 지속 감소 / 데이터 부족 → 0점

        2~3년 전 데이터는 self._db.get_op_income(code, year-2)로 조회하며,
        DB 미주입/조회 실패 시 rule 1만 평가하고 나머지는 데이터 부족 처리.
        """
        curr_op = fin.get("operating_income", 0)
        prev_op = fin.get("prev_operating_income", 0)
        code = fin.get("stock_code", "")
        year = fin.get("year", 0) or 0
        scores = self.cfg.TURNAROUND_SCORES

        # Rule 1: 적자→흑자 (가장 강한 시그널, DB 조회 불필요)
        if prev_op < 0 and curr_op > 0:
            return scores["loss_to_profit"], "적자→흑자 전환"

        # 2년 전 영업이익 조회 (선택적)
        op_y2: Any = None
        if self._db and code and year:
            try:
                op_y2 = self._db.get_op_income(code, year - 2)
            except Exception as e:
                logger.debug("turnaround DB 조회 실패 %s: %s", code, e)
                op_y2 = None

        # Rule 2: 감소→증가 전환 (전전기 > 전기 AND 전기 < 당기)
        if op_y2 is not None and op_y2 > prev_op and prev_op < curr_op:
            return scores["decline_to_growth"], "감소→증가 전환"

        # Rule 3: 2년 연속 증가 (전전기 < 전기 < 당기)
        if op_y2 is not None and op_y2 < prev_op < curr_op:
            return scores["continuous_growth"], "2년 연속 증가"

        # Rule 4: 데이터 부족 또는 지속 감소
        if op_y2 is None:
            return scores["no_data"], "데이터 부족"
        return scores["declining"], "지속 감소"

    def _score_growth(self, rate: float, thresholds: list, default: int) -> int:
        # rate == 0(또는 None)은 데이터 결손 신호로 취급. 임계값 (0.0, 2)와의
        # 첫 매칭에서 결손이 가산점을 받던 silent fail 차단.
        if rate is None or rate == 0:
            return default
        for t, s in thresholds:
            if rate >= t:
                return s
        return default

    def _score_profit_health(self, curr_op, prev_op, curr_net, prev_net, op_g, consec):
        rules = self.cfg.PROFIT_PENALTY_RULES
        if prev_net > 0 and curr_net < 0:
            s, l = rules["loss_turnaround"], "흑자→적자 전환"
        elif curr_net < 0:
            s, l = rules["severe_decline"], "적자 지속"
        elif op_g <= -50:
            s, l = rules["severe_decline"], f"이익 급감 ({op_g:.0f}%)"
        elif op_g <= -15:
            s, l = rules["significant_decline"], f"이익 큰 폭 감소 ({op_g:.0f}%)"
        elif op_g < 0:
            s, l = rules["slight_decline"], f"이익 소폭 감소 ({op_g:.0f}%)"
        else:
            s, l = rules["healthy"], "이익 건전"
        if consec >= 2 and curr_net > 0:
            e = rules["consecutive_decline_extra"]; s += e; l += f" +2년연속감소({e})"
        if consec >= 2 and curr_net < 0:
            e = rules["consecutive_loss_extra"]; s += e; l += f" +2년연속적자({e})"
        return max(s, 0), l

    # ================================================================
    # 모멘텀 (30점, v3.1)
    # ================================================================
    def _calc_momentum_score(self, price: dict, chart: list) -> dict:
        if not chart or len(chart) < 5:
            return {"ma20_score": 0, "ma60_score": 0, "volume_score": 0,
                    "rsi_score": 0, "macd_score": 0, "supply_demand_score": 0,
                    "week52_score": 0, "week52_pct": 0, "total": 0}
        closes = [c["close"] for c in chart if c["close"] > 0]
        volumes = [c["volume"] for c in chart if c["volume"] > 0]
        if len(closes) < 5:
            return {"ma20_score": 0, "ma60_score": 0, "volume_score": 0,
                    "rsi_score": 0, "macd_score": 0, "supply_demand_score": 0,
                    "week52_score": 0, "week52_pct": 0, "total": 0}
        cp = price.get("current_price", 0)
        if cp <= 0 and closes:
            cp = closes[0]

        ma20 = self._score_ma(cp, closes, 20, self.cfg.MA20_SCORES)
        ma60 = self._score_ma60(cp, closes)
        vol = self._score_volume(volumes)
        rsi = self._score_rsi(closes)
        macd = self._score_macd(closes)
        sd = self._score_supply_demand(price)
        w52, w52_pct = self._score_week52(cp, closes)

        return {"ma20_score": ma20, "ma60_score": ma60, "volume_score": vol,
                "rsi_score": rsi, "macd_score": macd, "supply_demand_score": sd,
                "week52_score": w52, "week52_pct": w52_pct,
                "total": ma20 + ma60 + vol + rsi + macd + sd + w52}

    def _score_ma(self, cp, closes, period, scores):
        ma = np.mean(closes[:min(period, len(closes))])
        rising = len(closes) >= 10 and np.mean(closes[:5]) > np.mean(closes[5:10])
        if cp > ma and rising: return scores["strong_up"]
        elif cp > ma: return scores["above"]
        elif rising: return scores["bounce"]
        return scores["down"]

    def _score_ma60(self, cp, closes):
        s = self.cfg.MA60_SCORES
        ma60 = np.mean(closes[:min(60, len(closes))])
        ma20 = np.mean(closes[:min(20, len(closes))])
        golden = False
        if len(closes) >= 25:
            golden = ma20 > ma60 and np.mean(closes[5:25]) <= ma60
        if cp > ma60 and golden: return s["golden_cross"]
        elif cp > ma60: return s["above"]
        elif len(closes) >= 65 and np.mean(closes[:60]) > np.mean(closes[5:65]): return s["bounce"]
        return s["down"]

    def _score_volume(self, vols):
        s = self.cfg.VOLUME_SCORES
        if len(vols) < 20: return s["decrease"]
        a5, a20 = np.mean(vols[:5]), np.mean(vols[:20])
        if a20 == 0: return s["decrease"]
        r = a5 / a20
        if r >= self.cfg.VOLUME_SURGE_RATIO: return s["surge"]
        elif r >= self.cfg.VOLUME_INCREASE_RATIO: return s["increase"]
        elif r >= 1.0: return s["normal"]
        return s["decrease"]

    def _score_rsi(self, closes):
        s = self.cfg.RSI_SCORES; p = self.cfg.RSI_PERIOD
        if len(closes) < p + 1: return s["neutral"]
        g_sum = l_sum = 0.0
        for i in range(p):
            c = closes[i] - closes[i+1]
            if c > 0: g_sum += c
            else: l_sum += abs(c)
        ag, al = g_sum/p, l_sum/p
        if al == 0: rsi = 100.0
        elif ag == 0: rsi = 0.0
        else: rsi = 100 - (100 / (1 + ag/al))
        if rsi >= 70: return s["overbought"]
        elif rsi >= 60: return s["strong_but_ok"]
        elif rsi >= 50: return s["neutral"]
        elif rsi >= 30: return s["healthy_up"]
        return s["oversold_bounce"]

    def _score_macd(self, closes):
        s = self.cfg.MACD_SCORES
        if len(closes) < 26: return s["bearish"]
        e12 = self._ema(closes, 12); e26 = self._ema(closes, 26)
        if e12 is None or e26 is None: return s["bearish"]
        ml = e12 - e26
        if len(closes) >= 31:
            pe12 = self._ema(closes[5:], 12); pe26 = self._ema(closes[5:], 26)
            if pe12 and pe26:
                pm = pe12 - pe26
                if ml > 0 and pm <= 0: return s["bullish_cross"]
                if ml < 0 and pm >= 0: return s["bearish_cross"]
        return s["bullish"] if ml > 0 else s["bearish"]

    @staticmethod
    def _ema(prices, period):
        if len(prices) < period: return None
        m = 2 / (period + 1)
        e = float(np.mean(prices[-period:]))
        for p in reversed(prices[:-period]):
            e = (p - e) * m + e
        return e

    def _score_supply_demand(self, price):
        """수급 점수 (총 12점, v3.1) - 외국인/기관 5일·20일 분리 채점.

        - 외국인 5일 (max 5): 연속 ≥5 → 5점, ≥3 → 3점
        - 외국인 20일 (max 4): 20일 중 매수일 ≥10 → 4점, ≥1 → 3점
        - 기관 5일 (max 2): 연속 ≥3 → 2점
        - 기관 20일 (max 1): 20일 중 매수일 ≥1 → 1점
        """
        s = self.cfg.SUPPLY_DEMAND_SCORES
        f5 = price.get("foreign_net_buy_5d", 0)
        f20 = price.get("foreign_net_buy_20d", 0)
        i5 = price.get("institutional_net_buy_5d", 0)
        i20 = price.get("institutional_net_buy_20d", 0)

        score = 0

        # 외국인 5일 연속
        if f5 >= 5:
            score += s["foreign_5d_streak_5"]
        elif f5 >= 3:
            score += s["foreign_5d_streak_3"]

        # 외국인 20일 누적
        if f20 >= 10:
            score += s["foreign_20d_strong"]
        elif f20 >= 1:
            score += s["foreign_20d_positive"]

        # 기관 5일 연속
        if i5 >= 3:
            score += s["inst_5d_streak_3"]

        # 기관 20일 누적
        if i20 >= 1:
            score += s["inst_20d_positive"]

        return score

    def _score_week52(self, cp, closes) -> tuple[int, float]:
        """52주 고저 대비 현재 위치를 점수화한다."""
        s = self.cfg.WEEK52_SCORES
        # 일봉 60일이므로 가용 데이터 전체 사용
        all_prices = closes[:min(250, len(closes))]
        if not all_prices:
            return 0, 0.0
        high = max(all_prices)
        low = min(all_prices)
        if high == low:
            return s["upper_half"], 50.0
        pct = ((cp - low) / (high - low)) * 100  # 0=최저, 100=최고

        if pct <= 20: return s["near_low"], round(pct, 1)
        elif pct <= 50: return s["lower_half"], round(pct, 1)
        elif pct <= 80: return s["upper_half"], round(pct, 1)
        return s["near_high"], round(pct, 1)

    # ================================================================
    # 퀄리티 (10점)
    # ================================================================
    def _calc_quality_score(self, price: dict, fin: dict) -> dict:
        mcap = price.get("market_cap", 0)
        fcf = fin.get("free_cash_flow", 0)
        revenue = fin.get("revenue", 0)

        fcf_yield_s, fcf_yield_v = self._score_fcf_yield(fcf, mcap)
        fcf_margin_s, fcf_margin_v = self._score_fcf_margin(fcf, revenue)

        return {"fcf_yield_score": fcf_yield_s, "fcf_yield_value": fcf_yield_v,
                "fcf_margin_score": fcf_margin_s, "fcf_margin_value": fcf_margin_v,
                "total": fcf_yield_s + fcf_margin_s}

    def _score_fcf_yield(self, fcf: int, mcap: int) -> tuple[int, float]:
        if mcap <= 0 or fcf <= 0:
            return self.cfg.FCF_YIELD_DEFAULT_SCORE, 0.0
        y = (fcf / mcap) * 100
        for t, s in self.cfg.FCF_YIELD_THRESHOLDS:
            if y >= t: return s, round(y, 1)
        return self.cfg.FCF_YIELD_DEFAULT_SCORE, round(y, 1)

    def _score_fcf_margin(self, fcf: int, revenue: int) -> tuple[int, float]:
        if revenue <= 0 or fcf <= 0:
            return self.cfg.FCF_MARGIN_DEFAULT_SCORE, 0.0
        m = (fcf / revenue) * 100
        for t, s in self.cfg.FCF_MARGIN_THRESHOLDS:
            if m >= t: return s, round(m, 1)
        return self.cfg.FCF_MARGIN_DEFAULT_SCORE, round(m, 1)

    # ================================================================
    # 유틸리티
    # ================================================================
    def _threshold_below(self, val, thresholds, default, reject_zero=False):
        if reject_zero and val <= 0: return default
        for t, s in thresholds:
            if val < t: return s
        return default

    @staticmethod
    def _threshold_above(val, thresholds, default):
        if val <= 0: return default
        for t, s in thresholds:
            if val >= t: return s
        return default

    def score_all_stocks(self, price_list, financial_list, chart_dict):
        fin_map = {f["stock_code"]: f for f in financial_list}
        results = []
        for price in price_list:
            code = price.get("stock_code", "")
            financial = fin_map.get(code, self._empty_fin(code))
            chart = chart_dict.get(code, [])
            try:
                results.append(self.calculate_score(price, financial, chart))
            except Exception as e:
                logger.warning("종목 %s 스코어링 실패: %s", code, e)
        results.sort(key=lambda x: x["total_score"], reverse=True)
        return results

    @staticmethod
    def _empty_fin(code):
        return {
            "stock_code": code, "roe": 0.0, "operating_margin": 0.0,
            "debt_ratio": 0.0, "current_ratio": 0.0, "dividend_yield": 0.0,
            "revenue_growth_yoy": 0.0, "op_income_growth_yoy": 0.0,
            "operating_income": 0, "prev_operating_income": 0,
            "net_income": 0, "prev_net_income": 0,
            "consecutive_loss_years": 0, "consecutive_op_decline_years": 0,
            "consecutive_revenue_decline_years": 0, "sector": "기타",
            "revenue": 0, "ebitda": 0, "total_liabilities": 0,
            "cash_equivalents": 0, "free_cash_flow": 0,
        }
