"""
KOSPI 저평가 기업 분석 시스템 - 메인 실행 모듈

설계서 섹션 8의 스케줄러 및 전체 파이프라인을 구현한다:
  15:35 토큰 확인 → 15:40 데이터 수집 → 15:55 분석 →
  16:00 리포트 생성 → 16:05 텔레그램 발송 → 16:10 DB 저장

APScheduler 기반으로 매일 자동 실행.
휴장일/주말에는 실행하지 않는다.
"""

import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from analysis.scorer import ScoringEngine
from analysis.signals import SignalGenerator
from analysis.stoploss import StopLossCalculator
from bot.telegram_bot import KOSPIBot
from collectors.dart_api import DARTClient
from collectors.kis_api import KISClient, KISAPIError
from config.settings import (
    KISConfig,
    LogConfig,
    SchedulerConfig,
    SignalConfig,
)
from database.history import AnalysisHistory
from database.models import Database

logger = logging.getLogger(__name__)


# ================================================================
# 로깅 설정
# ================================================================
def setup_logging() -> None:
    """로깅을 설정한다."""
    log_dir = Path(LogConfig.LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)

    # 파일 핸들러
    from logging.handlers import RotatingFileHandler

    file_handler = RotatingFileHandler(
        log_dir / "kospi_analyzer.log",
        maxBytes=LogConfig.MAX_FILE_SIZE_MB * 1024 * 1024,
        backupCount=LogConfig.BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(
        logging.Formatter(LogConfig.FORMAT, datefmt=LogConfig.DATE_FORMAT)
    )

    # 콘솔 핸들러
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(
        logging.Formatter(LogConfig.FORMAT, datefmt=LogConfig.DATE_FORMAT)
    )

    # 루트 로거 설정
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, LogConfig.LEVEL, logging.INFO))
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    # 외부 라이브러리 로그 레벨 조정
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


# ================================================================
# 휴장일 체크
# ================================================================
def is_trading_day() -> bool:
    """오늘이 거래일인지 확인한다.

    1) 주말(토/일)이면 즉시 False
    2) 평일이면 KIS API로 삼성전자(005930) 현재가를 조회하여
       데이터가 있으면 거래일, 없으면 휴장일로 판단
    3) API 오류/토큰 실패 시에는 안전하게 True 리턴 (실행 시도)
    """
    today = datetime.now()

    # 주말 체크
    if today.weekday() >= 5:  # 5=토, 6=일
        logger.info("주말 - 휴장일")
        return False

    # 평일: KIS API로 거래일 여부 확인
    try:
        kis = KISClient()
        if not kis.check_token():
            logger.warning("KIS 토큰 발급 실패 - 안전하게 거래일로 간주")
            return True

        price = kis.get_stock_price("005930")
        current_price = price.get("current_price", 0)
        volume = price.get("volume", 0)

        if current_price > 0 and volume > 0:
            logger.info("거래일 확인 (삼성전자: %s원, 거래량: %s)", f"{current_price:,}", f"{volume:,}")
            return True
        else:
            logger.info("휴장일 판단 (삼성전자 시세 없음)")
            return False

    except Exception as e:
        logger.warning("거래일 확인 실패 (%s) - 안전하게 거래일로 간주", e)
        return True


