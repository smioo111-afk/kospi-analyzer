"""
KOSPI 저평가 기업 분석 시스템 - DART OpenAPI 연동 모듈

DART(전자공시시스템)에서 재무제표 데이터를 수집한다:
  - 기업 고유번호(corp_code) 매핑 (종목코드 → DART 고유번호)
  - 단일회사 전체 재무제표 조회 (fnlttSinglAcntAll)
  - 재무 지표 추출: ROE, 영업이익률, 부채비율, 유동비율, 배당수익률

설계서 섹션 3.2 참조
"""

import io
import logging
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from xml.etree import ElementTree

import pandas as pd
import requests

from config.settings import DARTConfig

logger = logging.getLogger(__name__)


class DARTAPIError(Exception):
    """DART API 호출 에러."""


# 은행/은행지주 화이트리스트 — sector='금융' 안에서 금융업 매출 합산을 적용한다.
# (sector='금융'에는 일반 지주회사도 포함되므로 정확한 분기 위해 코드로 식별.)
# TODO: 신규 종목 상장/지정 시 갱신 필요.
_BANK_HOLDING_CODES: frozenset[str] = frozenset({
    "055550",  # 신한지주
    "105560",  # KB금융
    "086790",  # 하나금융지주
    "316140",  # 우리금융지주
    "024110",  # 기업은행
    "279570",  # 케이뱅크
    "138040",  # 메리츠금융지주
    "139130",  # iM금융지주
    "175330",  # JB금융지주
    "071050",  # 한국금융지주
    "138930",  # BNK금융지주
})


