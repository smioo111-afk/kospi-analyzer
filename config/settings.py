"""
KOSPI 저평가 기업 분석 시스템 - 설정값 모듈 (v3.0)

v3.0 변경사항:
  - 5개 신규 지표 추가: PEG, 52주 위치, EV/EBITDA, FCF, PSR
  - 100점 배분 재조정
  - 순위 변동 추적 설정 추가
"""

import os
from pathlib import Path
from dotenv import load_dotenv

_project_root = Path(__file__).parent.parent
_env_path = _project_root / "config" / ".env"
if _env_path.exists():
    load_dotenv(_env_path)
else:
    load_dotenv(_project_root / ".env")


class KISConfig:
    APP_KEY: str = os.getenv("KIS_APP_KEY", "")
    APP_SECRET: str = os.getenv("KIS_APP_SECRET", "")
    ACCOUNT_NO: str = os.getenv("KIS_ACCOUNT_NO", "")
    BASE_URL: str = os.getenv("KIS_BASE_URL", "https://openapivts.koreainvestment.com:29443")
    IS_PAPER_TRADING: bool = os.getenv("KIS_IS_PAPER_TRADING", "true").lower() == "true"
    # async 클라이언트(v4.0)에서 사용하는 초당 허용 호출 수.
    # KIS 공식 한도는 초당 20. 안전 마진 25% 확보 위해 기본 15.
    # 환경변수로 롤백/튜닝 가능: KIS_RATE_LIMIT_PER_SEC
    RATE_LIMIT_PER_SEC: int = int(os.getenv("KIS_RATE_LIMIT_PER_SEC", "15"))
    # [DEPRECATED] sync 동기 클라이언트 시절 호출 간격(초). v4.0 async 전환 후
    # 직접 사용되지 않으나, 비상 rollback 경로에서 의미가 생길 수 있어 보존.
    RATE_LIMIT_INTERVAL: float = 0.5
    TOKEN_REFRESH_BUFFER: int = 3600
    MAX_RETRIES: int = 3
    RETRY_BACKOFF_BASE: float = 2.0


class DARTConfig:
    API_KEY: str = os.getenv("DART_API_KEY", "")
    BASE_URL: str = "https://opendart.fss.or.kr/api"
    DAILY_CALL_LIMIT: int = 9000
    CACHE_DAYS: int = 90


class TelegramConfig:
    BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
    ERROR_CHAT_ID: str = os.getenv("TELEGRAM_ERROR_CHAT_ID", "")
    MAX_MESSAGE_LENGTH: int = 4000

    # 명령어 핸들러 화이트리스트. 콤마 구분된 chat_id 문자열을 set으로 파싱.
    # 미설정/공백이면 CHAT_ID(주 사용자)만 허용 (single-user 운영 기본값).
    @classmethod
    def allowed_chat_ids(cls) -> set[str]:
        raw = os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "").strip()
        if not raw:
            return {cls.CHAT_ID} if cls.CHAT_ID else set()
        return {x.strip() for x in raw.split(",") if x.strip()}