# ================================================================
# 분석 파이프라인
# ================================================================
class AnalysisPipeline:
    """전체 분석 파이프라인.

    데이터 수집 → 분석 → 신호 생성 → 리포트 → 발송 → 저장
    """

    def __init__(self) -> None:
        self.kis = KISClient()
        self.dart = DARTClient()
        self.scorer = ScoringEngine()
        self.signal_gen = SignalGenerator()
        self.stoploss_calc = StopLossCalculator()
        self.db = Database()
        self.history = AnalysisHistory(self.db)
        self.bot = KOSPIBot(self.db)
        # 턴어라운드 점수가 historical 영업이익 조회를 할 수 있도록 DB 주입
        self.scorer.set_db(self.db)

    def _get_top_stock_codes(self, n: int = 50) -> list[str]:
        """DB에서 최근 분석 결과의 상위 N개 종목코드를 가져온다."""
        try:
            conn = self.db._get_conn()
            cursor = conn.execute(
                """
                SELECT stock_code FROM stock_scores
                WHERE analysis_date = (
                    SELECT MAX(analysis_date) FROM stock_scores
                )
                ORDER BY total_score DESC
                LIMIT ?
                """,
                (n,),
            )
            return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            logger.warning("TOP %d 종목 조회 실패: %s", n, e)
            return []

    def _determine_target_codes(self) -> Optional[list[str]]:
        """요일에 따라 분석 대상 종목을 결정한다.

        월요일(weekday=0): None (전종목 스캔)
        화~금(weekday=1~4): TOP 50 + 포트폴리오 종목
        """
        weekday = datetime.now().weekday()

        if weekday == 0:
            logger.info("📊 월요일 전종목 스캔 모드")
            return None

        # 화~금: TOP 50 + 포트폴리오
        top_codes = self._get_top_stock_codes(50)
        portfolio = self.db.get_portfolio()
        portfolio_codes = {p["stock_code"] for p in portfolio} if portfolio else set()
        combined = list(set(top_codes) | portfolio_codes)

        if not combined:
            logger.info("이전 분석 결과 없음 → 전종목 스캔으로 전환")
            return None

        day_names = {1: "화", 2: "수", 3: "목", 4: "금"}
        day_name = day_names.get(weekday, str(weekday))
        logger.info(
            "📋 %s요일 TOP 50 + 포트폴리오 업데이트 모드 "
            "(TOP %d + 포트폴리오 %d = 합산 %d종목)",
            day_name, len(top_codes), len(portfolio_codes), len(combined),
        )
        return combined

    async def run(self) -> bool:
        """전체 파이프라인을 실행한다.

        Returns:
            bool: 성공 여부
        """
        analysis_date = datetime.now().strftime("%Y-%m-%d")
        logger.info("="*50)
        logger.info("분석 파이프라인 시작: %s", analysis_date)
        logger.info("="*50)

        try:
            # 1. 토큰 확인 (async 컨텍스트이므로 acheck_token 사용)
            logger.info("[1/6] KIS API 토큰 확인...")
            if not await self.kis.acheck_token():
                raise KISAPIError("KIS API 토큰 발급 실패")
            logger.info("[1/6] 토큰 확인 완료 ✅")

            # 1-1. 동적 업종 평균 로드 (월요일 전종목 스캔에서 DB에 저장된 값)
            #      _calc_fair_value/_score_sector_per에서 settings.py 고정값보다 우선 사용
            sector_avgs = self.db.get_sector_averages()
            if sector_avgs:
                self.scorer.set_sector_averages(sector_avgs)
                logger.info("동적 업종 평균 로드: %d개 업종", len(sector_avgs))

            # 2. 데이터 수집 (요일별 모드 분기)
            target_codes = self._determine_target_codes()
            logger.info("[2/6] 데이터 수집 시작...")
            price_list, chart_dict, financial_list = await self._collect_data(
                target_codes=target_codes,
            )
            logger.info(
                "[2/6] 데이터 수집 완료 ✅ (종목: %d, 차트: %d, 재무: %d)",
                len(price_list), len(chart_dict), len(financial_list)
            )

            if not price_list:
                raise RuntimeError("시세 데이터 수집 실패: 0건")

            # 3. 분석 실행
            logger.info("[3/6] 종합 스코어링 실행...")
            scored_list = self.scorer.score_all_stocks(
                price_list, financial_list, chart_dict
            )
            logger.info("[3/6] 스코어링 완료 ✅ (%d 종목)", len(scored_list))

            # 3-1. 월요일 전종목 스캔: 업종 평균 동적 계산 + DB 저장
            #      target_codes is None == 전종목 스캔 모드
            if target_codes is None:
                try:
                    new_sector_avgs = self.scorer.calculate_sector_averages(
                        scored_list)
                    if new_sector_avgs:
                        self.db.save_sector_averages(new_sector_avgs)
                        # 다음 단계 스코어링부터 즉시 반영되도록 주입
                        self.scorer.set_sector_averages(new_sector_avgs)
                        logger.info(
                            "업종 평균 동적 계산 완료: %d개 업종",
                            len(new_sector_avgs),
                        )
                except Exception as e:
                    logger.warning("업종 평균 계산/저장 실패: %s", e)

            # 4. 손절 라인 계산
            logger.info("[4/6] 손절 라인 계산...")
            stoploss_map = self.stoploss_calc.calculate_all_stoploss(
                price_list, chart_dict
            )

            # 손절 도달 여부 체크
            stoploss_hit_map: dict[str, bool] = {}
            for code, sl in stoploss_map.items():
                price = next(
                    (p["current_price"] for p in price_list if p["stock_code"] == code),
                    0,
                )
                stoploss_hit_map[code] = self.stoploss_calc.check_stoploss_hit(
                    price, sl.get("effective_stoploss", 0)
                )
            logger.info("[4/6] 손절 라인 계산 완료 ✅")

            # 5. 신호 생성 + TOP 10
            logger.info("[5/6] 신호 생성 + TOP 10 선별...")
            result = self.signal_gen.generate_signals(
                scored_list=scored_list,
                financial_list=financial_list,
                stoploss_map=stoploss_hit_map,
            )

            top_10 = result["top_10"]
            warnings = result["warnings"]
            all_signals = result["all_signals"]
            stats = result["stats"]
            logger.info(
                "[5/6] TOP 10 선별 완료 ✅ (경고: %d건)", len(warnings)
            )

            # 6. 신호 변경 감지
            signal_changes = self.history.detect_signal_changes(all_signals)

            # 전일 TOP 10 조회 (순위 변동 추적용)
            prev_result = self.db.get_latest_result()
            prev_top_10 = prev_result.get("top_10", []) if prev_result else []

            # 7. DB 저장
            logger.info("[6/6] 결과 저장 + 텔레그램 발송...")
            self.history.save_daily_result(
                analysis_date=analysis_date,
                top_10=top_10,
                warnings=warnings,
                all_signals=all_signals,
                stats=stats,
                stoploss_map=stoploss_map,
            )

            # 8. 포트폴리오 종목 스코어 매핑
            portfolio = self.db.get_portfolio()
            portfolio_scores_map: dict[str, dict[str, Any]] = {}
            if portfolio:
                scored_map = {s["stock_code"]: s for s in scored_list}
                fin_map = {f["stock_code"]: f for f in financial_list}

                # scored_list에 없는 종목만 추출해 병렬 조회
                missing_codes = [
                    p["stock_code"] for p in portfolio
                    if p["stock_code"] not in scored_map
                ]

                fetched: dict[str, tuple[Any, Any]] = {}
                if missing_codes:
                    async with self.kis:
                        price_task = asyncio.gather(
                            *[self.kis.aget_stock_price(c)
                              for c in missing_codes],
                            return_exceptions=True,
                        )
                        chart_task = asyncio.gather(
                            *[self.kis.aget_daily_chart(c, days=60)
                              for c in missing_codes],
                            return_exceptions=True,
                        )
                        prices, charts = await asyncio.gather(
                            price_task, chart_task)
                    for code, price_res, chart_res in zip(
                        missing_codes, prices, charts,
                    ):
                        fetched[code] = (price_res, chart_res)

                for p in portfolio:
                    code = p["stock_code"]
                    if code in scored_map:
                        portfolio_scores_map[code] = scored_map[code]
                        continue
                    price_res, chart_res = fetched.get(code, (None, None))
                    if (isinstance(price_res, Exception)
                            or isinstance(chart_res, Exception)
                            or price_res is None):
                        err = price_res if isinstance(
                            price_res, Exception) else chart_res
                        logger.warning(
                            "포트폴리오 종목 %s 조회 실패: %s", code, err)
                        continue
                    try:
                        fin = fin_map.get(code, self.scorer._empty_fin(code))
                        score = self.scorer.calculate_score(
                            price_res, fin, chart_res)
                        portfolio_scores_map[code] = score
                        sl = self.stoploss_calc.calculate_stoploss(
                            price_res.get("current_price", 0), chart_res)
                        stoploss_map[code] = sl
                    except Exception as e:
                        logger.warning(
                            "포트폴리오 종목 %s 스코어링 실패: %s", code, e)

            # 9. 텔레그램 발송
            await self.bot.send_daily_report(
                top_10=top_10,
                warnings=warnings,
                stats=stats,
                stoploss_map=stoploss_map,
                prev_top_10=prev_top_10,
                portfolio_scores_map=portfolio_scores_map,
                scored_list=scored_list,
            )

            # 신호 변경 알림
            if signal_changes:
                await self.bot.send_signal_changes(signal_changes)

            # 9. 리포트 로그 저장 (TOP 10 스���샷)
            top_10_with_rank = []
            for idx, stock in enumerate(top_10):
                entry = {**stock, "rank": idx + 1}
                sl = stoploss_map.get(stock.get("stock_code", ""), {})
                entry["stoploss_price"] = sl.get("effective_stoploss", 0)
                top_10_with_rank.append(entry)
            self.db.save_daily_report_log(analysis_date, top_10_with_rank)

            # 10. 과거 추천 종목 성과 추적 업데이트.
            # update_performance_tracking 내부는 sync KIS 래퍼를 호출하는데,
            # 여기가 async 컨텍스트이므로 직접 호출하면 _run_sync가
            # RuntimeError를 던진다 (→ cascade 상장폐지 오탐 회귀의 원인).
            # asyncio.to_thread로 스레드 격리 후 실행한다.
            try:
                updated = await asyncio.to_thread(
                    self.db.update_performance_tracking, self.kis
                )
                if updated > 0:
                    logger.info("성과 추적 %d건 업데이트 완료", updated)
            except Exception as e:
                logger.warning("성과 추적 업데이트 실패: %s", e)

            logger.info("[6/6] 완료 ✅")
            logger.info("="*50)
            logger.info("분석 파이프라인 완료: TOP1 = %s (%d점)",
                        top_10[0]["stock_name"] if top_10 else "N/A",
                        top_10[0]["total_score"] if top_10 else 0)
            logger.info("="*50)
            return True

        except Exception as e:
            logger.error("파이프라인 에러: %s", e, exc_info=True)
            try:
                await self.bot.send_error_alert(str(e), "main_pipeline")
            except Exception:
                pass
            return False

    async def _collect_price_data(
        self,
        target_codes: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        """시세 데이터 수집 + PER/PBR 보강 + 종목명 폴백.

        `async with self.kis:` 컨텍스트 안에서 호출되어야 한다.
        """
        if target_codes is None:
            # === 월요일: 전종목 스캔 ===
            price_list = await self.kis.aget_kospi_stock_list()
            if not price_list:
                return []
            logger.info("코스피 %d 종목 시세 수집 완료", len(price_list))

            # stock_master 갱신 (sync DB, 블로킹 무시 가능)
            try:
                saved = self.db.save_stock_master_batch(price_list)
                if saved > 0:
                    logger.info("stock_master 갱신: %d건", saved)
            except Exception as e:
                logger.warning("stock_master 저장 실패: %s", e)

            # 1차 필터
            before_count = len(price_list)
            price_list = [
                p for p in price_list
                if p.get("market_cap", 0) >= SignalConfig.MIN_MARKET_CAP
                and p.get("trading_value", 0) >= SignalConfig.MIN_TRADING_VALUE
            ]
            filtered_out = before_count - len(price_list)
            if filtered_out > 0:
                logger.info(
                    "1차 필터: %d → %d 종목 "
                    "(시총 %.0f억·거래대금 %.0f억 미달 %d건 제외)",
                    before_count, len(price_list),
                    SignalConfig.MIN_MARKET_CAP / 1e8,
                    SignalConfig.MIN_TRADING_VALUE / 1e8,
                    filtered_out,
                )
            if not price_list:
                logger.warning("1차 필터 후 남은 종목이 없습니다")
                return []

            # PER/PBR/EPS/BPS/sector 보강 — 개별 조회 병렬 실행.
            # aget_all_stock_prices가 아닌 gather를 직접 쓰는 이유:
            # 보강은 "있으면 좋고 없어도 무방"이라 failure threshold 적용 안 함.
            logger.info("PER/PBR 보강 조회 시작 (%d 종목)...", len(price_list))
            tasks = [
                self.kis.aget_stock_price(p["stock_code"]) for p in price_list
            ]
            details = await asyncio.gather(*tasks, return_exceptions=True)
            enriched = 0
            for price, detail in zip(price_list, details):
                if isinstance(detail, Exception):
                    logger.debug(
                        "PER/PBR 보강 실패 %s: %s",
                        price["stock_code"], detail,
                    )
                    continue
                price["per"] = detail.get("per", 0.0)
                price["pbr"] = detail.get("pbr", 0.0)
                price["eps"] = detail.get("eps", 0)
                price["bps"] = detail.get("bps", 0)
                price["sector"] = detail.get("sector", "기타")
                enriched += 1
            logger.info(
                "PER/PBR 보강 조회 %d/%d 완료",
                enriched, len(price_list),
            )
        else:
            # === 화~금: 대상 종목만 개별 시세 조회 ===
            logger.info("대상 %d 종목 개별 시세 조회...", len(target_codes))
            price_list = await self.kis.aget_all_stock_prices(target_codes)
            if not price_list:
                logger.warning("개별 시세 조회 결과 0건")
                return []
            logger.info("개별 시세 %d 종목 수집 완료", len(price_list))

            # 종목명 폴백 (sync DB)
            filled = 0
            missing = 0
            for p in price_list:
                if not p.get("stock_name"):
                    name = self.db.get_stock_name(p["stock_code"])
                    if name:
                        p["stock_name"] = name
                        filled += 1
                    else:
                        missing += 1
            if filled > 0 or missing > 0:
                logger.info(
                    "종목명 stock_master 보강: %d건 채움, %d건 미해결",
                    filled, missing,
                )

        return price_list

    async def _collect_data(
        self,
        target_codes: Optional[list[str]] = None,
    ) -> tuple[
        list[dict[str, Any]],
        dict[str, list[dict[str, Any]]],
        list[dict[str, Any]],
    ]:
        """데이터를 수집한다.

        Args:
            target_codes: 분석 대상 종목코드 리스트.
                None이면 전종목 스캔 (월요일),
                리스트이면 해당 종목만 개별 조회 (화~금).

        Returns:
            tuple: (시세 리스트, 차트 dict, 재무 리스트)
        """
        async with self.kis:
            price_list = await self._collect_price_data(target_codes)
            if not price_list:
                return [], {}, []

            stock_codes = [p["stock_code"] for p in price_list]

            # 일봉 차트 + 수급 데이터 동시 실행 (공용 _limiter가 rate 제어)
            logger.info(
                "일봉 차트 + 수급 데이터 동시 수집 (%d 종목)...",
                len(stock_codes),
            )
            chart_task = self.kis.aget_all_daily_charts(stock_codes, days=60)
            investor_task = self.kis.aget_all_investor_trading(
                stock_codes, days=25)
            chart_dict, investor_dict = await asyncio.gather(
                chart_task, investor_task)

        # 수급 데이터를 price_list에 병합
        for price in price_list:
            code = price["stock_code"]
            inv = investor_dict.get(code, {})
            price["foreign_net_buy_days"] = inv.get("foreign_net_buy_days", 0)
            price["institutional_net_buy_days"] = inv.get("institutional_net_buy_days", 0)
            price["foreign_net_buy_5d"] = inv.get("foreign_net_buy_5d", 0)
            price["foreign_net_buy_20d"] = inv.get("foreign_net_buy_20d", 0)
            price["institutional_net_buy_5d"] = inv.get("institutional_net_buy_5d", 0)
            price["institutional_net_buy_20d"] = inv.get("institutional_net_buy_20d", 0)
            price["foreign_cumulative"] = inv.get("foreign_cumulative", 0)
            price["institutional_cumulative"] = inv.get("institutional_cumulative", 0)
            price["foreign_trend"] = inv.get("foreign_trend", "neutral")
            price["institutional_trend"] = inv.get("institutional_trend", "neutral")

        # DART 재무제표 (DB 캐시 우선, 미스 시 API 호출)
        logger.info("재무 데이터 수집 시작 (%d 종목)...", len(stock_codes))
        year = datetime.now().year - 1
        financial_list: list[dict[str, Any]] = []
        db_hit = 0
        api_codes: list[str] = []

        for code in stock_codes:
            cached = self.db.get_financial_metrics(code, year)
            if cached is not None:
                db_hit += 1
                financial_list.append(cached)
            else:
                api_codes.append(code)

        logger.info(
            "재무 DB 캐시: %d/%d 히트, DART API 조회 대상 %d종목",
            db_hit, len(stock_codes), len(api_codes),
        )

        api_results: list[dict[str, Any]] = []
        if api_codes:
            self.dart.load_corp_codes()
            api_results = self.dart.get_all_financial_metrics(api_codes, year)
            for m in api_results:
                m.setdefault("quarter", "annual")
                financial_list.append(m)
            # save는 sector 주입 후 (아래) 일괄 처리

        # === sector 주입: KIS의 bstp_kor_isnm을 financial_data에 복사 ===
        # DART는 업종 정보를 제공하지 않으므로 KIS price_data["sector"]가 단일 진실 소스.
        price_sector_map = {
            p.get("stock_code", ""): p.get("sector", "기타") for p in price_list
        }
        for fin in financial_list:
            code = fin.get("stock_code", "")
            if code in price_sector_map:
                fin["sector"] = price_sector_map[code]

        # api_results를 (이제 sector가 채워진 상태로) DB에 저장
        if api_results:
            self.db.save_financial_metrics_batch(api_results)
            logger.info("재무 DB 캐시 저장: %d종목", len(api_results))

        # 캐시 히트된 row의 sector 컬럼도 KIS 값으로 사후 갱신
        # (다음 스캔의 SQL 진단/그루핑이 정확하도록)
        sector_updated = self.db.update_financial_sectors(price_sector_map)
        if sector_updated:
            logger.info("financial_metrics sector 갱신: %d건", sector_updated)

        return price_list, chart_dict, financial_list

    def cleanup(self) -> None:
        """리소스를 정리한다."""
        self.db.close()


# ================================================================
# 스케줄러
# ================================================================
async def scheduled_analysis() -> None:
    """스케줄러에서 호출하는 분석 작업.

    월요일: 전종목 스캔 (업종별 분할 조회, ~900종목)
    화~금: TOP 50 + 포트폴리오 종목만 업데이트 (개별 시세 조회)
    """
    # is_trading_day()는 sync KIS 래퍼(check_token, get_stock_price)를 호출한다.
    # 이벤트 루프 안에서 직접 호출하면 _run_sync가 RuntimeError를 던지므로
    # to_thread로 스레드 격리한다.
    if not await asyncio.to_thread(is_trading_day):
        logger.info("오늘은 휴장일입니다. 분석을 건너뜁니다.")
        return

    weekday = datetime.now().weekday()
    mode = "전종목 스캔" if weekday == 0 else "TOP 50 + 포트폴리오 업데이트"
    logger.info("스케줄러 분석 시작 (%s)", mode)

    pipeline = AnalysisPipeline()
    try:
        await pipeline.run()
    finally:
        pipeline.cleanup()


async def scheduled_performance_report() -> None:
    """월간/분기/반기/연간 성과 리포트를 발송한다.

    매월 1일 09:00에 호출되며, 해당 월에 맞는 리포트를 발송한다.
    """
    today = datetime.now()
    month = today.month

    db = Database()
    bot = KOSPIBot(db)

    try:
        # 월간 리포트 (매월)
        logger.info("월간 성과 리포트 발송...")
        await bot.send_performance_report("monthly")

        # 분기 리포트 (1, 4, 7, 10월)
        if month in (1, 4, 7, 10):
            logger.info("분기 성과 리포트 발송...")
            await bot.send_performance_report("quarterly")

        # 반기 리포트 (1, 7월)
        if month in (1, 7):
            logger.info("반기 성과 리포트 발송...")
            await bot.send_performance_report("half_yearly")

        # 연간 리포트 (1월)
        if month == 1:
            logger.info("연간 성과 리포트 발송...")
            await bot.send_performance_report("yearly")
    except Exception as e:
        logger.error("성과 리포트 발송 실패: %s", e)
    finally:
        db.close()


def start_scheduler() -> None:
    """APScheduler를 시작한다.

    설계서 8.1: 매일 15:40 자동 실행
    """
    scheduler = AsyncIOScheduler(timezone=SchedulerConfig.TIMEZONE)

    # 매일 장 마감 후 분석 실행
    scheduler.add_job(
        scheduled_analysis,
        CronTrigger(
            hour=SchedulerConfig.DATA_COLLECT_HOUR,
            minute=SchedulerConfig.DATA_COLLECT_MINUTE,
            day_of_week="mon-fri",
            timezone=SchedulerConfig.TIMEZONE,
        ),
        id="daily_analysis",
        name="일일 KOSPI 분석",
        misfire_grace_time=3600,  # 1시간 이내 지연 허용
    )

    # 매월 1일 09:00 성과 리포트 자동 발송
    scheduler.add_job(
        scheduled_performance_report,
        CronTrigger(
            day=1,
            hour=9,
            minute=0,
            timezone=SchedulerConfig.TIMEZONE,
        ),
        id="monthly_performance",
        name="월간 성과 리포트",
        misfire_grace_time=7200,
    )

    # 일회성: performance_tracking 유효 샘플 30건 도달 시 텔레그램 알림.
    # 플래그 파일 존재하면 잡 내부에서 즉시 종료 (재발송 방지).
    # 알림 수신 후 docs/sample_notifier_cleanup.md 절차로 제거.
    from tools.sample_threshold_notifier import (
        FLAG_PATH as _SAMPLE_FLAG_PATH,
        scheduled_job as _sample_threshold_job,
    )
    if not _SAMPLE_FLAG_PATH.exists():
        scheduler.add_job(
            _sample_threshold_job,
            CronTrigger(
                hour=16, minute=30,
                day_of_week="mon-fri",
                timezone=SchedulerConfig.TIMEZONE,
            ),
            id="sample_threshold_notifier",
            name="샘플 30개 도달 알림 (일회성)",
            misfire_grace_time=3600,
        )
        logger.info(
            "스케줄러 등록: 매일 16:30 샘플 30개 알림 (일회성)")
    else:
        logger.info(
            "샘플 30개 알림 이미 발송됨 → 잡 등록 스킵 (%s)",
            _SAMPLE_FLAG_PATH,
        )

    logger.info(
        "스케줄러 등록: 매일 %02d:%02d (월~금, %s)",
        SchedulerConfig.DATA_COLLECT_HOUR,
        SchedulerConfig.DATA_COLLECT_MINUTE,
        SchedulerConfig.TIMEZONE,
    )
    logger.info("스케줄러 등록: 매월 1일 09:00 성과 리포트")

    scheduler.start()


# ================================================================
# 메인 엔트리포인트
# ================================================================
async def main() -> None:
    """메인 실행 함수.

    사용법:
        python main.py              # 스케줄러 모드 (매일 자동 실행)
        python main.py --now        # 즉시 실행 (한 번)
        python main.py --bot        # 텔레그램 봇 + 스케줄러
    """
    setup_logging()
    logger.info("KOSPI 저평가 기업 분석 시스템 시작")

    # API 키 확인
    if not KISConfig.APP_KEY:
        logger.error("KIS_APP_KEY가 설정되지 않았습니다. config/.env를 확인하세요.")
        sys.exit(1)

    args = sys.argv[1:]

    # 즉시 실행 모드
    if "--now" in args:
        logger.info("즉시 실행 모드")
        pipeline = AnalysisPipeline()
        try:
            success = await pipeline.run()
            sys.exit(0 if success else 1)
        finally:
            pipeline.cleanup()

    # 봇 + 스케줄러 모드는 run_bot()에서 처리 (이벤트 루프 충돌 방지)
    if "--bot" in args:
        return  # run_bot()으로 위임

    # 기본: 스케줄러만 실행
    logger.info("스케줄러 모드 (Ctrl+C로 종료)")
    start_scheduler()

    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        logger.info("시스템 종료")


def run_bot() -> None:
    """텔레그램 봇 + 스케줄러를 실행한다.

    python-telegram-bot의 run_polling()이 자체 이벤트 루프를 관리하므로
    asyncio.run() 밖에서 직접 호출한다.
    스케줄러는 post_init 콜백에서 이벤트 루프가 준비된 후 시작한다.
    """
    setup_logging()
    logger.info("KOSPI 저평가 기업 분석 시스템 시작")
    logger.info("텔레그램 봇 + 스케줄러 모드")

    if not KISConfig.APP_KEY:
        logger.error("KIS_APP_KEY가 설정되지 않았습니다. config/.env를 확인하세요.")
        sys.exit(1)

    db = Database()
    bot = KOSPIBot(db)

    async def on_post_init(application):
        start_scheduler()

    app = bot.build_app(post_init=on_post_init)

    logger.info("텔레그램 봇 시작 (polling)...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--bot" in args:
        run_bot()
    else:
        asyncio.run(main())