class DARTClient:
    """DART OpenAPI 클라이언트.

    재무제표 데이터 수집 및 재무 지표 계산을 담당한다.
    일일 호출 한도(10,000회)를 관리하며, 분기별 캐싱을 지원한다.
    """

    # DART 보고서 코드
    REPORT_CODES = {
        "annual": "11011",      # 사업보고서
        "half": "11012",        # 반기보고서
        "q1": "11013",          # 1분기보고서
        "q3": "11014",          # 3분기보고서
    }

    def __init__(self) -> None:
        self._api_call_count: int = 0
        self._cache_hit: int = 0
        self._cache_miss: int = 0
        self._corp_code_map: dict[str, str] = {}  # 종목코드 → DART 고유번호
        self._cache_dir = Path("data/dart_cache")
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    # ================================================================
    # 기업 고유번호 매핑
    # ================================================================
    def load_corp_codes(self) -> dict[str, str]:
        """DART 기업 고유번호 매핑 테이블을 로드한다.

        종목코드(6자리) → DART corp_code(8자리) 매핑.
        DART API의 corpCode.xml 파일을 다운로드하여 파싱한다.

        Returns:
            dict: {종목코드: DART 고유번호}
        """
        if self._corp_code_map:
            return self._corp_code_map

        # 캐시 파일 확인
        cache_path = self._cache_dir / "corp_codes.csv"
        if cache_path.exists():
            age_days = (
                datetime.now() - datetime.fromtimestamp(cache_path.stat().st_mtime)
            ).days
            if age_days < 30:  # 30일 이내 캐시 사용
                return self._load_corp_codes_from_cache(cache_path)

        # DART API에서 다운로드
        return self._download_corp_codes(cache_path)

    def _download_corp_codes(self, cache_path: Path) -> dict[str, str]:
        """DART API에서 기업 고유번호 파일을 다운로드한다."""
        url = f"{DARTConfig.BASE_URL}/corpCode.xml"
        params = {"crtfc_key": DARTConfig.API_KEY}

        logger.info("DART 기업 고유번호 파일 다운로드 중...")
        self._increment_call_count()

        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
        except requests.RequestException as e:
            raise DARTAPIError(f"기업 고유번호 다운로드 실패: {e}") from e

        # ZIP 파일 해제 후 XML 파싱
        try:
            with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
                xml_name = zf.namelist()[0]
                xml_data = zf.read(xml_name)

            root = ElementTree.fromstring(xml_data)
            mapping: dict[str, str] = {}

            for corp in root.findall("list"):
                stock_code = corp.findtext("stock_code", "").strip()
                corp_code = corp.findtext("corp_code", "").strip()
                corp_name = corp.findtext("corp_name", "").strip()

                if stock_code and corp_code:
                    mapping[stock_code] = corp_code

            # 캐시 저장
            df = pd.DataFrame([
                {"stock_code": k, "corp_code": v}
                for k, v in mapping.items()
            ])
            df.to_csv(cache_path, index=False)

            self._corp_code_map = mapping
            logger.info("기업 고유번호 %d개 로드 완료", len(mapping))
            return mapping

        except (zipfile.BadZipFile, ElementTree.ParseError) as e:
            raise DARTAPIError(f"기업 고유번호 파싱 실패: {e}") from e

    def _load_corp_codes_from_cache(self, cache_path: Path) -> dict[str, str]:
        """캐시 파일에서 기업 고유번호를 로드한다."""
        try:
            df = pd.read_csv(cache_path, dtype=str)
            self._corp_code_map = dict(
                zip(df["stock_code"], df["corp_code"])
            )
            logger.info("캐시에서 기업 고유번호 %d개 로드", len(self._corp_code_map))
            return self._corp_code_map
        except Exception as e:
            logger.warning("캐시 로드 실패, 재다운로드: %s", e)
            return self._download_corp_codes(cache_path)

    def get_corp_code(self, stock_code: str) -> Optional[str]:
        """종목코드로 DART 고유번호를 조회한다.

        Args:
            stock_code: 종목코드 (예: "005930")

        Returns:
            str or None: DART 고유번호
        """
        if not self._corp_code_map:
            self.load_corp_codes()
        return self._corp_code_map.get(stock_code)

    # ================================================================
    # 재무제표 조회
    # ================================================================
    def get_financial_statements(
        self,
        stock_code: str,
        year: Optional[int] = None,
        report_type: str = "annual",
    ) -> Optional[pd.DataFrame]:
        """단일회사 전체 재무제표를 조회한다.

        DART API: fnlttSinglAcntAll

        Args:
            stock_code: 종목코드
            year: 사업연도 (기본: 전년도)
            report_type: 보고서 종류 ("annual", "half", "q1", "q3")

        Returns:
            DataFrame or None: 재무제표 데이터
        """
        corp_code = self.get_corp_code(stock_code)
        if not corp_code:
            logger.warning("종목 %s의 DART 고유번호를 찾을 수 없음", stock_code)
            return None

        if year is None:
            year = datetime.now().year - 1

        reprt_code = self.REPORT_CODES.get(report_type, "11011")

        # 캐시 확인
        cache_key = f"{stock_code}_{year}_{report_type}"
        cached = self._load_from_cache(cache_key)
        if cached is not None:
            self._cache_hit += 1
            return cached
        self._cache_miss += 1

        url = f"{DARTConfig.BASE_URL}/fnlttSinglAcntAll.json"
        params = {
            "crtfc_key": DARTConfig.API_KEY,
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": reprt_code,
            "fs_div": "CFS",  # 연결재무제표 우선
        }

        self._increment_call_count()
        time.sleep(0.2)  # DART API 부하 방지

        try:
            response = requests.get(url, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()

            status = data.get("status", "")
            if status == "013":
                # 연결재무제표 없으면 개별재무제표 시도
                params["fs_div"] = "OFS"
                self._increment_call_count()
                response = requests.get(url, params=params, timeout=15)
                data = response.json()
                status = data.get("status", "")

            if status != "000":
                msg = data.get("message", "알 수 없는 에러")
                logger.debug("종목 %s 재무제표 없음 (%s): %s", stock_code, year, msg)
                return None

            df = pd.DataFrame(data.get("list", []))
            self._save_to_cache(cache_key, df)
            return df

        except requests.RequestException as e:
            logger.warning("종목 %s 재무제표 조회 실패: %s", stock_code, e)
            return None

    # ================================================================
    # 재무 지표 추출
    # ================================================================
    def _calc_financial_revenue(
        self,
        df: pd.DataFrame,
        sector: Optional[str],
        stock_code: str,
    ) -> Optional[int]:
        """금융주(보험/증권/은행지주)의 매출은 단일 라인이 아닌 합산이다.

        반환값:
          - 합산 매출(int): sector가 금융업이고 합산이 0보다 클 때
          - None: 일반 종목 → 호출 측이 기본 룰 사용

        분기:
          - 보험: 보험수익 + 투자영업수익 (IFRS4 일반 손보)
                  실패 시 보험서비스수익 + 이자수익 + 수수료수익 (IFRS17 생보)
          - 증권: 영업수익 단일 (한화투자/미래에셋/키움 등 정상)
                  실패 시 수수료수익 + 이자수익 + 외환거래이익 (NH/삼성증권 등)
          - 금융 + BANK_HOLDING_CODES: 이자수익 + 수수료수익 + 보험수익(보험 자회사)
          - 금융 + BANK_HOLDING_CODES 외: None (일반 지주 회귀 방지)
          - 그 외 sector: None
        """
        if not sector:
            return None

        def exact(name: str) -> int:
            """sj_div=IS/CIS에서 account_nm 정확 일치 첫 행의 thstrm_amount."""
            sub = df[df["sj_div"].isin(["IS", "CIS"])]
            if "thstrm_amount" not in sub.columns:
                return 0
            m = sub[sub["account_nm"] == name]
            if m.empty:
                return 0
            return self._parse_amount(m.iloc[0]["thstrm_amount"])

        if sector == "보험":
            ifrs4 = exact("보험수익") + exact("투자영업수익")
            if ifrs4 > 0:
                return ifrs4
            # IFRS17 패턴 (예: 삼성생명)
            ifrs17 = (
                exact("보험서비스수익") + exact("이자수익") + exact("수수료수익")
            )
            return ifrs17 if ifrs17 > 0 else None

        if sector == "증권":
            op_rev = exact("영업수익")
            if op_rev > 0:
                return op_rev
            # NH투자증권/삼성증권 등: 영업수익 라벨 부재 시 합산
            comp = (
                exact("수수료수익")
                + exact("이자수익")
                + exact("외환거래이익")
            )
            return comp if comp > 0 else None

        if sector == "금융" and stock_code in _BANK_HOLDING_CODES:
            comp = (
                exact("이자수익")
                + exact("수수료수익")
                + exact("보험수익")  # 은행지주에 보험 자회사 있을 때
            )
            return comp if comp > 0 else None

        return None

    def extract_financial_metrics(
        self,
        stock_code: str,
        year: Optional[int] = None,
        sector: Optional[str] = None,
    ) -> dict[str, Any]:
        """재무제표에서 분석에 필요한 지표를 추출한다 (v2.0).

        v2.0 추가: 매출/이익 성장률, 전년도 대비, 연속 감소 연수
        v3.x 추가: sector 인자 — 금융주(보험/증권/은행지주) 매출 합산 분기.

        Args:
            stock_code: 종목코드
            year: 사업연도
            sector: 업종 라벨 (KIS bstp_kor_isnm). 보험/증권/금융이면
                    sector별 합산 룰로 매출을 산출한다. None이면 일반 룰.

        Returns:
            dict: 재무 지표 (성장률 포함)
        """
        if year is None:
            year = datetime.now().year - 1

        df = self.get_financial_statements(stock_code, year)
        if df is None or df.empty:
            return self._empty_metrics(stock_code, year)

        metrics: dict[str, Any] = {
            "stock_code": stock_code,
            "year": year,
        }

        # A1 Phase 0: 공시 추적 메타데이터.
        # DART parquet의 모든 행은 동일 보고서(rcept_no)에서 왔으므로
        # 첫 행의 rcept_no를 채택. rcept_dt는 rcept_no 앞 8자(YYYYMMDD).
        if "rcept_no" in df.columns and not df.empty:
            try:
                rcept_no = str(df["rcept_no"].iloc[0]).strip()
                if rcept_no and rcept_no.lower() not in ("none", "nan"):
                    metrics["rcept_no"] = rcept_no
                    if len(rcept_no) >= 8 and rcept_no[:8].isdigit():
                        metrics["rcept_dt"] = rcept_no[:8]
            except (IndexError, KeyError):
                pass

        # === 당기 재무제표 ===
        # sector 분기로 금융주 매출은 합산. 일반 종목/sector None은 기본 룰.
        # N1: 한글 account_nm 변형(공백/괄호) 광범위 → IFRS account_id
        # 우선 매칭. _get_account_value 1단계가 account_id로 정확 일치 시도.
        fin_rev = self._calc_financial_revenue(df, sector, stock_code)
        if fin_rev is not None:
            metrics["revenue"] = fin_rev
        else:
            metrics["revenue"] = self._get_account_value(
                df, "IS",
                ["매출액", "매출", "수익(매출액)", "영업수익"],
                account_ids=["ifrs-full_Revenue"],
            )
        metrics["operating_income"] = self._get_account_value(
            df, "IS",
            ["영업이익", "영업이익(손실)", "영업손익", "영업손실"],
            account_ids=[
                "dart_OperatingIncomeLoss",
                "ifrs-full_ProfitLossFromOperatingActivities",
            ],
        )
        metrics["net_income"] = self._get_account_value(
            df, "IS",
            ["당기순이익", "당기순이익(손실)"],
            account_ids=["ifrs-full_ProfitLoss"],
        )
        metrics["total_assets"] = self._get_account_value(df, "BS", ["자산총계"])
        metrics["total_liabilities"] = self._get_account_value(df, "BS", ["부채총계"])
        metrics["total_equity"] = self._get_account_value(df, "BS", ["자본총계"])
        metrics["current_assets"] = self._get_account_value(df, "BS", ["유동자산"])
        metrics["current_liabilities"] = self._get_account_value(df, "BS", ["유동부채"])

        # === 파생 지표 ===
        metrics["roe"] = self._calc_ratio(metrics["net_income"], metrics["total_equity"])
        metrics["operating_margin"] = self._calc_ratio(metrics["operating_income"], metrics["revenue"])
        metrics["debt_ratio"] = self._calc_ratio(metrics["total_liabilities"], metrics["total_equity"])
        metrics["current_ratio"] = self._calc_ratio(metrics["current_assets"], metrics["current_liabilities"])

        # === EBITDA, FCF (v3.0 신설) ===
        depreciation = self._get_account_value(
            df, "IS", ["감가상각비", "유무형자산상각비", "감가상각비와무형자산상각비"]
        )
        if depreciation == 0:
            # 현금흐름표에서 감가상각비 찾기
            depreciation = self._get_account_value(
                df, "CF", ["감가상각비", "유무형자산상각비"]
            )
        metrics["depreciation"] = depreciation
        metrics["ebitda"] = metrics["operating_income"] + abs(depreciation)

        # 현금및현금성자산
        metrics["cash_equivalents"] = self._get_account_value(
            df, "BS", ["현금및현금성자산", "현금및예치금"]
        )

        # 잉여현금흐름 (FCF = 영업활동현금흐름 - 자본적지출)
        # 한글 account_nm 변형(공백 등)이 광범위하므로 IFRS account_id 우선 매칭.
        operating_cf = self._get_account_value(
            df, "CF",
            ["영업활동현금흐름", "영업활동으로인한현금흐름"],
            account_ids=["ifrs-full_CashFlowsFromUsedInOperatingActivities"],
        )
        capex = abs(self._get_account_value(
            df, "CF",
            ["유형자산의취득", "유형자산취득", "투자활동으로인한유형자산취득"],
            account_ids=[
                "ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
                "ifrs-full_PurchaseOfPropertyPlantAndEquipment",
            ],
        ))
        if operating_cf != 0:
            metrics["free_cash_flow"] = operating_cf - capex
        else:
            metrics["free_cash_flow"] = 0
        metrics["operating_cash_flow"] = operating_cf
        metrics["capex"] = capex

        # === 전년도 데이터로 성장률 계산 (v2.0 신설) ===
        prev_df = self.get_financial_statements(stock_code, year - 1)
        if prev_df is not None and not prev_df.empty:
            prev_rev = self._get_account_value(
                prev_df, "IS",
                ["매출액", "매출", "수익(매출액)", "영업수익"],
                account_ids=["ifrs-full_Revenue"],
            )
            prev_op = self._get_account_value(
                prev_df, "IS",
                ["영업이익", "영업이익(손실)", "영업손익", "영업손실"],
                account_ids=[
                    "dart_OperatingIncomeLoss",
                    "ifrs-full_ProfitLossFromOperatingActivities",
                ],
            )
            prev_net = self._get_account_value(
                prev_df, "IS",
                ["당기순이익", "당기순이익(손실)"],
                account_ids=["ifrs-full_ProfitLoss"],
            )

            metrics["prev_revenue"] = prev_rev
            metrics["prev_operating_income"] = prev_op
            metrics["prev_net_income"] = prev_net

            # YoY 성장률 (%)
            metrics["revenue_growth_yoy"] = self._calc_growth_rate(metrics["revenue"], prev_rev)
            metrics["op_income_growth_yoy"] = self._calc_growth_rate(metrics["operating_income"], prev_op)
            metrics["net_income_growth_yoy"] = self._calc_growth_rate(metrics["net_income"], prev_net)
        else:
            metrics["prev_revenue"] = 0
            metrics["prev_operating_income"] = 0
            metrics["prev_net_income"] = 0
            metrics["revenue_growth_yoy"] = 0.0
            metrics["op_income_growth_yoy"] = 0.0
            metrics["net_income_growth_yoy"] = 0.0

        # === 배당수익률 (DART 배당 API) ===
        metrics["dividend_yield"] = self._get_dividend_yield(stock_code, year)

        # === 연속 적자/감소 연수 (v2.0) ===
        metrics["consecutive_loss_years"] = self._check_consecutive_losses(stock_code, year)
        metrics["consecutive_op_decline_years"] = self._check_consecutive_decline(
            stock_code, year, "operating_income"
        )
        metrics["consecutive_revenue_decline_years"] = self._check_consecutive_decline(
            stock_code, year, "revenue"
        )

        # 업종은 KIS API의 bstp_kor_isnm에서 별도로 주입됨 (main.py _collect_data)
        # DART는 업종 정보를 제공하지 않으므로 여기서 설정하지 않는다.

        return metrics

    @staticmethod
    def _calc_growth_rate(current: int, previous: int) -> float:
        """YoY 성장률을 계산한다 (%)."""
        if previous == 0:
            if current > 0:
                return 100.0  # 0→양수: 100% 성장
            elif current < 0:
                return -100.0
            return 0.0
        return round(((current - previous) / abs(previous)) * 100, 2)

    def _check_consecutive_decline(
        self, stock_code: str, year: int, metric: str
    ) -> int:
        """특정 지표의 연속 감소 연수를 확인한다.

        Args:
            metric: "operating_income" 또는 "revenue"
        """
        account_names = {
            "operating_income": ["영업이익", "영업이익(손실)", "영업손익", "영업손실"],
            "revenue": ["매출액", "매출", "수익(매출액)", "영업수익"],
        }
        # N1: IFRS account_id를 우선 매칭하여 한글 변형(공백/괄호) 누락 방지.
        account_ids_map = {
            "operating_income": [
                "dart_OperatingIncomeLoss",
                "ifrs-full_ProfitLossFromOperatingActivities",
            ],
            "revenue": ["ifrs-full_Revenue"],
        }
        names = account_names.get(metric, ["매출액"])
        ids = account_ids_map.get(metric)

        decline_count = 0
        for y in range(year, year - 3, -1):
            curr_df = self.get_financial_statements(stock_code, y)
            prev_df = self.get_financial_statements(stock_code, y - 1)
            if curr_df is None or prev_df is None:
                break

            curr_val = self._get_account_value(
                curr_df, "IS", names, account_ids=ids,
            )
            prev_val = self._get_account_value(
                prev_df, "IS", names, account_ids=ids,
            )

            if curr_val < prev_val:
                decline_count += 1
            else:
                break

        return decline_count

    def _get_dividend_yield(self, stock_code: str, year: int) -> float:
        """DART 배당 API에서 배당수익률을 조회한다.

        당해 사업보고서에 배당수익률이 미공시('-')이면 전년도(year-1)로
        한 번 폴백한다. 보험·증권·금융지주 등 결산기일·배당 의사결정이
        늦은 종목은 사업보고서 공시 시점에 배당 정보가 없는 경우가 흔하다.
        """
        v = self._fetch_dividend_yield_for_year(stock_code, year)
        if v > 0:
            return v
        # 당해 미공시 → 전년도로 1회 폴백
        prev = self._fetch_dividend_yield_for_year(stock_code, year - 1)
        return prev

    def _fetch_dividend_yield_for_year(
        self, stock_code: str, year: int,
    ) -> float:
        """단일 연도 alotMatter API 호출 — 현금배당수익률 추출."""
        corp_code = self.get_corp_code(stock_code)
        if not corp_code:
            return 0.0

        url = f"{DARTConfig.BASE_URL}/alotMatter.json"
        params = {
            "crtfc_key": DARTConfig.API_KEY,
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": "11011",
        }

        self._increment_call_count()
        try:
            response = requests.get(url, params=params, timeout=10)
            data = response.json()
            if data.get("status") != "000":
                return 0.0
            for item in data.get("list", []):
                if "배당수익률" in item.get("se", ""):
                    val = item.get("thstrm", "0")
                    parsed = self._parse_float(val)
                    if parsed > 0:
                        return parsed
            return 0.0
        except Exception:
            return 0.0

    @staticmethod
    def _parse_float(value: Any) -> float:
        """문자열을 float로 안전 변환."""
        if pd.isna(value) or value == "" or value == "-":
            return 0.0
        try:
            return float(str(value).replace(",", "").strip())
        except (ValueError, TypeError):
            return 0.0

    @staticmethod
    def _normalize_nm(name: str) -> str:
        # 공백·NBSP 제거: K-IFRS 회사마다 "영업활동현금흐름" vs
        # "영업활동으로 인한 현금흐름" 식의 공백 변형이 광범위.
        return str(name).replace(" ", "").replace(" ", "").strip()

    def _get_account_value(
        self,
        df: pd.DataFrame,
        sj_div: str,
        account_names: list[str],
        account_ids: Optional[list[str]] = None,
    ) -> int:
        """재무제표 DataFrame에서 특정 계정과목의 당기 금액을 추출한다.

        Args:
            df: 재무제표 DataFrame
            sj_div: 재무제표 구분 (BS, IS, CF 등)
            account_names: 계정과목명 후보 리스트
            account_ids: IFRS account_id 후보 리스트 (선택, 최우선 매칭)

        Returns:
            int: 금액 (원)
        """
        # K-IFRS 단일 포괄손익계산서(CIS)만 제출하는 기업이 다수이므로
        # IS 호출 시 CIS로 fallback. IS가 비어있지 않으면 IS 우선.
        candidates: list[str] = [sj_div]
        if sj_div == "IS":
            candidates.append("CIS")

        amount_col = "thstrm_amount"
        for div in candidates:
            filtered = df[df["sj_div"] == div]
            if filtered.empty:
                continue
            if amount_col not in filtered.columns:
                continue
            # 1단계: account_id 정확 일치 (IFRS 표준 코드, 회사간 동일)
            if account_ids and "account_id" in filtered.columns:
                for aid in account_ids:
                    match = filtered[filtered["account_id"] == aid]
                    if not match.empty:
                        return self._parse_amount(match.iloc[0][amount_col])
            # 2단계: account_nm 정확 일치 (e.g. "매출" vs "매출원가" 구분)
            for name in account_names:
                match = filtered[filtered["account_nm"] == name]
                if not match.empty:
                    return self._parse_amount(match.iloc[0][amount_col])
            # 3단계: 공백 정규화 후 정확 일치
            #   "영업활동으로 인한 현금흐름" → "영업활동으로인한현금흐름" 매칭
            normalized_col = filtered["account_nm"].fillna("").map(self._normalize_nm)
            for name in account_names:
                target = self._normalize_nm(name)
                match = filtered[normalized_col == target]
                if not match.empty:
                    return self._parse_amount(match.iloc[0][amount_col])
            # 4단계: 부분 일치 (e.g. "당기순이익" → "당기순이익(손실)")
            for name in account_names:
                match = filtered[
                    filtered["account_nm"].str.contains(name, na=False, regex=False)
                ]
                if not match.empty:
                    return self._parse_amount(match.iloc[0][amount_col])
        return 0

    @staticmethod
    def _parse_amount(value: Any) -> int:
        """금액 문자열을 int로 변환한다 (쉼표, 빈값 처리)."""
        if pd.isna(value) or value == "" or value == "-":
            return 0
        try:
            cleaned = str(value).replace(",", "").replace(" ", "").strip()
            return int(float(cleaned))
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def _calc_ratio(numerator: int, denominator: int) -> float:
        """비율을 계산한다 (0 나누기 방지, 퍼센트)."""
        if denominator == 0:
            return 0.0
        return round((numerator / denominator) * 100, 2)

    def _check_consecutive_losses(self, stock_code: str, year: int) -> int:
        """연속 적자 연수를 확인한다 (최대 3년 조회).

        설계서 5.2: 3년 연속 적자 기업 제외

        Args:
            stock_code: 종목코드
            year: 기준 사업연도

        Returns:
            int: 연속 적자 연수
        """
        loss_count = 0
        for y in range(year, year - 3, -1):
            df = self.get_financial_statements(stock_code, y)
            if df is None:
                break

            net_income = self._get_account_value(
                df, "IS",
                ["당기순이익", "당기순이익(손실)"],
                account_ids=["ifrs-full_ProfitLoss"],
            )
            if net_income < 0:
                loss_count += 1
            else:
                break

        return loss_count

    def _empty_metrics(self, stock_code: str, year: int) -> dict[str, Any]:
        """데이터 없는 경우의 기본 재무 지표를 반환한다."""
        return {
            "stock_code": stock_code, "year": year,
            "revenue": 0, "operating_income": 0, "net_income": 0,
            "total_assets": 0, "total_liabilities": 0, "total_equity": 0,
            "current_assets": 0, "current_liabilities": 0,
            "roe": 0.0, "operating_margin": 0.0, "debt_ratio": 0.0, "current_ratio": 0.0,
            "dividend_yield": 0.0,
            "prev_revenue": 0, "prev_operating_income": 0, "prev_net_income": 0,
            "revenue_growth_yoy": 0.0, "op_income_growth_yoy": 0.0, "net_income_growth_yoy": 0.0,
            "consecutive_loss_years": 0, "consecutive_op_decline_years": 0,
            "consecutive_revenue_decline_years": 0,
            "ebitda": 0, "depreciation": 0, "cash_equivalents": 0,
            "free_cash_flow": 0, "operating_cash_flow": 0, "capex": 0,
        }

    # ================================================================
    # 대량 배치 조회
    # ================================================================
    def get_all_financial_metrics(
        self,
        stock_codes: list[str],
        year: Optional[int] = None,
        sector_map: Optional[dict[str, str]] = None,
    ) -> list[dict[str, Any]]:
        """여러 종목의 재무 지표를 배치 조회한다.

        Args:
            stock_codes: 종목코드 리스트
            year: 사업연도
            sector_map: {stock_code: sector(KIS bstp_kor_isnm)}. 금융주(보험/
                        증권/은행지주)의 매출 합산 분기에 사용. None이면 일반 룰.

        Returns:
            list[dict]: 종목별 재무 지표
        """
        results: list[dict[str, Any]] = []
        total = len(stock_codes)
        self._cache_hit = 0
        self._cache_miss = 0
        sm = sector_map or {}

        for idx, code in enumerate(stock_codes, 1):
            if self._api_call_count >= DARTConfig.DAILY_CALL_LIMIT:
                logger.warning("DART API 일일 호출 한도 도달 (%d)", self._api_call_count)
                break

            try:
                metrics = self.extract_financial_metrics(
                    code, year, sector=sm.get(code),
                )
                results.append(metrics)

                if idx % 50 == 0:
                    hit_total = self._cache_hit + self._cache_miss
                    hit_rate = (self._cache_hit / hit_total * 100) if hit_total else 0
                    logger.info(
                        "재무 지표 수집 진행: %d/%d (API 호출: %d, 캐시 히트: %d/%d = %.0f%%)",
                        idx, total, self._api_call_count,
                        self._cache_hit, hit_total, hit_rate,
                    )

            except DARTAPIError as e:
                logger.warning("종목 %s 재무 지표 수집 실패: %s", code, e)
                results.append(self._empty_metrics(code, year or datetime.now().year - 1))

        hit_total = self._cache_hit + self._cache_miss
        hit_rate = (self._cache_hit / hit_total * 100) if hit_total else 0
        logger.info(
            "전체 %d/%d 종목 재무 지표 수집 완료 (API 호출: %d, 캐시 히트: %d/%d = %.0f%%)",
            len(results), total, self._api_call_count,
            self._cache_hit, hit_total, hit_rate,
        )
        return results

    # ================================================================
    # 캐싱
    # ================================================================
    def _save_to_cache(self, key: str, df: pd.DataFrame) -> None:
        """재무제표 데이터를 로컬에 캐싱한다."""
        try:
            path = self._cache_dir / f"{key}.parquet"
            df.to_parquet(path, index=False)
        except Exception as e:
            logger.warning("캐시 저장 실패 (%s): %s", key, e)

    def _load_from_cache(self, key: str) -> Optional[pd.DataFrame]:
        """캐시에서 재무제표 데이터를 로드한다."""
        path = self._cache_dir / f"{key}.parquet"
        if not path.exists():
            return None

        age_days = (
            datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
        ).days
        if age_days > DARTConfig.CACHE_DAYS:
            return None

        try:
            return pd.read_parquet(path)
        except Exception:
            return None

    def _increment_call_count(self) -> None:
        """API 호출 횟수를 증가시킨다."""
        self._api_call_count += 1
        if self._api_call_count % 100 == 0:
            logger.info("DART API 호출 횟수: %d", self._api_call_count)


# ================================================================
# 테스트 실행
# ================================================================
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    client = DARTClient()

    # 1) 기업 고유번호 로드
    print("=== 기업 고유번호 로드 ===")
    codes = client.load_corp_codes()
    print(f"  총 {len(codes)}개 기업 로드")
    print(f"  삼성전자: {codes.get('005930', 'N/A')}")

    # 2) 삼성전자 재무 지표
    print("\n=== 삼성전자 재무 지표 ===")
    metrics = client.extract_financial_metrics("005930")
    for key, val in metrics.items():
        print(f"  {key}: {val}")
