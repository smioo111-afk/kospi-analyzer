"""
KOSPI 저평가 기업 분석 시스템 - KIS Open API 연동 모듈 (async v4.0)

한국투자증권 REST API를 통해 다음 데이터를 수집한다:
  - Access Token 발급/갱신 (24시간 유효, 토큰 재발급은 동기)
  - 주식 현재가 조회 (현재가, PER, PBR, 시가총액, 거래량)
  - 주식 일봉 차트 조회 (60일간 OHLCV)
  - 투자자별 매매동향 조회 (외국인/기관 수급)
  - 코스피 전종목 리스트 조회

v4.0 변경:
  - 네트워크 계층 aiohttp 전환
  - aiolimiter로 초당 15콜 토큰 버킷 rate limit
  - 배치 메서드는 asyncio.gather로 병렬 처리
  - 배치 실패율 > 20% 시 KISBatchFailureError raise
  - sync 래퍼는 Phase 2-②에서 추가 예정

환경변수:
  - KIS_RATE_LIMIT_PER_SEC (기본 15, 롤백 시 낮추면 거의 sync 속도)

설계서 섹션 3.1 / docs/async_refactoring_design.md 참조
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import aiohttp
import requests
from aiolimiter import AsyncLimiter

from config.settings import KISConfig

logger = logging.getLogger(__name__)


# ================================================================
# Exceptions
# ================================================================
class KISAPIError(Exception):
    """KIS API 호출 에러."""


class KISBatchFailureError(KISAPIError):
    """배치 호출 실패율이 임계치를 초과한 경우."""


# ================================================================
# Token Manager
# ================================================================
class KISTokenManager:
    """KIS API Access Token 관리.

    토큰 발급, 캐싱, 자동 갱신을 담당한다.
    토큰은 24시간 유효하며, 만료 1시간 전에 자동 갱신한다.

    네트워크 호출은 하루 1회 수준이라 requests 동기 유지.
    async 컨텍스트에서 동시 갱신을 막기 위해 get_token_async에서
    asyncio.Lock으로 직렬화한다.
    """

    def __init__(self) -> None:
        self._access_token: str = ""
        self._token_expired_at: datetime = datetime.min
        self._token_cache_path = Path("token_cache/kis_token.json")
        self._async_lock: Optional[asyncio.Lock] = None

    def get_token(self) -> str:
        """유효한 Access Token을 반환한다 (sync)."""
        if self._is_token_valid():
            return self._access_token
        if self._load_token_from_cache() and self._is_token_valid():
            logger.info("캐시된 토큰 로드 성공")
            return self._access_token
        self._issue_new_token()
        return self._access_token

    async def get_token_async(self) -> str:
        """async 컨텍스트에서 토큰을 안전하게 반환한다.

        동시에 여러 코루틴이 토큰 발급을 요청해도 Lock으로 직렬화되어
        중복 발급이 발생하지 않는다.
        """
        if self._async_lock is None:
            self._async_lock = asyncio.Lock()
        async with self._async_lock:
            if self._is_token_valid():
                return self._access_token
            if self._load_token_from_cache() and self._is_token_valid():
                logger.info("캐시된 토큰 로드 성공")
                return self._access_token
            # 토큰 발급은 requests(sync). 하루 1회 수준이라
            # 이벤트 루프 블로킹 감수. 정 싫으면 asyncio.to_thread로 감싸면 됨.
            self._issue_new_token()
            return self._access_token

    def _is_token_valid(self) -> bool:
        if not self._access_token:
            return False
        buffer = timedelta(seconds=KISConfig.TOKEN_REFRESH_BUFFER)
        return datetime.now() < (self._token_expired_at - buffer)

    def _issue_new_token(self) -> None:
        url = f"{KISConfig.BASE_URL}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey": KISConfig.APP_KEY,
            "appsecret": KISConfig.APP_SECRET,
        }
        logger.info("KIS API Access Token 발급 요청")
        response = requests.post(url, json=body, timeout=10)
        if response.status_code != 200:
            raise KISAPIError(
                f"토큰 발급 실패: HTTP {response.status_code}, {response.text}")
        data = response.json()
        self._access_token = data["access_token"]
        expired_str = data.get("access_token_token_expired", "")
        if expired_str:
            self._token_expired_at = datetime.strptime(
                expired_str, "%Y-%m-%d %H:%M:%S")
        else:
            self._token_expired_at = datetime.now() + timedelta(hours=24)
        self._save_token_to_cache()
        logger.info("토큰 발급 성공 (만료: %s)",
                    self._token_expired_at.strftime("%Y-%m-%d %H:%M"))

    def _save_token_to_cache(self) -> None:
        try:
            self._token_cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_data = {
                "access_token": self._access_token,
                "expired_at": self._token_expired_at.isoformat(),
            }
            self._token_cache_path.write_text(
                json.dumps(cache_data, ensure_ascii=False), encoding="utf-8")
        except OSError as e:
            logger.warning("토큰 캐시 저장 실패: %s", e)

    def _load_token_from_cache(self) -> bool:
        try:
            if not self._token_cache_path.exists():
                return False
            cache_data = json.loads(
                self._token_cache_path.read_text(encoding="utf-8"))
            self._access_token = cache_data["access_token"]
            self._token_expired_at = datetime.fromisoformat(
                cache_data["expired_at"])
            return True
        except (OSError, json.JSONDecodeError, KeyError) as e:
            logger.warning("토큰 캐시 로드 실패: %s", e)
            return False


# ================================================================
# Async KIS Client
# ================================================================
def _default_rate_limit() -> int:
    """기본 rate limit을 결정한다.

    우선순위:
      1. KIS_RATE_LIMIT_PER_SEC 환경변수 (settings.py도 같은 변수 사용)
      2. KISConfig.RATE_LIMIT_PER_SEC
      3. 하드 기본 15
    최소 1로 클램프.
    """
    raw = os.getenv("KIS_RATE_LIMIT_PER_SEC")
    if raw is not None:
        try:
            v = int(raw)
            return max(1, v)
        except ValueError:
            pass
    return max(1, getattr(KISConfig, "RATE_LIMIT_PER_SEC", 15))


def _run_sync(coro):
    """sync 래퍼용: 현재 이벤트 루프 상태에 따라 코루틴을 안전 실행.

    - 실행 중인 루프 없음 → asyncio.run으로 새 루프에서 실행.
    - 이미 실행 중인 루프 있음 → RuntimeError. async 컨텍스트에서는
      sync 래퍼 대신 a-prefixed 메서드를 직접 await해야 한다.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # 코루틴을 닫아 "coroutine was never awaited" 경고 방지
    coro.close()
    raise RuntimeError(
        "이미 실행 중인 이벤트 루프에서 sync 래퍼를 호출할 수 없다. "
        "async 컨텍스트에서는 a-prefixed 메서드를 직접 await하라.")


