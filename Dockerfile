FROM python:3.11-slim

LABEL maintainer="KOSPI Analyzer"
LABEL description="KOSPI 저평가 기업 분석 + 텔레그램 봇"

WORKDIR /app

# 시스템 패키지 (tzdata for Asia/Seoul)
RUN apt-get update && \
    apt-get install -y --no-install-recommends tzdata && \
    ln -sf /usr/share/zoneinfo/Asia/Seoul /etc/localtime && \
    echo "Asia/Seoul" > /etc/timezone && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Python 의존성
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스 복사
COPY config/ config/
COPY collectors/ collectors/
COPY analysis/ analysis/
COPY bot/ bot/
COPY database/ database/
COPY main.py .

# 데이터/로그 디렉토리
RUN mkdir -p data logs token_cache

# 환경변수 (런타임에 오버라이드)
ENV PYTHONUNBUFFERED=1
ENV TIMEZONE=Asia/Seoul
ENV LOG_LEVEL=INFO
ENV DB_PATH=data/kospi_analyzer.db

# 헬스체크
HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD python -c "from database.models import Database; db = Database(); db.close(); print('OK')" || exit 1

# 실행
ENTRYPOINT ["python", "main.py"]
CMD ["--bot"]
