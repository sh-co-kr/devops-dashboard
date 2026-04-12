#!/bin/bash

# DevOps Dashboard 배포 스크립트

set -e

DASHBOARD_PORT="${PORT:-4040}"

echo "=========================================="
echo "🚀 DevOps Dashboard 배포 시작"
echo "=========================================="

cd "$(dirname "$0")"

# 1. 기존 컨테이너 중지 및 제거
echo ""
echo "📦 기존 컨테이너 확인 중..."
if docker ps -a | grep -q devops-dashboard; then
    echo "   기존 컨테이너 중지 및 제거 중..."
    docker compose down 2>/dev/null || true
    docker rm -f devops-dashboard 2>/dev/null || true
fi

# 2. 이미지 빌드
echo ""
echo "🔨 Docker 이미지 빌드 중..."
docker compose build

# 3. 컨테이너 시작
echo ""
echo "▶️  컨테이너 시작 중..."
docker compose up -d

# 4. 상태 확인
echo ""
echo "⏳ 컨테이너 시작 대기 중 (5초)..."
sleep 5

echo ""
echo "📊 컨테이너 상태:"
docker compose ps

echo ""
echo "=========================================="
echo "✅ 배포 완료!"
echo "=========================================="
echo ""
echo "🌐 대시보드 주소: http://localhost:${DASHBOARD_PORT}"
echo ""
echo "📝 로그 확인: docker compose logs -f"
echo "🛑 중지: docker compose down"
echo ""