class ScoringConfig:
    """종합 스코어링 기준 100점 만점 (v3.1).

    가치(20) + 재무(20) + 성장성(20) + 모멘텀(30) + 퀄리티(10) = 100

    v3.1 변경:
      - 가치 30→20, 모멘텀 20→30 (떨어지는 칼날 차단 + 추세 강화)
      - 하위 항목 max 점수 비례 축소/확대

    v3.0 신규 지표:
      가치: PEG, EV/EBITDA, PSR 추가
      모멘텀: 52주 위치 추가
      퀄리티: FCF 수익률 + FCF 마진 신설
    """

    # === 카테고리 비중 (v3.1) ===
    WEIGHT_VALUE: int = 20
    WEIGHT_FINANCIAL: int = 20
    WEIGHT_GROWTH: int = 20
    WEIGHT_MOMENTUM: int = 30
    WEIGHT_QUALITY: int = 10

    # =========================================================
    # 가치투자 (20점, v3.1)
    # PER(4) + PBR(3) + 배당(2) + 업종상대PER(3) + PEG(3) + EV/EBITDA(3) + PSR(2)
    # =========================================================

    PER_MAX_SCORE: int = 4
    PER_THRESHOLDS: list[tuple[float, int]] = [
        (5.0, 4), (10.0, 3), (15.0, 2), (25.0, 1),
    ]
    PER_DEFAULT_SCORE: int = 0

    PBR_MAX_SCORE: int = 3
    PBR_THRESHOLDS: list[tuple[float, int]] = [
        (0.5, 3), (0.8, 2), (1.0, 1),
    ]
    PBR_DEFAULT_SCORE: int = 0

    DIVIDEND_MAX_SCORE: int = 2
    DIVIDEND_THRESHOLDS: list[tuple[float, int]] = [
        (5.0, 2), (2.0, 1),
    ]
    DIVIDEND_DEFAULT_SCORE: int = 0

    SECTOR_PER_MAX_SCORE: int = 3
    SECTOR_PER_THRESHOLDS: list[tuple[float, int]] = [
        (0.5, 3), (0.8, 2), (1.0, 1),
    ]
    SECTOR_PER_DEFAULT_SCORE: int = 0

    # KIS API 'bstp_kor_isnm' 필드와 직접 매칭되는 KOSPI 표준산업분류 키.
    # 실제 KIS API 응답에서 추출한 정식 키 19개 + 폴백 '기타' = 20개.
    # 사용자 제공 평균값을 KIS 정식 키로 매핑함.
    SECTOR_AVG_PER: dict[str, float] = {
        "전기·전자": 18.0,
        "운송장비·부품": 10.0,
        "금융": 6.0,
        "보험": 8.0,            # 2차 probe에서 발견 (KIS는 금융과 분리)
        "증권": 15.0,           # 2차 probe에서 발견 (KIS는 금융과 분리)
        "음식료·담배": 15.0,    # was 음식료품
        "화학": 12.0,
        "제약": 25.0,           # was 의약품
        "금속": 8.0,            # was 철강금속
        "기계·장비": 12.0,      # was 기계
        "의료·정밀기기": 20.0,  # was 의료정밀
        "비금속": 10.0,         # was 비금속광물
        "섬유·의류": 10.0,      # was 섬유의복
        "종이·목재": 10.0,      # was 종이목재
        "유통": 12.0,           # was 유통업
        "전기·가스": 8.0,       # was 전기가스업
        "건설": 8.0,            # was 건설업
        "운송·창고": 10.0,      # was 운수창고업
        "통신": 10.0,           # was 통신업
        "IT 서비스": 15.0,      # was 서비스업 (KIS는 IT와 일반 서비스 분리)
        "일반서비스": 15.0,     # 2차 probe에서 발견
        "오락·문화": 15.0,      # 신규 발견 (강원랜드 등 엔터)
        "인프라투용": 10.0,     # 2차 probe에서 발견 (인프라 투자/리츠)
        "기타": 12.0,
    }
    DEFAULT_SECTOR_PER: float = 12.0

    # 적정주가 복합 모델용 - 업종 평균 PBR (BPS 모델)
    SECTOR_AVG_PBR: dict[str, float] = {
        "전기·전자": 2.0,
        "운송장비·부품": 0.8,
        "금융": 0.5,
        "보험": 0.6,
        "증권": 1.3,
        "음식료·담배": 1.5,
        "화학": 0.8,
        "제약": 3.0,
        "금속": 0.5,
        "기계·장비": 1.0,
        "의료·정밀기기": 2.5,
        "비금속": 0.6,
        "섬유·의류": 0.7,
        "종이·목재": 0.6,
        "유통": 0.6,
        "전기·가스": 0.4,
        "건설": 0.6,
        "운송·창고": 0.7,
        "통신": 0.8,
        "IT 서비스": 1.5,
        "일반서비스": 1.5,
        "오락·문화": 1.5,
        "인프라투용": 0.7,
        "기타": 1.0,
    }
    DEFAULT_SECTOR_PBR: float = 1.0

    # 적정주가 복합 모델용 - 업종 평균 EV/EBITDA 배수 (EV/EBITDA 모델)
    SECTOR_AVG_EV_EBITDA: dict[str, float] = {
        "전기·전자": 12.0,
        "운송장비·부품": 8.0,
        "금융": 10.0,
        "보험": 8.0,
        "증권": 10.0,
        "음식료·담배": 12.0,
        "화학": 8.0,
        "제약": 18.0,
        "금속": 6.0,
        "기계·장비": 9.0,
        "의료·정밀기기": 15.0,
        "비금속": 7.0,
        "섬유·의류": 8.0,
        "종이·목재": 7.0,
        "유통": 9.0,
        "전기·가스": 7.0,
        "건설": 7.0,
        "운송·창고": 8.0,
        "통신": 8.0,
        "IT 서비스": 12.0,
        "일반서비스": 12.0,
        "오락·문화": 12.0,
        "인프라투용": 8.0,
        "기타": 10.0,
    }
    DEFAULT_SECTOR_EV_EBITDA: float = 10.0

    # EV/EBITDA 모델을 적용하지 않는 업종 (적정주가 모델3 자동 스킵).
    # 금융/보험/증권은 영업이익·EBITDA 개념이 일반 제조업과 달라
    # EV/EBITDA 멀티플이 의미를 갖지 않는다.
    # _calc_fair_value의 모델3이 자동으로 스킵되고 모델1·모델2의
    # 가중치가 재배분된다 (40+30 → 0.571:0.429).
    EV_EBITDA_EXCLUDED_SECTORS: set[str] = {"금융", "보험", "증권"}

    # --- PEG (3점, v3.1) ---
    # PEG = PER / 이익성장률. 1.0 이하면 성장 대비 저평가
    PEG_MAX_SCORE: int = 3
    PEG_THRESHOLDS: list[tuple[float, int]] = [
        (0.5, 3),    # PEG < 0.5: 극단적 저평가 성장주
        (1.0, 2),    # PEG 1.0: 적정
        (1.5, 1),
    ]
    PEG_DEFAULT_SCORE: int = 0

    # --- EV/EBITDA (3점, v3.1) ---
    # 기업가치 대비 영업현금흐름. 낮을수록 저평가
    EV_EBITDA_MAX_SCORE: int = 3
    EV_EBITDA_THRESHOLDS: list[tuple[float, int]] = [
        (4.0, 3),
        (8.0, 2),
        (12.0, 1),
    ]
    EV_EBITDA_DEFAULT_SCORE: int = 0

    # --- PSR (2점, v3.1) ---
    # 시총/매출액. 적자기업도 평가 가능. 낮을수록 저평가
    PSR_MAX_SCORE: int = 2
    PSR_THRESHOLDS: list[tuple[float, int]] = [
        (0.5, 2),
        (1.0, 1),
    ]
    PSR_DEFAULT_SCORE: int = 0

    # =========================================================
    # 재무건전성 (20점)
    # ROE(5) + 영업이익률(5) + 부채비율(5) + 유동비율(5)
    # =========================================================

    ROE_MAX_SCORE: int = 5
    ROE_THRESHOLDS: list[tuple[float, int]] = [
        (15.0, 5), (10.0, 4), (5.0, 2), (0.0, 1),
    ]
    ROE_DEFAULT_SCORE: int = 0

    OPR_MARGIN_MAX_SCORE: int = 5
    OPR_MARGIN_THRESHOLDS: list[tuple[float, int]] = [
        (15.0, 5), (10.0, 4), (5.0, 2), (0.0, 1),
    ]
    OPR_MARGIN_DEFAULT_SCORE: int = 0

    DEBT_RATIO_MAX_SCORE: int = 5
    DEBT_RATIO_THRESHOLDS: list[tuple[float, int]] = [
        (50.0, 5), (100.0, 3), (200.0, 1),
    ]
    DEBT_RATIO_DEFAULT_SCORE: int = 0

    CURRENT_RATIO_MAX_SCORE: int = 5
    CURRENT_RATIO_THRESHOLDS: list[tuple[float, int]] = [
        (200.0, 5), (150.0, 3), (100.0, 1),
    ]
    CURRENT_RATIO_DEFAULT_SCORE: int = 0

    # =========================================================
    # 성장성 (20점) - 이익 감소 냉정 평가
    # 매출성장률(7) + 영업이익성장률(7) + 이익건전성(6)
    # =========================================================

    REVENUE_GROWTH_MAX_SCORE: int = 7
    REVENUE_GROWTH_THRESHOLDS: list[tuple[float, int]] = [
        (20.0, 7), (10.0, 5), (5.0, 4), (0.0, 2), (-10.0, 1), (-20.0, 0),
    ]
    REVENUE_GROWTH_DEFAULT_SCORE: int = 0

    OP_INCOME_GROWTH_MAX_SCORE: int = 7
    OP_INCOME_GROWTH_THRESHOLDS: list[tuple[float, int]] = [
        (30.0, 7), (15.0, 5), (5.0, 4), (0.0, 2), (-15.0, 0),
    ]
    OP_INCOME_GROWTH_DEFAULT_SCORE: int = 0

    # 이익건전성 6점 → 3점으로 축소 (나머지 3점은 턴어라운드로 분할)
    PROFIT_HEALTH_BASE_SCORE: int = 3
    PROFIT_HEALTH_MAX_SCORE: int = 3
    PROFIT_PENALTY_RULES: dict[str, int] = {
        "healthy": 3, "slight_decline": 2, "significant_decline": 1,
        "severe_decline": 0, "loss_turnaround": 0,
        "consecutive_decline_extra": -2, "consecutive_loss_extra": -3,
    }

    # === 턴어라운드 (3점, 신설) ===
    # 4기간 영업이익 흐름으로 방향 전환을 채점
    TURNAROUND_MAX_SCORE: int = 3
    TURNAROUND_SCORES: dict[str, int] = {
        "loss_to_profit": 3,     # 적자→흑자 전환
        "decline_to_growth": 2,  # 감소→증가 전환
        "continuous_growth": 1,  # 2년 연속 증가
        "declining": 0,          # 지속 감소
        "no_data": 0,            # 데이터 부족
    }

    TOTAL_SCORE_PENALTIES: dict[str, int] = {
        "3yr_revenue_decline": -5, "profit_to_loss": -8, "3yr_consecutive_loss": -15,
    }

    # =========================================================
    # 모멘텀 (30점, v3.1)
    # 20MA(3) + 60MA(2) + 거래량(3) + RSI(4) + MACD(3) + 수급(12) + 52주위치(3)
    # =========================================================

    MA20_SCORES: dict[str, int] = {
        "strong_up": 3, "above": 2, "bounce": 1, "down": 0,
    }
    MA60_SCORES: dict[str, int] = {
        "golden_cross": 2, "above": 2, "bounce": 1, "down": 0,
    }
    VOLUME_SCORES: dict[str, int] = {
        "surge": 3, "increase": 2, "normal": 1, "decrease": 0,
    }
    VOLUME_SURGE_RATIO: float = 1.5
    VOLUME_INCREASE_RATIO: float = 1.2

    RSI_PERIOD: int = 14
    RSI_SCORES: dict[str, int] = {
        "oversold_bounce": 4, "healthy_up": 4, "neutral": 3,
        "strong_but_ok": 1, "overbought": 0,
    }
    MACD_SCORES: dict[str, int] = {
        "bullish_cross": 3, "bullish": 2, "bearish": 0, "bearish_cross": 0,
    }

    # === 수급 (총 12점, v3.1) - 외국인/기관 5일·20일 분리 채점 ===
    # 외국인 5일(max 5) + 외국인 20일(max 4) + 기관 5일(max 2) + 기관 20일(max 1) = 12
    SUPPLY_DEMAND_MAX_SCORE: int = 12
    SUPPLY_DEMAND_SCORES: dict[str, int] = {
        "foreign_5d_streak_3": 3,    # 외국인 5일 연속 순매수 3일 이상
        "foreign_5d_streak_5": 5,    # 외국인 5일 연속 순매수 5일 이상
        "foreign_20d_positive": 3,   # 외국인 20일 누적 양수
        "foreign_20d_strong": 4,     # 외국인 20일 중 10일 이상 매수
        "inst_5d_streak_3": 2,       # 기관 5일 연속 3일 이상
        "inst_20d_positive": 1,      # 기관 20일 누적 양수
    }

    # --- 52주 고저 대비 위치 (3점, v3.1) ---
    WEEK52_SCORES: dict[str, int] = {
        "near_low": 3,        # 52주 최저 근처 (하위 20%) → 과매도 반등 기회
        "lower_half": 2,      # 하위 20~50%
        "upper_half": 1,      # 상위 50~80%
        "near_high": 0,       # 52주 최고 근처 (상위 20%) → 추가 상승 제한
    }

    # =========================================================
    # 퀄리티 (10점, 신설)
    # FCF 수익률(5) + FCF 마진(5)
    # =========================================================

    # FCF 수익률 = FCF / 시가총액 (높을수록 현금 창출력 대비 저평가)
    FCF_YIELD_MAX_SCORE: int = 5
    FCF_YIELD_THRESHOLDS: list[tuple[float, int]] = [
        (10.0, 5),    # FCF 수익률 10%↑: 극단적 저평가
        (7.0, 4),
        (5.0, 3),
        (3.0, 2),
        (1.0, 1),
    ]
    FCF_YIELD_DEFAULT_SCORE: int = 0

    # FCF 마진 = FCF / 매출액 (높을수록 현금 창출 효율성 높음)
    FCF_MARGIN_MAX_SCORE: int = 5
    FCF_MARGIN_THRESHOLDS: list[tuple[float, int]] = [
        (15.0, 5),
        (10.0, 4),
        (5.0, 3),
        (0.0, 1),     # FCF 양수이면 최소 1점
    ]
    FCF_MARGIN_DEFAULT_SCORE: int = 0   # FCF 음수: 0점


