# Jenkins 브랜치 배포 가이드

`devops-dashboard`는 브랜치 이름에 따라 Jenkins가 배포 대상을 자동으로 결정하도록 구성한다.

## 배포 규칙

| 브랜치 | 대상 환경 | Docker Compose 서비스 | 확인 포트 |
|--------|-----------|------------------------|-----------|
| `main` | 운영 | `devops-dashboard_prod` | `7110` |
| `develop` | 개발 | `devops-dashboard_dev` | `7111` |
| 그 외 | 배포 안 함 | 없음 | 없음 |

## Jenkins 파이프라인 흐름

1. `checkout scm`
2. `BRANCH_NAME` 확인
3. 브랜치에 맞는 `DEPLOY_SERVICE` 결정
4. `docker compose build`
5. `docker compose up -d`
6. `curl`로 배포 확인
7. 마지막에 최근 로그 출력

## Jenkins 설정 체크리스트

1. Jenkins Job은 멀티브랜치 파이프라인 또는 브랜치 정보를 전달하는 Pipeline Job이어야 한다.
2. Jenkins 워크스페이스 안에 `apps/devops-dashboard` 경로가 실제로 존재해야 한다.
3. Jenkins 실행 사용자에게 Docker 실행 권한이 있어야 한다.
4. Jenkins 에이전트에 `docker compose`와 `curl`이 설치되어 있어야 한다.
5. GitHub webhook 또는 Jenkins SCM polling이 설정되어 있어야 한다.

## Jenkins 멀티브랜치 권장 설정

1. Job 종류는 `Multibranch Pipeline` 사용
2. Branch Source는 GitHub 저장소 `sh-co-kr/devops-dashboard`
3. Discover branches 전략은 최소 `main`, `develop`를 포함해야 함
4. Script Path는 `Jenkinsfile`
5. Scan by webhook 사용 시 GitHub webhook 이벤트는 `push` 기준으로 설정

## Jenkins UI 클릭 순서

1. Jenkins 메인 화면에서 `New Item`
2. 이름 입력
   `devops-dashboard`
3. `Multibranch Pipeline` 선택 후 `OK`
4. `Branch Sources`에서 `Add source` -> `GitHub`
5. Repository는 `https://github.com/sh-co-kr/devops-dashboard.git` 또는 연결된 GitHub source 선택
6. Credentials가 필요하면 GitHub 토큰 연결
7. `Behaviors`에서 브랜치 탐색 정책 확인
   `main`, `develop`가 스캔 대상이어야 함
8. `Build Configuration`의 `Script Path`를 `Jenkinsfile`로 입력
9. `Scan Multibranch Pipeline Triggers`에서 webhook 기반 스캔 사용
10. `Save`
11. Job 화면에서 `Scan Repository Now`
12. `main`, `develop` 브랜치 Job이 생성되었는지 확인

## GitHub webhook 권장 설정

1. Payload URL: `http://suho0213.iptime.org:9090/github-webhook/`
2. Content type: `application/json`
3. 이벤트: `Just the push event`
4. GitHub 저장소에 실제 `main`, `develop` 브랜치가 있어야 함

현재 확인 결과:
- `GET http://suho0213.iptime.org:9090/github-webhook/` -> `405`
- `POST http://suho0213.iptime.org:9090/github-webhook/` -> `400`

위 응답은 Jenkins webhook 엔드포인트가 살아 있다는 의미다.

## GitHub UI 클릭 순서

1. GitHub 저장소로 이동
2. `Settings` -> `Webhooks`
3. `Add webhook`
4. Payload URL에 `https://<jenkins-host>/github-webhook/` 입력
   실제 값: `http://suho0213.iptime.org:9090/github-webhook/`
5. Content type은 `application/json`
6. Secret이 있으면 Jenkins와 동일한 값 입력
7. 이벤트는 `Just the push event`
8. `Active` 체크
9. `Add webhook`
10. 저장 후 `Recent Deliveries`에서 `200` 응답 확인

## 권장 운영 방식

1. `develop` 브랜치에 머지되면 개발 대시보드 `7111`로 자동 반영
2. 검증 후 `main`에 머지되면 운영 대시보드 `7110`로 자동 반영
3. 기능 브랜치는 자동배포하지 않고 PR 검토 용도로만 사용

## 수동 점검 명령

```bash
cd /home/user/sh-co-kr/apps/devops-dashboard/deploy
docker compose -f docker-compose.yml ps
curl -fsS http://127.0.0.1:7110/ >/dev/null
curl -fsS http://127.0.0.1:7111/ >/dev/null
```

## 브랜치 준비

```bash
cd /home/user/sh-co-kr/apps/devops-dashboard
git checkout main
git checkout develop
git push -u origin develop
git checkout main
```

## 참고

- Jenkins 파이프라인 정의: [Jenkinsfile](/home/user/sh-co-kr/apps/devops-dashboard/Jenkinsfile)
- 운영 가이드: [DEVOPS_DASHBOARD_GUIDE.md](/home/user/sh-co-kr/apps/devops-dashboard/DEVOPS_DASHBOARD_GUIDE.md)
