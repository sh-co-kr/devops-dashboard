pipeline {
  agent any

  options {
    timestamps()
  }

  environment {
    PROJECT_NAME = "devops-dashboard"
    PROJECT_SLUG = "devops-dashboard"
    PROJECT_DIR = "${WORKSPACE}"
    DEPLOY_DIR = "${PROJECT_DIR}/deploy"
    DEVOPS_DASHBOARD_REPO_ROOT = "${PROJECT_DIR}"
    CANONICAL_PROJECT_DIR = "/home/user/sh-co-kr/apps/devops-dashboard"
    CANONICAL_DEPLOY_DIR = "/home/user/sh-co-kr/apps/devops-dashboard/deploy"
  }

  stages {
    stage('Checkout') {
      steps {
        dir("${PROJECT_DIR}") {
          checkout scm
        }
      }
    }

    stage('Resolve Environment By Branch') {
      steps {
        script {
          if (env.BRANCH_NAME == 'main') {
            env.TARGET_ENV = 'prod'
            env.DEPLOY_SERVICE = "${PROJECT_SLUG}_prod"
          } else if (env.BRANCH_NAME == 'develop') {
            env.TARGET_ENV = 'dev'
            env.DEPLOY_SERVICE = "${PROJECT_SLUG}_dev"
          } else {
            env.TARGET_ENV = ''
            env.DEPLOY_SERVICE = ''
          }
          echo "BRANCH_NAME=${env.BRANCH_NAME}, TARGET_ENV=${env.TARGET_ENV}, DEPLOY_SERVICE=${env.DEPLOY_SERVICE}"
        }
      }
    }

    stage('Skip Unsupported Branch') {
      when {
        expression { return !env.TARGET_ENV?.trim() }
      }
      steps {
        echo "지원 브랜치가 아니어서 배포를 건너뜁니다. 지원 브랜치: main, develop"
      }
    }

    stage('Docker Build & Deploy') {
      when {
        expression { return env.TARGET_ENV?.trim() }
      }
      steps {
        dir("${PROJECT_DIR}") {
          sh '''
            set -eux
            cp -a "${PROJECT_DIR}/." "${CANONICAL_PROJECT_DIR}/"
            if docker compose version >/dev/null 2>&1; then
              COMPOSE_CMD="docker compose"
            else
              COMPOSE_CMD="docker-compose"
            fi
            export DEVOPS_DASHBOARD_REPO_ROOT="${CANONICAL_PROJECT_DIR}"
            cd "${CANONICAL_DEPLOY_DIR}"
            $COMPOSE_CMD -f docker-compose.yml build ${DEPLOY_SERVICE}
            $COMPOSE_CMD -f docker-compose.yml up -d ${DEPLOY_SERVICE}
            $COMPOSE_CMD -f docker-compose.yml ps
          '''
        }
      }
    }

    stage('Verify Deployment') {
      when {
        expression { return env.TARGET_ENV?.trim() }
      }
      steps {
        dir("${CANONICAL_DEPLOY_DIR}") {
          sh '''
            set -eux
            if docker compose version >/dev/null 2>&1; then
              COMPOSE_CMD="docker compose"
            else
              COMPOSE_CMD="docker-compose"
            fi
            if [ "${TARGET_ENV}" = "prod" ]; then
              for i in 1 2 3 4 5 6 7 8 9 10; do
                curl -fsS http://127.0.0.1:7110/ >/dev/null && exit 0
                sleep 3
              done
            elif [ "${TARGET_ENV}" = "dev" ]; then
              for i in 1 2 3 4 5 6 7 8 9 10; do
                curl -fsS http://127.0.0.1:7111/ >/dev/null && exit 0
                sleep 3
              done
            fi
            exit 1
          '''
        }
      }
    }
  }

  post {
    always {
      dir("${CANONICAL_DEPLOY_DIR}") {
          sh '''
            if docker compose version >/dev/null 2>&1; then
              COMPOSE_CMD="docker compose"
            else
              COMPOSE_CMD="docker-compose"
            fi
          $COMPOSE_CMD -f docker-compose.yml logs --no-color --tail=120 || true
        '''
      }
    }
  }
}
