#!/bin/bash
# ============================================================
# KOSPI 저평가 기업 분석 시스템 - 서버 배포 스크립트
#
# 사용법:
#   chmod +x deploy/setup.sh
#   sudo ./deploy/setup.sh
#
# 지원 환경: Ubuntu 22.04+ / Debian 12+
# ============================================================

set -e

APP_DIR="/opt/kospi-analyzer"
APP_USER="kospi"
PYTHON_VERSION="python3.11"

echo "============================================"
echo "  KOSPI 분석 시스템 배포 스크립트"
echo "============================================"

# --- 1. 시스템 패키지 ---
echo ""
echo "[1/6] 시스템 패키지 설치..."
apt-get update -qq
apt-get install -y -qq python3.11 python3.11-venv python3-pip git

# --- 2. 사용자 생성 ---
echo "[2/6] 서비스 사용자 생성..."
if ! id "$APP_USER" &>/dev/null; then
    useradd --system --home-dir "$APP_DIR" --shell /bin/false "$APP_USER"
    echo "  사용자 '$APP_USER' 생성 완료"
else
    echo "  사용자 '$APP_USER' 이미 존재"
fi

# --- 3. 디렉토리 설정 ---
echo "[3/6] 디렉토리 설정..."
mkdir -p "$APP_DIR"/{data,logs,token_cache,config,deploy}

# 소스 복사 (이 스크립트가 프로젝트 루트에서 실행된다고 가정)
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
if [ -f "$SCRIPT_DIR/main.py" ]; then
    cp -r "$SCRIPT_DIR"/{main.py,requirements.txt,.gitignore} "$APP_DIR/"
    cp -r "$SCRIPT_DIR"/{config,collectors,analysis,bot,database} "$APP_DIR/"
    cp -r "$SCRIPT_DIR"/tests "$APP_DIR/" 2>/dev/null || true
    echo "  소스 코드 복사 완료"
else
    echo "  ⚠️ 소스 코드를 $APP_DIR 에 직접 복사해주세요"
fi

# --- 4. Python 가상환경 ---
echo "[4/6] Python 가상환경 설정..."
if [ ! -d "$APP_DIR/venv" ]; then
    $PYTHON_VERSION -m venv "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
echo "  의존성 설치 완료"

# --- 5. .env 확인 ---
echo "[5/6] 환경변수 확인..."
if [ ! -f "$APP_DIR/config/.env" ]; then
    if [ -f "$APP_DIR/config/.env.template" ]; then
        cp "$APP_DIR/config/.env.template" "$APP_DIR/config/.env"
        echo "  ⚠️ config/.env 생성됨 → API 키를 입력해주세요!"
        echo "     sudo nano $APP_DIR/config/.env"
    else
        echo "  ❌ .env.template 없음. 수동으로 생성해주세요."
    fi
else
    echo "  config/.env 확인됨"
fi

# 권한 설정
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod 600 "$APP_DIR/config/.env" 2>/dev/null || true

# --- 6. systemd 서비스 ---
echo "[6/6] systemd 서비스 등록..."
cp "$APP_DIR/deploy/kospi-analyzer.service" /etc/systemd/system/ 2>/dev/null || \
cp "$SCRIPT_DIR/deploy/kospi-analyzer.service" /etc/systemd/system/ 2>/dev/null || true

systemctl daemon-reload
systemctl enable kospi-analyzer

echo ""
echo "============================================"
echo "  ✅ 배포 완료!"
echo "============================================"
echo ""
echo "다음 단계:"
echo "  1. API 키 설정:  sudo nano $APP_DIR/config/.env"
echo "  2. 테스트 실행:  cd $APP_DIR && sudo -u $APP_USER venv/bin/python tests/test_integration.py"
echo "  3. 즉시 실행:    sudo -u $APP_USER venv/bin/python main.py --now"
echo "  4. 서비스 시작:  sudo systemctl start kospi-analyzer"
echo "  5. 상태 확인:    sudo systemctl status kospi-analyzer"
echo "  6. 로그 확인:    sudo journalctl -u kospi-analyzer -f"
echo ""
echo "Docker 배포:"
echo "  1. API 키 설정:  nano config/.env"
echo "  2. 빌드 & 실행:  docker compose up -d"
echo "  3. 로그 확인:    docker compose logs -f"
echo ""