class KISClient:
    """한국투자증권 Open API 비동기 클라이언트 (v4.0).

    사용 예:
        async with KISClient() as kis:
            prices = await kis.aget_all_stock_prices(codes)

    Rate Limit은 aiolimiter 토큰 버킷으로 엄격 보장.
    배치 메서드는 asyncio.gather + 실패 격리.
    """

    def __init__(
        self,
        rate_limit_per_sec: Optional[int] = None,
        fail_threshold: float = 0.2,
    ) -> None:
        if rate_limit_per_sec is None:
            rate_limit_per_sec = _default_rate_limit()
        self._rate = rate_limit_per_sec
        self._token_manager = KISTokenManager()
        # AsyncLimiter는 생성 시점 이벤트 루프에 바인딩되므로,
        # sync 래퍼가 asyncio.run을 반복 호출하면 "loop 재사용" 경고가 난다.
        # 따라서 __aenter__에서 지연 생성한다.
        self._limiter: Optional[AsyncLimiter] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._fail_threshold = fail_threshold

    # ---------- Lifecycle ----------
    async def __aenter__(self) -> "KISClient":
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10),
            )
        # 현재 이벤트 루프에 묶인 limiter 재생성. 세션 단위로 rate 누적.
        # 세션이 닫혔다 다시 열리면 (= 새 asyncio.run 호출) 카운터도 리셋.
        self._limiter = AsyncLimiter(self._rate, time_period=1.0)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
        self._limiter = None

    # ---------- Internal ----------
    async def _get_headers(self, tr_id: str) -> dict[str, str]:
        token = await self._token_manager.get_token_async()
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": KISConfig.APP_KEY,
            "appsecret": KISConfig.APP_SECRET,
            "tr_id": tr_id,
            "custtype": "P",
        }

    def _require_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._limiter is None:
            raise RuntimeError(
                "KISClient: async with 컨텍스트 밖에서 호출되었다. "
                "`async with KISClient() as kis:` 블록 안에서 호출하거나 "
                "sync 래퍼(get_stock_price 등)를 사용하라.")
        return self._session

    async def _request_get(
        self,
        path: str,
        tr_id: str,
        params: dict[str, str],
        extra_headers: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        """GET 요청. Rate Limit + 재시도 + 지수 백오프."""
        url = f"{KISConfig.BASE_URL}{path}"
        headers = await self._get_headers(tr_id)
        if extra_headers:
            headers.update(extra_headers)
        session = self._require_session()

        last_err: Optional[str] = None
        for attempt in range(1, KISConfig.MAX_RETRIES + 1):
            async with self._limiter:
                try:
                    async with session.get(
                        url, headers=headers, params=params,
                    ) as response:
                        if response.status == 200:
                            data = await response.json()
                            rt_cd = data.get("rt_cd", "")
                            if rt_cd == "0":
                                return data
                            msg = data.get("msg1", "알 수 없는 에러")
                            last_err = f"API rt_cd={rt_cd} msg={msg}"
                            logger.warning(
                                "API 에러 (시도 %d/%d): %s",
                                attempt, KISConfig.MAX_RETRIES, msg)
                        else:
                            last_err = f"HTTP {response.status}"
                            logger.warning(
                                "HTTP %d (시도 %d/%d)",
                                response.status, attempt,
                                KISConfig.MAX_RETRIES)
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    last_err = f"network: {e}"
                    logger.warning(
                        "네트워크 에러 (시도 %d/%d): %s",
                        attempt, KISConfig.MAX_RETRIES, e)

            if attempt < KISConfig.MAX_RETRIES:
                wait = KISConfig.RETRY_BACKOFF_BASE ** attempt
                logger.info("%.1f초 후 재시도...", wait)
                await asyncio.sleep(wait)

        raise KISAPIError(
            f"API 호출 실패: {path} ({last_err}, "
            f"최대 재시도 {KISConfig.MAX_RETRIES}회 초과)")

    # ---------- 단건 async ----------
    async def aget_stock_price(self, stock_code: str) -> dict[str, Any]:
        """주식 현재가 시세 조회 (async)."""
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": stock_code,
        }
        data = await self._request_get(
            path="/uapi/domestic-stock/v1/quotations/inquire-price",
            tr_id="FHKST01010100",
            params=params,
        )
        return self._parse_stock_price(stock_code, data.get("output", {}))

    async def aget_daily_chart(
        self,
        stock_code: str,
        days: int = 60,
        end_date: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """주식 일봉 차트 조회 (async)."""
        if end_date is None:
            end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=days + 30)
                      ).strftime("%Y%m%d")
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": stock_code,
            "FID_INPUT_DATE_1": start_date,
            "FID_INPUT_DATE_2": end_date,
            "FID_PERIOD_DIV_CODE": "D",
            "FID_ORG_ADJ_PRC": "0",
        }
        data = await self._request_get(
            path="/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            tr_id="FHKST03010100",
            params=params,
        )
        raw_list = data.get("output2", [])
        chart_data = []
        for item in raw_list:
            parsed = self._parse_daily_candle(item)
            if parsed and parsed["volume"] > 0:
                chart_data.append(parsed)
        return chart_data[:days]

    async def aget_investor_trading(
        self,
        stock_code: str,
        days: int = 25,
    ) -> dict[str, Any]:
        """투자자별 매매동향 조회 (async)."""
        days = max(days, 25)
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=days + 10)
                      ).strftime("%Y%m%d")
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": stock_code,
            "FID_INPUT_DATE_1": start_date,
            "FID_INPUT_DATE_2": end_date,
        }
        try:
            data = await self._request_get(
                path="/uapi/domestic-stock/v1/quotations/inquire-investor",
                tr_id="FHKST01010900",
                params=params,
            )
            raw_list = data.get("output", [])
            daily_data: list[dict[str, Any]] = []
            for item in raw_list[:days]:
                parsed = {
                    "date": item.get("stck_bsop_date", ""),
                    "foreign_net": self._safe_int(
                        item.get("frgn_ntby_qty", "0")),
                    "institutional_net": self._safe_int(
                        item.get("orgn_ntby_qty", "0")),
                    "individual_net": self._safe_int(
                        item.get("prsn_ntby_qty", "0")),
                }
                if parsed["date"]:
                    daily_data.append(parsed)
            return self._analyze_investor_trend(stock_code, daily_data)
        except KISAPIError as e:
            logger.warning("종목 %s 투자자 매매동향 조회 실패: %s", stock_code, e)
            return self._empty_investor_data(stock_code)

    async def aget_kospi_daily_index(
        self,
        days: int = 30,
        end_date: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """KOSPI 종합지수 일자별 OHLCV 조회 (백필용).

        KIS API: inquire-daily-indexchartprice (TR FHKUP03500100), iscd '0001'.

        Returns:
            list[dict]: [{"date": "YYYY-MM-DD", "close": float}, ...] 최신순.
        """
        if end_date is None:
            end_date = datetime.now().strftime("%Y%m%d")
        start_date = (
            datetime.now() - timedelta(days=days + 10)
        ).strftime("%Y%m%d")
        params = {
            "FID_COND_MRKT_DIV_CODE": "U",
            "FID_INPUT_ISCD": "0001",
            "FID_INPUT_DATE_1": start_date,
            "FID_INPUT_DATE_2": end_date,
            "FID_PERIOD_DIV_CODE": "D",
        }
        try:
            data = await self._request_get(
                path="/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice",
                tr_id="FHKUP03500100",
                params=params,
            )
        except KISAPIError as e:
            logger.warning("KOSPI 일자별 지수 조회 실패: %s", e)
            return []
        out: list[dict[str, Any]] = []
        for it in data.get("output2", []) or []:
            d = it.get("stck_bsop_date", "")
            if not d:
                continue
            iso = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
            close = self._safe_float(it.get("bstp_nmix_prpr", "0"))
            if close > 0:
                out.append({"date": iso, "close": close})
        return out[:days]

    async def aget_kospi_index(self) -> dict[str, Any]:
        """KOSPI 종합지수 현재가를 조회한다 (async).

        KIS API: inquire-index-price (TR FHPUP02100000), iscd '0001'.

        Returns:
            dict: {"index": float, "change": float, "change_rate": float}
                  실패 또는 미응답 시 0.0 들어감.
        """
        params = {
            "FID_COND_MRKT_DIV_CODE": "U",  # 업종 지수
            "FID_INPUT_ISCD": "0001",       # KOSPI 종합지수
        }
        try:
            data = await self._request_get(
                path="/uapi/domestic-stock/v1/quotations/inquire-index-price",
                tr_id="FHPUP02100000",
                params=params,
            )
        except KISAPIError as e:
            logger.warning("KOSPI 지수 조회 실패: %s", e)
            return {"index": 0.0, "change": 0.0, "change_rate": 0.0}
        out = data.get("output", {}) or {}
        return {
            "index": self._safe_float(out.get("bstp_nmix_prpr", "0")),
            "change": self._safe_float(out.get("bstp_nmix_prdy_vrss", "0")),
            "change_rate": self._safe_float(out.get("prdy_ctrt", "0")),
        }

    async def aget_kospi_stock_list(self) -> list[dict[str, Any]]:
        """코스피 전종목 현재가 조회 (async).

        KIS 모의투자 API는 업종별 시세 조회 시 최대 30건만 반환하고
        연속 조회를 지원하지 않는다. 업종 26개 × 가격대 세분화로 우회.
        업종 루프는 seen_codes 공유 때문에 순차 실행. 실제 병목은
        개별 종목 API라 여기서의 순차 실행은 영향 미미.
        """
        all_stocks: list[dict[str, Any]] = []
        seen_codes: set[str] = set()

        async def _fetch(iscd: str, price1: str = "", price2: str = "") -> int:
            params = {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_COND_SCR_DIV_CODE": "20170",
                "FID_INPUT_ISCD": iscd,
                "FID_DIV_CLS_CODE": "0",
                "FID_BLNG_CLS_CODE": "0",
                "FID_TRGT_CLS_CODE": "0",
                "FID_TRGT_EXLS_CLS_CODE": "0",
                "FID_INPUT_PRICE_1": price1,
                "FID_INPUT_PRICE_2": price2,
                "FID_VOL_CNT": "",
                "FID_INPUT_DATE_1": "",
            }
            data = await self._request_get(
                path="/uapi/domestic-stock/v1/quotations/inquire-daily-price",
                tr_id="FHPST01710000",
                params=params,
            )
            raw_list = data.get("output", [])
            for item in raw_list:
                stock = self._parse_stock_list_item(item)
                if stock and stock["stock_code"] not in seen_codes:
                    seen_codes.add(stock["stock_code"])
                    all_stocks.append(stock)
            return len(raw_list)

        try:
            capped_sectors: list[str] = []
            for iscd in self._KOSPI_SECTOR_CODES:
                count = await _fetch(iscd)
                if count >= 30:
                    capped_sectors.append(iscd)
            logger.info(
                "코스피 1단계(업종별): %d개 종목 (포화 업종 %d개)",
                len(all_stocks), len(capped_sectors))
            if capped_sectors:
                for iscd in capped_sectors:
                    for price1, price2 in self._PRICE_RANGES:
                        await _fetch(iscd, price1, price2)
                logger.info(
                    "코스피 2단계(가격대 세분화): %d개 종목", len(all_stocks))
        except KISAPIError as e:
            logger.error(
                "코스피 전종목 조회 실패 (수집 %d건): %s", len(all_stocks), e)
        logger.info("코스피 종목 %d개 조회 완료", len(all_stocks))
        return all_stocks

    async def acheck_token(self) -> bool:
        """토큰 유효성 확인 (async)."""
        try:
            await self._token_manager.get_token_async()
            return True
        except KISAPIError:
            return False

    # ---------- 배치 async ----------
    async def aget_all_stock_prices(
        self, stock_codes: list[str],
    ) -> list[dict[str, Any]]:
        """여러 종목 현재가 배치 조회.

        asyncio.gather로 병렬. rate는 공용 _limiter가 강제.
        종목별 실패는 개별 스킵. 전체 실패율 > threshold 시 예외.
        """
        if not stock_codes:
            return []
        tasks = [self.aget_stock_price(c) for c in stock_codes]
        raw = await asyncio.gather(*tasks, return_exceptions=True)
        results: list[dict[str, Any]] = []
        failures = 0
        for code, res in zip(stock_codes, raw):
            if isinstance(res, Exception):
                failures += 1
                logger.warning("종목 %s 조회 실패: %s", code, res)
            else:
                results.append(res)
        self._check_failure_rate(failures, len(stock_codes), "stock_prices")
        logger.info("전체 %d/%d 종목 현재가 조회 완료",
                    len(results), len(stock_codes))
        return results

    async def aget_all_daily_charts(
        self, stock_codes: list[str], days: int = 60,
    ) -> dict[str, list[dict[str, Any]]]:
        """여러 종목 일봉 차트 배치 조회."""
        if not stock_codes:
            return {}
        tasks = [self.aget_daily_chart(c, days=days) for c in stock_codes]
        raw = await asyncio.gather(*tasks, return_exceptions=True)
        results: dict[str, list[dict[str, Any]]] = {}
        failures = 0
        for code, res in zip(stock_codes, raw):
            if isinstance(res, Exception):
                failures += 1
                logger.warning("종목 %s 일봉 조회 실패: %s", code, res)
            elif res:
                results[code] = res
        self._check_failure_rate(failures, len(stock_codes), "daily_charts")
        logger.info("전체 %d/%d 종목 일봉 차트 조회 완료",
                    len(results), len(stock_codes))
        return results

    async def aget_all_investor_trading(
        self, stock_codes: list[str], days: int = 25,
    ) -> dict[str, dict[str, Any]]:
        """여러 종목 투자자 매매동향 배치 조회.

        aget_investor_trading은 개별 실패 시 _empty_investor_data를
        반환(예외 억제)하므로 여기서 failure_rate 판정 불가.
        대신 반환된 dict 중 'neutral' + 전부 0인 건수로 실패 추정 가능하지만
        정상적으로 데이터가 모두 0인 종목과 구분 불가 → 실패율 판정 생략.
        """
        if not stock_codes:
            return {}
        tasks = [self.aget_investor_trading(c, days=days) for c in stock_codes]
        raw = await asyncio.gather(*tasks, return_exceptions=True)
        results: dict[str, dict[str, Any]] = {}
        failures = 0
        for code, res in zip(stock_codes, raw):
            if isinstance(res, Exception):
                failures += 1
                logger.warning("종목 %s 수급 조회 실패: %s", code, res)
                results[code] = self._empty_investor_data(code)
            else:
                results[code] = res
        self._check_failure_rate(
            failures, len(stock_codes), "investor_trading")
        logger.info("전체 %d/%d 종목 수급 데이터 조회 완료",
                    len(results), len(stock_codes))
        return results

    def _check_failure_rate(
        self, failures: int, total: int, label: str,
    ) -> None:
        if total == 0:
            return
        rate = failures / total
        if rate > self._fail_threshold:
            raise KISBatchFailureError(
                f"{label} 실패율 {rate:.1%} > 임계치 "
                f"{self._fail_threshold:.0%} ({failures}/{total})")

    # ================================================================
    # Parsers & Utils (순수 함수. sync/async 무관)
    # ================================================================
    def _parse_stock_price(
        self, stock_code: str, raw: dict[str, str],
    ) -> dict[str, Any]:
        """현재가 API 응답을 정규화한다.

        inquire-price(FHKST01010100)는 종목명 필드를 반환하지 않으므로
        빈 종목명은 호출 측에서 stock_master lookup으로 보강.
        """
        stock_name = (
            raw.get("hts_kor_isnm")
            or raw.get("prdt_abrv_name")
            or raw.get("prdt_name")
            or ""
        )
        return {
            "stock_code": stock_code,
            "stock_name": stock_name,
            "sector": raw.get("bstp_kor_isnm", "") or "기타",
            "current_price": self._safe_int(raw.get("stck_prpr", "0")),
            "change_rate": self._safe_float(raw.get("prdy_ctrt", "0")),
            "volume": self._safe_int(raw.get("acml_vol", "0")),
            "trading_value": self._safe_int(raw.get("acml_tr_pbmn", "0")),
            "market_cap":
                self._safe_int(raw.get("hts_avls", "0")) * 100_000_000,
            "per": self._safe_float(raw.get("per", "0")),
            "pbr": self._safe_float(raw.get("pbr", "0")),
            "eps": self._safe_int(raw.get("eps", "0")),
            "bps": self._safe_int(raw.get("bps", "0")),
            "high_52w": self._safe_int(raw.get("stck_dryc_hgpr", "0")),
            "low_52w": self._safe_int(raw.get("stck_dryc_lwpr", "0")),
        }

    def _parse_daily_candle(
        self, raw: dict[str, str],
    ) -> Optional[dict[str, Any]]:
        date_str = raw.get("stck_bsop_date", "")
        if not date_str:
            return None
        return {
            "date": date_str,
            "open": self._safe_int(raw.get("stck_oprc", "0")),
            "high": self._safe_int(raw.get("stck_hgpr", "0")),
            "low": self._safe_int(raw.get("stck_lwpr", "0")),
            "close": self._safe_int(raw.get("stck_clpr", "0")),
            "volume": self._safe_int(raw.get("acml_vol", "0")),
        }

    def _parse_stock_list_item(
        self, raw: dict[str, str],
    ) -> Optional[dict[str, Any]]:
        """전종목 시세 API 응답 한 건을 정규화한다."""
        code = raw.get("mksc_shrn_iscd", "") or raw.get("stck_shrn_iscd", "")
        if not code:
            return None
        current_price = self._safe_int(raw.get("stck_prpr", "0"))
        hts_avls = self._safe_int(raw.get("hts_avls", "0"))
        if hts_avls > 0:
            market_cap = hts_avls * 100_000_000
        else:
            listed_shares = self._safe_int(raw.get("lstn_stcn", "0"))
            market_cap = current_price * listed_shares
        return {
            "stock_code": code,
            "stock_name": raw.get("hts_kor_isnm", ""),
            "sector": raw.get("bstp_kor_isnm", "") or "기타",
            "current_price": current_price,
            "change_rate": self._safe_float(raw.get("prdy_ctrt", "0")),
            "volume": self._safe_int(raw.get("acml_vol", "0")),
            "trading_value": self._safe_int(raw.get("acml_tr_pbmn", "0")),
            "market_cap": market_cap,
            "per": self._safe_float(raw.get("per", "0")),
            "pbr": self._safe_float(raw.get("pbr", "0")),
        }

    def _analyze_investor_trend(
        self,
        stock_code: str,
        daily_data: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not daily_data:
            return self._empty_investor_data(stock_code)

        foreign_consecutive = 0
        for d in daily_data:
            if d["foreign_net"] > 0:
                foreign_consecutive += 1
            else:
                break
        if foreign_consecutive == 0:
            for d in daily_data:
                if d["foreign_net"] < 0:
                    foreign_consecutive -= 1
                else:
                    break

        inst_consecutive = 0
        for d in daily_data:
            if d["institutional_net"] > 0:
                inst_consecutive += 1
            else:
                break
        if inst_consecutive == 0:
            for d in daily_data:
                if d["institutional_net"] < 0:
                    inst_consecutive -= 1
                else:
                    break

        foreign_cum = sum(d["foreign_net"] for d in daily_data)
        inst_cum = sum(d["institutional_net"] for d in daily_data)

        def consecutive_buy_days(
            items: list[dict[str, Any]], key: str, window: int,
        ) -> int:
            count = 0
            for d in items[:window]:
                if d[key] > 0:
                    count += 1
                else:
                    break
            return count

        def positive_day_count(
            items: list[dict[str, Any]], key: str, window: int,
        ) -> int:
            return sum(1 for d in items[:window] if d[key] > 0)

        foreign_5d = consecutive_buy_days(daily_data, "foreign_net", 5)
        foreign_20d = positive_day_count(daily_data, "foreign_net", 20)
        inst_5d = consecutive_buy_days(daily_data, "institutional_net", 5)
        inst_20d = positive_day_count(daily_data, "institutional_net", 20)

        def trend_label(consecutive: int, cumulative: int) -> str:
            if consecutive >= 3 and cumulative > 0:
                return "strong_buying"
            elif consecutive >= 1 and cumulative > 0:
                return "buying"
            elif consecutive <= -3 and cumulative < 0:
                return "strong_selling"
            elif consecutive <= -1 and cumulative < 0:
                return "selling"
            return "neutral"

        return {
            "stock_code": stock_code,
            "foreign_net_buy_days": foreign_consecutive,
            "institutional_net_buy_days": inst_consecutive,
            "foreign_net_buy_5d": foreign_5d,
            "foreign_net_buy_20d": foreign_20d,
            "institutional_net_buy_5d": inst_5d,
            "institutional_net_buy_20d": inst_20d,
            "foreign_cumulative": foreign_cum,
            "institutional_cumulative": inst_cum,
            "foreign_trend": trend_label(foreign_consecutive, foreign_cum),
            "institutional_trend": trend_label(inst_consecutive, inst_cum),
            "daily_data": daily_data[:5],
        }

    def _empty_investor_data(self, stock_code: str) -> dict[str, Any]:
        return {
            "stock_code": stock_code,
            "foreign_net_buy_days": 0,
            "institutional_net_buy_days": 0,
            "foreign_net_buy_5d": 0,
            "foreign_net_buy_20d": 0,
            "institutional_net_buy_5d": 0,
            "institutional_net_buy_20d": 0,
            "foreign_cumulative": 0,
            "institutional_cumulative": 0,
            "foreign_trend": "neutral",
            "institutional_trend": "neutral",
            "daily_data": [],
        }

    @staticmethod
    def _safe_int(value: str) -> int:
        try:
            cleaned = str(value).replace(",", "").replace(" ", "").strip()
            if not cleaned or cleaned == "-":
                return 0
            return int(float(cleaned))
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def _safe_float(value: str) -> float:
        try:
            cleaned = str(value).replace(",", "").replace(" ", "").strip()
            if not cleaned or cleaned == "-":
                return 0.0
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0

    # ================================================================
    # Sync 래퍼 (하위 호환)
    # ================================================================
    # 기존 호출부(main.py, database/models.py)가 그대로 동작하도록
    # async 메서드를 asyncio.run으로 감싼다. 각 호출은 필요 시
    # aiohttp 세션을 생성·종료한다. Rate limiter는 인스턴스 변수라
    # 여러 sync 호출 사이에서도 상태가 유지된다.

    async def _with_session(self, coro_fn, *args, **kwargs):
        """세션이 없으면 만들었다가 종료한다."""
        if self._session is not None:
            return await coro_fn(*args, **kwargs)
        async with self:
            return await coro_fn(*args, **kwargs)

    def get_stock_price(self, stock_code: str) -> dict[str, Any]:
        return _run_sync(
            self._with_session(self.aget_stock_price, stock_code))

    def get_daily_chart(
        self,
        stock_code: str,
        days: int = 60,
        end_date: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        return _run_sync(
            self._with_session(
                self.aget_daily_chart, stock_code, days, end_date))

    def get_investor_trading(
        self, stock_code: str, days: int = 25,
    ) -> dict[str, Any]:
        return _run_sync(
            self._with_session(
                self.aget_investor_trading, stock_code, days))

    def get_kospi_stock_list(self) -> list[dict[str, Any]]:
        return _run_sync(
            self._with_session(self.aget_kospi_stock_list))

    def get_kospi_index(self) -> dict[str, Any]:
        return _run_sync(
            self._with_session(self.aget_kospi_index))

    def get_kospi_daily_index(
        self, days: int = 30, end_date: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        return _run_sync(
            self._with_session(
                self.aget_kospi_daily_index, days, end_date))

    def get_all_stock_prices(
        self, stock_codes: list[str],
    ) -> list[dict[str, Any]]:
        return _run_sync(
            self._with_session(self.aget_all_stock_prices, stock_codes))

    def get_all_daily_charts(
        self, stock_codes: list[str], days: int = 60,
    ) -> dict[str, list[dict[str, Any]]]:
        return _run_sync(
            self._with_session(
                self.aget_all_daily_charts, stock_codes, days))

    def get_all_investor_trading(
        self, stock_codes: list[str], days: int = 25,
    ) -> dict[str, dict[str, Any]]:
        return _run_sync(
            self._with_session(
                self.aget_all_investor_trading, stock_codes, days))

    def check_token(self) -> bool:
        """토큰 유효성 확인 (sync).

        네트워크는 최대 1회라 async 래퍼 없이 바로 동기 경로 사용.
        """
        try:
            self._token_manager.get_token()
            return True
        except KISAPIError:
            return False

    # ================================================================
    # 상수
    # ================================================================
    _KOSPI_SECTOR_CODES: list[str] = [
        "0001",
        "0002", "0003", "0004",
        "0005", "0006", "0007", "0008", "0009", "0010",
        "0011", "0012", "0013", "0014", "0015", "0016",
        "0017", "0018", "0019", "0020", "0021", "0022",
        "0024", "0025", "0026", "0027",
    ]

    _PRICE_RANGES: list[tuple[str, str]] = [
        ("", "1000"), ("1001", "1500"), ("1501", "2000"),
        ("2001", "3000"), ("3001", "4000"), ("4001", "5000"),
        ("5001", "7000"), ("7001", "10000"),
        ("10001", "15000"), ("15001", "20000"),
        ("20001", "30000"), ("30001", "50000"),
        ("50001", "100000"), ("100001", "200000"),
        ("200001", "500000"), ("500001", "9999999"),
    ]


# ================================================================
# __main__ 테스트 실행 (sync 래퍼 경로)
# ================================================================
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    client = KISClient()

    print("=== 토큰 확인 ===")
    if not client.check_token():
        print("토큰 발급 실패 (.env 설정 확인)")
        raise SystemExit(1)
    print("토큰 유효")

    print("\n=== 삼성전자 현재가 ===")
    price = client.get_stock_price("005930")
    for key, val in price.items():
        print(f"  {key}: {val}")

    print("\n=== 삼성전자 일봉 (최근 10일) ===")
    chart = client.get_daily_chart("005930", days=10)
    for candle in chart[:5]:
        print(
            f"  {candle['date']}: 시{candle['open']} 고{candle['high']} "
            f"저{candle['low']} 종{candle['close']} 량{candle['volume']:,}"
        )
