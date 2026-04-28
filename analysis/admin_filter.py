"""관리종목·거래정지·정리매매 종목 필터.

KIS API `inquire-price` (FHKST01010100) 응답에서 추출한 상태 코드를 기반으로
거래 불가 또는 정리 단계 종목을 분석에서 제외한다.

판정 기준 (보수적 — 명확한 거래 불가/정리 단계만 제외):
  - iscd_stat_cls_code == '51'   → 관리종목
  - iscd_stat_cls_code == '58'   → 거래정지
  - mang_issu_cls_code == 'Y'    → 관리종목 플래그
  - temp_stop_yn       == 'Y'    → 임시정지
  - sltr_yn            == 'Y'    → 정리매매

투자주의/경고/위험 (52/53/54), 단기과열은 분석 유지 (false positive 위험).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# 판정
# ----------------------------------------------------------------------
ADMIN_ISCD_CODES = {"51", "58"}


def is_admin_or_suspended(stock: dict[str, Any]) -> tuple[bool, str]:
    """단일 종목이 제외 대상인지 판정한다.

    Args:
        stock: KIS API `_parse_stock_price` 결과 dict.
               admin_status 필드가 없으면 정상으로 간주 (fail-open).

    Returns:
        (excluded, reason). excluded=False면 reason은 빈 문자열.
    """
    iscd = (stock.get("iscd_stat_cls_code") or "").strip()
    mang = (stock.get("mang_issu_cls_code") or "").strip().upper()
    temp = (stock.get("temp_stop_yn") or "").strip().upper()
    sltr = (stock.get("sltr_yn") or "").strip().upper()

    if iscd == "51" or mang == "Y":
        return True, "관리종목"
    if iscd == "58":
        return True, "거래정지"
    if temp == "Y":
        return True, "임시정지"
    if sltr == "Y":
        return True, "정리매매"
    return False, ""


def filter_admin_stocks(
    price_list: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """관리종목·거래정지를 분석 대상에서 제외한다.

    Args:
        price_list: KIS price_list (admin_status 필드 포함).

    Returns:
        (filtered_list, excluded_map):
          - filtered_list: 통과한 종목 리스트
          - excluded_map: {reason → [stock_code, ...]} 로깅/감사용
    """
    filtered: list[dict[str, Any]] = []
    excluded: dict[str, list[str]] = {}

    for stock in price_list:
        excluded_flag, reason = is_admin_or_suspended(stock)
        if excluded_flag:
            excluded.setdefault(reason, []).append(
                stock.get("stock_code", "?")
            )
            continue
        filtered.append(stock)

    if excluded:
        total = sum(len(v) for v in excluded.values())
        breakdown = ", ".join(
            f"{reason} {len(codes)}건" for reason, codes in excluded.items()
        )
        logger.info(
            "admin filter: %d → %d 종목 (제외 %d건; %s)",
            len(price_list), len(filtered), total, breakdown,
        )
        for reason, codes in excluded.items():
            logger.info("admin filter [%s]: %s", reason, ", ".join(codes))

    return filtered, excluded
