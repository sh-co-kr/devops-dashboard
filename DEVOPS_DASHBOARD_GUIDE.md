# DevOps Dashboard Guide

## 개요

`devops-dashboard`는 저장소 안의 프로젝트 문서와 Docker/프로세스 상태를 한 화면에서 확인할 수 있도록 만든 통합 운영 대시보드입니다.

주요 목적은 다음과 같습니다.

- 프로젝트별 실행 상태 확인
- Docker 컨테이너 메모리/CPU/헬스 상태 확인
- 프로젝트 문서(`.md`) 탐색
- 자주 쓰는 운영 링크(Quick Links) 관리
- 간단한 컨테이너 제어 시작/중지/재시작

앱 진입점은 [`dashboard.py`](/home/user/sh-co-kr/apps/devops-dashboard/dashboard.py) 하나로 구성되어 있고, 설정은 [`dashboard_config.json`](/home/user/sh-co-kr/apps/devops-dashboard/dashboard_config.json)에서 관리합니다.

## 현재 구조

핵심 파일:

- [`dashboard.py`](/home/user/sh-co-kr/apps/devops-dashboard/dashboard.py): Flask 앱, 상태 수집, 마크다운 스캔, 프론트엔드 템플릿 포함
- [`dashboard_config.json`](/home/user/sh-co-kr/apps/devops-dashboard/dashboard_config.json): 프로젝트 매핑, 표시 순서, 대시보드 링크 설정
- [`docker-compose.yml`](/home/user/sh-co-kr/apps/devops-dashboard/docker-compose.yml): 로컬 실행용 compose
- [`deploy/docker-compose.yml`](/home/user/sh-co-kr/apps/devops-dashboard/deploy/docker-compose.yml): 브랜치/환경별 배포용 compose
- [`Jenkinsfile`](/home/user/sh-co-kr/apps/devops-dashboard/Jenkinsfile): Jenkins 배포 파이프라인

## 주요 기능

### 1. 프로젝트 상태 모니터링

프로젝트는 `docker`, `process`, `info` 타입으로 구분됩니다.

- `docker`: 컨테이너 상태, 헬스, 메모리, CPU, 업타임 표시
- `process`: 로컬 프로세스 상태 표시
- `info`: 설명성 프로젝트를 단순 정보 카드로 표시

### 2. 프로젝트 문서 탐색

`SCAN_PATH` 아래의 `.md` 파일을 스캔해서 프로젝트별 문서를 자동으로 묶어 보여줍니다.

- 루트 프로젝트와 `apps/<project>` 구조 모두 지원
- 폴더별 그룹화 표시
- 마크다운 렌더링 지원

### 3. Quick Links

운영에 자주 쓰는 링크를 대시보드 상단 시스템 화면에서 제공합니다.

예시:

- 공유기 관리자
- Jenkins
- GitHub
- Linear
- 특정 서비스 배포 주소

이제는 코드 하드코딩이 아니라 설정 파일과 UI에서 수정 가능합니다.

### 4. 설정 모달

설정 모달에서 아래 항목을 수정할 수 있습니다.

- 프로젝트 이름 매핑
- 프로젝트 표시 순서
- 프로젝트 설정
- 대시보드 링크 설정

## 설정 파일 구조

`dashboard_config.json`은 크게 3개 영역으로 나뉩니다.

### 1. 이름 매핑

```json
"project_name_mapping": {
  "barocut": "barocut",
  "meeting-compass": "미팅 집중도 테스트"
}
```

실제 폴더명과 화면에 표시할 프로젝트 이름을 연결합니다.

### 2. 프로젝트 순서

```json
"project_order": [
  "DevOps 대시보드",
  "젠킨스 서버",
  "barocut"
]
```

사이드바 표시 순서를 제어합니다.

### 3. 대시보드 설정

```json
"dashboard_settings": {
  "site_base_url": "http://suho0213.iptime.org",
  "site_host_label": "suho0213.iptime.org",
  "jenkins_webhook_url": "",
  "jenkins_webhook_type": "auto",
  "quick_links": [
    { "title": "공유기 관리자", "icon": "📡", "port": 9080 }
  ]
}
```

설정 항목:

