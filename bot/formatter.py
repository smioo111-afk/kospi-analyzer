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
    ) -> list[str]:
        """일일 분석 리포트를 생성한다.

        Args:
            prev_top_10: 전일 TOP 10 (순위/점수 변동 추적용)
            scored_list: 전종목 스코어링 결과. 주어지면 종합 TOP 10 다음에
                fair_value_gap 기준 저평가 괴리율 TOP 10 섹션을 추가한다.
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

        # 저평가 괴리율 TOP 10 (scored_list가 주어졌을 때만)
        # 종합 TOP 10과 겹치는 종목도 그대로 노출
        # 필터: (1) 외국인 20일 순매수 ≥ 0, (2) ROE ≥ 3%, (3) 2년 연속 이익감소 제외
        if scored_list:
            undervalued = [
                s for s in scored_list
                if s.get("fair_value_gap", 0) < 0
                and s.get("fair_value_low", 0) > 0
                and s.get("foreign_net_buy_20d", 0) >= 0
                and s.get("roe", 0) >= 3.0
                and s.get("consecutive_op_decline_years", 0) < 2
            ]
            # 괴리율이 큰 (가장 음수인) 순으로 정렬
            undervalued.sort(key=lambda s: s.get("fair_value_gap", 0))
            top_undervalued = undervalued[:10]

            if top_undervalued:
                lines.append(
                    f"💎 저평가 괴리율 TOP {len(top_undervalued)} "
                    f"(외국인 순매수 + ROE 3%↑ + 이익감소 제외)"
                )
                for idx, s in enumerate(top_undervalued):
                    emoji = NUM_EMOJI[idx] if idx < 10 else f"{idx+1}."
                    code = s.get("stock_code", "")
                    name = s.get("stock_name", "")
                    price = s.get("current_price", 0)
                    fair_low = s.get("fair_value_low", 0)
                    fair_high = s.get("fair_value_high", 0)
                    gap = s.get("fair_value_gap", 0)
                    lines.append(
                        f"{emoji} {self._format_name_code(name, code)}"
                    )
                    lines.append(
                        f"   현재가: {price:,}원 | "
                        f"적정주가: {fair_low:,}~{fair_high:,}원 | "
                        f"괴리율: {gap}%"
                    )
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
        """개별 종목 상세 분석 결과를 포맷팅한다.

        텔레그램 /stock 명령어 응답용.

        Args:
            stock: 종목 스코어 데이터
            stoploss: 손절 정보
            history: 최근 스코어 이력

        Returns:
            str: 포맷팅된 메시지
        """
        code = stock.get("stock_code", "")
        name = stock.get("stock_name", "")

        lines = [
            f"📈 {self._format_name_code(name, code)} 상세 분석",
            "",
            f"종합점수: {stock.get('total_score', 0)}/100 | {stock.get('signal_label', '')}",
            "",
            "── 가치투자 ──",
            f"  PER: {stock.get('per', 0)} | PBR: {stock.get('pbr', 0)}",
            f"  배당수익률: {stock.get('dividend_yield', 0)}%",
            f"  가치 점수: {stock.get('value_score', 0)}/40",
            "",
            "── 재무건전성 ──",
            f"  ROE: {stock.get('roe', 0)}%",
            f"  영업이익률: {stock.get('operating_margin', 0)}%",
            f"  부채비율: {stock.get('debt_ratio', 0)}%",
            f"  재무 점수: {stock.get('financial_score', 0)}/35",
            "",
            "── 모멘텀 ──",
            f"  모멘텀 점수: {stock.get('momentum_score', 0)}/25",
            "",
            f"현재가: {stock.get('current_price', 0):,}원",
            f"시가총액: {self._format_market_cap(stock.get('market_cap', 0))}",
        ]

        # 손절 정보
        if stoploss:
            lines.extend([
                "",
                "── 손절 라인 ──",
                f"  ATR({stoploss.get('atr', 0):,.0f}) × {stoploss.get('multiplier', 2.0)}배",
                f"  손절가: {stoploss.get('effective_stoploss', 0):,}원 "
                f"({stoploss.get('effective_stoploss_pct', 0)}%)",
            ])
            warns = stoploss.get("warnings", [])
            if warns:
                for w in warns:
                    lines.append(f"  ⚠️ {w}")

        # 이력
        if history and len(history) > 1:
            lines.extend(["", "── 최근 이력 ──"])
            for h in history[:5]:
                lines.append(
                    f"  {h.get('analysis_date', '')}: "
                    f"{h.get('total_score', 0)}점 {h.get('signal_label', '')}"
                )

        lines.extend([
            "",
            "⚠️ 투자 참고용이며 투자 손실의 책임은 지지 않습니다."
        ])

        return "\n".join(lines)

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
    ) -> str:
        """포트폴리오 현황을 포맷팅한다.

        텔레그램 /portfolio 명령어 및 일일 리포트 하단에 사용.

        Args:
            portfolio: 종목별 포트폴리오 리스트
            scores_map: {종목코드: 최신 스코어 데이터}
            stoploss_map: {종목코드: 손절 정보}
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

        lines = ["💼 내 포트폴리오", ""]

        total_invested = 0
        total_current = 0

        for p in portfolio:
            code = p["stock_code"]
            name = p.get("stock_name") or ""
            avg_price = p["avg_buy_price"]
            qty = p["total_quantity"]
            invested = p["total_invested"]

            # 최신 스코어에서 현재가 가져오기
            score = scores_map.get(code, {})
            current_price = score.get("current_price", 0)
            signal_label = score.get("signal_label", "")
            total_score = score.get("total_score", 0)

            # 현재가가 없으면 매수가 기준
            if current_price <= 0:
                current_price = avg_price

            current_value = current_price * qty
            pnl = current_value - invested
            pnl_pct = (pnl / invested * 100) if invested > 0 else 0
            pnl_emoji = "📈" if pnl >= 0 else "📉"

            total_invested += invested
            total_current += current_value

            lines.append(f"┌ {self._format_name_code(name, code)}")

            # 추가 매수 이력 표시
            if p["buy_count"] > 1:
                lines.append(f"│ 매수 {p['buy_count']}회 (평단: {avg_price:,}원)")
                for lot in p["lots"]:
                    lines.append(
                        f"│   {lot['buy_date']}: {lot['buy_price']:,}원 × {lot['quantity']}주"
                    )
            else:
                lines.append(f"│ 매수: {avg_price:,}원 × {qty}주 = {invested:,}원")

            lines.append(f"│ 현재: {current_price:,}원 × {qty}주 = {current_value:,}원")

            sign = "+" if pnl >= 0 else ""
            lines.append(f"│ 손익: {sign}{pnl:,}원 ({sign}{pnl_pct:.1f}%) {pnl_emoji}")

            if signal_label:
                lines.append(f"│ 신호: {signal_label} ({total_score}점)")

            # 손절라인 경고
            sl = stoploss_map.get(code, {})
            sl_price = sl.get("effective_stoploss", 0)
            if sl_price > 0 and current_price > 0:
                sl_pct = sl.get("effective_stoploss_pct", 0)
                lines.append(f"│ 손절: {sl_price:,}원 ({sl_pct}%)")
                if current_price <= sl_price * 1.02:  # 손절 2% 이내
                    lines.append(f"│ ⚠️ 손절라인 접근 주의!")

            lines.append("└──────────────")
            lines.append("")

        # 합계
        total_pnl = total_current - total_invested
        total_pnl_pct = (total_pnl / total_invested * 100) if total_invested > 0 else 0
        sign = "+" if total_pnl >= 0 else ""

        lines.append(f"📊 합계")
        lines.append(f"   투자: {total_invested:,}원")
        lines.append(f"   평가: {total_current:,}원")
        lines.append(f"   손익: {sign}{total_pnl:,}원 ({sign}{total_pnl_pct:.1f}%)")

        return "\n".join(lines)

    def format_portfolio_for_report(
        self,
        portfolio: list[dict[str, Any]],
        scores_map: Optional[dict[str, dict[str, Any]]] = None,
        stoploss_map: Optional[dict[str, dict[str, Any]]] = None,
    ) -> Optional[str]:
        """일일 리포트에 포함할 간략 포트폴리오를 포맷팅한다."""
        if not portfolio:
            return None

        if scores_map is None:
            scores_map = {}
        if stoploss_map is None:
            stoploss_map = {}

        total_invested = 0
        total_current = 0
        stock_lines: list[str] = []
        warning_lines: list[str] = []

        for p in portfolio:
            code = p["stock_code"]
            name = p.get("stock_name") or ""
            # 종목명이 비어있으면 stock_master 테이블에서 lookup해 보강.
            # (포트폴리오 테이블의 historical row 일부에 stock_name이 누락된 경우 대응)
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

            current_value = current_price * qty
            pnl = current_value - invested
            pnl_pct = (pnl / invested * 100) if invested > 0 else 0
            sign = "+" if pnl >= 0 else ""
            emoji = "📈" if pnl >= 0 else "📉"

            total_invested += invested
            total_current += current_value

            buy_label = f"(평단 {avg_price:,})" if p["buy_count"] > 1 else f"({avg_price:,})"
            stock_lines.append(
                f"   {display} {buy_label} × {qty}주 → {sign}{pnl_pct:.1f}% {emoji} {signal_label}"
            )

            # 손절 접근 경고
            sl = stoploss_map.get(code, {})
            sl_price = sl.get("effective_stoploss", 0)
            if sl_price > 0 and current_price <= sl_price * 1.02:
                warning_lines.append(f"   ⚠️ {display}: 손절라인 접근!")

            # 매도 신호 경고
            if score.get("signal") == "sell":
                warning_lines.append(f"   🔴 {display}: 매도 신호 ({total_score}점)")

        total_pnl = total_current - total_invested
        total_pnl_pct = (total_pnl / total_invested * 100) if total_invested > 0 else 0
        sign = "+" if total_pnl >= 0 else ""

        lines = [
            f"💼 내 포트폴리오 ({len(portfolio)}종목)",
        ]
        lines.extend(stock_lines)
        lines.append(f"   합계: {sign}{total_pnl:,}원 ({sign}{total_pnl_pct:.1f}%)")

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
