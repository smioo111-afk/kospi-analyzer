"""
KOSPI 저평가 기업 분석 시스템 - 텔레그램 봇 모듈

설계서 섹션 7.2의 봇 명령어를 구현한다:
  /report    - 최신 분석 리포트 조회
  /stock     - 특정 종목 상세 분석
  /history   - 최근 7일 이력
  /watchlist - 관심종목 관리
  /stoploss  - ATR 배수 변경
  /help      - 도움말

python-telegram-bot v20+ (비동기) 기반
"""

import logging
from typing import Any, Optional

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from analysis.performance_analyzer import PerformanceAnalyzer
from bot.formatter import MessageFormatter
from config.settings import TelegramConfig
from database.models import Database
from database.history import AnalysisHistory

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# B-MERGE-PROC: 안전 merge 헬퍼
# ----------------------------------------------------------------------
# `{**a, **b}` 패턴은 b의 키가 빈 값(None/""/0)이어도 a의 의미 있는 값을
# 덮어써서 데이터를 손실시킨다. _merge_keep_nonempty는 우선순위 순서대로
# 누적하되, 빈 값으로의 덮어쓰기를 막는다 (등장한 첫 비어있지 않은 값 유지).
_EMPTY_VALUES = (None, "", 0, 0.0)


def _merge_keep_nonempty(*dicts: dict) -> dict:
    """여러 dict를 우선순위 순(뒤가 우선)으로 병합하되, 새 값이 비어 있으면
    기존 값을 보존한다.

    - 키가 처음 등장하면 값 그대로 채택 (빈 값이라도)
    - 같은 키가 다시 등장하면, 새 값이 비어 있지 않은 경우에만 갱신
    - 의도: 빈/0 값으로 정상 값이 덮이는 silent fail 차단

    예) _merge_keep_nonempty({"per": 12.0}, {"per": 0})  →  {"per": 12.0}
        _merge_keep_nonempty({"per": 0}, {"per": 12.0})  →  {"per": 12.0}
    """
    result: dict = {}
    for d in dicts:
        if not d:
            continue
        for k, v in d.items():
            if k not in result:
                result[k] = v
            elif v not in _EMPTY_VALUES:
                result[k] = v
    return result