class SignalConfig:
    # v3.1: 모멘텀 max 20→30 가중 변경에 따라 momentum 임계 비례 상향
    # (STRONG_BUY_MOMENTUM 10→15: 50% 유지, SELL_MOMENTUM_MIN 4→6: 20% 유지)
    STRONG_BUY_SCORE: int = 75
    STRONG_BUY_MOMENTUM: int = 15
    STRONG_BUY_FINANCIAL: int = 12
    STRONG_BUY_GROWTH: int = 10

    BUY_SCORE_MIN: int = 60
    BUY_SCORE_MAX: int = 74
    BUY_FINANCIAL_MIN: int = 10

    HOLD_SCORE_MIN: int = 45
    HOLD_SCORE_MAX: int = 59

    SELL_SCORE: int = 45
    SELL_MOMENTUM_MIN: int = 6

    MIN_MARKET_CAP: int = 500_000_000_000
    MIN_TRADING_VALUE: int = 5_000_000_000
    EXCLUDE_CONSECUTIVE_LOSS_YEARS: int = 3
    FINANCIAL_SECTOR_CODES: list[str] = ["0500", "0600", "0700"]
    TOP_N: int = 10

    # v3.1: 60일선 추세 필터 (떨어지는 칼날 차단)
    # 현재가 < 60MA × (1 - MA60_FILTER_BUFFER_PCT/100) 종목 제외.
    # chart_data 부족 시 (60일 미만) 필터 통과 (보수적).
    MA60_FILTER_BUFFER_PCT: float = 3.0


