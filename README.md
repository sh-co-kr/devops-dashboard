# DevOps Dashboard v3.0

통합 인프라 모니터링 대시보드 - Docker 컨테이너, 프로젝트 상태, 마크다운 문서를 한 곳에서 관리

## 주요 기능

### 🐳 Docker 컨테이너 모니터링
- 실시간 컨테이너 상태 확인 (Running/Stopped/Unhealthy)
- CPU, 메모리 사용량 표시
- Health Check 상태
- 가동 시간 (Uptime)

### 📊 멀티 환경 지원
- Prod/Dev 환경 분리 표시
- 환경별 컨테이너 매핑
- 포트 정보 표시

### 📝 프로젝트 문서 뷰어
- 프로젝트별 마크다운 파일 자동 스캔
- **폴더 구조별 그룹화** (2026-01-21 추가)
- 마크다운 렌더링 (코드 하이라이팅)

### ⚡ AJAX 기반 비동기 로딩
- 페이지 리로드 없이 프로젝트 전환
- 10초 TTL 캐싱
- 실시간 상태 업데이트

## 기술 스택

| 분류 | 기술 |
|------|------|
| Backend | Python 3.x, Flask |
| Docker | docker-py |
| System | psutil |
| Markdown | markdown (fenced_code, tables, toc) |
| Frontend | Vanilla JS, CSS (다크 모드) |

## 설치

```bash
cd devops-dashboard
pip install -r requirements.txt
```

### 의존성
```
flask
docker
psutil
markdown
```

## 실행

```bash
# venv 활성화 필요
source /home/user/sh-co-kr/venv/bin/activate

# 대시보드 실행
python dashboard.py

# 또는 백그라운드 실행
nohup python dashboard.py > /tmp/dashboard.log 2>&1 &
```

브라우저에서 **http://localhost:4040** 접속

## 설정

### dashboard_config.json
프로젝트별 Docker 컨테이너 매핑 설정

```json
{
  "meeting-compass": {
    "type": "docker",
    "envs": {
      "prod": { "container": "meeting-compass", "port": 3030 },
      "dev": { "container": "meeting-compass-dev", "port": 3031 }
    }
  }
}
```

### 환경 변수 (dashboard.py 상단)
- `PORT`: 서버 포트 (기본: 4040)
- `BASE_DIR`: 프로젝트 스캔 경로
- `CACHE_TTL`: 캐시 유효 시간 (기본: 10초)
- `IGNORE_PATTERNS`: 무시할 폴더 패턴

## 파일 구조

```
devops-dashboard/
├── dashboard.py           # 메인 서버 (Flask + 모든 로직)
├── dashboard_config.json  # 프로젝트-컨테이너 매핑
├── requirements.txt       # 의존성
└── README.md
```

## 최근 업데이트

### 2026-01-21
- 프로젝트 문서 폴더 구조별 그룹화 기능 추가
- `scan_markdown_files()` 함수에 `folder` 필드 추가
- 프론트엔드에서 폴더별 시각적 구분 (border-left)

## 라이선스

MIT
