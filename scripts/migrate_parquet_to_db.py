"""
parquet 캐시 → financial_metrics DB 마이그레이션 스크립트.

data/dart_cache/*.parquet 파일에서 재무제표 원본 데이터를 읽어
DARTClient.extract_financial_metrics()로 지표를 추출한 뒤
DB financial_metrics 테이블에 저장한다.

사용법:
    cd ~/kospianal
    source venv/bin/activate
    PYTHONPATH=kospi-analyzer python kospi-analyzer/scripts/migrate_parquet_to_db.py
"""

import logging
import re
import sys
from collections import defaultdict
from pathlib import Path

# 프로젝트 루트를 path에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collectors.dart_api import DARTClient
from database.models import Database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def find_parquet_targets(cache_dir: Path) -> dict[str, set[int]]:
    """parquet 파일명에서 (stock_code, year) 쌍을 추출한다.

    파일명 형식: {stock_code}_{year}_{report_type}.parquet
    annual 보고서 기준으로 고유한 (stock_code, year) 세트를 반환.
    """
    pattern = re.compile(r"^(\w+)_(\d{4})_(\w+)\.parquet$")
    targets: dict[str, set[int]] = defaultdict(set)

    for f in cache_dir.glob("*.parquet"):
        m = pattern.match(f.name)
        if m:
            code, year, _ = m.groups()
            targets[code].add(int(year))

    return targets


def main() -> None:
    # parquet 캐시 경로 탐색
    candidates = [
        Path("data/dart_cache"),
        Path("kospi-analyzer/data/dart_cache"),
    ]
    cache_dir = None
    for c in candidates:
        if c.exists() and list(c.glob("*.parquet")):
            cache_dir = c
            break

    if cache_dir is None:
        logger.info("parquet 캐시 파일이 없습니다. 마이그레이션 불필요.")
        return

    parquet_count = len(list(cache_dir.glob("*.parquet")))
    logger.info("parquet 캐시 디렉토리: %s (%d 파일)", cache_dir, parquet_count)

    # (stock_code → {year, ...}) 매핑
    targets = find_parquet_targets(cache_dir)
    # 가장 최근 연도만 마이그레이션 (분석에 사용되는 연도)
    stock_years: list[tuple[str, int]] = []
    for code, years in targets.items():
        for y in years:
            stock_years.append((code, y))

    stock_years.sort()
    total = len(stock_years)
    logger.info("마이그레이션 대상: %d개 (stock_code, year) 쌍", total)

    # DARTClient (parquet 캐시가 아직 있으므로 get_financial_statements는 캐시 히트)
    dart = DARTClient()
    dart.load_corp_codes()

    db = Database()
    migrated = 0
    skipped = 0

    for idx, (code, year) in enumerate(stock_years, 1):
        # 이미 DB에 있으면 스킵
        existing = db.get_financial_metrics(code, year)
        if existing is not None:
            skipped += 1
            continue

        try:
            metrics = dart.extract_financial_metrics(code, year)
            metrics["quarter"] = "annual"
            db.save_financial_metrics(metrics)
            migrated += 1
        except Exception as e:
            logger.debug("마이그레이션 실패 %s/%d: %s", code, year, e)

        if idx % 50 == 0:
            logger.info(
                "마이그레이션 진행: %d/%d (저장: %d, 스킵: %d, API: %d)",
                idx, total, migrated, skipped, dart._api_call_count,
            )

    logger.info(
        "마이그레이션 완료: %d건 저장, %d건 스킵 (DART API: %d회)",
        migrated, skipped, dart._api_call_count,
    )

    # parquet 파일 정리
    deleted = 0
    for f in cache_dir.glob("*.parquet"):
        f.unlink()
        deleted += 1
    logger.info("parquet 캐시 %d 파일 삭제 완료", deleted)


if __name__ == "__main__":
    main()
