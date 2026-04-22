"""
KOSPI 저평가 기업 분석 시스템 - KIS Open API 연동 모듈

한국투자증권 REST API를 통해 다음 데이터를 수집한다:
  - Access Token 발급/갱신 (24시간 유효)
  - 주식 현재가 조회 (현재가, PER, PBR, 시가총액, 거래량)
  - 주식 일봉 차트 조회 (60일간 OHLCV)
  - 코스피 전종목 리스트 조회

설계서 섹션 3.1 참조
"""

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import requests

from config.settings import KISConfig

logger = logging.getLogger(__name__)


class KISTokenManager:
    """KIS API Access Token 관리.

    토큰 발급, 캐싱, 자동 갱신을 담당한다.
    토큰은 24시간 유효하며, 만료 1시간 전에 자동 갱신한다.
    """

    def __init__(self) -> None:
        self._access_token: str = ""
        self._token_expired_at: datetime = datetime.min
        self._token_cache_path = Path("token_cache/kis_token.json")

    def get_token(self) -> str:
        """유효한 Access Token을 반환한다.

        Returns:
            str: Access Token 문자열

        Raises:
            KISAPIError: 토큰 발급 실패 시
        """
        # 캐시된 토큰이 유효하면 재사용
        if self._is_token_valid():
            return self._access_token

        # 파일 캐시에서 로드 시도
        if self._load_token_from_cache():
            if self._is_token_valid():
                logger.info("캐시된 토큰 로드 성공")
                return self._access_token

        # 새 토큰 발급
        self._issue_new_token()
        return self._access_token

    def _is_token_valid(self) -> bool:
        """토큰이 유효한지 확인한다 (만료 1시간 전부터 무효 처리)."""
        if not self._access_token:
            return False
        buffer = timedelta(seconds=KISConfig.TOKEN_REFRESH_BUFFER)
        return datetime.now() < (self._token_expired_at - buffer)

    def _issue_new_token(self) -> None:
        """새 Access Token을 발급받는다.

        Raises:
            KISAPIError: API 호출 실패 시
        """
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
                f"토큰 발급 실패: HTTP {response.status_code}, {response.text}"
            )

        data = response.json()
        self._access_token = data["access_token"]
        # 토큰 만료 시간 파싱 (KIS 응답: "access_token_token_expired" 필드)
        expired_str = data.get("access_token_token_expired", "")
        if expired_str:
            self._token_expired_at = datetime.strptime(
                expired_str, "%Y-%m-%d %H:%M:%S"
            )
        else:
            # 응답에 만료시간이 없으면 24시간 후로 설정
            self._token_expired_at = datetime.now() + timedelta(hours=24)

        self._save_token_to_cache()
        logger.info(
            "토큰 발급 성공 (만료: %s)", self._token_expired_at.strftime("%Y-%m-%d %H:%M")
        )

    def _save_token_to_cache(self) -> None:
        """토큰을 파일에 캐싱한다."""
        try:
            self._token_cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_data = {
                "access_token": self._access_token,
                "expired_at": self._token_expired_at.isoformat(),
            }
            self._token_cache_path.write_text(
                json.dumps(cache_data, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as e:
            logger.warning("토큰 캐시 저장 실패: %s", e)

    def _load_token_from_cache(self) -> bool:
        """파일 캐시에서 토큰을 로드한다."""
        try:
            if not self._token_cache_path.exists():
                return False
            cache_data = json.loads(
                self._token_cache_path.read_text(encoding="utf-8")
            )
            self._access_token = cache_data["access_token"]
            self._token_expired_at = datetime.fromisoformat(cache_data["expired_at"])
            return True
        except (OSError, json.JSONDecodeError, KeyError) as e:
            logger.warning("토큰 캐시 로드 실패: %s", e)
            return False


class KISAPIError(Exception):
    """KIS API 호출 에러."""


class KISClient:
    """한국투자증권 Open API 클라이언트.

    설계서 3.1절의 모든 데이터 수집 기능을 제공한다.
    Rate Limit (1초 20회)을 준수하며, 실패 시 지수 백오프로 재시도한다.
    """

    def __init__(self) -> None:
        self._token_manager = KISTokenManager()
        self._last_call_time: float = 0.0

    def _get_headers(self, tr_id: str) -> dict[str, str]:
        """API 호출용 공통 헤더를 생성한다.

        Args:
            tr_id: 거래 ID (API별 고유 코드)

        Returns:
            dict: HTTP 헤더
        """
        token = self._token_manager.get_token()
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": KISConfig.APP_KEY,
            "appsecret": KISConfig.APP_SECRET,
            "tr_id": tr_id,
            "custtype": "P",  # 개인
        }

    def _rate_limit_wait(self) -> None:
        """API Rate Limit을 준수하기 위해 대기한다."""
        elapsed = time.time() - self._last_call_time
        if elapsed < KISConfig.RATE_LIMIT_INTERVAL:
            wait_time = KISConfig.RATE_LIMIT_INTERVAL - elapsed
            time.sleep(wait_time)
        self._last_call_time = time.time()

    def _request_get(
        self,
        path: str,
        tr_id: str,
        params: dict[str, str],
        extra_headers: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        """GET 요청을 실행한다 (Rate Limit + 재시도 포함).

        Args:
            path: API 경로 (예: "/uapi/domestic-stock/v1/quotations/inquire-price")
            tr_id: 거래 ID
            params: 쿼리 파라미터
            extra_headers: 추가 헤더 (연속 조회 등)

        Returns:
            dict: API 응답 JSON

        Raises:
            KISAPIError: 최대 재시도 초과 시
        """
        url = f"{KISConfig.BASE_URL}{path}"
        headers = self._get_headers(tr_id)
        if extra_headers:
            headers.update(extra_headers)

        for attempt in range(1, KISConfig.MAX_RETRIES + 1):
            self._rate_limit_wait()

            try:
                response = requests.get(
                    url, headers=headers, params=params, timeout=10
                )

                if response.status_code == 200:
                    data = response.json()
                    rt_cd = data.get("rt_cd", "")
                    if rt_cd == "0":
                        return data
                    # API 레벨 에러
                    msg = data.get("msg1", "알 수 없는 에러")
                    logger.warning(
                        "API 에러 (시도 %d/%d): %s", attempt, KISConfig.MAX_RETRIES, msg
                    )
                else:
                    logger.warning(
                        "HTTP %d (시도 %d/%d)", response.status_code,
                        attempt, KISConfig.MAX_RETRIES
                    )

            except requests.RequestException as e:
                logger.warning(
                    "네트워크 에러 (시도 %d/%d): %s", attempt, KISConfig.MAX_RETRIES, e
                )

            # 지수 백오프 대기
            if attempt < KISConfig.MAX_RETRIES:
                wait = KISConfig.RETRY_BACKOFF_BASE ** attempt
                logger.info("%.1f초 후 재시도...", wait)
                time.sleep(wait)

        raise KISAPIError(f"API 호출 실패: {path} (최대 재시도 {KISConfig.MAX_RETRIES}회 초과)")

    # ================================================================
    # 주식 현재가 조회
    # ================================================================
    def get_stock_price(self, stock_code: str) -> dict[str, Any]:
        """주식 현재가 시세를 조회한다.

        설계서 3.1.2: 현재가, 전일대비, 거래량, 시가총액, PER, PBR 수집

        Args:
            stock_code: 종목코드 (예: "005930")

        Returns:
            dict: 정규화된 시세 데이터
                {
                    "stock_code": str,          # 종목코드
                    "stock_name": str,           # 종목명
                    "current_price": int,        # 현재가
                    "change_rate": float,        # 전일대비 등락률 (%)
                    "volume": int,               # 누적 거래량
                    "trading_value": int,        # 거래대금
                    "market_cap": int,           # 시가총액
                    "per": float,                # PER
                    "pbr": float,                # PBR
                    "eps": int,                  # EPS
                    "bps": int,                  # BPS
                    "high_52w": int,             # 52주 최고가
                    "low_52w": int,              # 52주 최저가
                }
        """
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",  # 주식
            "FID_INPUT_ISCD": stock_code,
        }

        # 모의투자 vs 실전 tr_id 분기
        tr_id = "FHKST01010100"

        data = self._request_get(
            path="/uapi/domestic-stock/v1/quotations/inquire-price",
            tr_id=tr_id,
            params=params,
        )

        output = data.get("output", {})
        return self._parse_stock_price(stock_code, output)

    def _parse_stock_price(
        self, stock_code: str, raw: dict[str, str]
    ) -> dict[str, Any]:
        """현재가 API 응답을 정규화한다.

        KIS의 inquire-price(FHKST01010100) 단일 시세 엔드포인트는 실제로
        종목명 필드를 반환하지 않는다(진단 결과 73개 키 중 종목명 없음).
        업종명(`bstp_kor_isnm`)은 종목명이 아니므로 폴백에서 제외한다.

        종목명이 비어있는 결과는 호출 측(main.py)에서 stock_master 테이블
        lookup으로 보강한다. 이 메소드 자체는 DB에 접근하지 않는다.
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
            "market_cap": self._safe_int(raw.get("hts_avls", "0")) * 100_000_000,
            "per": self._safe_float(raw.get("per", "0")),
            "pbr": self._safe_float(raw.get("pbr", "0")),
            "eps": self._safe_int(raw.get("eps", "0")),
            "bps": self._safe_int(raw.get("bps", "0")),
            "high_52w": self._safe_int(raw.get("stck_dryc_hgpr", "0")),
            "low_52w": self._safe_int(raw.get("stck_dryc_lwpr", "0")),
        }

    # ================================================================
    # 주식 일봉 차트 조회
    # ================================================================
    def get_daily_chart(
        self,
        stock_code: str,
        days: int = 60,
        end_date: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """주식 일봉 차트 데이터를 조회한다.

        설계서 3.1.2: 60일간 OHLCV 데이터 (ATR 계산 및 모멘텀 분석용)

        Args:
            stock_code: 종목코드
            days: 조회 일수 (기본 60일)
            end_date: 종료일 (YYYYMMDD, 기본 오늘)

        Returns:
            list[dict]: 일봉 데이터 리스트 (최신순)
                [{
                    "date": str,          # 날짜 (YYYYMMDD)
                    "open": int,          # 시가
                    "high": int,          # 고가
                    "low": int,           # 저가
                    "close": int,         # 종가
                    "volume": int,        # 거래량
                }, ...]
        """
        if end_date is None:
            end_date = datetime.now().strftime("%Y%m%d")

        start_date = (
            datetime.now() - timedelta(days=days + 30)
        ).strftime("%Y%m%d")  # 영업일 고려 여유분

        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": stock_code,
            "FID_INPUT_DATE_1": start_date,
            "FID_INPUT_DATE_2": end_date,
            "FID_PERIOD_DIV_CODE": "D",  # 일봉
            "FID_ORG_ADJ_PRC": "0",      # 수정주가 미적용
        }

        tr_id = "FHKST03010100"

        data = self._request_get(
            path="/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            tr_id=tr_id,
            params=params,
        )

        raw_list = data.get("output2", [])
        chart_data = []
        for item in raw_list:
            parsed = self._parse_daily_candle(item)
            if parsed and parsed["volume"] > 0:
                chart_data.append(parsed)

        # 최신 days일만 반환
        return chart_data[:days]

    def _parse_daily_candle(self, raw: dict[str, str]) -> Optional[dict[str, Any]]:
        """일봉 API 응답 한 건을 정규화한다."""
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

    # ================================================================
    # 코스피 전종목 시세 조회
    # ================================================================

    # 코스피 업종 코드
    _KOSPI_SECTOR_CODES: list[str] = [
        "0001",  # 종합
        "0002", "0003", "0004",  # 대형/중형/소형
        "0005", "0006", "0007", "0008", "0009", "0010",
        "0011", "0012", "0013", "0014", "0015", "0016",
        "0017", "0018", "0019", "0020", "0021", "0022",
        "0024", "0025", "0026", "0027",
    ]

    # 가격대 분할 구간 (포화 업종 재조회용)
    _PRICE_RANGES: list[tuple[str, str]] = [
        ("", "1000"), ("1001", "1500"), ("1501", "2000"),
        ("2001", "3000"), ("3001", "4000"), ("4001", "5000"),
        ("5001", "7000"), ("7001", "10000"),
        ("10001", "15000"), ("15001", "20000"),
        ("20001", "30000"), ("30001", "50000"),
        ("50001", "100000"), ("100001", "200000"),
        ("200001", "500000"), ("500001", "9999999"),
    ]

    def get_kospi_stock_list(self) -> list[dict[str, Any]]:
        """코스피 전종목의 현재가를 조회한다.

        설계서 3.1.2: 코스피 전종목 리스트 + 시세 데이터 수집

        KIS 모의투자 API는 업종별 시세 조회 시 최대 30건만 반환하고
        연속 조회(페이징)를 지원하지 않는다. 이를 우회하기 위해:
          1단계: 26개 업종 코드별로 각각 조회 (최대 30건×26)
          2단계: 30건 포화된 업종을 가격대별로 세분화 재조회
        중복은 seen_codes로 제거한다.

        Returns:
            list[dict]: 종목 리스트
                [{
                    "stock_code": str,
                    "stock_name": str,
                    "current_price": int,
                    "change_rate": float,
                    "volume": int,
                    "trading_value": int,
                    "market_cap": int,
                    "per": float,
                    "pbr": float,
                }, ...]
        """
        all_stocks: list[dict[str, Any]] = []
        seen_codes: set[str] = set()

        def _fetch(iscd: str, price1: str = "", price2: str = "") -> int:
            """단일 조회 실행. 반환된 건수(페이지 크기)를 리턴한다."""
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
            data = self._request_get(
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
            # 1단계: 업종별 전체 조회
            capped_sectors: list[str] = []
            for iscd in self._KOSPI_SECTOR_CODES:
                count = _fetch(iscd)
                if count >= 30:
                    capped_sectors.append(iscd)

            logger.info(
                "코스피 1단계(업종별): %d개 종목 (포화 업종 %d개)",
                len(all_stocks), len(capped_sectors),
            )

            # 2단계: 포화 업종을 가격대별로 세분화하여 누락 종목 수집
            if capped_sectors:
                for iscd in capped_sectors:
                    for price1, price2 in self._PRICE_RANGES:
                        _fetch(iscd, price1, price2)

                logger.info(
                    "코스피 2단계(가격대 세분화): %d개 종목", len(all_stocks),
                )

        except KISAPIError as e:
            logger.error("코스피 전종목 조회 실패 (수집 %d건): %s", len(all_stocks), e)

        logger.info("코스피 종목 %d개 조회 완료", len(all_stocks))
        return all_stocks

    def _parse_stock_list_item(
        self, raw: dict[str, str]
    ) -> Optional[dict[str, Any]]:
        """전종목 시세 API 응답 한 건을 정규화한다.

        업종별 시세 API(FHPST01710000)는 hts_avls(시가총액), per, pbr을
        제공하지 않으므로, 시총은 현재가 × 상장주수로 계산한다.
        PER/PBR은 이후 개별 시세 조회에서 보충한다.
        """
        code = raw.get("mksc_shrn_iscd", "") or raw.get("stck_shrn_iscd", "")
        if not code:
            return None

        current_price = self._safe_int(raw.get("stck_prpr", "0"))

        # 시가총액: hts_avls가 있으면 사용, 없으면 현재가 × 상장주수로 계산
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

    # ================================================================
    # 투자자별 매매동향 조회 (외국인/기관 수급)
    # ================================================================
    def get_investor_trading(
        self,
        stock_code: str,
        days: int = 25,
    ) -> dict[str, Any]:
        """종목의 투자자별 매매동향을 조회한다.

        외국인/기관/개인의 일별 순매수 데이터를 수집하여
        연속 순매수 일수와 누적 순매수량을 계산한다.

        Args:
            stock_code: 종목코드
            days: 조회 일수 (기본 25일 — 20일 분석 윈도우 보장)

        Returns:
            dict: 수급 분석 데이터
                {
                    "stock_code": str,
                    "foreign_net_buy_days": int,         # 외국인 연속 순매수 일수 (호환)
                    "institutional_net_buy_days": int,   # 기관 연속 순매수 일수 (호환)
                    "foreign_net_buy_5d": int,           # 외국인 최근 5일 연속 순매수일수
                    "foreign_net_buy_20d": int,          # 외국인 최근 20일 중 순매수일수
                    "institutional_net_buy_5d": int,     # 기관 최근 5일 연속 순매수일수
                    "institutional_net_buy_20d": int,    # 기관 최근 20일 중 순매수일수
                    "foreign_cumulative": int,           # 외국인 누적 순매수량 (주)
                    "institutional_cumulative": int,     # 기관 누적 순매수량 (주)
                    "foreign_trend": str,                # "buying" / "selling" / "neutral"
                    "institutional_trend": str,
                    "daily_data": list,                  # 일별 상세 데이터
                }
        """
        # 20일 윈도우 분석을 위해 최소 25일치 데이터 확보
        days = max(days, 25)
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (
            datetime.now() - timedelta(days=days + 10)
        ).strftime("%Y%m%d")  # 영업일 여유분

        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": stock_code,
            "FID_INPUT_DATE_1": start_date,
            "FID_INPUT_DATE_2": end_date,
        }

        # 주식현재가 투자자 API
        tr_id = "FHKST01010900"

        try:
            data = self._request_get(
                path="/uapi/domestic-stock/v1/quotations/inquire-investor",
                tr_id=tr_id,
                params=params,
            )

            raw_list = data.get("output", [])
            daily_data: list[dict[str, Any]] = []

            for item in raw_list[:days]:
                parsed = {
                    "date": item.get("stck_bsop_date", ""),
                    "foreign_net": self._safe_int(
                        item.get("frgn_ntby_qty", "0")
                    ),
                    "institutional_net": self._safe_int(
                        item.get("orgn_ntby_qty", "0")
                    ),
                    "individual_net": self._safe_int(
                        item.get("prsn_ntby_qty", "0")
                    ),
                }
                if parsed["date"]:
                    daily_data.append(parsed)

            return self._analyze_investor_trend(stock_code, daily_data)

        except KISAPIError as e:
            logger.warning("종목 %s 투자자 매매동향 조회 실패: %s", stock_code, e)
            return self._empty_investor_data(stock_code)

    def _analyze_investor_trend(
        self,
        stock_code: str,
        daily_data: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """투자자별 매매 데이터를 분석한다.

        연속 순매수 일수, 누적 순매수량, 추세를 계산한다.
        """
        if not daily_data:
            return self._empty_investor_data(stock_code)

        # 외국인 연속 순매수 일수 (최신부터 카운트)
        foreign_consecutive = 0
        for d in daily_data:
            if d["foreign_net"] > 0:
                foreign_consecutive += 1
            else:
                break

        # 외국인 연속 순매도 일수 (음수로 표현)
        if foreign_consecutive == 0:
            for d in daily_data:
                if d["foreign_net"] < 0:
                    foreign_consecutive -= 1
                else:
                    break

        # 기관 연속 순매수 일수
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

        # 누적 순매수량 (최근 days일)
        foreign_cum = sum(d["foreign_net"] for d in daily_data)
        inst_cum = sum(d["institutional_net"] for d in daily_data)

        # === 5일·20일 분리 지표 ===
        # 5일 윈도우: 최신부터 최대 5일까지 연속 순매수일수
        def consecutive_buy_days(items: list[dict[str, Any]], key: str,
                                 window: int) -> int:
            count = 0
            for d in items[:window]:
                if d[key] > 0:
                    count += 1
                else:
                    break
            return count

        # 20일 윈도우: 20일 중 순매수(양수)였던 일수 카운트
        def positive_day_count(items: list[dict[str, Any]], key: str,
                               window: int) -> int:
            return sum(1 for d in items[:window] if d[key] > 0)

        foreign_5d = consecutive_buy_days(daily_data, "foreign_net", 5)
        foreign_20d = positive_day_count(daily_data, "foreign_net", 20)
        inst_5d = consecutive_buy_days(daily_data, "institutional_net", 5)
        inst_20d = positive_day_count(daily_data, "institutional_net", 20)

        # 추세 판정
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
            "daily_data": daily_data[:5],  # 최근 5일만 저장
        }

    def _empty_investor_data(self, stock_code: str) -> dict[str, Any]:
        """빈 투자자 매매 데이터를 반환한다."""
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

    # ================================================================
    # 투자자별 매매동향 배치 조회
    # ================================================================
    def get_all_investor_trading(
        self, stock_codes: list[str], days: int = 10
    ) -> dict[str, dict[str, Any]]:
        """여러 종목의 투자자별 매매동향을 배치 조회한다.

        Args:
            stock_codes: 종목코드 리스트
            days: 조회 일수

        Returns:
            dict: {종목코드: 수급 분석 데이터}
        """
        results: dict[str, dict[str, Any]] = {}
        total = len(stock_codes)

        for idx, code in enumerate(stock_codes, 1):
            try:
                investor_data = self.get_investor_trading(code, days)
                results[code] = investor_data

                if idx % 50 == 0:
                    logger.info("수급 데이터 조회 진행: %d/%d", idx, total)

            except KISAPIError as e:
                logger.warning("종목 %s 수급 조회 실패: %s", code, e)
                results[code] = self._empty_investor_data(code)

        logger.info("전체 %d/%d 종목 수급 데이터 조회 완료", len(results), total)
        return results

    # ================================================================
    # 코스피 전종목 개별 조회 (대량 배치)
    # ================================================================
    def get_all_stock_prices(
        self, stock_codes: list[str]
    ) -> list[dict[str, Any]]:
        """여러 종목의 현재가를 순차 조회한다.

        Rate Limit (0.5초 간격)을 준수하며 배치 조회.

        Args:
            stock_codes: 종목코드 리스트

        Returns:
            list[dict]: 종목별 시세 데이터
        """
        results: list[dict[str, Any]] = []
        total = len(stock_codes)

        for idx, code in enumerate(stock_codes, 1):
            try:
                price_data = self.get_stock_price(code)
                results.append(price_data)

                if idx % 50 == 0:
                    logger.info("현재가 조회 진행: %d/%d", idx, total)

            except KISAPIError as e:
                logger.warning("종목 %s 조회 실패: %s", code, e)
                continue

        logger.info("전체 %d/%d 종목 현재가 조회 완료", len(results), total)
        return results

    def get_all_daily_charts(
        self, stock_codes: list[str], days: int = 60
    ) -> dict[str, list[dict[str, Any]]]:
        """여러 종목의 일봉 차트를 순차 조회한다.

        Args:
            stock_codes: 종목코드 리스트
            days: 조회 일수

        Returns:
            dict: {종목코드: 일봉 데이터 리스트}
        """
        results: dict[str, list[dict[str, Any]]] = {}
        total = len(stock_codes)

        for idx, code in enumerate(stock_codes, 1):
            try:
                chart = self.get_daily_chart(code, days=days)
                if chart:
                    results[code] = chart

                if idx % 50 == 0:
                    logger.info("일봉 차트 조회 진행: %d/%d", idx, total)

            except KISAPIError as e:
                logger.warning("종목 %s 일봉 조회 실패: %s", code, e)
                continue

        logger.info("전체 %d/%d 종목 일봉 차트 조회 완료", len(results), total)
        return results

    # ================================================================
    # 토큰 상태 확인
    # ================================================================
    def check_token(self) -> bool:
        """토큰 유효성을 확인한다.

        Returns:
            bool: 토큰 유효 여부
        """
        try:
            self._token_manager.get_token()
            return True
        except KISAPIError:
            return False

    # ================================================================
    # 유틸리티
    # ================================================================
    @staticmethod
    def _safe_int(value: str) -> int:
        """문자열을 안전하게 int로 변환한다."""
        try:
            # 쉼표, 공백 제거
            cleaned = value.replace(",", "").replace(" ", "").strip()
            if not cleaned or cleaned == "-":
                return 0
            return int(float(cleaned))
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def _safe_float(value: str) -> float:
        """문자열을 안전하게 float로 변환한다."""
        try:
            cleaned = value.replace(",", "").replace(" ", "").strip()
            if not cleaned or cleaned == "-":
                return 0.0
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0


# ================================================================
# 테스트 실행
# ================================================================
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    client = KISClient()

    # 1) 토큰 확인
    print("=== 토큰 확인 ===")
    if client.check_token():
        print("✅ 토큰 유효")
    else:
        print("❌ 토큰 발급 실패 (.env 설정을 확인하세요)")
        exit(1)

    # 2) 삼성전자 현재가 조회
    print("\n=== 삼성전자 현재가 ===")
    price = client.get_stock_price("005930")
    for key, val in price.items():
        print(f"  {key}: {val}")

    # 3) 삼성전자 일봉 차트 (최근 10일)
    print("\n=== 삼성전자 일봉 (최근 10일) ===")
    chart = client.get_daily_chart("005930", days=10)
    for candle in chart[:5]:
        print(f"  {candle['date']}: 시{candle['open']} 고{candle['high']} "
              f"저{candle['low']} 종{candle['close']} 량{candle['volume']:,}")