- `site_base_url`: 서비스 링크 생성 시 기준이 되는 공개 주소
- `site_host_label`: 화면에 표시할 호스트 라벨
- `jenkins_webhook_url`: Jenkins 실패/복구 상태 변경 시 JSON payload를 보낼 웹훅 URL. 비워두면 알림 비활성
- `jenkins_webhook_type`: `auto`, `generic`, `discord`, `teams` 중 선택. `auto`면 URL 패턴으로 자동 감지
- `quick_links`: 상단 Quick Links 목록

Quick Link 항목은 아래 형식을 사용합니다.

포트 기반 링크:

```json
{ "title": "Jenkins", "icon": "👷", "port": 9090 }
```

절대 URL 기반 링크:

```json
{ "title": "GitHub", "icon": "🐙", "url": "https://github.com/sh-co-kr", "label": "github.com/sh-co-kr" }
```

## 환경 변수

주요 환경 변수:

- `PORT`: 대시보드 실행 포트
- `SCAN_PATH`: 마크다운 스캔 기준 경로
- `SITE_BASE_URL`: 공개 주소 override
- `SITE_HOST_LABEL`: 호스트 라벨 override
- `DEFAULT_PUBLIC_SITE`: fallback 공개 주소
- `HEALTH_CHECK_HOST`: 헬스 체크 대상 호스트 override

우선순위는 대체로 `환경 변수 > dashboard_config.json > 코드 기본값`입니다.

## 최근에 정리한 개선 사항

### 하드코딩 제거

예전에는 아래 값들이 코드와 배포 파일에 직접 박혀 있었습니다.

- DDNS 주소
- Quick Links
- Jenkins 절대 경로
- 저장소 절대 경로
- 대시보드 포트 표시값

현재는 설정 파일 또는 환경 변수 기반으로 정리되어 있습니다.

### 성능 개선

초기 로딩과 상세 조회 병목을 줄이기 위해 아래 개선이 들어가 있습니다.

- 첫 화면은 경량 summary 상태만 조회
- Markdown 전체 스캔 대신 프로젝트 이름만 빠르게 수집
- 프로젝트 상세 조회 캐시 추가
- 프로젝트 상세 환경별 병렬 조회
- 시스템 상태 조회 비차단화
- 전체 프로젝트 summary 병렬화

이 구조 덕분에 첫 화면과 재조회 체감 속도가 많이 줄었습니다.

## 실행 방법

### 로컬 Python 실행

```bash
cd /home/user/sh-co-kr/apps/devops-dashboard
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python dashboard.py
```

### Docker Compose 실행

```bash
docker compose -f /home/user/sh-co-kr/apps/devops-dashboard/docker-compose.yml up -d --build
```

중지:

```bash
docker compose -f /home/user/sh-co-kr/apps/devops-dashboard/docker-compose.yml down
```

## 배포 흐름

배포는 Jenkins와 `deploy/docker-compose.yml`을 통해 수행됩니다.

브랜치 기준:

- `main` -> `prod`
- `develop` -> `dev`
- 그 외 -> `local`

Jenkins는 현재 `WORKSPACE` 기준 경로를 사용하므로 특정 서버 절대 경로에 덜 묶여 있습니다.

## 운영 시 참고할 점

- `SCAN_PATH`를 너무 큰 경로로 잡으면 문서 스캔 비용이 커질 수 있습니다.
- Docker 안에서 헬스 체크를 할 때는 `host.docker.internal` 접근이 필요할 수 있습니다.
- 첫 화면은 빠르게, 상세 화면은 정확하게라는 방향으로 최적화되어 있습니다.
- 설정 변경 후 저장하면 페이지 새로고침으로 반영됩니다.

## 추천 수정 포인트

운영 중 자주 바꾸게 되는 값은 아래입니다.

- `dashboard_settings.site_base_url`
- `dashboard_settings.site_host_label`
- `dashboard_settings.quick_links`
- 프로젝트별 `environments`
- `project_order`

## 한 줄 요약

`devops-dashboard`는 "프로젝트 상태 + 문서 + 운영 링크"를 한 곳에서 다루는 통합 운영 보드이며, 현재는 설정 기반 구조와 성능 최적화가 적용된 상태입니다.
