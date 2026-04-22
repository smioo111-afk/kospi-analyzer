# KOSPI 저평가 기업 분석 시스템

코스피 상장 기업 전체를 종합 분석하여 **저평가 기업 TOP 10**을 매일 자동으로 텔레그램에 알려주는 시스템입니다.

## 핵심 기능

- **100점 만점 종합 스코어링**: 가치투자(40) + 재무건전성(35) + 모멘텀(25)
- **매수/매도/보유 자동 판정**: 점수 + 모멘텀 + 재무 조건 종합 판단
- **ATR 기반 동적 손절라인**: 종목별 변동성에 맞는 유연한 손절
- **텔레그램 자동 발송**: 매일 장 마감 후 분석 리포트 발송
- **봇 명령어**: /report, /stock, /history, /watchlist, /stoploss

## 데이터 소스

| 소스 | 용도 |
|------|------|
| **한국투자증권 KIS Open API** | 시세, 일봉 차트, PER/PBR |
| **DART OpenAPI** | 재무제표, ROE, 부채비율 |

## 빠른 시작

### 1. 사전 준비

- Python 3.11+
- [KIS Developers](https://apiportal.koreainvestment.com) App Key/Secret
- [DART OpenAPI](https://opendart.fss.or.kr) 인증키
- 텔레그램 봇 토큰 ([BotFather](https://t.me/BotFather))

### 2. 설치

```bash
git clone <your-repo-url> kospi-analyzer
cd kospi-analyzer
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. 설정

```bash
cp config/.env.template config/.env
nano config/.env  # API 키 입력
```

### 4. 테스트

```bash
python tests/test_integration.py
```

### 5. 실행

```bash
# 즉시 1회 실행
python main.py --now

# 텔레그램 봇 + 매일 자동 실행
python main.py --bot

# 스케줄러만 (봇 없이)
python main.py
```

## Docker 배포

```bash
# .env 설정 후
docker compose up -d
docker compose logs -f
```

## Linux 서버 배포

```bash
sudo ./deploy/setup.sh
sudo nano /opt/kospi-analyzer/config/.env  # API 키 입력
sudo systemctl start kospi-analyzer
```

## 프로젝트 구조

```
kospi-analyzer/
├── config/settings.py       # 스코어링 기준값, ATR 배수, 스케줄 설정
├── collectors/
│   ├── kis_api.py           # KIS API 연동 (시세, 차트)
│   └── dart_api.py          # DART API 연동 (재무제표)
├── analysis/
│   ├── scorer.py            # 100점 만점 종합 스코어링
│   ├── signals.py           # 매수/매도/보유 판정
│   └── stoploss.py          # ATR 기반 손절 라인
├── bot/
│   ├── telegram_bot.py      # 텔레그램 봇 명령어
│   └── formatter.py         # 메시지 포맷팅
├── database/
│   ├── models.py            # SQLite DB 모델
│   └── history.py           # 분석 이력 관리
├── tests/
│   └── test_integration.py  # 통합 테스트 (103개)
├── deploy/
│   ├── setup.sh             # 서버 배포 스크립트
│   └── kospi-analyzer.service  # systemd 서비스
├── main.py                  # 메인 실행 (스케줄러)
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## 스코어링 기준

| 카테고리 | 비중 | 세부 지표 |
|---------|------|----------|
| 가치투자 | 40점 | PER(15) + PBR(15) + 배당수익률(10) |
| 재무건전성 | 35점 | ROE(10) + 영업이익률(10) + 부채비율(10) + 유동비율(5) |
| 모멘텀 | 25점 | 20일MA(8) + 60일MA(7) + 거래량추세(10) |

## 투자 경고

**본 시스템의 분석 결과는 투자 참고 자료일 뿐이며, 어떠한 투자 손실에 대해서도 책임을 지지 않습니다.**
분산투자 및 리스크 관리를 반드시 병행하세요.
