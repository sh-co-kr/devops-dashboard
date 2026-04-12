pipeline {
  agent any

  options {
    timestamps()
    ansiColor('xterm')
  }

  environment {
    PROJECT_NAME = "devops-dashboard"
    PROJECT_SLUG = "devops-dashboard"
    PROJECT_DIR = "${WORKSPACE}/apps/devops-dashboard"
    DEPLOY_DIR = "${PROJECT_DIR}/deploy"
    DEVOPS_DASHBOARD_REPO_ROOT = "${WORKSPACE}"
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
          } else if (env.BRANCH_NAME == 'develop') {
            env.TARGET_ENV = 'dev'
          } else {
            env.TARGET_ENV = 'local'
          }
          echo "BRANCH_NAME=${env.BRANCH_NAME}, TARGET_ENV=${env.TARGET_ENV}"
        }
      }
    }

    stage('Docker Build & Deploy') {
      steps {
        dir("${DEPLOY_DIR}") {
          sh '''
            set -eux
            export DEVOPS_DASHBOARD_REPO_ROOT="${DEVOPS_DASHBOARD_REPO_ROOT}"
            docker compose -f docker-compose.yml build ${PROJECT_SLUG}_${TARGET_ENV}
            docker compose -f docker-compose.yml up -d ${PROJECT_SLUG}_${TARGET_ENV}
            docker compose -f docker-compose.yml ps
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