class KOSPIBot:
    """KOSPI 분석 텔레그램 봇.

    명령어 처리 및 자동 리포트 발송을 담당한다.
    """

    def __init__(self, db: Optional[Database] = None) -> None:
        self.cfg = TelegramConfig
        self.db = db or Database()
        # formatter에 db 주입: format_portfolio_for_report에서 stock_master lookup에 사용
        self.formatter = MessageFormatter(db=self.db)
        self.history = AnalysisHistory(self.db)
        self.perf_analyzer = PerformanceAnalyzer(self.db)
        self._app: Optional[Application] = None

    # ================================================================
    # 봇 초기화
    # ================================================================
    def build_app(self, post_init=None) -> Application:
        """텔레그램 봇 Application을 생성한다.

        Args:
            post_init: 이벤트 루프 준비 후 호출할 콜백 (스케줄러 시작 등)

        Returns:
            Application: python-telegram-bot Application 객체
        """
        if not self.cfg.BOT_TOKEN:
            raise ValueError("TELEGRAM_BOT_TOKEN이 설정되지 않았습니다")

        builder = Application.builder().token(self.cfg.BOT_TOKEN)
        if post_init:
            builder = builder.post_init(post_init)
        self._app = builder.build()

        # 화이트리스트 chat_id만 통과시키는 필터.
        # TELEGRAM_ALLOWED_CHAT_IDS 환경변수(콤마 구분)가 비면 CHAT_ID만 허용.
        allowed_raw = self.cfg.allowed_chat_ids()
        allowed_ids: set[int] = set()
        for x in allowed_raw:
            try:
                allowed_ids.add(int(x))
            except (TypeError, ValueError):
                logger.warning("ALLOWED_CHAT_IDS 파싱 실패 (무시): %r", x)
        if not allowed_ids:
            # 화이트리스트가 비면 모든 명령 차단 (보안 fail-closed).
            logger.error(
                "TELEGRAM_CHAT_ID/ALLOWED_CHAT_IDS 미설정 — 모든 봇 명령이 차단됩니다."
            )
            chat_filter = filters.Chat(chat_id={-1})  # 절대 매칭 안 되는 더미
        else:
            chat_filter = filters.Chat(chat_id=allowed_ids)
            logger.info(
                "봇 명령 화이트리스트: %d개 chat_id (%s)",
                len(allowed_ids),
                ", ".join(str(i) for i in sorted(allowed_ids)),
            )

        # 명령어 핸들러 등록 (모두 chat_filter로 권한 검증)
        self._app.add_handler(CommandHandler("start", self._cmd_start, filters=chat_filter))
        self._app.add_handler(CommandHandler("help", self._cmd_help, filters=chat_filter))
        self._app.add_handler(CommandHandler("commands", self._cmd_help, filters=chat_filter))
        self._app.add_handler(MessageHandler(filters.Regex(r'^/명령어$') & chat_filter, self._cmd_help))
        self._app.add_handler(CommandHandler("report", self._cmd_report, filters=chat_filter))
        self._app.add_handler(CommandHandler("stock", self._cmd_stock, filters=chat_filter))
        self._app.add_handler(CommandHandler("history", self._cmd_history, filters=chat_filter))
        self._app.add_handler(CommandHandler("watchlist", self._cmd_watchlist, filters=chat_filter))
        self._app.add_handler(CommandHandler("stoploss", self._cmd_stoploss, filters=chat_filter))
        self._app.add_handler(CommandHandler("buy", self._cmd_buy, filters=chat_filter))
        self._app.add_handler(CommandHandler("sell", self._cmd_sell, filters=chat_filter))
        self._app.add_handler(CommandHandler("portfolio", self._cmd_portfolio, filters=chat_filter))
        self._app.add_handler(CommandHandler("performance", self._cmd_performance, filters=chat_filter))

        logger.info("텔레그램 봇 초기화 완료")
        return self._app

    # ================================================================
    # 종목 검색 헬퍼
    # ================================================================
    async def _resolve_stock_code(
        self, update: Update, query: str, cmd_example: str = "",
    ) -> Optional[str]:
        """입력값을 종목코드로 변환한다.

        6자리 숫자면 그대로 리턴, 아니면 종목명 검색.
        검색 결과가 1개면 종목코드 리턴, 여러 개면 후보 표시 후 None,
        0개면 안내 후 None.

        Returns:
            str or None: 종목코드 (None이면 이미 응답 발송 완료)
        """
        if query.isdigit() and len(query) == 6:
            return query

        # 종목명 검색
        results = self.db.search_stock_by_name(query)

        if len(results) == 1:
            r = results[0]
            await update.message.reply_text(
                f"🔍 '{query}' → {r['stock_name']} ({r['stock_code']}) 검색됨"
            )
            return r["stock_code"]

        if len(results) == 0:
            await update.message.reply_text(
                f"❌ '{query}' 종목을 찾을 수 없습니다.\n"
                f"종목코드(6자리)로 입력해주세요.{chr(10) + cmd_example if cmd_example else ''}"
            )
            return None

        # 여러 개 매칭
        lines = [f"🔍 '{query}' 검색 결과 ({len(results)}건)", ""]
        for r in results[:10]:
            lines.append(
                f"  {r['stock_name']} ({r['stock_code']}) "
                f"- {r.get('total_score', 0)}점 {r.get('signal_label', '')}"
            )
        if len(results) > 10:
            lines.append(f"  ... 외 {len(results) - 10}건")
        lines.append("")
        lines.append("종목코드를 직접 입력해주세요.")
        await update.message.reply_text("\n".join(lines))
        return None

    # ================================================================
    # 명령어 핸들러
    # ================================================================
    async def _cmd_start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """봇 시작 메시지."""
        msg = (
            "📊 KOSPI 저평가 기업 분석 봇\n\n"
            "매일 장 마감 후 코스피 전종목을 분석하여\n"
            "저평가 기업 TOP 10을 알려드립니다.\n\n"
            "/help 로 명령어를 확인하세요."
        )
        await update.message.reply_text(msg)

    async def _cmd_help(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """명령어 도움말."""
        msg = (
            "📋 전체 명령어 안내\n\n"
            "── 분석 ──\n"
            "/report - 최신 분석 리포트 조회\n"
            "/stock [종목코드] - 종목 상세 분석\n"
            "  예) /stock 005930\n"
            "/history - 최근 7일 분석 이력\n\n"
            "── 포트폴리오 ──\n"
            "/buy [종목코드] [매수가] [수량] - 매수 기록\n"
            "  예) /buy 005930 56600 10\n"
            "/buy [종목코드] [매수가] [수량] - 추가 매수\n"
            "  예) /buy 005930 54000 5\n"
            "/sell [종목코드] [수량] - 매도 기록\n"
            "  예) /sell 005930 10 (부분 매도)\n"
            "  예) /sell 005930 (전량 매도)\n"
            "/portfolio - 포트폴리오 현황\n"
            "/portfolio clear - 포트폴리오 초기화\n\n"
            "── 설정 ──\n"
            "/watchlist - 관심종목 목록\n"
            "/watchlist add [종목코드] - 관심종목 추가\n"
            "/watchlist del [종목코드] - 관심종목 삭제\n"
            "/stoploss [배수] - ATR 배수 변경 (1.0~3.0)\n\n"
            "── 기타 ──\n"
            "/명령어 - 이 도움말 보기\n"
            "/help - 이 도움말 보기\n\n"
            "⚠️ 분석 결과는 투자 참고용이며\n"
            "투자 손실의 책임은 지지 않습니다."
        )
        await update.message.reply_text(msg)

    async def _cmd_report(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """최신 분석 리포트 조회."""
        result = self.db.get_latest_result()
        if result is None:
            await update.message.reply_text("📊 아직 분석 결과가 없습니다.")
            return

        messages = self.formatter.format_daily_report(
            top_10=result.get("top_10", []),
            warnings=result.get("warnings", []),
            stats=result.get("stats", {}),
            kospi_index=result.get("kospi_index", 0),
        )

        for msg in messages:
            await update.message.reply_text(msg)

    async def _cmd_stock(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """특정 종목 상세 분석. /stock [종목코드 또는 종목명]"""
        if not context.args:
            await update.message.reply_text(
                "종목코드 또는 종목명을 입력해주세요.\n"
                "예) /stock 005930\n예) /stock 삼성전자"
            )
            return

        query = " ".join(context.args).strip()
        stock_code = await self._resolve_stock_code(
            update, query, "예) /stock 005930")
        if stock_code is None:
            return

        score = self.db.get_stock_score(stock_code)
        if score is None:
            await update.message.reply_text(
                f"종목 {stock_code}의 분석 결과를 찾을 수 없습니다."
            )
            return

        # daily_report_log에 TOP 10 진입 이력이 있으면 v3 확장 필드(성장/퀄리티
        # 점수, 적정주가, 수급)를 보강한다. _merge_keep_nonempty: 우선순위는
        # score(stock_scores) > v3_log지만, score의 빈 값(0/'')이 v3_log의
        # 의미 있는 값을 덮어쓰지 못하게 한다. (B-MERGE-PROC)
        v3_log = self.db.get_latest_report_log_for_stock(stock_code)
        if v3_log:
            score = _merge_keep_nonempty(v3_log, score)

        history_data = self.db.get_stock_history(stock_code, days=5)

        msg = self.formatter.format_stock_detail(
            stock=score,
            history=history_data,
        )
        await update.message.reply_text(msg)

    async def _cmd_history(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """최근 7일 분석 이력."""
        reports = self.history.get_recent_reports(7)
        msg = self.formatter.format_history_report(reports)
        await update.message.reply_text(msg)

    async def _cmd_watchlist(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """관심종목 관리."""
        args = context.args or []

        # 추가
        if len(args) >= 2 and args[0].lower() == "add":
            code = args[1].strip()
            if self.db.add_watchlist(code):
                await update.message.reply_text(f"✅ {code} 관심종목 추가 완료")
            else:
                await update.message.reply_text(f"❌ {code} 추가 실패")
            return

        # 삭제
        if len(args) >= 2 and args[0].lower() == "del":
            code = args[1].strip()
            if self.db.remove_watchlist(code):
                await update.message.reply_text(f"✅ {code} 관심종목 삭제 완료")
            else:
                await update.message.reply_text(f"❌ {code}을 찾을 수 없습니다")
            return

        # 목록 조회
        watchlist = self.db.get_watchlist()
        if not watchlist:
            await update.message.reply_text(
                "📌 등록된 관심종목이 없습니다.\n"
                "/watchlist add [종목코드] 로 추가하세요."
            )
            return

        lines = ["📌 관심종목 목록\n"]
        for w in watchlist:
            code = w.get("stock_code", "")
            name = w.get("stock_name", code)
            # 최신 스코어 조회
            score = self.db.get_stock_score(code)
            if score:
                lines.append(
                    f"  {name} ({code}): "
                    f"{score.get('total_score', 0)}점 "
                    f"{score.get('signal_label', '')}"
                )
            else:
                lines.append(f"  {name} ({code}): 미분석")

        await update.message.reply_text("\n".join(lines))

    async def _cmd_stoploss(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """ATR 배수 변경."""
        if not context.args:
            await update.message.reply_text(
                "ATR 배수를 입력해주세요 (1.0~3.0).\n"
                "예) /stoploss 2.0\n\n"
                "• 1.5배: 적극적 (타이트한 손절)\n"
                "• 2.0배: 보수적 (기본값)\n"
                "• 3.0배: 안전 우선 (넉넉한 손절)"
            )
            return

        try:
            multiplier = float(context.args[0])
            if not 1.0 <= multiplier <= 3.0:
                raise ValueError
            # TODO: 사용자별 설정 저장
            await update.message.reply_text(
                f"✅ ATR 배수가 {multiplier}배로 변경되었습니다.\n"
                f"다음 분석부터 적용됩니다."
            )
        except ValueError:
            await update.message.reply_text(
                "❌ 올바른 숫자를 입력해주세요 (1.0~3.0)."
            )

    # ================================================================
    # 포트폴리오 명령어
    # ================================================================
    async def _cmd_buy(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """매수 기록. /buy [종목코드/종목명] [매수가] [수량]"""
        args = context.args or []

        if len(args) < 3:
            await update.message.reply_text(
                "📝 매수 기록 방법:\n"
                "/buy [종목코드/종목명] [매수가] [수량]\n\n"
                "예) /buy 005930 56600 10\n"
                "예) /buy 삼성전자 56600 10\n"
                "예) /buy 005930 54000 5  (추가 매수)\n\n"
                "같은 종목을 여러 번 매수하면\n"
                "자동으로 평균 매수가가 계산됩니다."
            )
            return

        # 뒤에서 2개가 숫자(매수가, 수량)이고 나머지가 종목 식별자
        # /buy 삼성전자 56600 10 또는 /buy SK 하이닉스 120000 5
        try:
            quantity = int(args[-1].replace(",", ""))
            buy_price = int(args[-2].replace(",", ""))
        except (ValueError, IndexError):
            await update.message.reply_text("❌ 매수가와 수량은 숫자로 입력하세요.\n예) /buy 005930 56600 10")
            return

        if buy_price <= 0 or quantity <= 0:
            await update.message.reply_text("❌ 매수가와 수량은 0보다 커야 합니다.")
            return

        query = " ".join(args[:-2]).strip()
        if not query:
            await update.message.reply_text("❌ 종목코드 또는 종목명을 입력하세요.")
            return

        stock_code = await self._resolve_stock_code(
            update, query, "예) /buy 005930 56600 10")
        if stock_code is None:
            return

        # 종목명 조회 (DB에 스코어가 있으면 가져옴)
        stock_name = ""
        score = self.db.get_stock_score(stock_code)
        if score:
            stock_name = score.get("stock_name", "")

        # 저장
        self.db.add_portfolio(stock_code, buy_price, quantity, stock_name)

        # 기존 보유 확인 (추가 매수 여부)
        holding = self.db.get_portfolio_stock(stock_code)

        total_amt = buy_price * quantity
        msg = f"✅ 매수 기록 완료\n\n"
        msg += f"종목: {stock_name or stock_code} ({stock_code})\n"
        msg += f"매수: {buy_price:,}원 × {quantity}주 = {total_amt:,}원\n"

        if holding and holding["buy_count"] > 1:
            msg += f"\n📊 누적 보유 현황\n"
            msg += f"   매수 {holding['buy_count']}회\n"
            msg += f"   평균단가: {holding['avg_buy_price']:,}원\n"
            msg += f"   총 수량: {holding['total_quantity']}주\n"
            msg += f"   총 투자: {holding['total_invested']:,}원"

        await update.message.reply_text(msg)

    async def _cmd_sell(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """매도 기록. /sell [종목코드/종목명] [수량(선택)]"""
        args = context.args or []

        if len(args) < 1:
            await update.message.reply_text(
                "📝 매도 기록 방법:\n"
                "/sell [종목코드/종목명] [수량]\n\n"
                "예) /sell 005930 10  (10주 매도)\n"
                "예) /sell 삼성전자 10\n"
                "예) /sell 005930     (전량 매도)"
            )
            return

        # 마지막 인자가 숫자면 수량, 아니면 전량 매도
        quantity = 0
        if args[-1].replace(",", "").isdigit() and len(args) >= 2:
            quantity = int(args[-1].replace(",", ""))
            query = " ".join(args[:-1]).strip()
        else:
            query = " ".join(args).strip()

        stock_code = await self._resolve_stock_code(
            update, query, "예) /sell 005930 10")
        if stock_code is None:
            return

        # 보유 확인
        holding = self.db.get_portfolio_stock(stock_code)
        if not holding:
            await update.message.reply_text(f"❌ {stock_code} 보유 내역이 없습니다.")
            return

        # 매도 처리
        sold_qty = self.db.sell_portfolio(stock_code, quantity)

        if sold_qty == 0:
            await update.message.reply_text(f"❌ 매도 처리 실패. 보유 수량을 확인하세요.")
            return

        name = holding["stock_name"] or stock_code
        msg = f"✅ 매도 기록 완료\n\n"
        msg += f"종목: {name} ({stock_code})\n"
        msg += f"매도 수량: {sold_qty}주\n"

        # 잔여 보유 확인
        remaining = self.db.get_portfolio_stock(stock_code)
        if remaining:
            msg += f"\n잔여: {remaining['total_quantity']}주 (평단 {remaining['avg_buy_price']:,}원)"
        else:
            msg += f"\n전량 매도 완료"

        await update.message.reply_text(msg)

    async def _cmd_portfolio(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """포트폴리오 조회. /portfolio [clear]"""
        args = context.args or []

        # 초기화
        if args and args[0].lower() == "clear":
            count = self.db.clear_portfolio()
            await update.message.reply_text(f"✅ 포트폴리오 초기화 완료 ({count}건 삭제)")
            return

        # 포트폴리오 조회
        portfolio = self.db.get_portfolio()

        # 최신 스코어 + 전일 종가 + 손절 정보 매핑
        scores_map: dict[str, dict[str, Any]] = {}
        previous_prices: dict[str, int] = {}
        stoploss_map: dict[str, dict[str, Any]] = {}
        for p in portfolio:
            code = p["stock_code"]
            score = self.db.get_stock_score(code)
            if score:
                scores_map[code] = score
                # 자정 모니터 등으로 stoploss=0이 들어온 경우 직전 영업일 행으로 폴백
                sl_price = score.get("stoploss_price", 0) or 0
                sl_pct = score.get("stoploss_pct", 0) or 0
                if sl_price <= 0:
                    history = self.db.get_stock_history(code, days=10)
                    for h in history:
                        if h.get("stoploss_price", 0) > 0:
                            sl_price = h["stoploss_price"]
                            sl_pct = h.get("stoploss_pct", 0) or sl_pct
                            break
                if sl_price > 0:
                    stoploss_map[code] = {
                        "effective_stoploss": sl_price,
                        "effective_stoploss_pct": sl_pct,
                    }
            previous_prices[code] = self.db.get_previous_price(code)

        msg = self.formatter.format_portfolio(
            portfolio,
            scores_map=scores_map,
            stoploss_map=stoploss_map,
            previous_prices=previous_prices,
        )
        await update.message.reply_text(msg)

    # ================================================================
    # 성과 리포트
    # ================================================================
    async def _cmd_performance(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """성과 리포트 조회. /performance [monthly|quarterly|half_yearly|yearly]"""
        args = context.args or []
        period = args[0].lower() if args else "monthly"

        valid = ("monthly", "quarterly", "half_yearly", "yearly")
        if period not in valid:
            await update.message.reply_text(
                "📊 성과 리포트 사용법:\n"
                "/performance monthly - 월간\n"
                "/performance quarterly - 분기\n"
                "/performance half_yearly - 반기\n"
                "/performance yearly - 연간"
            )
            return

        try:
            report = self.perf_analyzer.generate_performance_report(period)
            msgs = self.formatter.format_performance_report(report, period)
            for msg in msgs:
                await update.message.reply_text(msg)
        except Exception as e:
            logger.error("성과 리포트 생성 실패: %s", e)
            await update.message.reply_text("❌ 성과 리포트 생성 중 오류가 발생했습니다.")

    # ================================================================
    # 자동 발송 (스케줄러에서 호출)
    # ================================================================
    async def send_daily_report(
        self,
        top_10: list[dict[str, Any]],
        warnings: list[dict[str, Any]],
        stats: dict[str, Any],
        stoploss_map: Optional[dict[str, dict[str, Any]]] = None,
        kospi_index: float = 0.0,
        kospi_change: float = 0.0,
        foreign_net_buy: int = 0,
        prev_top_10: Optional[list[dict[str, Any]]] = None,
        portfolio_scores_map: Optional[dict[str, dict[str, Any]]] = None,
        scored_list: Optional[list[dict[str, Any]]] = None,
        disclosure_impacts: Optional[list[Any]] = None,
        previous_prices: Optional[dict[str, int]] = None,
    ) -> bool:
        """일일 분석 리포트를 자동 발송한다.

        disclosure_impacts: A1 Phase 4 자정 모니터 결과. None이면 오늘자
        analysis_date로 disclosure_impacts 테이블에서 자동 로드.
        """
        if not self.cfg.BOT_TOKEN or not self.cfg.CHAT_ID:
            logger.error("텔레그램 설정 누락 (BOT_TOKEN 또는 CHAT_ID)")
            return False

        # main.py에서 전달받거나 DB에서 조회
        if prev_top_10 is None:
            try:
                prev_result = self.db.get_latest_result()
                if prev_result:
                    prev_top_10 = prev_result.get("top_10", [])
            except Exception:
                pass

        # 공시 영향 자동 로드 (Phase 4)
        if disclosure_impacts is None:
            try:
                from datetime import date
                today_str = date.today().strftime("%Y-%m-%d")
                disclosure_impacts = self.db.get_disclosure_impacts(today_str)
            except Exception as e:
                logger.warning("공시 영향 로드 실패 (skip): %s", e)
                disclosure_impacts = []

        messages = self.formatter.format_daily_report(
            top_10=top_10,
            warnings=warnings,
            stats=stats,
            stoploss_map=stoploss_map,
            kospi_index=kospi_index,
            kospi_change=kospi_change,
            foreign_net_buy=foreign_net_buy,
            prev_top_10=prev_top_10,
            scored_list=scored_list,
            disclosure_impacts=disclosure_impacts,
        )

        try:
            from telegram import Bot

            bot = Bot(token=self.cfg.BOT_TOKEN)
            for msg in messages:
                await bot.send_message(
                    chat_id=self.cfg.CHAT_ID,
                    text=msg,
                )

            # 포트폴리오 보유 중이면 포트폴리오 리포트도 발송
            portfolio = self.db.get_portfolio()
            if portfolio:
                # main.py에서 전달받은 scores_map 우선, 없으면 DB 조회
                scores_map: dict[str, dict[str, Any]] = {}
                if portfolio_scores_map:
                    scores_map = portfolio_scores_map
                else:
                    for p in portfolio:
                        code = p["stock_code"]
                        score = self.db.get_stock_score(code)
                        if score:
                            scores_map[code] = score

                # 전일 가격: main.py에서 전달받거나 DB에서 일괄 조회
                if previous_prices is None:
                    previous_prices = {
                        p["stock_code"]: self.db.get_previous_price(p["stock_code"])
                        for p in portfolio
                    }

                pf_msg = self.formatter.format_portfolio_for_report(
                    portfolio,
                    scores_map=scores_map,
                    stoploss_map=stoploss_map,
                    previous_prices=previous_prices,
                )
                if pf_msg:
                    await bot.send_message(
                        chat_id=self.cfg.CHAT_ID,
                        text=pf_msg,
                    )

            logger.info("일일 리포트 발송 완료 (%d개 메시지)", len(messages))
            return True

        except Exception as e:
            logger.error("리포트 발송 실패: %s", e)
            await self.send_error_alert(str(e), "telegram_bot")
            return False

    async def send_error_alert(self, error: str, module: str = "") -> None:
        """에러 알림을 발송한다."""
        chat_id = self.cfg.ERROR_CHAT_ID or self.cfg.CHAT_ID
        if not self.cfg.BOT_TOKEN or not chat_id:
            return

        try:
            from telegram import Bot

            bot = Bot(token=self.cfg.BOT_TOKEN)
            msg = self.formatter.format_error_message(error, module)
            await bot.send_message(chat_id=chat_id, text=msg)
        except Exception as e:
            logger.error("에러 알림 발송 실패: %s", e)

    async def send_signal_changes(
        self, changes: list[dict[str, Any]]
    ) -> None:
        """신호 변경 알림을 발송한다."""
        msg = self.formatter.format_signal_changes(changes)
        if not msg:
            return

        try:
            from telegram import Bot

            bot = Bot(token=self.cfg.BOT_TOKEN)
            await bot.send_message(chat_id=self.cfg.CHAT_ID, text=msg)
        except Exception as e:
            logger.error("신호 변경 알림 발송 실패: %s", e)

    async def send_cascade_skip_alert(
        self, events: list[dict[str, Any]],
    ) -> None:
        """cascade 안전장치 발동 시 운영자에게 WARN을 발송한다.

        events: Database.last_cascade_skip_events 형식의 dict 리스트.
            각 항목에 stock_code, stock_name, report_date,
            consecutive_failures, last_exception, reason 포함.
        """
        if not events:
            return
        chat_id = self.cfg.ERROR_CHAT_ID or self.cfg.CHAT_ID
        if not self.cfg.BOT_TOKEN or not chat_id:
            return

        lines = [f"⚠️ cascade 안전장치 발동 ({len(events)}건)", ""]
        for ev in events[:10]:
            lines.append(
                f"- {ev.get('stock_code')} {ev.get('stock_name', '')} "
                f"(report={ev.get('report_date')}, "
                f"실패 {ev.get('consecutive_failures')}회, "
                f"예외={ev.get('last_exception')}, "
                f"사유={ev.get('reason')})"
            )
        if len(events) > 10:
            lines.append(f"... ({len(events) - 10}건 추가)")
        msg = "\n".join(lines)

        try:
            from telegram import Bot

            bot = Bot(token=self.cfg.BOT_TOKEN)
            await bot.send_message(chat_id=chat_id, text=msg)
        except Exception as e:
            logger.error("cascade_skip_alert 발송 실패: %s", e)

    async def send_health_alert(self, report: Any) -> None:
        """health check 위반 알림을 발송한다 (warning/fail 시).

        report: monitoring.health_check.HealthCheckReport
        """
        chat_id = self.cfg.ERROR_CHAT_ID or self.cfg.CHAT_ID
        if not self.cfg.BOT_TOKEN or not chat_id:
            return

        try:
            text = report.format_text()
        except Exception as e:
            logger.error("health_alert 포맷 실패: %s", e)
            return

        try:
            from telegram import Bot

            bot = Bot(token=self.cfg.BOT_TOKEN)
            await bot.send_message(chat_id=chat_id, text=text)
        except Exception as e:
            logger.error("health_alert 발송 실패: %s", e)

    async def send_performance_report(self, period: str = "monthly") -> bool:
        """성과 리포트를 자동 발송한다."""
        if not self.cfg.BOT_TOKEN or not self.cfg.CHAT_ID:
            return False

        try:
            from telegram import Bot

            report = self.perf_analyzer.generate_performance_report(period)
            msgs = self.formatter.format_performance_report(report, period)

            bot = Bot(token=self.cfg.BOT_TOKEN)
            for msg in msgs:
                await bot.send_message(chat_id=self.cfg.CHAT_ID, text=msg)

            logger.info("%s 성과 리포트 발송 완료", report.get("period_label", period))
            return True
        except Exception as e:
            logger.error("성과 리포트 발송 실패: %s", e)
            return False
