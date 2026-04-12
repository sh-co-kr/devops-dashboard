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
        dir("${DEPLOY_DIR}") {
          sh '''
            set -eux
            export DEVOPS_DASHBOARD_REPO_ROOT="${DEVOPS_DASHBOARD_REPO_ROOT}"
            docker compose -f docker-compose.yml build ${DEPLOY_SERVICE}
            docker compose -f docker-compose.yml up -d ${DEPLOY_SERVICE}
            docker compose -f docker-compose.yml ps
          '''
        }
      }
    }

    stage('Verify Deployment') {
      when {
        expression { return env.TARGET_ENV?.trim() }
      }
      steps {
        dir("${DEPLOY_DIR}") {
          sh '''
            set -eux
            if [ "${TARGET_ENV}" = "prod" ]; then
              curl -fsS http://127.0.0.1:7110/ >/dev/null
            elif [ "${TARGET_ENV}" = "dev" ]; then
              curl -fsS http://127.0.0.1:7111/ >/dev/null
            fi
          '''
        }
      }
    }
  }

  post {
    always {
      dir("${DEPLOY_DIR}") {
        sh 'docker compose -f docker-compose.yml logs --no-color --tail=120 || true'
      }
    }
  }
}
