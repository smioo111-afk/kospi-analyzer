"""
KOSPI 저평가 기업 분석 시스템 - 텔레그램 메시지 포맷팅 모듈

설계서 섹션 7.1의 메시지 포맷을 구현한다:
  - TOP 10 리포트 (종합점수, 신호, PER/PBR/ROE, 손절라인)
  - 경고 종목 메시지
  - 시장 요약
  - 개별 종목 상세 분석
"""

import logging
from datetime import datetime
from typing import Any, Optional

from config.settings import TelegramConfig

logger = logging.getLogger(__name__)

# 숫자 이모지
NUM_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

# 요일 한글
WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]


class MessageFormatter:
    """텔레그램 메시지 포맷터.

    분석 결과를 텔레그램에 최적화된 텍스트로 변환한다.
    텔레그램 메시지 최대 길이(4096자)를 고려하여 분할 발송을 지원한다.
    """

    def __init__(self, db: Optional[Any] = None) -> None:
        self.max_length = TelegramConfig.MAX_MESSAGE_LENGTH
        # 종목명 폴백 lookup용 Database (optional).
        # 주입되면 format_portfolio_for_report에서 stock_master 조회에 사용.
        self._db = db

    # ================================================================
    # 메인 리포트
    # ================================================================
    def format_daily_report(
        self,
        top_10: list[dict[str, Any]],
        warnings: list[dict[str, Any]],
        stats: dict[str, Any],
        stoploss_map: Optional[dict[str, dict[str, Any]]] = None,
        kospi_index: float = 0.0,
        kospi_change: float = 0.0,
        foreign_net_buy: int = 0,
        prev_top_10: Optional[list[dict[str, Any]]] = None,
        scored_list: Optional[list[dict[str, Any]]] = None,
        disclosure_impacts: Optional[list[Any]] = None,
    ) -> list[str]:
        """일일 분석 리포트를 생성한다.

        Args:
            prev_top_10: 전일 TOP 10 (순위/점수 변동 추적용)
            scored_list: 전종목 스코어링 결과. 주어지면 종합 TOP 10 다음에
                momentum_score 기준 모멘텀 TOP 10 보조 섹션을 추가한다.
        """
        if stoploss_map is None:
            stoploss_map = {}

        # 전일 데이터 매핑 (종목코드 → {rank, total_score, stock_name})
        prev_map: dict[str, dict[str, Any]] = {}
        if prev_top_10:
            for idx, s in enumerate(prev_top_10):
                prev_map[s.get("stock_code", "")] = {
                    "rank": idx + 1,
                    "total_score": s.get("total_score", 0),
                    "stock_name": s.get("stock_name", ""),
                }

        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        weekday = WEEKDAYS[now.weekday()]

        lines: list[str] = []
        lines.append("📊 KOSPI 저평가 기업 TOP 10")
        lines.append(f"📅 {date_str} ({weekday}) 장 마감 분석")
        lines.append("")

        for idx, stock in enumerate(top_10[:10]):
            emoji = NUM_EMOJI[idx] if idx < 10 else f"{idx+1}."
            lines.append(self._format_stock_entry(
                emoji, stock, stoploss_map, idx + 1, prev_map
            ))
            lines.append("")

        # 모멘텀 TOP 10 보조 섹션 (scored_list가 주어졌을 때만)
        # 종합 TOP 10(가치+모멘텀 하이브리드)과 별도로 순수 모멘텀 시각 제공.
        # 필터: 시총 1,000억+, 거래대금 10억+ (유동성). 가치 필터 없음.
        # 정렬: momentum_score 내림차순 → foreign_net_buy_5d 내림차순.
        if scored_list:
            momentum_pool = [
                s for s in scored_list
                if s.get("market_cap", 0) >= 100_000_000_000
                and s.get("trading_value", 0) >= 1_000_000_000
            ]
            momentum_pool.sort(
                key=lambda s: (
                    s.get("momentum_score", 0),
                    s.get("foreign_net_buy_5d", 0),
                ),
                reverse=True,
            )
            top_momentum = momentum_pool[:10]

            if top_momentum:
                lines.append(
                    f"🚀 모멘텀 TOP {len(top_momentum)} "
                    f"(시총 1,000억+, 거래대금 10억+)"
                )
                for idx, s in enumerate(top_momentum):
                    emoji = NUM_EMOJI[idx] if idx < 10 else f"{idx+1}."
                    code = s.get("stock_code", "")
                    name = s.get("stock_name", "")
                    price = s.get("current_price", 0)
                    mom = s.get("momentum_score", 0)
                    w52 = s.get("week52_position", 0)
                    f5 = s.get("foreign_net_buy_5d", 0)
                    i5 = s.get("institutional_net_buy_5d", 0)
                    price_str = f"{price:,}원" if price else "—"
                    w52_str = f"{w52:.0f}%" if w52 else "—"
                    f5_str = f"{f5:+,}" if f5 else "0"
                    i5_str = f"{i5:+,}" if i5 else "0"
                    lines.append(
                        f"{emoji} {self._format_name_code(name, code)}"
                    )
                    lines.append(
                        f"   모멘텀: {mom}/20 | 52주: {w52_str} | "
                        f"현재가: {price_str}"
                    )
                    lines.append(
                        f"   수급(5일): 외국인 {f5_str} | 기관 {i5_str}"
                    )
                lines.append("")

        # 공시 영향 변화 섹션 (A1 Phase 3, 자정 모니터 결과)
        # disclosure_impacts가 주어지고 비어있지 않을 때만 추가.
        if disclosure_impacts:
            section = format_disclosure_section(disclosure_impacts)
            if section:
                lines.append(section)
                lines.append("")

        # 경고 종목
        if warnings:
            lines.append("⚠️ 경고 종목")
            for w in warnings:
                code = w.get("stock_code", "")
                name = w.get("stock_name", "")
                sl = stoploss_map.get(code, {})
                pct = sl.get("effective_stoploss_pct", 0)
                warn_msgs = sl.get("warnings", [])

                line = f"   ❌ {self._format_name_code(name, code)}: 손절라인 접근 ({pct}%)"
                if warn_msgs:
                    line += f" | {warn_msgs[0]}"
                lines.append(line)
            lines.append("")

        # TOP 10 탈락 종목 (전일 대비)
        if prev_map:
            today_codes = {s.get("stock_code", "") for s in top_10[:10]}
            dropouts = []
            for code, prev in prev_map.items():
                if code not in today_codes:
                    dropouts.append((prev["rank"], code, prev.get("stock_name", ""), prev["total_score"]))
            if dropouts:
                dropouts.sort(key=lambda x: x[0])
                lines.append("📉 TOP 10 탈락 종목")
                for prev_rank, code, name, prev_score in dropouts:
                    lines.append(f"   ↘️ {self._format_name_code(name, code)}: 전일 {prev_rank}위({prev_score}점) → 탈락")
                lines.append("")

        # 시장 요약
        lines.append("📋 시장 요약")
        if kospi_index > 0:
            sign = "+" if kospi_change >= 0 else ""
            lines.append(f"   KOSPI: {kospi_index:,.1f} ({sign}{kospi_change}%)")
        if foreign_net_buy != 0:
            sign = "+" if foreign_net_buy >= 0 else ""
            lines.append(f"   외국인 순매수: {sign}{foreign_net_buy:,}억")

        # 통계
        total = stats.get("total_analyzed", 0)
        filtered = stats.get("after_filter", 0)
        lines.append(f"   분석: {total}종목 → 필터 {filtered}종목")

        # 분할 발송
        return self._split_messages(lines)

    def _format_stock_entry(
        self,
        emoji: str,
        stock: dict[str, Any],
        stoploss_map: dict[str, dict[str, Any]],
        current_rank: int = 0,
        prev_map: Optional[dict[str, dict[str, Any]]] = None,
    ) -> str:
        """개별 종목 항목을 포맷팅한다."""
        if prev_map is None:
            prev_map = {}
        code = stock.get("stock_code", "")
        name = stock.get("stock_name", "")
        total = stock.get("total_score", 0)
        signal = stock.get("signal_label", "")
        per = stock.get("per", 0)
        pbr = stock.get("pbr", 0)
        roe = stock.get("roe", 0)
        price = stock.get("current_price", 0)
        reason = stock.get("reason", "")

        # 손절 정보
        sl = stoploss_map.get(code, {})
        sl_price = sl.get("effective_stoploss", 0)
        sl_pct = sl.get("effective_stoploss_pct", 0)

        # 순위/점수 변동
        change_str = ""
        if prev_map and code in prev_map:
            prev = prev_map[code]
            prev_rank = prev["rank"]
            prev_score = prev["total_score"]
            score_diff = total - prev_score

            # 순위 변동
            rank_diff = prev_rank - current_rank  # 양수=상승
            if rank_diff > 0:
                rank_str = f"▲{rank_diff}"
            elif rank_diff < 0:
                rank_str = f"▼{abs(rank_diff)}"
            else:
                rank_str = "─"

            # 점수 변동
            if score_diff > 0:
                score_str = f"+{score_diff}"
            elif score_diff < 0:
                score_str = f"{score_diff}"
            else:
                score_str = "0"

            change_str = f" [{rank_str} | {score_str}점]"
        elif prev_map and code not in prev_map:
            change_str = " [🆕 신규진입]"

        lines = [
            f"{emoji} {self._format_name_code(name, code)}{change_str}",
            f"   종합점수: {total}/100 | 신호: {signal}",
            f"   PER: {per} | PBR: {pbr} | ROE: {roe}%",
            f"   현재가: {price:,}원",
        ]

        # 적정주가
        fair_low = stock.get("fair_value_low", 0)
        fair_high = stock.get("fair_value_high", 0)
        if fair_low > 0 and fair_high > 0:
            gap = stock.get("fair_value_gap", 0)
            if gap < 0:
                lines.append(f"   적정주가: {fair_low:,}~{fair_high:,}원 ({gap}% 저평가)")
            elif gap > 0:
                lines.append(f"   적정주가: {fair_low:,}~{fair_high:,}원 (+{gap}% 고평가)")
            else:
                lines.append(f"   적정주가: {fair_low:,}~{fair_high:,}원 (적정)")

        if sl_price > 0:
            lines.append(f"   손절라인: {sl_price:,}원 ({sl_pct}%)")

        # 수급 정보
        foreign_days = stock.get("foreign_net_buy_days", 0)
        inst_days = stock.get("institutional_net_buy_days", 0)
        if foreign_days != 0 or inst_days != 0:
            f_label = f"+{foreign_days}일" if foreign_days > 0 else f"{foreign_days}일"
            i_label = f"+{inst_days}일" if inst_days > 0 else f"{inst_days}일"
            lines.append(f"   수급: 외국인 {f_label} | 기관 {i_label}")

        if reason:
            lines.append(f"   사유: {reason}")

        return "\n".join(lines)

    # ================================================================
    # 개별 종목 상세
    # ================================================================
    def format_stock_detail(
        self,
        stock: dict[str, Any],
        stoploss: Optional[dict[str, Any]] = None,
        history: Optional[list[dict[str, Any]]] = None,
    ) -> str:
        """개별 종목 상세 분석 결과를 v3 5카테고리로 포맷팅한다.

        텔레그램 /stock 명령어 응답용.

        Args:
            stock: 종목 스코어 데이터 (stock_scores + daily_report_log merge 후)
            stoploss: 손절 정보 (stock['stoploss_price']가 우선)
            history: 최근 스코어 이력
        """
        code = stock.get("stock_code", "")
        name = stock.get("stock_name", "")

        lines = [
            f"📈 {self._format_name_code(name, code)} 상세 분석",
            "",
            f"종합점수: {stock.get('total_score', 0)}/100 | "
            f"{stock.get('signal_label', '')}",
        ]

        reason = stock.get("reason", "")
        if reason:
            lines.append(f"사유: {reason}")
        lines.append("")

        # ── 가치 (30) ──
        lines.append("── 가치 (30) ──")
        lines.append(
            f"  PER: {self._fmt_num(stock.get('per'))} | "
            f"PBR: {self._fmt_num(stock.get('pbr'))} | "
            f"배당: {self._fmt_pct(stock.get('dividend_yield'))}"
        )
        peg = stock.get("peg")
        ev = stock.get("ev_ebitda")
        psr = stock.get("psr")
        ext = []
        if peg is not None:
            ext.append(f"PEG: {self._fmt_num(peg)}")
        if ev is not None:
            ext.append(f"EV/EBITDA: {self._fmt_num(ev)}")
        if psr is not None:
            ext.append(f"PSR: {self._fmt_num(psr)}")
        if ext:
            lines.append("  " + " | ".join(ext))
        lines.append(f"  점수: {stock.get('value_score', 0)}/30")
        lines.append("")

        # ── 재무 (20) ──
        lines.append("── 재무 (20) ──")
        lines.append(
            f"  ROE: {self._fmt_pct(stock.get('roe'))} | "
            f"영업이익률: {self._fmt_pct(stock.get('operating_margin'))}"
        )
        lines.append(
            f"  부채비율: {self._fmt_pct(stock.get('debt_ratio'))}"
        )
        lines.append(f"  점수: {stock.get('financial_score', 0)}/20")
        lines.append("")

        # ── 성장 (20) — daily_report_log 또는 top_10_json 보강분 ──
        rev_g = stock.get("revenue_growth")
        op_g = stock.get("op_income_growth")
        growth_score = stock.get("growth_score")
        lines.append("── 성장 (20) ──")
        if rev_g is None and op_g is None and growth_score is None:
            lines.append("  데이터 없음 (TOP 10 진입 이력 없음)")
        else:
            lines.append(
                f"  매출 성장: {self._fmt_pct(rev_g)} | "
                f"영업이익 성장: {self._fmt_pct(op_g)}"
            )
            if growth_score is not None:
                lines.append(f"  점수: {growth_score}/20")
        lines.append("")

        # ── 모멘텀 (20) ──
        lines.append("── 모멘텀 (20) ──")
        f5 = stock.get("foreign_net_buy_5d")
        f20 = stock.get("foreign_net_buy_20d")
        i5 = stock.get("institutional_net_buy_5d")
        i20 = stock.get("institutional_net_buy_20d")
        if f5 is not None or f20 is not None:
            lines.append(
                f"  수급(외/기 5d): {self._fmt_signed(f5)} / "
                f"{self._fmt_signed(i5)}"
            )
            lines.append(
                f"  수급(외/기 20d): {self._fmt_signed(f20)} / "
                f"{self._fmt_signed(i20)}"
            )
        else:
            f_days = stock.get("foreign_net_buy_days")
            i_days = stock.get("institutional_net_buy_days")
            if f_days is not None or i_days is not None:
                lines.append(
                    f"  수급(누적일): 외국인 {self._fmt_signed(f_days)} | "
                    f"기관 {self._fmt_signed(i_days)}"
                )
            else:
                lines.append("  수급 데이터 없음")
        w52 = stock.get("week52_position")
        if w52 is not None:
            lines.append(f"  52주 위치: {self._fmt_num(w52)}%")
        lines.append(f"  점수: {stock.get('momentum_score', 0)}/20")
        lines.append("")

        # ── 퀄리티 (10) ──
        lines.append("── 퀄리티 (10) ──")
        fcf_y = stock.get("fcf_yield")
        if fcf_y is not None:
            lines.append(f"  FCF 수익률: {self._fmt_pct(fcf_y)}")
        quality_score = stock.get("quality_score")
        if quality_score is not None:
            lines.append(f"  점수: {quality_score}/10")
        else:
            lines.append("  데이터 없음")
        lines.append("")

        # 시세·시총
        lines.append(f"현재가: {stock.get('current_price', 0):,}원")
        lines.append(
            f"시가총액: {self._format_market_cap(stock.get('market_cap', 0))}"
        )

        # 적정주가
        fair_low = stock.get("fair_value_low") or 0
        fair_high = stock.get("fair_value_high") or 0
        if fair_low > 0 and fair_high > 0:
            gap = stock.get("fair_value_gap", 0)
            if gap < 0:
                tag = f"({gap}% 저평가)"
            elif gap > 0:
                tag = f"(+{gap}% 고평가)"
            else:
                tag = "(적정)"
            lines.append(
                f"적정주가: {fair_low:,}~{fair_high:,}원 {tag}"
            )

        # 손절 (stock dict에 저장된 값 우선, 없으면 인자 stoploss 사용)
        sl_price = stock.get("stoploss_price") or 0
        sl_pct = stock.get("stoploss_pct") or 0
        if sl_price > 0:
            lines.append(f"손절라인: {sl_price:,}원 ({sl_pct}%)")
        elif stoploss:
            lines.extend([
                "",
                "── 손절 라인 ──",
                f"  ATR({stoploss.get('atr', 0):,.0f}) × "
                f"{stoploss.get('multiplier', 2.0)}배",
                f"  손절가: {stoploss.get('effective_stoploss', 0):,}원 "
                f"({stoploss.get('effective_stoploss_pct', 0)}%)",
            ])
            for w in stoploss.get("warnings", []):
                lines.append(f"  ⚠️ {w}")

        # 이력
        if history and len(history) > 1:
            lines.extend(["", "── 최근 이력 ──"])
            for h in history[:5]:
                lines.append(
                    f"  {h.get('analysis_date', '')}: "
                    f"{h.get('total_score', 0)}점 "
                    f"{h.get('signal_label', '')}"
                )

        lines.extend([
            "",
            "⚠️ 투자 참고용이며 투자 손실의 책임은 지지 않습니다."
        ])

        return "\n".join(lines)

    @staticmethod
    def _fmt_num(v: Any) -> str:
        if v is None:
            return "—"
        try:
            f = float(v)
        except (TypeError, ValueError):
            return "—"
        if f == 0:
            return "—"
        return f"{f:,.2f}".rstrip("0").rstrip(".") or "0"

    @staticmethod
    def _fmt_pct(v: Any) -> str:
        if v is None:
            return "—"
        try:
            f = float(v)
        except (TypeError, ValueError):
            return "—"
        if f == 0:
            return "—"
        return f"{f:.2f}%"

    @staticmethod
    def _fmt_signed(v: Any) -> str:
        if v is None:
            return "—"
        try:
            f = float(v)
        except (TypeError, ValueError):
            return "—"
        if f == 0:
            return "0"
        return f"{f:+g}"

    # ================================================================
    # 이력 리포트
    # ================================================================
    def format_history_report(
        self, reports: list[dict[str, Any]]
    ) -> str:
        """최근 7일 분석 이력을 포맷팅한다.

        텔레그램 /history 명령어 응답용.
        """
        if not reports:
            return "📋 분석 이력이 없습니다."

        lines = ["📋 최근 분석 이력", ""]

        for report in reports[:7]:
            date = report.get("analysis_date", "")
            top_10 = report.get("top_10", [])
            kospi = report.get("kospi_index", 0)

            top1_name = top_10[0].get("stock_name", "-") if top_10 else "-"
            top1_score = top_10[0].get("total_score", 0) if top_10 else 0

            lines.append(
                f"📅 {date} | KOSPI {kospi:,.1f}"
            )
            lines.append(
                f"   TOP1: {top1_name} ({top1_score}점)"
            )
            lines.append("")

        return "\n".join(lines)

    # ================================================================
    # 포트폴리오
    # ================================================================
    def format_portfolio(
        self,
        portfolio: list[dict[str, Any]],
        scores_map: Optional[dict[str, dict[str, Any]]] = None,
        stoploss_map: Optional[dict[str, dict[str, Any]]] = None,
        previous_prices: Optional[dict[str, int]] = None,
    ) -> str:
        """포트폴리오 현황을 포맷팅한다.

        텔레그램 /portfolio 명령어 및 일일 리포트 하단에 사용.

        Args:
            portfolio: 종목별 포트폴리오 리스트
            scores_map: {종목코드: 최신 스코어 데이터}
            stoploss_map: {종목코드: 손절 정보}
            previous_prices: {종목코드: 직전 영업일 종가} — 전일 대비 표시용
        """
        if not portfolio:
            return (
                "💼 보유 종목이 없습니다.\n"
                "/buy [종목코드] [매수가] [수량] 으로 등록하세요.\n"
                "예) /buy 005930 56600 10"
            )

        if scores_map is None:
            scores_map = {}
        if stoploss_map is None:
            stoploss_map = {}
        if previous_prices is None:
            previous_prices = {}

        lines = ["💼 내 포트폴리오", ""]

        total_invested = 0
        total_current = 0
        total_prev_value = 0

        for p in portfolio:
            code = p["stock_code"]
            name = p.get("stock_name") or ""
            avg_price = p["avg_buy_price"]
            qty = p["total_quantity"]
            invested = p["total_invested"]

            score = scores_map.get(code, {})
            current_price = score.get("current_price", 0)
            signal_label = score.get("signal_label", "")
            total_score = score.get("total_score", 0)

            if current_price <= 0:
                current_price = avg_price

            prev_price = previous_prices.get(code, 0)

            current_value = current_price * qty
            pnl = current_value - invested
            pnl_pct = (pnl / invested * 100) if invested > 0 else 0
            pnl_emoji = "📈" if pnl >= 0 else "📉"

            if prev_price > 0:
                daily_change = current_price - prev_price
                daily_pct = (daily_change / prev_price * 100)
                daily_sign = "+" if daily_change >= 0 else ""
                daily_emoji = "📈" if daily_change >= 0 else "📉"
                total_prev_value += prev_price * qty
            else:
                daily_change = 0
                daily_pct = 0.0
                daily_sign = ""
                daily_emoji = ""
                total_prev_value += current_value

            total_invested += invested
            total_current += current_value

            lines.append(f"┌ {self._format_name_code(name, code)}")

            if p["buy_count"] > 1:
                lines.append(f"│ 매수 {p['buy_count']}회 (평단: {avg_price:,}원)")
                for lot in p["lots"]:
                    lines.append(
                        f"│   {lot['buy_date']}: {lot['buy_price']:,}원 × {lot['quantity']}주"
                    )
            else:
                lines.append(f"│ 매수: {avg_price:,}원 × {qty}주 = {invested:,}원")

            lines.append(f"│ 현재: {current_price:,}원 × {qty}주 = {current_value:,}원")

            if prev_price > 0 and daily_change != 0:
                lines.append(
                    f"│ 전일대비: {daily_sign}{daily_change:,}원 "
                    f"({daily_sign}{daily_pct:.2f}%) {daily_emoji}"
                )

            sign = "+" if pnl >= 0 else ""
            lines.append(f"│ 손익: {sign}{pnl:,}원 ({sign}{pnl_pct:.1f}%) {pnl_emoji}")

            if signal_label:
                lines.append(f"│ 신호: {signal_label} ({total_score}점)")

            sl = stoploss_map.get(code, {})
            sl_price = sl.get("effective_stoploss", 0)
            if sl_price > 0 and current_price > 0:
                sl_pct = sl.get("effective_stoploss_pct", 0)
                lines.append(f"│ 손절: {sl_price:,}원 ({sl_pct}%)")
                if current_price <= sl_price * 1.02:
                    lines.append(f"│ ⚠️ 손절라인 접근 주의!")

            lines.append("└──────────────")
            lines.append("")

        total_pnl = total_current - total_invested
        total_pnl_pct = (total_pnl / total_invested * 100) if total_invested > 0 else 0
        sign = "+" if total_pnl >= 0 else ""
        total_pnl_emoji = "📈" if total_pnl >= 0 else "📉"

        total_daily_change = total_current - total_prev_value
        total_daily_pct = (
            (total_daily_change / total_prev_value * 100) if total_prev_value > 0 else 0
        )
        total_daily_sign = "+" if total_daily_change >= 0 else ""
        total_daily_emoji = "📈" if total_daily_change >= 0 else "📉"

        lines.append(f"📊 합계")
        lines.append(f"   투자: {total_invested:,}원")
        lines.append(f"   평가: {total_current:,}원")
        lines.append(
            f"   손익: {sign}{total_pnl:,}원 "
            f"({sign}{total_pnl_pct:.1f}%) {total_pnl_emoji}"
        )

        if total_prev_value > 0 and total_daily_change != 0:
            lines.append(
                f"   전일대비: {total_daily_sign}{total_daily_change:,}원 "
                f"({total_daily_sign}{total_daily_pct:.2f}%) {total_daily_emoji}"
            )

        return "\n".join(lines)

    def format_portfolio_for_report(
        self,
        portfolio: list[dict[str, Any]],
        scores_map: Optional[dict[str, dict[str, Any]]] = None,
        stoploss_map: Optional[dict[str, dict[str, Any]]] = None,
        previous_prices: Optional[dict[str, int]] = None,
    ) -> Optional[str]:
        """일일 리포트에 포함할 상세 포트폴리오 (박스 형식 + 전일 대비)."""
        if not portfolio:
            return None

        if scores_map is None:
            scores_map = {}
        if stoploss_map is None:
            stoploss_map = {}
        if previous_prices is None:
            previous_prices = {}

        lines: list[str] = [f"💼 내 포트폴리오 ({len(portfolio)}종목)", ""]

        total_invested = 0
        total_current = 0
        total_prev_value = 0
        warning_lines: list[str] = []

        for p in portfolio:
            code = p["stock_code"]
            name = p.get("stock_name") or ""
            if not name and self._db is not None:
                name = self._db.get_stock_name(code)
            display = self._format_name_code(name, code)
            avg_price = p["avg_buy_price"]
            qty = p["total_quantity"]
            invested = p["total_invested"]

            score = scores_map.get(code, {})
            current_price = score.get("current_price", avg_price)
            if current_price <= 0:
                current_price = avg_price
            signal_label = score.get("signal_label", "")
            total_score = score.get("total_score", 0)

            prev_price = previous_prices.get(code, 0)

            current_value = current_price * qty
            pnl = current_value - invested
            pnl_pct = (pnl / invested * 100) if invested > 0 else 0
            pnl_sign = "+" if pnl >= 0 else ""
            pnl_emoji = "📈" if pnl >= 0 else "📉"

            if prev_price > 0:
                daily_change = current_price - prev_price
                daily_pct = (daily_change / prev_price * 100)
                daily_sign = "+" if daily_change >= 0 else ""
                daily_emoji = "📈" if daily_change >= 0 else "📉"
                total_prev_value += prev_price * qty
            else:
                daily_change = 0
                daily_pct = 0.0
                daily_sign = ""
                daily_emoji = ""
                # 전일 데이터가 없으면 합계 변화에서 제외(현재가로 처리)
                total_prev_value += current_value

            total_invested += invested
            total_current += current_value

            lines.append(f"┌ {display}")

            if p.get("buy_count", 1) > 1:
                lines.append(f"│ 매수 {p['buy_count']}회 (평단: {avg_price:,}원)")
            else:
                lines.append(
                    f"│ 매수: {avg_price:,}원 × {qty}주 = {invested:,}원"
                )

            lines.append(
                f"│ 현재: {current_price:,}원 × {qty}주 = {current_value:,}원"
            )

            if prev_price > 0 and daily_change != 0:
                lines.append(
                    f"│ 전일대비: {daily_sign}{daily_change:,}원 "
                    f"({daily_sign}{daily_pct:.2f}%) {daily_emoji}"
                )

            lines.append(
                f"│ 손익: {pnl_sign}{pnl:,}원 "
                f"({pnl_sign}{pnl_pct:.1f}%) {pnl_emoji}"
            )

            if signal_label:
                lines.append(f"│ 신호: {signal_label} ({total_score}점)")

            sl = stoploss_map.get(code, {})
            sl_price = sl.get("effective_stoploss", 0)
            if sl_price > 0:
                sl_pct = sl.get("effective_stoploss_pct", 0)
                lines.append(f"│ 손절: {sl_price:,}원 ({sl_pct}%)")
                if current_price <= sl_price * 1.02:
                    warning_lines.append(f"⚠️ {display}: 손절라인 접근 주의")

            if score.get("signal") == "sell":
                warning_lines.append(f"🔴 {display}: 매도 신호 ({total_score}점)")

            lines.append("└──────────────")
            lines.append("")

        total_pnl = total_current - total_invested
        total_pnl_pct = (
            (total_pnl / total_invested * 100) if total_invested > 0 else 0
        )
        total_pnl_sign = "+" if total_pnl >= 0 else ""
        total_pnl_emoji = "📈" if total_pnl >= 0 else "📉"

        total_daily_change = total_current - total_prev_value
        total_daily_pct = (
            (total_daily_change / total_prev_value * 100) if total_prev_value > 0 else 0
        )
        total_daily_sign = "+" if total_daily_change >= 0 else ""
        total_daily_emoji = "📈" if total_daily_change >= 0 else "📉"

        lines.append("📊 합계")
        lines.append(f"   투자: {total_invested:,}원")
        lines.append(f"   평가: {total_current:,}원")
        lines.append(
            f"   손익: {total_pnl_sign}{total_pnl:,}원 "
            f"({total_pnl_sign}{total_pnl_pct:.1f}%) {total_pnl_emoji}"
        )

        if total_prev_value > 0 and total_daily_change != 0:
            lines.append(
                f"   전일대비: {total_daily_sign}{total_daily_change:,}원 "
                f"({total_daily_sign}{total_daily_pct:.2f}%) {total_daily_emoji}"
            )

        if warning_lines:
            lines.append("")
            lines.extend(warning_lines)

        return "\n".join(lines)

    # ================================================================
    # 에러 알림
    # ================================================================
    def format_error_message(self, error: str, module: str = "") -> str:
        """에러 알림 메시지를 생성한다."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            "🚨 KOSPI 분석 시스템 에러",
            f"시간: {now}",
        ]
        if module:
            lines.append(f"모듈: {module}")
        lines.append(f"에러: {error}")
        lines.append("")
        lines.append("시스템을 확인해주세요.")
        return "\n".join(lines)

    # ================================================================
    # 신호 변경 알림
    # ================================================================
    def format_signal_changes(
        self, changes: list[dict[str, Any]]
    ) -> Optional[str]:
        """신호 변경 알림을 포맷팅한다."""
        if not changes:
            return None

        lines = ["🔄 신호 변경 알림", ""]

        for c in changes:
            name = c.get("stock_name", "")
            prev = c.get("prev_signal_label", "")
            new = c.get("new_signal_label", "")
            score_chg = c.get("score_change", 0)
            sign = "+" if score_chg >= 0 else ""

            lines.append(f"  {name}: {prev} → {new} ({sign}{score_chg}점)")

        return "\n".join(lines)

    # ================================================================
    # 성과 리포트
    # ================================================================
    def format_performance_report(
        self,
        report: dict[str, Any],
        period: str = "monthly",
    ) -> list[str]:
        """성과 리포트를 포맷팅한다."""
        if not report.get("has_data"):
            return [f"📊 {report.get('period_label', '')} 성과 리포트\n\n"
                    "아직 성과 데이터가 없습니다.\n"
                    "분석 시작 후 최소 1주일 이상 경과해야 합니다."]

        label = report.get("period_label", "")
        now = datetime.now()
        if period == "monthly":
            title = f"{now.year}년 {now.month}월"
        elif period == "quarterly":
            q = (now.month - 1) // 3 + 1
            title = f"{now.year}년 {q}분기"
        elif period == "half_yearly":
            h = "상반기" if now.month <= 6 else "하반기"
            title = f"{now.year}년 {h}"
        else:
            title = f"{now.year}년"

        lines: list[str] = []
        lines.append(f"📊 {label} 성과 리포트 ({title})")
        lines.append("")

        # 기본 통계
        lines.append(f"분석 종목: 총 {report['total_stocks']}종목 "
                      f"({report['total_days']}거래일)")

        # 신호 적중률
        sa = report.get("signal_accuracy", {})
        if sa.get("buy_total", 0) > 0 or sa.get("sell_total", 0) > 0:
            parts = []
            if sa["buy_total"] > 0:
                parts.append(f"매수 {sa['buy_accuracy']}%")
            if sa["sell_total"] > 0:
                parts.append(f"매도 {sa['sell_accuracy']}%")
            lines.append(f"신호 적중률: {' | '.join(parts)}")

        # 평균 수익률
        ar = report.get("avg_return", {})
        if ar.get("buy_count", 0) > 0:
            sign = "+" if ar["buy_avg"] >= 0 else ""
            lines.append(f"평균 수익률: {sign}{ar['buy_avg']}% "
                          f"(매수 추천 {ar['buy_count']}종목)")
        lines.append("")

        # TOP 5
        top5 = report.get("top5", [])
        if top5:
            lines.append("🏆 TOP 5")
            for i, t in enumerate(top5):
                sign = "+" if t["return_pct"] >= 0 else ""
                lines.append(
                    f"  {i+1}. {t['stock_name']} {sign}{t['return_pct']}% "
                    f"(추천시 {t['score']}점)"
                )
            lines.append("")

        # WORST 5
        worst5 = report.get("worst5", [])
        if worst5:
            lines.append("💀 WORST 5")
            for i, w in enumerate(worst5):
                lines.append(
                    f"  {i+1}. {w['stock_name']} {w['return_pct']}% "
                    f"(추천시 {w['score']}점)"
                )
            lines.append("")

        # 점수별 수익률
        brackets = report.get("score_brackets", [])
        if any(b["count"] > 0 for b in brackets):
            lines.append("점수별 수익률:")
            for b in brackets:
                if b["count"] > 0:
                    sign = "+" if b["avg_return"] >= 0 else ""
                    lines.append(
                        f"  {b['label']}: 평균 {sign}{b['avg_return']}% "
                        f"({b['count']}종목)"
                    )
            lines.append("")

        # 적정주가 상관관계
        fv = report.get("fair_value_correlation", {})
        if fv.get("sample_size", 0) >= 3:
            lines.append(f"적정주가 괴리율 상관: {fv['correlation']}")
            lines.append(f"  → {fv['insight']}")
            lines.append("")

        lines.append("⚠️ 과거 성과가 미래 수익을 보장하지 않습니다.")

        return self._split_messages(lines)

    # ================================================================
    # 유틸리티
    # ================================================================
    def _split_messages(self, lines: list[str]) -> list[str]:
        """메시지를 최대 길이에 맞게 분할한다."""
        messages: list[str] = []
        current: list[str] = []
        current_len = 0

        for line in lines:
            line_len = len(line) + 1  # 줄바꿈 포함
            if current_len + line_len > self.max_length and current:
                messages.append("\n".join(current))
                current = []
                current_len = 0

            current.append(line)
            current_len += line_len

        if current:
            messages.append("\n".join(current))

        return messages

    @staticmethod
    def _format_name_code(name: str, code: str) -> str:
        """종목명과 코드를 '{name} ({code})' 형식으로 포맷팅한다.

        name이 비어있거나 code와 동일하면 code만 반환한다.
        """
        if name and name != code:
            return f"{name} ({code})"
        return code

    @staticmethod
    def _format_market_cap(market_cap: int) -> str:
        """시가총액을 읽기 쉬운 형태로 변환한다."""
        if market_cap >= 1_000_000_000_000:
            return f"{market_cap / 1_000_000_000_000:,.1f}조원"
        elif market_cap >= 100_000_000:
            return f"{market_cap / 100_000_000:,.0f}억원"
        else:
            return f"{market_cap:,}원"


# ================================================================
# A1 Phase 3: 공시 영향 변화 섹션
# ================================================================
# import는 모듈 상단 의존을 가벼이 유지하기 위해 함수 내부로 미룬다.
# 단위 테스트는 helper를 직접 호출하므로 dataclass 결합만 발생.

_SIGNAL_KOREAN = {
    "strong_buy": "🟢 강매수",
    "buy": "🟡 매수",
    "hold": "⭐ 보유",
    "sell": "🔴 매도",
}

# 길이 폭주 방지 한도 — 카테고리당 최대 표시 항목 수.
_DISCLOSURE_MAX_ITEMS_PER_GROUP = 5


def _signal_korean(signal: str) -> str:
    return _SIGNAL_KOREAN.get((signal or "").lower(), signal or "—")


def _format_impact_significant(impact: Any) -> str:
    """5점 이상 변동 또는 신호 변경된 종목의 상세 표시."""
    diff = int(impact.total_diff)
    diff_sign = "+" if diff > 0 else ""
    icon = "🚀" if diff > 0 else ("⚠️" if diff < 0 else "📋")
    name = (impact.disclosure.corp_name or "").strip()
    code = impact.stock_code

    out = [
        f"{icon} {name} ({code})",
        f"   공시: {impact.disclosure.report_nm}",
    ]
    before = impact.before.total_score if impact.before else 0
    after = impact.after.total_score if impact.after else 0
    out.append(f"   점수: {before} → {after} ({diff_sign}{diff})")

    if impact.signal_changed and impact.before and impact.after:
        out.append(
            f"   신호: {_signal_korean(impact.before.signal)} → "
            f"{_signal_korean(impact.after.signal)}"
        )

    # 카테고리별 |3+| 변화만 표시 (작은 변동은 노이즈)
    parts: list[str] = []
    if abs(impact.value_diff) >= 3:
        parts.append(f"가치 {impact.value_diff:+d}")
    if abs(impact.financial_diff) >= 3:
        parts.append(f"재무 {impact.financial_diff:+d}")
    if abs(impact.growth_diff) >= 3:
        parts.append(f"성장 {impact.growth_diff:+d}")
    if abs(impact.quality_diff) >= 3:
        parts.append(f"퀄리티 {impact.quality_diff:+d}")
    if parts:
        out.append("   변화: " + ", ".join(parts))
    return "\n".join(out)


def _group_info_disclosures(impacts: list[Any]) -> dict[str, list[Any]]:
    """정보성 공시(점수 영향 0)를 BUYBACK/DIVIDEND/MA/기타로 그룹화."""
    from collectors.dart_disclosure import DisclosureType, classify_disclosure

    groups: dict[str, list[Any]] = {
        "자사주": [], "배당": [], "M&A": [], "기타": [],
    }
    for imp in impacts:
        dtype = classify_disclosure(imp.disclosure)
        if dtype == DisclosureType.BUYBACK:
            groups["자사주"].append(imp)
        elif dtype == DisclosureType.DIVIDEND:
            groups["배당"].append(imp)
        elif dtype == DisclosureType.MA:
            groups["M&A"].append(imp)
        else:
            groups["기타"].append(imp)
    return {k: v for k, v in groups.items() if v}


def format_disclosure_section(impacts: list[Any]) -> str:
    """공시 영향 변화 섹션 본문 (헤더 포함). 빈 리스트면 빈 문자열.

    표시 정책:
      - 헤더 1줄
      - significant (|총점|≥5 또는 signal 변경): 상세 다중 줄
      - minor (점수 변화 있으나 not significant): 최대 5건 한 줄씩
      - info-only (점수 변화 0): 자사주/배당/M&A/기타 그룹 + 그룹당 최대 5건

    호출자는 일일 리포트 lines에 추가하기만 하면 _split_messages가
    4096자 분할을 처리한다.
    """
    if not impacts:
        return ""

    significant = [i for i in impacts if i.is_significant]
    minor = [i for i in impacts
             if (not i.is_significant) and i.total_diff != 0]
    info_only = [i for i in impacts
                 if (not i.is_significant) and i.total_diff == 0]

    out: list[str] = ["📋 공시 영향 변화 (어제)"]
    out.append("")

    if significant:
        # is_significant 종목들 — process_disclosures가 이미 정렬했지만
        # 여기서도 재안전하게 |total_diff| 큰 순으로.
        for imp in sorted(significant,
                          key=lambda i: (-int(bool(i.signal_changed)),
                                          -abs(i.total_diff))):
            out.append(_format_impact_significant(imp))
            out.append("")

    if minor:
        out.append(f"📊 작은 변화 ({len(minor)}건)")
        for imp in minor[:_DISCLOSURE_MAX_ITEMS_PER_GROUP]:
            d = int(imp.total_diff)
            sign = "+" if d > 0 else ""
            before = imp.before.total_score if imp.before else 0
            after = imp.after.total_score if imp.after else 0
            name = (imp.disclosure.corp_name or "").strip()
            out.append(
                f"   - {imp.stock_code} {name}: "
                f"{before} → {after} ({sign}{d})"
            )
        if len(minor) > _DISCLOSURE_MAX_ITEMS_PER_GROUP:
            out.append(
                f"   ... 외 {len(minor) - _DISCLOSURE_MAX_ITEMS_PER_GROUP}건"
            )
        out.append("")

    if info_only:
        for category, items in _group_info_disclosures(info_only).items():
            out.append(f"📌 {category} ({len(items)}건)")
            for imp in items[:_DISCLOSURE_MAX_ITEMS_PER_GROUP]:
                name = (imp.disclosure.corp_name or "").strip()
                out.append(f"   - {name} ({imp.stock_code})")
            if len(items) > _DISCLOSURE_MAX_ITEMS_PER_GROUP:
                out.append(
                    f"   ... 외 {len(items) - _DISCLOSURE_MAX_ITEMS_PER_GROUP}건"
                )
            out.append("")

    return "\n".join(out).rstrip()


# ================================================================
# 테스트
# ================================================================
if __name__ == "__main__":
    fmt = MessageFormatter()

    test_top10 = [
        {"stock_code": "005930", "stock_name": "삼성전자", "total_score": 87,
         "signal_label": "🟢 강력매수", "per": 8.2, "pbr": 0.9, "roe": 12.5,
         "current_price": 72300, "reason": "반도체 업황 회복 + 저PER"},
        {"stock_code": "005380", "stock_name": "현대차", "total_score": 82,
         "signal_label": "🟢 강력매수", "per": 5.1, "pbr": 0.6, "roe": 14.0,
         "current_price": 245000, "reason": "EV 성장 + 글로벌 확장"},
    ]

    stoploss = {
        "005930": {"effective_stoploss": 68500, "effective_stoploss_pct": -5.3, "warnings": []},
        "005380": {"effective_stoploss": 231000, "effective_stoploss_pct": -5.7, "warnings": []},
    }

    messages = fmt.format_daily_report(
        top_10=test_top10, warnings=[], stats={"total_analyzed": 800, "after_filter": 650},
        stoploss_map=stoploss, kospi_index=2680.5, kospi_change=0.8, foreign_net_buy=1200,
    )

    for i, msg in enumerate(messages):
        print(f"=== 메시지 {i+1} ({len(msg)}자) ===")
        print(msg)
