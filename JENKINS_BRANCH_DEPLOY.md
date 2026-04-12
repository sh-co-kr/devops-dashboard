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
4. Script Path는 `apps/devops-dashboard/Jenkinsfile`
5. Scan by webhook 사용 시 GitHub webhook 이벤트는 `push` 기준으로 설정

## GitHub webhook 권장 설정

1. Payload URL: `https://<jenkins-host>/github-webhook/`
2. Content type: `application/json`
3. 이벤트: `Just the push event`
4. GitHub 저장소에 실제 `main`, `develop` 브랜치가 있어야 함

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
git checkout -b develop
git push -u origin develop
git checkout main
```

## 참고

- Jenkins 파이프라인 정의: [Jenkinsfile](/home/user/sh-co-kr/apps/devops-dashboard/Jenkinsfile)
- 운영 가이드: [DEVOPS_DASHBOARD_GUIDE.md](/home/user/sh-co-kr/apps/devops-dashboard/DEVOPS_DASHBOARD_GUIDE.md)
