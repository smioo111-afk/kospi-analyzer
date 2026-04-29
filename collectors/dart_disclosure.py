"""DART 공시 수집 + 분류 모듈 (A1 Phase 1).

매일 정기 모니터(00:00) 및 backfill 도구가 사용한다. Phase 0.5의
DARTClient.fetch_disclosure_list 헬퍼를 그대로 활용해 외부 endpoint를
새로 추가하지 않는다.

분류 우선순위:
    AMENDMENT > PERIODIC > BUYBACK > DIVIDEND > MA > MAJOR > OTHER

needs_data_refresh가 True인 유형(PERIODIC, AMENDMENT, MA)은 다음
사이클에서 financial_metrics 재수집이 필요. 나머지는 알림만 발송하고
점수는 자연스럽게 다음 정기 사이클의 시세로 반영된다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from collectors.dart_api import DARTClient

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# 데이터 모델
# ----------------------------------------------------------------------
class DisclosureType(Enum):
    """공시 유형. classify_disclosure가 반환.

    PERIODIC은 연간 사업보고서만 포함한다. financial_metrics가 annual-only
    정책을 유지하기 때문에, 분기/반기는 별도 타입(QUARTERLY/HALFLY)으로
    두고 needs_data_refresh에서 제외한다 (알림만 발송).
    """

    PERIODIC = "periodic"      # 연간 사업보고서
    QUARTERLY = "quarterly"    # 분기보고서 (1Q, 3Q)
    HALFLY = "halfly"          # 반기보고서
    MAJOR = "major"            # 주요사항보고서 (분류 안 된 기타)
    AMENDMENT = "amendment"    # [기재정정] 등 정정공시
    DIVIDEND = "dividend"      # 배당 결정
    BUYBACK = "buyback"        # 자기주식 취득/소각
    MA = "ma"                  # 합병/분할/주식교환/영업양수도
    OTHER = "other"            # 분류되지 않은 공시


@dataclass
class Disclosure:
    """DART list.json의 단일 공시 항목."""

    rcept_no: str
    corp_code: str
    stock_code: str
    corp_name: str
    report_nm: str
    rcept_dt: str
    rm: str = ""

    @property
    def is_amendment(self) -> bool:
        """report_nm 또는 rm으로 정정공시 판별.

        DART는 정정공시에 '[기재정정]' / '[정정]' / '[첨부정정]' 등의
        접두어를 붙인다. rm 컬럼에는 '정' 코드가 들어올 수 있다.
        """
        nm = self.report_nm or ""
        rm = self.rm or ""
        if "정정" in nm:
            return True
        return "정" in rm


# ----------------------------------------------------------------------
# 분류
# ----------------------------------------------------------------------
# M1: PERIODIC은 연간 사업보고서만. 분기/반기는 별도 타입으로 두어
# annual-only financial_metrics 정책에 맞춰 재수집을 건너뛴다.
_BUYBACK_KEYWORDS = ("자기주식취득", "자기주식소각", "자사주")
_MA_KEYWORDS = ("합병", "분할", "주식교환", "영업양수도")
_DIVIDEND_KEYWORDS = ("배당", "주당배당금")


def classify_disclosure(d: Disclosure) -> DisclosureType:
    """공시 유형을 분류한다.

    우선순위:
      1) is_amendment → AMENDMENT (정정공시는 모든 분류를 덮어씀)
      2) HALFLY (반기) > QUARTERLY (분기) > PERIODIC (연간 사업)
         — '반기보고서'가 '사업보고서' 부분문자열을 포함하지 않으므로
         단순 순서로도 충돌 없으나, 명시적 분기로 가독성 확보.
      3) BUYBACK > DIVIDEND > MA > MAJOR > OTHER
    """
    if d.is_amendment:
        return DisclosureType.AMENDMENT

    name = d.report_nm or ""
    if "반기보고서" in name:
        return DisclosureType.HALFLY
    if "분기보고서" in name:
        return DisclosureType.QUARTERLY
    if "사업보고서" in name:
        return DisclosureType.PERIODIC
    if any(kw in name for kw in _BUYBACK_KEYWORDS):
        return DisclosureType.BUYBACK
    # 배당은 자사주 키워드와 겹치지 않으므로 BUYBACK 다음.
    if any(kw in name for kw in _DIVIDEND_KEYWORDS):
        return DisclosureType.DIVIDEND
    if any(kw in name for kw in _MA_KEYWORDS):
        return DisclosureType.MA
    if "주요사항보고서" in name:
        return DisclosureType.MAJOR
    return DisclosureType.OTHER


# financial_metrics 재수집이 필요한 유형 집합. needs_data_refresh가 참조.
# M1: QUARTERLY/HALFLY는 제외 — financial_metrics는 annual-only 저장 정책
# 이라 분기/반기 공시로 재수집해도 처리할 데이터가 맞지 않는다. 알림만
# 일일 리포트의 minor/info 섹션에 노출된다.
_REFRESH_REQUIRED_TYPES: frozenset[DisclosureType] = frozenset({
    DisclosureType.PERIODIC,
    DisclosureType.AMENDMENT,
    DisclosureType.MA,
})


def needs_data_refresh(d: Disclosure) -> bool:
    """financial_metrics 재수집 필요 여부.

    True: PERIODIC(연간 사업), AMENDMENT, MA — 재무제표/자본 구조 직접 영향.
    False: QUARTERLY, HALFLY, DIVIDEND, BUYBACK, MAJOR, OTHER — 알림만.
    """
    return classify_disclosure(d) in _REFRESH_REQUIRED_TYPES


# ----------------------------------------------------------------------
# 수집
# ----------------------------------------------------------------------
def _to_disclosure(item: dict) -> Disclosure:
    return Disclosure(
        rcept_no=str(item.get("rcept_no") or ""),
        corp_code=str(item.get("corp_code") or ""),
        stock_code=str(item.get("stock_code") or ""),
        corp_name=str(item.get("corp_name") or ""),
        report_nm=str(item.get("report_nm") or ""),
        rcept_dt=str(item.get("rcept_dt") or ""),
        rm=str(item.get("rm") or ""),
    )


def fetch_disclosures(
    date_from: str,
    date_to: str,
    corp_cls: str = "Y",
    analyzed_codes: Optional[set[str]] = None,
    client: Optional[DARTClient] = None,
) -> list[Disclosure]:
    """DART 공시 목록을 조회해 Disclosure 객체 리스트로 반환.

    Phase 0.5의 DARTClient.fetch_disclosure_list 헬퍼를 그대로 사용 — 새
    endpoint 추가 없음. KOSPI 기본(corp_cls='Y'). analyzed_codes를 주면
    해당 종목코드만 남기고 필터.

    Args:
        date_from: 시작일 YYYYMMDD
        date_to: 종료일 YYYYMMDD
        corp_cls: 'Y'(KOSPI), 'K'(KOSDAQ), 'N'(KONEX), 'E'(기타)
        analyzed_codes: 분석 종목 set (None이면 필터 없음)
        client: DARTClient 인스턴스 (테스트용 주입, 기본은 새로 생성)

    Returns:
        list[Disclosure]: 공시 항목. 빈 리스트면 결과 없음.
    """
    client = client or DARTClient()
    raw = client.fetch_disclosure_list(
        bgn_de=date_from, end_de=date_to, corp_cls=corp_cls,
    )
    items = [_to_disclosure(it) for it in raw]
    if analyzed_codes is not None:
        items = [d for d in items if d.stock_code in analyzed_codes]
    return items