class StopLossConfig:
    ATR_PERIOD: int = 14
    ATR_MULTIPLIER: float = 2.0
    ATR_MULTIPLIER_MIN: float = 1.0
    ATR_MULTIPLIER_MAX: float = 3.0
    ATR_PROFILES: dict[str, float] = {"aggressive": 1.5, "conservative": 2.0, "safe": 3.0}
    HARD_STOP_LOSS_PCT: float = -7.0
    MA60_BREAK_WARNING: bool = True
    CONSECUTIVE_DOWN_DAYS: int = 3


class SchedulerConfig:
    TOKEN_CHECK_HOUR: int = 15
    TOKEN_CHECK_MINUTE: int = 35
    DATA_COLLECT_HOUR: int = 15
    DATA_COLLECT_MINUTE: int = 40
    ANALYSIS_HOUR: int = 15
    ANALYSIS_MINUTE: int = 55
    REPORT_HOUR: int = 16
    REPORT_MINUTE: int = 0
    SEND_HOUR: int = 16
    SEND_MINUTE: int = 5
    DB_SAVE_HOUR: int = 16
    DB_SAVE_MINUTE: int = 10
    TIMEZONE: str = os.getenv("TIMEZONE", "Asia/Seoul")


class DBConfig:
    DB_PATH: str = os.getenv("DB_PATH", "data/kospi_analyzer.db")
    HISTORY_RETENTION_DAYS: int = 365


