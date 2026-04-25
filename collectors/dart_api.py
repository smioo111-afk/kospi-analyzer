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
    def extract_financial_metrics(
        self, stock_code: str, year: Optional[int] = None
    ) -> dict[str, Any]:
        """재무제표에서 분석에 필요한 지표를 추출한다 (v2.0).

        v2.0 추가: 매출/이익 성장률, 전년도 대비, 연속 감소 연수

        Args:
            stock_code: 종목코드
            year: 사업연도

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

        # === 당기 재무제표 ===
        metrics["revenue"] = self._get_account_value(
            df, "IS", ["매출액", "매출", "수익(매출액)", "영업수익"]
        )
        metrics["operating_income"] = self._get_account_value(
            df, "IS", ["영업이익", "영업이익(손실)", "영업손익"]
        )
        metrics["net_income"] = self._get_account_value(
            df, "IS", ["당기순이익", "당기순이익(손실)"]
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
        operating_cf = self._get_account_value(
            df, "CF", ["영업활동현금흐름", "영업활동으로인한현금흐름"]
        )
        capex = abs(self._get_account_value(
            df, "CF", ["유형자산의취득", "유형자산취득",
                       "투자활동으로인한유형자산취득"]
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
            prev_rev = self._get_account_value(prev_df, "IS", ["매출액", "매출", "수익(매출액)", "영업수익"])
            prev_op = self._get_account_value(prev_df, "IS", ["영업이익", "영업이익(손실)", "영업손익"])
            prev_net = self._get_account_value(prev_df, "IS", ["당기순이익", "당기순이익(손실)"])

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
            "operating_income": ["영업이익", "영업이익(손실)", "영업손익"],
            "revenue": ["매출액", "매출", "수익(매출액)", "영업수익"],
        }
        names = account_names.get(metric, ["매출액"])

        decline_count = 0
        for y in range(year, year - 3, -1):
            curr_df = self.get_financial_statements(stock_code, y)
            prev_df = self.get_financial_statements(stock_code, y - 1)
            if curr_df is None or prev_df is None:
                break

            curr_val = self._get_account_value(curr_df, "IS", names)
            prev_val = self._get_account_value(prev_df, "IS", names)

            if curr_val < prev_val:
                decline_count += 1
            else:
                break

        return decline_count

    def _get_dividend_yield(self, stock_code: str, year: int) -> float:
        """DART 배당 API에서 배당수익률을 조회한다."""
        corp_code = self.get_corp_code(stock_code)
        if not corp_code:
            return 0.0

        url = f"{DARTConfig.BASE_URL}/alotMatter.json"
        params = {
            "crtfc_key": DARTConfig.API_KEY,
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": "11011",  # 사업보고서
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
                    return self._parse_float(val)

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

    def _get_account_value(
        self,
        df: pd.DataFrame,
        sj_div: str,
        account_names: list[str],
    ) -> int:
        """재무제표 DataFrame에서 특정 계정과목의 당기 금액을 추출한다.

        Args:
            df: 재무제표 DataFrame
            sj_div: 재무제표 구분 (BS, IS, CF 등)
            account_names: 계정과목명 후보 리스트

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
            # 1단계: 정확 일치 (e.g. "매출"이 "매출원가"에 매칭되는 것을 방지)
            for name in account_names:
                match = filtered[filtered["account_nm"] == name]
                if not match.empty:
                    return self._parse_amount(match.iloc[0][amount_col])
            # 2단계: 부분 일치 (e.g. "당기순이익" → "당기순이익(손실)")
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
                df, "IS", ["당기순이익", "당기순이익(손실)"]
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
        self, stock_codes: list[str], year: Optional[int] = None
    ) -> list[dict[str, Any]]:
        """여러 종목의 재무 지표를 배치 조회한다.

        Args:
            stock_codes: 종목코드 리스트
            year: 사업연도

        Returns:
            list[dict]: 종목별 재무 지표
        """
        results: list[dict[str, Any]] = []
        total = len(stock_codes)
        self._cache_hit = 0
        self._cache_miss = 0

        for idx, code in enumerate(stock_codes, 1):
            if self._api_call_count >= DARTConfig.DAILY_CALL_LIMIT:
                logger.warning("DART API 일일 호출 한도 도달 (%d)", self._api_call_count)
                break

            try:
                metrics = self.extract_financial_metrics(code, year)
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