class LogConfig:
    LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    FORMAT: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    DATE_FORMAT: str = "%Y-%m-%d %H:%M:%S"
    LOG_DIR: str = "logs"
    MAX_FILE_SIZE_MB: int = 10
    BACKUP_COUNT: int = 5


# ----------------------------------------------------------------------
# SEC-4: 필수 환경변수 fail-fast 검증
# ----------------------------------------------------------------------
# 봇/파이프라인 시작 직후 호출. 운영 필수 키가 비어 있으면 즉시 종료시켜
# silent fail (예: KIS 토큰 없이 401 무한 재시도, 텔레그램 발송 실패 누적)
# 을 차단한다. 클래스 속성은 모듈 import 시점에 os.getenv("..", "")로
# 캡처되므로, 이 검증도 환경변수가 아닌 캡처된 값(클래스 속성)을 본다.
REQUIRED_ENV_VARS: tuple[tuple[str, str], ...] = (
    ("KIS_APP_KEY", "KIS Open API 앱키"),
    ("KIS_APP_SECRET", "KIS Open API 앱시크릿"),
    ("DART_API_KEY", "DART Open API 키"),
    ("TELEGRAM_BOT_TOKEN", "텔레그램 봇 토큰"),
    ("TELEGRAM_CHAT_ID", "텔레그램 채팅 ID"),
)


def validate_required_env() -> None:
    """필수 환경변수가 모두 채워졌는지 확인. 비면 EnvironmentError 발생.

    config/.env (또는 .env)는 settings.py 모듈 로드 시점에 load_dotenv로
    이미 적용된다. 이 함수는 그 이후 실제로 채워진 값만 통과시킨다.
    공백만 있는 값(`"   "`)도 누락으로 본다.
    """
    missing: list[str] = []
    for key, desc in REQUIRED_ENV_VARS:
        val = os.getenv(key, "")
        if not val or not val.strip():
            missing.append(f"{key} ({desc})")
    if missing:
        raise EnvironmentError(
            "필수 환경변수 누락 — 시작할 수 없습니다:\n  - "
            + "\n  - ".join(missing)
            + "\nconfig/.env 또는 .env 파일을 확인하세요."
        )
