#!/usr/bin/env python3
"""
통합 DevOps 대시보드 v3.0
AJAX 기반 비동기 로딩 - 빠른 프로젝트 전환

포트: 4040
실행: python dashboard.py

v3.0 업데이트:
- AJAX 기반 비동기 로딩 (페이지 리로드 없음)
- 멀티 환경 지원 (Prod/Dev)
- 실시간 상태 업데이트
"""

import os
import json
import time
import threading
import urllib.error
import urllib.request
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse
from flask import Flask, render_template_string, request, jsonify

# 선택적 라이브러리 임포트
try:
    import docker
    DOCKER_AVAILABLE = True
except ImportError:
    DOCKER_AVAILABLE = False

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

try:
    import markdown
    MARKDOWN_AVAILABLE = True
except ImportError:
    MARKDOWN_AVAILABLE = False

# 설정 (배포 시 compose에서 PORT=호스트포트 로 1:1 매핑)
PORT = int(os.environ.get("PORT", "4040"))

CONFIG_META_KEYS = ('project_name_mapping', 'project_order', 'dashboard_settings')
ENV_DEFAULT_PUBLIC_SITE = (os.environ.get("DEFAULT_PUBLIC_SITE") or "").strip()
DEFAULT_DASHBOARD_SETTINGS = {
    'site_base_url': ENV_DEFAULT_PUBLIC_SITE,
    'site_host_label': '',
    'quick_links': [
        {'title': '공유기 관리자', 'icon': '📡', 'port': 9080},
        {'title': 'Jenkins 서버', 'icon': '👷', 'port': 9090},
        {'title': 'GitHub 프로필', 'icon': '🐙', 'url': 'https://github.com/sh-co-kr'},
        {'title': 'Linear', 'icon': '⚡', 'url': 'https://linear.app/dev-sh/'},
        {'title': 'DD-WAY Frontend', 'icon': '🌐', 'port': 3000},
        {'title': 'Meeting Compass (배포)', 'icon': '🧭', 'port': 7130},
    ],
}


def _default_public_site() -> str:
    return ENV_DEFAULT_PUBLIC_SITE or f"http://localhost:{PORT}"


def normalize_public_site_base(raw: str | None) -> str:
    """🌐 링크용 베이스 URL 정규화."""
    base = (raw or '').strip().rstrip('/') or _default_public_site().rstrip('/')
    try:
        host = (urlparse(base).hostname or '').lower()
    except Exception:
        host = base.split("//", 1)[-1].split("/")[0].lower()
    if (
        not host
        or host in ("localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0")
        or host.startswith("127.")
    ):
        return _default_public_site().rstrip('/')
    return base


def _normalize_quick_links(raw_links) -> list[dict]:
    if not isinstance(raw_links, list):
        return list(DEFAULT_DASHBOARD_SETTINGS['quick_links'])

    normalized = []
    for item in raw_links:
        if not isinstance(item, dict):
            continue
        title = str(item.get('title', '')).strip()
        if not title:
            continue
        link = {'title': title, 'icon': str(item.get('icon', '🔗'))}
        url = str(item.get('url', '')).strip()
        if url:
            link['url'] = url
        else:
            try:
                port = int(item.get('port'))
                if 1 <= port <= 65535:
                    link['port'] = port
            except (TypeError, ValueError):
                pass
            path = str(item.get('path', '')).strip()
            if path:
                link['path'] = path
        label = str(item.get('label', '')).strip()
        if label:
            link['label'] = label
        normalized.append(link)

    return normalized or list(DEFAULT_DASHBOARD_SETTINGS['quick_links'])


def get_dashboard_settings(config: dict | None = None) -> dict:
    if config is None:
        config = load_config()

    settings = dict(DEFAULT_DASHBOARD_SETTINGS)
    raw_settings = config.get('dashboard_settings', {})
    if isinstance(raw_settings, dict):
        for key in ('site_base_url', 'site_host_label'):
            value = raw_settings.get(key)
            if isinstance(value, str):
                settings[key] = value.strip()
        settings['quick_links'] = _normalize_quick_links(raw_settings.get('quick_links'))
    else:
        settings['quick_links'] = _normalize_quick_links(None)

    env_site_base_url = (os.environ.get("SITE_BASE_URL") or '').strip()
    if env_site_base_url:
        settings['site_base_url'] = env_site_base_url
    settings['site_base_url'] = normalize_public_site_base(settings.get('site_base_url'))

    derived_host_label = (urlparse(settings['site_base_url']).netloc or settings['site_base_url']).split('/')[0]
    env_site_host_label = (os.environ.get("SITE_HOST_LABEL") or '').strip()
    settings['site_host_label'] = env_site_host_label or settings.get('site_host_label') or derived_host_label
    settings['default_public_site'] = _default_public_site().rstrip('/')
    return settings


def is_project_config_entry(key: str, value) -> bool:
    return key not in CONFIG_META_KEYS and isinstance(value, dict) and value is not None
# Docker 환경에서는 SCAN_PATH 환경변수 사용, 없으면 상위 디렉토리(로컬: …/apps).
# 이미지에 dashboard만 /app 에 두면 parent.parent 가 '/' 가 되어 전체 파일시스템 스캔을 유발하므로 방지한다.
_default_scan = Path(__file__).parent.parent
if _default_scan.resolve() == Path('/').resolve():
    _default_scan = Path(__file__).parent
BASE_DIR = Path(os.environ.get('SCAN_PATH', _default_scan)).resolve()
CONFIG_FILE = Path(__file__).parent / "dashboard_config.json"
CACHE_TTL = 10

IGNORE_PATTERNS = [
    'venv', 'node_modules', '.git', '__pycache__', '.next',
    'dist', 'build', '.cache', 'jenkins_home', '.venv'
]

# 루트의 apps/ 아래에 소스 프로젝트가 있을 때, 프로젝트 키는 apps/<이름> 이 아니라 <이름>으로 묶는다.
APPS_ROOT = 'apps'


def _project_key_from_relative_parts(parts):
    """SCAN_PATH 기준 상대 경로 segments → (프로젝트 키, 프로젝트 루트 segment 인덱스)."""
    if len(parts) < 2:
        return ('root', 0)
    if parts[0] == APPS_ROOT:
        if len(parts) >= 3:
            return (parts[1], 1)
        return (APPS_ROOT, 0)
    return (parts[0], 0)

# ============================================================
# 캐싱 시스템
# ============================================================
class SimpleCache:
    def __init__(self, ttl: int = 10):
        self.ttl = ttl
        self._cache = {}
        self._lock = threading.Lock()
    
    def get(self, key: str):
        with self._lock:
            if key in self._cache:
                data, timestamp = self._cache[key]
                if time.time() - timestamp < self.ttl:
                    return data
                del self._cache[key]
        return None
    
    def set(self, key: str, data):
        with self._lock:
            self._cache[key] = (data, time.time())
    
    def invalidate(self, key: str = None):
        with self._lock:
            if key:
                self._cache.pop(key, None)
            else:
                self._cache.clear()
    
    def get_age(self, key: str) -> int:
        with self._lock:
            if key in self._cache:
                _, timestamp = self._cache[key]
                return int(time.time() - timestamp)
        return -1

cache = SimpleCache(ttl=CACHE_TTL)
app = Flask(__name__)

# Docker 클라이언트
docker_client = None
if DOCKER_AVAILABLE:
    try:
        docker_client = docker.from_env()
        docker_client.ping()
    except Exception as e:
        docker_client = None

if PSUTIL_AVAILABLE:
    try:
        # 첫 샘플을 미리 초기화해 두면 이후 비차단 cpu_percent 조회가 즉시 의미 있는 값을 준다.
        psutil.cpu_percent(interval=None)
    except Exception:
        pass

# ============================================================
# 데이터 로딩 함수
# ============================================================
def load_config() -> dict:
    try:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f"설정 로드 실패: {e}")
    return {}


def collect_docker_targets_from_config(config: dict) -> set:
    """dashboard_config에 등록된 Docker 컨테이너 이름만 제어 허용."""
    allowed = set()
    normalized = normalize_project_configs(config)
    for _pname, pc in normalized.items():
        if pc.get('type') != 'docker':
            continue
        for env in pc.get('environments', []):
            t = (env.get('target') or '').strip()
            if t:
                allowed.add(t)
    return allowed


def normalize_project_configs(config: dict) -> dict:
    """프로젝트 설정 키를 매핑된 이름으로 변환"""
    name_mapping = config.get('project_name_mapping', {})
    project_order = config.get('project_order', [])
    
    # 역매핑 생성 (표시명 -> 폴더명)
    reverse_mapping = {v: k for k, v in name_mapping.items()}
    
    normalized = {}
    for key, value in config.items():
        if not is_project_config_entry(key, value):
            continue
        
        # 매핑이 있으면 매핑된 이름 사용, 없으면 원래 키 사용
        if key in name_mapping:
            mapped_name = name_mapping[key]
            normalized[mapped_name] = value
        elif key in reverse_mapping:
            # 이미 매핑된 이름이면 그대로 사용
            normalized[key] = value
        else:
            # 매핑이 없으면 원래 키 사용
            normalized[key] = value
    
    # 순서 적용
    if project_order:
        ordered = {}
        # 순서에 있는 것부터 추가
        for name in project_order:
            if name in normalized:
                ordered[name] = normalized[name]
        # 순서에 없는 것들 추가
        for name, value in normalized.items():
            if name not in ordered:
                ordered[name] = value
        return ordered
    
    return normalized


def scan_markdown_files(force_refresh: bool = False) -> dict:
    cache_key = 'markdown_files'
    if not force_refresh:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
    
    # 프로젝트 이름 매핑 로드
    config = load_config()
    name_mapping = config.get('project_name_mapping', {})
    
    projects = {}
    for root, dirs, files in os.walk(BASE_DIR):
        dirs[:] = [d for d in dirs if d not in IGNORE_PATTERNS]
        root_path = Path(root)
        
        skip = any(p in root_path.parts for p in IGNORE_PATTERNS)
        if skip:
            continue
        
        for file in files:
            if file.endswith('.md'):
                file_path = root_path / file
                try:
                    relative = file_path.relative_to(BASE_DIR)
                    parts = relative.parts
                    folder_name, start_idx = _project_key_from_relative_parts(parts)
                    
                    # 매핑이 있으면 매핑된 이름 사용, 없으면 폴더명 사용
                    project_name = name_mapping.get(folder_name, folder_name)
                    
                    if project_name not in projects:
                        projects[project_name] = []
                    
                    # 폴더 경로 추출 (프로젝트명 제외, 파일명 제외)
                    if len(parts) > start_idx + 1:
                        folder = '/'.join(parts[start_idx + 1:-1])
                    else:
                        folder = ''
                    
                    mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
                    projects[project_name].append({
                        'name': file,
                        'path': str(relative),
                        'folder': folder,
                        'modified': mtime.strftime('%Y-%m-%d %H:%M'),
                    })
                except (PermissionError, OSError):
                    continue
    
    for project in projects:
        projects[project].sort(key=lambda x: (x['folder'], -datetime.strptime(x['modified'], '%Y-%m-%d %H:%M').timestamp()))
    
    result = dict(sorted(projects.items()))
    cache.set(cache_key, result)
    return result


def scan_markdown_project_names(force_refresh: bool = False) -> set[str]:
    cache_key = 'markdown_project_names'
    if not force_refresh:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    config = load_config()
    name_mapping = config.get('project_name_mapping', {})
    project_names = set()

    for root, dirs, files in os.walk(BASE_DIR):
        dirs[:] = [d for d in dirs if d not in IGNORE_PATTERNS]
        root_path = Path(root)
        if any(p in root_path.parts for p in IGNORE_PATTERNS):
            continue

        if not any(file.endswith('.md') for file in files):
            continue

        try:
            relative = root_path.relative_to(BASE_DIR)
        except ValueError:
            continue

        parts = relative.parts
        if not parts:
            project_names.add('root')
            continue

        folder_name, _ = _project_key_from_relative_parts(parts)
        project_names.add(name_mapping.get(folder_name, folder_name))

    cache.set(cache_key, project_names)
    return project_names


def get_docker_status(container_name: str, include_metrics: bool = True) -> dict:
    result = {
        'running': False, 'status': 'unknown', 'health': 'unknown',
        'memory_mb': 0, 'cpu_percent': 0, 'uptime': 'N/A', 'error': None,
        'host_ports': [],
    }
    
    if not docker_client:
        result['error'] = 'Docker 연결 안됨'
        return result
    
    try:
        container = docker_client.containers.get(container_name)
        result['status'] = container.status
        result['running'] = container.status == 'running'
        
        health = container.attrs.get('State', {}).get('Health')
        result['health'] = health.get('Status', 'none') if health else 'none'
        
        started_at = container.attrs.get('State', {}).get('StartedAt', '')
        if started_at and result['running']:
            try:
                start_time = datetime.fromisoformat(started_at.replace('Z', '+00:00'))
                uptime = datetime.now(start_time.tzinfo) - start_time
                days, hours = uptime.days, uptime.seconds // 3600
                result['uptime'] = f"{days}일 {hours}시간" if days > 0 else f"{hours}시간 {(uptime.seconds % 3600) // 60}분"
            except:
                pass
        
        if result['running'] and include_metrics:
            try:
                stats = container.stats(stream=False)
                result['memory_mb'] = round(stats.get('memory_stats', {}).get('usage', 0) / (1024 * 1024), 1)
                cpu_delta = stats['cpu_stats']['cpu_usage']['total_usage'] - stats['precpu_stats']['cpu_usage']['total_usage']
                system_delta = stats['cpu_stats']['system_cpu_usage'] - stats['precpu_stats']['system_cpu_usage']
                if system_delta > 0:
                    result['cpu_percent'] = round((cpu_delta / system_delta) * stats['cpu_stats'].get('online_cpus', 1) * 100, 1)
            except:
                pass
        try:
            ports_map = container.attrs.get('NetworkSettings', {}).get('Ports') or {}
            seen = set()
            for _, bindings in ports_map.items():
                if not bindings:
                    continue
                for b in bindings:
                    hp = b.get('HostPort')
                    if hp:
                        seen.add(int(hp))
            result['host_ports'] = sorted(seen)
        except (TypeError, ValueError, AttributeError):
            pass
    except docker.errors.NotFound:
        result['error'] = '컨테이너 없음'
        result['status'] = 'not found'
    except Exception as e:
        result['error'] = str(e)
    
    return result


def _health_probe_host() -> str:
    """
    헬스 체크 대상 호스트. 대시보드가 Docker 안에서 돌면 127.0.0.1은 컨테이너 자신이라
    호스트에 노출된 서비스 포트에 닿지 않음 → host.docker.internal(또는 HEALTH_CHECK_HOST).
    """
    explicit = (os.environ.get("HEALTH_CHECK_HOST") or "").strip()
    if explicit:
        return explicit
    if Path("/.dockerenv").exists():
        return "host.docker.internal"
    return "127.0.0.1"


def probe_http_health(port, health_path: str):
    """
    설정의 path(예: /api/health)로 백엔드 HTTP 상태를 확인한다.
    Docker HEALTHCHECK가 없을 때 대시보드 Health 컬럼에 반영한다.
    반환: 'healthy' | 'unhealthy' | None(프로브 생략)
    """
    if port is None:
        return None
    hp = (health_path or "").strip()
    if not hp or hp.lower().startswith("http"):
        return None
    if not hp.startswith("/"):
        hp = "/" + hp
    try:
        p = int(port)
    except (TypeError, ValueError):
        return None
    host = _health_probe_host()
    url = f"http://{host}:{p}{hp}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "devops-dashboard-health"})
        with urllib.request.urlopen(req, timeout=2.5) as resp:
            code = resp.getcode()
            if 200 <= code < 300:
                return "healthy"
            return "unhealthy"
    except (urllib.error.URLError, TimeoutError, ValueError):
        return "unhealthy"


def get_process_status(target: str, keyword: str, include_metrics: bool = True) -> dict:
    result = {
        'running': False, 'status': 'not running', 'pid': None,
        'memory_mb': 0, 'cpu_percent': 0, 'uptime': 'N/A', 'error': None
    }
    
    if not PSUTIL_AVAILABLE:
        result['error'] = 'psutil 없음'
        return result
    
    try:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'create_time', 'memory_info']):
            try:
                pinfo = proc.info
                name = pinfo.get('name', '').lower()
                cmdline = ' '.join(pinfo.get('cmdline', []) or []).lower()
                
                if target.lower() in name and keyword.lower() in cmdline:
                    result['running'] = True
                    result['status'] = 'running'
                    result['pid'] = pinfo['pid']
                    
                    if include_metrics:
                        mem = pinfo.get('memory_info')
                        if mem:
                            result['memory_mb'] = round(mem.rss / (1024 * 1024), 1)
                        result['cpu_percent'] = proc.cpu_percent(interval=0.1)
                    
                    create_time = pinfo.get('create_time')
                    if create_time:
                        uptime = datetime.now() - datetime.fromtimestamp(create_time)
                        days, hours = uptime.days, uptime.seconds // 3600
                        result['uptime'] = f"{days}일 {hours}시간" if days > 0 else f"{hours}시간 {(uptime.seconds % 3600) // 60}분"
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception as e:
        result['error'] = str(e)
    
    return result


def get_project_status(project_name: str, project_config: dict, force_refresh: bool = False, include_metrics: bool = True) -> dict:
    """단일 프로젝트 상태 조회"""
    cache_key = f"project_detail:{project_name}:{'full' if include_metrics else 'summary'}"
    if not force_refresh:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    ptype = project_config.get('type', 'info')
    environments = project_config.get('environments', [])
    
    result = {
        'name': project_name,
        'type': ptype,
        'environments': [],
        'has_issues': False,
        'running_count': 0,
        'total_count': len(environments),
        'description': project_config.get('description', '')
    }
    
    if ptype == 'info':
        cache.set(cache_key, result)
        return result

    def build_env_status(env: dict) -> dict:
        _site_port_raw = env.get('site_port', '__missing__')
        if _site_port_raw == '__missing__':
            _site_port = env.get('port')
            _site_enabled = bool(_site_port)
        elif _site_port_raw is None:
            _site_port = None
            _site_enabled = False
        else:
            _site_port = _site_port_raw
            _site_enabled = bool(_site_port_raw)
        env_status = {
            'name': env.get('name', 'Default'),
            'target': env.get('target', ''),
            'port': env.get('port'),
            'keyword': env.get('keyword', ''),
            'path': env.get('path', ''),
            'site_port': _site_port,
            'site_enabled': _site_enabled,
            'site_path': env.get('site_path', env.get('path', ''))
        }
        
        if ptype == 'docker':
            status = get_docker_status(env.get('target', ''), include_metrics=include_metrics)
            health_path = (env.get('path') or '').strip()
            if status.get('running') and health_path:
                http_h = probe_http_health(env.get('port'), health_path)
                if http_h is not None:
                    status['health'] = http_h
        elif ptype == 'process':
            status = get_process_status(env.get('target', ''), env.get('keyword', ''), include_metrics=include_metrics)
        else:
            status = {'running': False, 'status': 'info'}

        env_status.update(status)
        if ptype == 'docker':
            hp_list = status.get('host_ports') or []
            cfg_port = env.get('port')
            if 'site_port' in env and env.get('site_port') is not None:
                env_status['port_container_display'] = env['site_port']
            elif hp_list:
                env_status['port_container_display'] = hp_list[0]
            else:
                env_status['port_container_display'] = cfg_port
            env_status['port_backend_display'] = cfg_port
        return env_status

    if len(environments) > 1:
        max_workers = min(4, len(environments))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            env_results = list(executor.map(build_env_status, environments))
    else:
        env_results = [build_env_status(env) for env in environments]

    result['environments'].extend(env_results)
    for env_status in env_results:
        if env_status.get('running'):
            result['running_count'] += 1
        if env_status.get('health') == 'unhealthy' or (ptype == 'docker' and not env_status.get('running')):
            result['has_issues'] = True
    
    cache.set(cache_key, result)
    return result


def get_all_project_status(config: dict, force_refresh: bool = False, include_metrics: bool = True) -> dict:
    cache_key = f"project_status:{'full' if include_metrics else 'summary'}"
    if not force_refresh:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    def build_project_status(item):
        project_name, project_config = item
        return project_name, get_project_status(
            project_name,
            project_config,
            force_refresh=force_refresh,
            include_metrics=include_metrics,
        )

    items = list(config.items())
    if len(items) > 1:
        max_workers = min(8, len(items))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            pairs = list(executor.map(build_project_status, items))
        result = dict(pairs)
    else:
        result = dict(build_project_status(item) for item in items)
    
    cache.set(cache_key, result)
    return result


def get_system_stats(force_refresh: bool = False) -> dict:
    cache_key = 'system_stats'
    if not force_refresh:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
    
    result = {
        'cpu': {'percent': 0, 'cores': 0, 'cores_logical': 0},
        'memory': {'total_gb': 0, 'used_gb': 0, 'percent': 0, 'available_gb': 0},
        'disk': {'total_gb': 0, 'used_gb': 0, 'percent': 0, 'free_gb': 0},
        'boot_time': 'N/A', 'uptime': 'N/A', 'error': None
    }
    
    if not PSUTIL_AVAILABLE:
        result['error'] = 'psutil 없음'
        return result
    
    try:
        result['cpu']['percent'] = psutil.cpu_percent(interval=None)
        result['cpu']['cores'] = psutil.cpu_count(logical=False) or 0
        result['cpu']['cores_logical'] = psutil.cpu_count(logical=True) or 0
        
        mem = psutil.virtual_memory()
        result['memory']['total_gb'] = round(mem.total / (1024**3), 1)
        result['memory']['used_gb'] = round(mem.used / (1024**3), 1)
        result['memory']['available_gb'] = round(mem.available / (1024**3), 1)
        result['memory']['percent'] = mem.percent
        
        disk = psutil.disk_usage('/')
        result['disk']['total_gb'] = round(disk.total / (1024**3), 1)
        result['disk']['used_gb'] = round(disk.used / (1024**3), 1)
        result['disk']['free_gb'] = round(disk.free / (1024**3), 1)
        result['disk']['percent'] = disk.percent
        
        boot_ts = psutil.boot_time()
        boot_dt = datetime.fromtimestamp(boot_ts)
        result['boot_time'] = boot_dt.strftime('%Y-%m-%d %H:%M:%S')
        
        uptime = datetime.now() - boot_dt
        days, hours, mins = uptime.days, uptime.seconds // 3600, (uptime.seconds % 3600) // 60
        result['uptime'] = f"{days}일 {hours}시간 {mins}분" if days > 0 else f"{hours}시간 {mins}분" if hours > 0 else f"{mins}분"
    except Exception as e:
        result['error'] = str(e)
    
    cache.set(cache_key, result)
    return result


def render_markdown_content(content: str) -> str:
    if MARKDOWN_AVAILABLE:
        md = markdown.Markdown(extensions=['fenced_code', 'tables', 'toc', 'nl2br'])
        return md.convert(content)
    import html
    return f"<pre>{html.escape(content)}</pre>"


# ============================================================
# HTML 템플릿 (AJAX 기반)
# ============================================================
HTML_TEMPLATE = r'''
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>DevOps Dashboard v3.0</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
    <style>
        :root {
            --bg-primary: #0d1117;
            --bg-secondary: #161b22;
            --bg-tertiary: #21262d;
            --bg-card: #1c2128;
            --text-primary: #e6edf3;
            --text-secondary: #8b949e;
            --text-muted: #6e7681;
            --accent: #58a6ff;
            --accent-hover: #79c0ff;
            --success: #3fb950;
            --warning: #d29922;
            --error: #f85149;
            --border: #30363d;
        }
        
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans KR', sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            line-height: 1.6;
            min-height: 100vh;
        }
        
        .container { display: flex; min-height: 100vh; }
        
        /* 사이드바 */
        .sidebar {
            width: 280px;
            background: var(--bg-secondary);
            border-right: 1px solid var(--border);
            display: flex;
            flex-direction: column;
            position: fixed;
            height: 100vh;
            overflow: hidden;
            z-index: 100;
        }
        
        .sidebar-header {
            padding: 20px;
            border-bottom: 1px solid var(--border);
            background: var(--bg-tertiary);
            cursor: pointer;
            transition: opacity 0.2s;
        }
        
        .sidebar-header:hover { opacity: 0.85; }
        
        .sidebar-header h1 {
            font-size: 1.1rem;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        
        .sidebar-header p {
            font-size: 0.75rem;
            color: var(--text-secondary);
            margin-top: 4px;
        }
        
        .system-summary {
            padding: 12px 20px;
            background: var(--bg-tertiary);
            border-bottom: 1px solid var(--border);
            display: flex;
            gap: 15px;
            font-size: 0.8rem;
        }
        
        .summary-item {
            display: flex;
            align-items: center;
            gap: 5px;
            color: var(--text-secondary);
        }
        
        .summary-value { color: var(--text-primary); font-weight: 500; }
        
        .fixed-menu {
            padding: 8px 0;
            border-bottom: 1px solid var(--border);
        }
        
        .fixed-menu-item, .project-item {
            display: flex;
            align-items: center;
            padding: 12px 20px;
            cursor: pointer;
            color: var(--text-secondary);
            font-size: 0.875rem;
            transition: all 0.15s;
            gap: 10px;
            border-left: 3px solid transparent;
            user-select: none;
        }
        
        .fixed-menu-item:hover, .project-item:hover {
            background: var(--bg-tertiary);
            color: var(--text-primary);
        }
        
        .fixed-menu-item.active, .project-item.active {
            background: rgba(88, 166, 255, 0.15);
            color: var(--accent);
            border-left-color: var(--accent);
        }
        
        .fixed-menu-item { font-weight: 500; }
        
        .menu-badge {
            margin-left: auto;
            background: var(--accent);
            color: var(--bg-primary);
            padding: 2px 8px;
            border-radius: 10px;
            font-size: 0.65rem;
            font-weight: 600;
        }
        
        .project-list {
            flex: 1;
            overflow-y: auto;
            padding: 10px 0;
        }
        
        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            flex-shrink: 0;
        }
        
        .status-dot.running { background: var(--success); box-shadow: 0 0 6px var(--success); }
        .status-dot.unhealthy { background: var(--error); box-shadow: 0 0 6px var(--error); animation: pulse 1.5s infinite; }
        .status-dot.stopped { background: var(--text-muted); }
        .status-dot.info { background: var(--accent); }
        
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
        
        .project-name { flex: 1; }
        
        .env-count {
            background: var(--bg-tertiary);
            padding: 2px 6px;
            border-radius: 8px;
            font-size: 0.7rem;
            color: var(--text-muted);
        }
        
        /* 메인 콘텐츠 */
        .main-content {
            flex: 1;
            margin-left: 280px;
            display: flex;
            flex-direction: column;
            min-height: 100vh;
        }
        
        /* 로딩 스피너 */
        .loading-container {
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            padding: 60px;
            color: var(--text-secondary);
            gap: 15px;
        }
        
        .spinner {
            width: 40px;
            height: 40px;
            border: 3px solid var(--border);
            border-top-color: var(--accent);
            border-radius: 50%;
            animation: spin 1s linear infinite;
        }
        
        @keyframes spin { to { transform: rotate(360deg); } }
        
        /* 버튼 */
        .btn {
            border: 1px solid var(--border);
            padding: 8px 16px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 0.8rem;
            transition: all 0.2s;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            background: var(--bg-tertiary);
            color: var(--text-secondary);
        }
        
        .btn:hover { background: var(--bg-card); color: var(--text-primary); }
        
        .btn-primary {
            background: var(--accent);
            color: var(--bg-primary);
            border-color: var(--accent);
            font-weight: 500;
        }
        
        .btn-primary:hover { background: var(--accent-hover); }
        
        .btn-icon {
            width: 32px;
            height: 32px;
            padding: 0;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 0.9rem;
        }
        
        .btn-sm {
            padding: 4px 10px;
            font-size: 0.72rem;
            border-radius: 6px;
        }
        
        .env-ctl-btns {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            margin-top: 10px;
            padding-top: 10px;
            border-top: 1px solid var(--border);
        }
        
        /* 패널 헤더 */
        .panel-header {
            padding: 20px;
            background: var(--bg-secondary);
            border-bottom: 1px solid var(--border);
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 10px;
        }
        
        .panel-title {
            font-size: 1.25rem;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        .status-badge {
            font-size: 0.75rem;
            padding: 4px 10px;
            border-radius: 12px;
            font-weight: 500;
        }
        
        .status-badge.running { background: rgba(63, 185, 80, 0.2); color: var(--success); }
        .status-badge.unhealthy { background: rgba(248, 81, 73, 0.2); color: var(--error); }
        .status-badge.stopped { background: rgba(110, 118, 129, 0.2); color: var(--text-muted); }
        .status-badge.info { background: rgba(88, 166, 255, 0.2); color: var(--accent); }
        
        .panel-actions { display: flex; gap: 10px; align-items: center; }

        .toast {
            min-width: 260px;
            max-width: 420px;
            padding: 12px 14px;
            border-radius: 10px;
            border: 1px solid var(--border);
            background: var(--bg-card);
            color: var(--text-primary);
            box-shadow: 0 10px 24px rgba(0, 0, 0, 0.28);
            font-size: 0.9rem;
            line-height: 1.45;
        }
        .toast.success { border-left: 4px solid var(--success); }
        .toast.error { border-left: 4px solid var(--error); }
        .toast.info { border-left: 4px solid var(--accent); }
        
        /* 환경 그리드 */
        .env-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
            gap: 15px;
            padding: 20px;
        }
        
        /* 컨테이너 행 + 백엔드 행 (포트가 다른 2종 카드 정렬) */
        .env-grid-stacked {
            display: flex;
            flex-direction: column;
            gap: 15px;
            padding: 20px;
        }
        .env-grid-row {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(min(100%, 320px), 1fr));
            gap: 15px;
            align-items: stretch;
        }
        
        .env-card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 15px;
            transition: all 0.2s;
        }
        
        .env-card:hover { border-color: var(--accent); transform: translateY(-2px); }
        .env-card.running { border-left: 3px solid var(--success); }
        .env-card.unhealthy { border-left: 3px solid var(--error); }
        .env-card.stopped { border-left: 3px solid var(--text-muted); }
        
        .env-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 12px;
            padding-bottom: 10px;
            border-bottom: 1px solid var(--border);
        }
        
        .env-name {
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        
        .env-dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
        }
        
        .env-dot.running { background: var(--success); box-shadow: 0 0 6px var(--success); }
        .env-dot.unhealthy { background: var(--error); box-shadow: 0 0 6px var(--error); animation: pulse 1.5s infinite; }
        .env-dot.stopped { background: var(--text-muted); }
        .env-dot.warn { background: var(--warning); box-shadow: 0 0 6px rgba(210, 153, 34, 0.5); }
        
        .env-actions { display: flex; gap: 8px; }
        
        .env-metrics {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 10px;
            margin-bottom: 12px;
        }
        
        .env-metric {
            text-align: center;
            padding: 8px;
            background: var(--bg-tertiary);
            border-radius: 6px;
        }
        
        .env-metric .label {
            display: block;
            font-size: 0.65rem;
            color: var(--text-muted);
            text-transform: uppercase;
            margin-bottom: 3px;
        }
        
        .env-metric .value { font-size: 0.85rem; font-weight: 600; }
        .env-metric .value.success { color: var(--success); }
        .env-metric .value.error { color: var(--error); }
        .env-metric .value.warning { color: var(--warning); }
        
        .env-target {
            font-size: 0.75rem;
            color: var(--text-muted);
            padding-top: 10px;
            border-top: 1px solid var(--border);
        }
        
        /* 문서 영역 */
        .docs-section {
            padding: 20px;
            border-top: 1px solid var(--border);
        }
        
        .docs-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
        }
        
        .docs-header h2 {
            font-size: 1rem;
            font-weight: 500;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        
        .file-list {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-bottom: 20px;
        }
        
        .file-list-grouped {
            margin-bottom: 20px;
        }
        
        .folder-group {
            margin-bottom: 12px;
        }
        
        .folder-header {
            font-size: 0.85rem;
            color: var(--text-secondary);
            margin-bottom: 8px;
            padding-left: 4px;
            font-weight: 500;
        }
        
        .folder-files {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            padding-left: 8px;
            border-left: 2px solid var(--border);
        }
        
        .file-chip {
            background: var(--bg-tertiary);
            border: 1px solid var(--border);
            padding: 6px 12px;
            border-radius: 16px;
            font-size: 0.8rem;
            color: var(--text-secondary);
            cursor: pointer;
            transition: all 0.2s;
        }
        
        .file-chip:hover, .file-chip.active {
            background: var(--accent);
            color: var(--bg-primary);
            border-color: var(--accent);
        }
        
        .markdown-content {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 25px;
            line-height: 1.8;
        }
        
        .markdown-content h1, .markdown-content h2, .markdown-content h3 {
            margin-top: 20px;
            margin-bottom: 10px;
        }
        
        .markdown-content h1 { font-size: 1.75em; border-bottom: 1px solid var(--border); padding-bottom: 0.3em; }
        .markdown-content h2 { font-size: 1.4em; border-bottom: 1px solid var(--border); padding-bottom: 0.3em; }
        .markdown-content p { margin-bottom: 12px; }
        
        .markdown-content code {
            background: var(--bg-tertiary);
            padding: 0.2em 0.4em;
            border-radius: 4px;
            font-family: 'SF Mono', Consolas, monospace;
        }
        
        .markdown-content pre {
            background: var(--bg-tertiary);
            padding: 15px;
            border-radius: 8px;
            overflow-x: auto;
            margin: 15px 0;
            border: 1px solid var(--border);
        }
        
        .markdown-content pre code { background: none; padding: 0; }
        
        .markdown-content table { width: 100%; border-collapse: collapse; margin: 15px 0; }
        .markdown-content th, .markdown-content td { border: 1px solid var(--border); padding: 10px 12px; text-align: left; }
        .markdown-content th { background: var(--bg-tertiary); }
        .markdown-content a { color: var(--accent); }
        
        /* 시스템 대시보드 */
        .system-dashboard { padding: 30px; }
        
        .dashboard-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 25px;
            flex-wrap: wrap;
            gap: 15px;
        }
        
        .dashboard-header h2 {
            font-size: 1.5rem;
            display: flex;
            align-items: center;
            gap: 12px;
        }
        
        .dashboard-header p { color: var(--text-secondary); font-size: 0.9rem; margin-top: 5px; }
        
        .system-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 20px;
            max-width: 1000px;
        }
        
        .system-card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 25px;
            transition: transform 0.2s;
        }
        
        .system-card:hover { transform: translateY(-2px); }
        .system-card.full-width { grid-column: span 2; }
        
        .card-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
        }
        
        .card-title {
            display: flex;
            align-items: center;
            gap: 10px;
            font-weight: 500;
        }
        
        .card-title .icon { font-size: 1.25rem; }
        
        .card-value {
            font-size: 2rem;
            font-weight: 700;
        }
        
        .card-value.warning { color: var(--warning); }
        .card-value.danger { color: var(--error); }
        
        .progress-bar {
            width: 100%;
            height: 12px;
            background: var(--bg-tertiary);
            border-radius: 6px;
            overflow: hidden;
            margin-top: 15px;
        }
        
        .progress-fill {
            height: 100%;
            border-radius: 6px;
            transition: width 0.5s;
            background: linear-gradient(90deg, var(--success), #2ea043);
        }
        
        .progress-fill.warning { background: linear-gradient(90deg, var(--warning), #e3b341); }
        .progress-fill.danger { background: linear-gradient(90deg, var(--error), #ff6b6b); }
        
        .progress-labels {
            display: flex;
            justify-content: space-between;
            margin-top: 8px;
            font-size: 0.75rem;
            color: var(--text-muted);
        }
        
        .card-details { margin-top: 20px; padding-top: 15px; border-top: 1px solid var(--border); }
        
        .detail-row {
            display: flex;
            justify-content: space-between;
            padding: 6px 0;
            font-size: 0.85rem;
        }
        
        .detail-label { color: var(--text-muted); }
        .detail-value { color: var(--text-secondary); font-weight: 500; }
        
        .info-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 15px;
        }
        
        .info-item {
            text-align: center;
            padding: 15px;
            background: var(--bg-tertiary);
            border-radius: 8px;
        }
        
        .info-item .icon { font-size: 1.5rem; margin-bottom: 8px; }
        .info-item .label { font-size: 0.75rem; color: var(--text-muted); text-transform: uppercase; margin-bottom: 5px; }
        .info-item .value { font-weight: 600; }
        
        /* Quick Links */
        .quick-links { margin-top: 25px; }
        .section-title { font-size: 1rem; margin-bottom: 15px; color: var(--text-secondary); display: flex; align-items: center; gap: 8px; }
        
        .quick-links-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
        }
        
        .quick-link-card {
            display: flex;
            align-items: center;
            gap: 15px;
            padding: 20px;
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            text-decoration: none;
            color: var(--text-primary);
            transition: all 0.2s;
        }
        
        .quick-link-card:hover {
            background: var(--bg-tertiary);
            border-color: var(--accent);
            transform: translateY(-2px);
        }
        
        .quick-link-icon {
            width: 50px;
            height: 50px;
            display: flex;
            align-items: center;
            justify-content: center;
            background: var(--bg-tertiary);
            border-radius: 12px;
            font-size: 1.5rem;
            transition: background 0.2s;
        }
        
        .quick-link-card:hover .quick-link-icon { background: var(--accent); }
        
        .quick-link-info { flex: 1; }
        .quick-link-info .title { font-weight: 600; margin-bottom: 4px; }
        .quick-link-info .url { font-size: 0.75rem; color: var(--text-muted); }
        
        .quick-link-arrow { color: var(--text-muted); font-size: 1.25rem; transition: transform 0.2s; }
        .quick-link-card:hover .quick-link-arrow { color: var(--accent); transform: translateX(3px); }
        
        /* 모달 */
        .modal-overlay {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.8);
            z-index: 1000;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }
        
        .modal-overlay.show { display: flex; }
        
        .modal-content {
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: 12px;
            width: 100%;
            max-width: 900px;
            max-height: 80vh;
            display: flex;
            flex-direction: column;
        }
        
        .modal-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 15px 20px;
            border-bottom: 1px solid var(--border);
            background: var(--bg-tertiary);
            border-radius: 12px 12px 0 0;
        }
        
        .modal-title { font-weight: 600; display: flex; align-items: center; gap: 10px; }
        
        .modal-close {
            background: none;
            border: none;
            color: var(--text-muted);
            font-size: 1.5rem;
            cursor: pointer;
            transition: color 0.2s;
        }
        
        .modal-close:hover { color: var(--error); }
        
        .modal-body { flex: 1; overflow-y: auto; }
        
        .log-container {
            background: #000;
            color: #00ff00;
            font-family: 'SF Mono', Consolas, monospace;
            font-size: 0.8rem;
            line-height: 1.5;
            padding: 15px;
            white-space: pre-wrap;
            word-break: break-all;
            min-height: 300px;
            max-height: 60vh;
            overflow-y: auto;
        }
        
        .log-container.loading { display: flex; align-items: center; justify-content: center; color: var(--text-muted); }
        .log-container.error { color: var(--error); }
        
        .modal-footer {
            padding: 12px 20px;
            border-top: 1px solid var(--border);
            display: flex;
            justify-content: flex-end;
            gap: 10px;
        }
        
        /* 스크롤바 */
        ::-webkit-scrollbar { width: 8px; height: 8px; }
        ::-webkit-scrollbar-track { background: var(--bg-primary); }
        ::-webkit-scrollbar-thumb { background: var(--bg-tertiary); border-radius: 4px; }
        ::-webkit-scrollbar-thumb:hover { background: var(--border); }
        
        @media (max-width: 900px) {
            .system-grid { grid-template-columns: 1fr; }
            .system-card.full-width { grid-column: span 1; }
            .info-grid { grid-template-columns: repeat(2, 1fr); }
        }
    </style>
</head>
<body>
    <div class="container">
        <aside class="sidebar">
            <div class="sidebar-header" onclick="loadSystemStatus()">
                <h1>🚀 INTEGRATED CONTROL</h1>
                <p>실시간 인프라 모니터링 v3.0</p>
            </div>
            
            <div class="system-summary">
                <div class="summary-item">
                    <span>🟢</span>
                    <span class="summary-value" id="runningCount">{{ running_count }}</span>
                </div>
                <div class="summary-item">
                    <span>🔴</span>
                    <span class="summary-value" id="issueCount">{{ issue_count }}</span>
                </div>
                <div class="summary-item">
                    <span>⏱️</span>
                    <span class="summary-value" id="cacheAge">0s</span>
                </div>
            </div>
            
            <div class="fixed-menu">
                <div class="fixed-menu-item active" id="systemStatusBtn" onclick="loadSystemStatus()">
                    <span>🖥️</span>
                    <span>System Status</span>
                    <span class="menu-badge">Live</span>
                </div>
                <div class="fixed-menu-item" onclick="openConfigModal()">
                    <span>⚙️</span>
                    <span>설정</span>
                </div>
            </div>
            
            <div class="project-list" id="projectList">
                {% for project_name, project_info in projects.items() %}
                <div class="project-item" data-project="{{ project_name }}" onclick="loadProject('{{ project_name }}')">
                    <span class="status-dot {{ project_info.status_class }}"></span>
                    <span class="project-name">{{ project_name }}</span>
                    {% if project_info.total_count > 0 %}
                    <span class="env-count">{{ project_info.running_count }}/{{ project_info.total_count }}</span>
                    {% endif %}
                </div>
                {% endfor %}
            </div>
        </aside>
        
        <main class="main-content" id="mainContent">
            <!-- 초기 로딩: 시스템 상태 -->
        </main>
    </div>

    <div id="toastContainer" style="position: fixed; top: 20px; right: 20px; z-index: 2000; display: flex; flex-direction: column; gap: 10px;"></div>
    
    <!-- 로그 모달 -->
    <div class="modal-overlay" id="logModal" onclick="closeLogModal(event)">
        <div class="modal-content" onclick="event.stopPropagation()">
            <div class="modal-header">
                <div class="modal-title">
                    <span>📜</span>
                    <span id="modalTitle">컨테이너 로그</span>
                </div>
                <button class="modal-close" onclick="closeLogModal()">&times;</button>
            </div>
            <div class="modal-body">
                <div class="log-container" id="logContent">로그를 불러오는 중...</div>
            </div>
            <div class="modal-footer">
                <button class="btn" onclick="refreshLogs()">🔄 새로고침</button>
                <button class="btn" onclick="closeLogModal()">닫기</button>
            </div>
        </div>
    </div>
    
    <!-- 설정 모달 -->
    <div class="modal-overlay" id="configModal" onclick="closeConfigModal(event)">
        <div class="modal-content" onclick="event.stopPropagation()" style="max-width: 1000px; max-height: 90vh;">
            <div class="modal-header">
                <div class="modal-title">
                    <span>⚙️</span>
                    <span>대시보드 설정</span>
                </div>
                <button class="modal-close" onclick="closeConfigModal()">&times;</button>
            </div>
            <div class="modal-body" style="padding: 0; display: flex; flex-direction: column; height: calc(90vh - 120px);">
                <!-- 탭 메뉴 -->
                <div style="display: flex; border-bottom: 1px solid var(--border); background: var(--bg-tertiary);">
                    <div class="config-tab active" onclick="switchConfigTab('mapping')" data-tab="mapping">
                        <span>📝 이름 매핑</span>
                    </div>
                    <div class="config-tab" onclick="switchConfigTab('order')" data-tab="order">
                        <span>📋 순서 설정</span>
                    </div>
                    <div class="config-tab" onclick="switchConfigTab('projects')" data-tab="projects">
                        <span>🔧 프로젝트 설정</span>
                    </div>
                    <div class="config-tab" onclick="switchConfigTab('dashboard')" data-tab="dashboard">
                        <span>🌐 대시보드 설정</span>
                    </div>
                </div>
                
                <!-- 탭 컨텐츠 -->
                <div style="flex: 1; overflow-y: auto; padding: 20px;">
                    <!-- 이름 매핑 탭 -->
                    <div id="tab-mapping" class="config-tab-content active">
                        <h3 style="font-size: 1rem; margin-bottom: 10px; color: var(--text-primary);">📝 프로젝트 이름 매핑</h3>
                        <div style="margin-bottom: 15px; color: var(--text-secondary); font-size: 0.85rem;">
                            폴더명과 표시할 이름을 매핑할 수 있습니다. 매핑이 없으면 폴더명이 그대로 표시됩니다.
                        </div>
                        <div id="configMappingList" style="margin-bottom: 15px;">
                            <!-- 동적으로 생성됨 -->
                        </div>
                        <div style="display: flex; gap: 10px; margin-bottom: 15px;">
                            <button class="btn btn-primary" onclick="addMappingRow()">➕ 추가</button>
                            <button class="btn" onclick="loadAvailableFolders()">📁 폴더 목록 새로고침</button>
                        </div>
                        <div id="availableFolders" style="display: none; padding: 15px; background: var(--bg-tertiary); border-radius: 8px; margin-bottom: 15px;">
                            <div style="font-weight: 500; margin-bottom: 10px;">사용 가능한 폴더:</div>
                            <div id="folderList" style="display: flex; flex-wrap: wrap; gap: 8px;"></div>
                        </div>
                    </div>
                    
                    <!-- 순서 설정 탭 -->
                    <div id="tab-order" class="config-tab-content">
                        <h3 style="font-size: 1rem; margin-bottom: 10px; color: var(--text-primary);">📋 프로젝트 표시 순서</h3>
                        <div style="margin-bottom: 15px; color: var(--text-secondary); font-size: 0.85rem;">
                            드래그 앤 드롭으로 순서를 변경하거나, 위/아래 버튼을 사용할 수 있습니다.
                        </div>
                        <div id="projectOrderList" style="margin-bottom: 15px;">
                            <!-- 동적으로 생성됨 -->
                        </div>
                    </div>
                    
                    <!-- 프로젝트 설정 탭 -->
                    <div id="tab-projects" class="config-tab-content">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px;">
                            <h3 style="font-size: 1rem; margin: 0; color: var(--text-primary);">🔧 프로젝트 설정</h3>
                            <button class="btn btn-primary" onclick="addProjectConfig()">➕ 프로젝트 추가</button>
                        </div>
                        <div style="margin-bottom: 15px; color: var(--text-secondary); font-size: 0.85rem;">
                            프로젝트의 타입, 환경, 포트 등을 설정할 수 있습니다.
                        </div>
                        <div id="projectConfigList">
                            <!-- 동적으로 생성됨 -->
                        </div>
                    </div>

                    <!-- 대시보드 설정 탭 -->
                    <div id="tab-dashboard" class="config-tab-content">
                        <h3 style="font-size: 1rem; margin-bottom: 10px; color: var(--text-primary);">🌐 대시보드 링크 설정</h3>
                        <div style="margin-bottom: 15px; color: var(--text-secondary); font-size: 0.85rem;">
                            Quick Links와 공개 사이트 주소를 설정할 수 있습니다. 비워두면 현재 환경 변수 또는 기본값을 사용합니다.
                        </div>
                        <div style="display: grid; gap: 12px; margin-bottom: 20px;">
                            <label style="display: grid; gap: 6px;">
                                <span style="font-size: 0.85rem; color: var(--text-secondary);">SITE_BASE_URL</span>
                                <input id="dashboardSiteBaseUrl" type="text" placeholder="예: https://ops.example.com" style="width: 100%; padding: 10px; background: var(--bg-tertiary); border: 1px solid var(--border); border-radius: 6px; color: var(--text-primary);" onchange="updateDashboardSetting('site_base_url', this.value)">
                            </label>
                            <label style="display: grid; gap: 6px;">
                                <span style="font-size: 0.85rem; color: var(--text-secondary);">SITE_HOST_LABEL</span>
                                <input id="dashboardSiteHostLabel" type="text" placeholder="예: ops.example.com" style="width: 100%; padding: 10px; background: var(--bg-tertiary); border: 1px solid var(--border); border-radius: 6px; color: var(--text-primary);" onchange="updateDashboardSetting('site_host_label', this.value)">
                            </label>
                        </div>
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                            <h4 style="font-size: 0.95rem; margin: 0; color: var(--text-primary);">🔗 Quick Links</h4>
                            <button class="btn btn-primary" onclick="addQuickLinkRow()">➕ 링크 추가</button>
                        </div>
                        <div id="dashboardQuickLinksList" style="display: grid; gap: 12px;">
                            <!-- 동적으로 생성됨 -->
                        </div>
                    </div>
                </div>
            </div>
            <div class="modal-footer">
                <button class="btn btn-primary" onclick="saveConfig()">💾 저장</button>
                <button class="btn" onclick="closeConfigModal()">취소</button>
            </div>
        </div>
    </div>
    
    <style>
        .config-tab {
            padding: 12px 20px;
            cursor: pointer;
            border-bottom: 2px solid transparent;
            color: var(--text-secondary);
            transition: all 0.2s;
            user-select: none;
        }
        .config-tab:hover {
            background: var(--bg-secondary);
            color: var(--text-primary);
        }
        .config-tab.active {
            color: var(--accent);
            border-bottom-color: var(--accent);
            background: var(--bg-secondary);
        }
        .config-tab-content {
            display: none;
        }
        .config-tab-content.active {
            display: block;
        }
        .draggable-item {
            cursor: move;
            user-select: none;
        }
        .draggable-item.dragging {
            opacity: 0.5;
        }
        .drag-over {
            border-top: 2px solid var(--accent);
        }
    </style>
    
    <script>
        const SITE_BASE_URL = {{ site_base_url | tojson }};
        const SITE_HOST_LABEL = {{ site_host_label | tojson }};
        const DEFAULT_PUBLIC_SITE = {{ default_public_site | tojson }};
        const DASHBOARD_PORT = {{ dashboard_port | tojson }};
        const QUICK_LINKS = {{ quick_links | tojson }};
        const CONFIG_META_KEYS = ['project_name_mapping', 'project_order', 'dashboard_settings'];
        /** localhost/127.* 가 들어와도 외부 접근용 기본 베이스로 정규화 */
        function getPublicSiteBase() {
            let b = (typeof SITE_BASE_URL === 'string' && SITE_BASE_URL.trim()) ? SITE_BASE_URL.trim().replace(/\/$/, '') : DEFAULT_PUBLIC_SITE;
            try {
                const u = new URL(b.match(/^https?:\/\//i) ? b : 'http://' + b);
                const h = (u.hostname || '').toLowerCase();
                if (!h || h === 'localhost' || h === '127.0.0.1' || h === '::1' || h === '0.0.0.0' || h.startsWith('127.')) return DEFAULT_PUBLIC_SITE;
            } catch (e) {
                return DEFAULT_PUBLIC_SITE;
            }
            return b;
        }
        /** 서버가 내려준 베이스(우선) + 포트 → 절대 origin. 문자열 ':포트'만 붙이는 방식은 호스트가 localhost일 때 오동작할 수 있어 URL로 조합 */
        function absoluteOriginForPort(baseRaw, port) {
            const p = Number(port);
            if (!p || p < 1 || p > 65535) return '#';
            let base = String(baseRaw || '').trim().replace(/\/$/, '');
            if (!base) base = DEFAULT_PUBLIC_SITE;
            try {
                const withScheme = /^https?:\/\//i.test(base) ? base : 'http://' + base;
                const u = new URL(withScheme);
                const fallbackUrl = new URL(/^https?:\/\//i.test(DEFAULT_PUBLIC_SITE) ? DEFAULT_PUBLIC_SITE : 'http://' + DEFAULT_PUBLIC_SITE);
                let h = (u.hostname || '').toLowerCase();
                if (!h || h === 'localhost' || h === '127.0.0.1' || h === '::1' || h === '0.0.0.0' || h.startsWith('127.')) {
                    u.hostname = fallbackUrl.hostname;
                    u.protocol = fallbackUrl.protocol;
                }
                u.port = String(p);
                return u.origin;
            } catch (e) {
                return getPublicSiteBase() + ':' + p;
            }
        }
        function absoluteUrlForPortAndPath(baseRaw, port, pathRaw) {
            const origin = absoluteOriginForPort(baseRaw, port);
            if (origin === '#') return '#';
            const path = String(pathRaw || '').trim();
            if (!path) return origin;
            if (/^https?:\/\//i.test(path)) return path;
            return origin + (path.startsWith('/') ? path : '/' + path);
        }
        function showToast(message, type = 'info', durationMs = 3200) {
            const container = document.getElementById('toastContainer');
            if (!container) return;
            const toast = document.createElement('div');
            toast.className = `toast ${type}`;
            toast.textContent = message;
            container.appendChild(toast);
            window.setTimeout(() => {
                toast.style.opacity = '0';
                toast.style.transform = 'translateY(-4px)';
                toast.style.transition = 'opacity 0.2s ease, transform 0.2s ease';
                window.setTimeout(() => toast.remove(), 220);
            }, durationMs);
        }
        function getDashboardLabel() {
            return `${SITE_HOST_LABEL}:${DASHBOARD_PORT}`;
        }
        function getQuickLinkHref(link) {
            if (!link || typeof link !== 'object') return '#';
            if (link.url) return String(link.url);
            if (link.port) return absoluteUrlForPortAndPath(getPublicSiteBase(), link.port, link.path || '');
            return '#';
        }
        function getQuickLinkLabel(link) {
            if (!link || typeof link !== 'object') return '';
            if (link.label) return String(link.label);
            if (link.url) {
                try {
                    return new URL(String(link.url)).host;
                } catch (e) {
                    return String(link.url);
                }
            }
            if (link.port) {
                return `${SITE_HOST_LABEL}:${link.port}${link.path || ''}`;
            }
            return '';
        }
        function renderQuickLinks() {
            if (!Array.isArray(QUICK_LINKS) || QUICK_LINKS.length === 0) {
                return '<div style="color: var(--text-muted);">표시할 Quick Link가 없습니다.</div>';
            }
            return QUICK_LINKS.map(link => `
                <a class="quick-link-card" href="${getQuickLinkHref(link)}" target="_blank">
                    <div class="quick-link-icon">${link.icon || '🔗'}</div>
                    <div class="quick-link-info"><div class="title">${link.title || 'Quick Link'}</div><div class="url">${getQuickLinkLabel(link)}</div></div>
                    <div class="quick-link-arrow">→</div>
                </a>
            `).join('');
        }
        let currentProject = null;
        let currentLogTarget = null;
        let currentFile = null;
        
        // 초기화
        document.addEventListener('DOMContentLoaded', () => {
            loadSystemStatus();
            updateCacheAge();
            setInterval(updateCacheAge, 1000);
        });
        
        // 캐시 나이 업데이트
        let cacheTime = Date.now();
        function updateCacheAge() {
            const age = Math.floor((Date.now() - cacheTime) / 1000);
            document.getElementById('cacheAge').textContent = age + 's';
        }
        
        // 사이드바 활성 상태 업데이트
        function setActiveProject(projectName) {
            document.querySelectorAll('.project-item').forEach(el => el.classList.remove('active'));
            document.getElementById('systemStatusBtn').classList.remove('active');
            
            if (projectName) {
                const item = document.querySelector(`.project-item[data-project="${projectName}"]`);
                if (item) item.classList.add('active');
            } else {
                document.getElementById('systemStatusBtn').classList.add('active');
            }
        }
        
        // 로딩 표시
        function showLoading() {
            document.getElementById('mainContent').innerHTML = `
                <div class="loading-container">
                    <div class="spinner"></div>
                    <span>로딩 중...</span>
                </div>
            `;
        }
        
        // 시스템 상태 로드
        async function loadSystemStatus() {
            currentProject = null;
            currentFile = null;
            setActiveProject(null);
            showLoading();
            
            try {
                const response = await fetch('/api/system');
                const data = await response.json();
                cacheTime = Date.now();
                renderSystemStatus(data);
            } catch (error) {
                document.getElementById('mainContent').innerHTML = `
                    <div class="loading-container">
                        <span style="color: var(--error);">오류: ${error.message}</span>
                    </div>
                `;
            }
        }
        
        // 시스템 상태 렌더링
        function renderSystemStatus(data) {
            const progressClass = (percent) => percent >= 90 ? 'danger' : percent >= 70 ? 'warning' : '';
            
            document.getElementById('mainContent').innerHTML = `
                <div class="system-dashboard">
                    <div class="dashboard-header">
                        <div>
                            <h2>📊 시스템 리소스 현황</h2>
                            <p>서버의 실시간 상태를 모니터링합니다.</p>
                        </div>
                        <button class="btn" onclick="loadSystemStatus()">🔄 새로고침</button>
                    </div>
                    
                    <div class="system-grid">
                        <div class="system-card">
                            <div class="card-header">
                                <div class="card-title"><span class="icon">🖥️</span>CPU 사용률</div>
                                <div class="card-value ${progressClass(data.cpu.percent)}">${data.cpu.percent}%</div>
                            </div>
                            <div class="progress-bar">
                                <div class="progress-fill ${progressClass(data.cpu.percent)}" style="width: ${data.cpu.percent}%"></div>
                            </div>
                            <div class="progress-labels"><span>0%</span><span>50%</span><span>100%</span></div>
                            <div class="card-details">
                                <div class="detail-row"><span class="detail-label">물리 코어</span><span class="detail-value">${data.cpu.cores}개</span></div>
                                <div class="detail-row"><span class="detail-label">논리 코어</span><span class="detail-value">${data.cpu.cores_logical}개</span></div>
                            </div>
                        </div>
                        
                        <div class="system-card">
                            <div class="card-header">
                                <div class="card-title"><span class="icon">🧠</span>메모리 사용률</div>
                                <div class="card-value ${progressClass(data.memory.percent)}">${data.memory.percent}%</div>
                            </div>
                            <div class="progress-bar">
                                <div class="progress-fill ${progressClass(data.memory.percent)}" style="width: ${data.memory.percent}%"></div>
                            </div>
                            <div class="progress-labels"><span>0 GB</span><span>${(data.memory.total_gb/2).toFixed(1)} GB</span><span>${data.memory.total_gb} GB</span></div>
                            <div class="card-details">
                                <div class="detail-row"><span class="detail-label">사용 중</span><span class="detail-value">${data.memory.used_gb} GB</span></div>
                                <div class="detail-row"><span class="detail-label">사용 가능</span><span class="detail-value">${data.memory.available_gb} GB</span></div>
                            </div>
                        </div>
                        
                        <div class="system-card">
                            <div class="card-header">
                                <div class="card-title"><span class="icon">💾</span>디스크 사용률</div>
                                <div class="card-value ${progressClass(data.disk.percent)}">${data.disk.percent}%</div>
                            </div>
                            <div class="progress-bar">
                                <div class="progress-fill ${progressClass(data.disk.percent)}" style="width: ${data.disk.percent}%"></div>
                            </div>
                            <div class="progress-labels"><span>0 GB</span><span>${Math.round(data.disk.total_gb/2)} GB</span><span>${Math.round(data.disk.total_gb)} GB</span></div>
                            <div class="card-details">
                                <div class="detail-row"><span class="detail-label">사용 중</span><span class="detail-value">${data.disk.used_gb} GB</span></div>
                                <div class="detail-row"><span class="detail-label">남은 용량</span><span class="detail-value">${data.disk.free_gb} GB</span></div>
                            </div>
                        </div>
                        
                        <div class="system-card">
                            <div class="card-header">
                                <div class="card-title"><span class="icon">⏱️</span>서버 가동 시간</div>
                            </div>
                            <div class="card-value" style="font-size: 1.5rem; margin-top: 10px;">${data.uptime}</div>
                            <div class="card-details">
                                <div class="detail-row"><span class="detail-label">부팅 시간</span><span class="detail-value">${data.boot_time}</span></div>
                            </div>
                        </div>
                        
                        <div class="system-card full-width">
                            <div class="card-header">
                                <div class="card-title"><span class="icon">🐳</span>서비스 현황 요약</div>
                            </div>
                            <div class="info-grid">
                                <div class="info-item"><div class="icon">🟢</div><div class="label">Running</div><div class="value" id="summaryRunning">${document.getElementById('runningCount').textContent}</div></div>
                                <div class="info-item"><div class="icon">🔴</div><div class="label">Issues</div><div class="value" id="summaryIssues">${document.getElementById('issueCount').textContent}</div></div>
                                <div class="info-item"><div class="icon">📁</div><div class="label">Projects</div><div class="value">${document.querySelectorAll('.project-item').length}</div></div>
                                <div class="info-item"><div class="icon">🌐</div><div class="label">Dashboard</div><div class="value">${getDashboardLabel()}</div></div>
                            </div>
                        </div>
                    </div>
                    
                    <div class="quick-links">
                        <div class="section-title">🔗 Quick Links</div>
                        <div class="quick-links-grid">${renderQuickLinks()}</div>
                    </div>
                </div>
            `;
        }
        
        async function dockerAction(target, action, projectName) {
            const labels = { start: '시작', stop: '중지', restart: '재시작' };
            if (!confirm(`컨테이너 "${target}" → ${labels[action] || action} 할까요?`)) return;
            showToast(`"${target}" ${labels[action] || action} 요청 중...`, 'info', 1800);
            try {
                const response = await fetch('/api/docker/action', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ target, action })
                });
                const res = await response.json();
                if (!response.ok || res.status === 'error') {
                    showToast(res.error || res.message || '요청 실패', 'error', 4200);
                    return;
                }
                showToast(res.message || '완료', 'success', 3200);
                await loadProject(projectName);
            } catch (e) {
                showToast('오류: ' + e.message, 'error', 4200);
            }
        }
        
        // 프로젝트 로드
        async function loadProject(name, forceRefresh = false) {
            currentProject = name;
            currentFile = null;
            setActiveProject(name);
            showLoading();
            
            try {
                const qs = forceRefresh ? '?refresh=true' : '';
                const response = await fetch(`/api/project/${encodeURIComponent(name)}${qs}`);
                const data = await response.json();
                cacheTime = Date.now();
                renderProject(data);
            } catch (error) {
                document.getElementById('mainContent').innerHTML = `
                    <div class="loading-container">
                        <span style="color: var(--error);">오류: ${error.message}</span>
                    </div>
                `;
            }
        }
        
        // 프로젝트 렌더링
        function renderProject(data) {
            const statusClass = data.has_issues ? 'unhealthy' : data.running_count > 0 ? 'running' : 'stopped';
            const statusText = data.type === 'info' ? 'Info' : 
                data.has_issues ? `${data.running_count}/${data.total_count} (Issues)` :
                data.running_count > 0 ? `${data.running_count}/${data.total_count} Running` : 'Stopped';
            const siteBaseForLinks = (data.public_site_base && String(data.public_site_base).trim())
                ? String(data.public_site_base).trim().replace(/\/$/, '')
                : getPublicSiteBase();
            
            let envCards = '';
            let envGridWrapClass = 'env-grid';
            if (data.type !== 'info' && data.environments) {
                const hasBackendProbe = (env) =>
                    data.type === 'docker' && String(env.path || '').trim() !== '';
                const portForContainerCard = (env) => {
                    if (data.type === 'docker' && env.port_container_display != null && env.port_container_display !== undefined)
                        return env.port_container_display;
                    return env.port != null ? env.port : 'N/A';
                };
                const portForBackendCard = (env) => {
                    if (env.port_backend_display != null && env.port_backend_display !== undefined)
                        return env.port_backend_display;
                    return env.port != null ? env.port : 'N/A';
                };
                const renderMainCard = (env, splitBackend) => {
                    const envClass = splitBackend
                        ? (env.running ? 'running' : 'stopped')
                        : (env.running ? (env.health === 'unhealthy' ? 'unhealthy' : 'running') : 'stopped');
                    const dotClass = envClass;
                    const statusValue = env.running ? 'Running' : 'Stopped';
                    const statusValueClass = env.running ? 'success' : 'error';
                    const siteEnabled = env.site_enabled !== false;
                    const sitePort = env.site_port;
                    const sitePath = env.site_path || env.path || '';
                    return `
                        <div class="env-card ${envClass}">
                            <div class="env-header">
                                <div class="env-name">
                                    <span class="env-dot ${dotClass}"></span>
                                    ${env.name}
                                </div>
                                <div class="env-actions">
                                    ${data.type === 'docker' ? `<button class="btn btn-icon" onclick="openLogModal('${env.target}', '${data.name} - ${env.name}')" title="콘솔 로그">📜</button>` : ''}
                                    ${(siteEnabled && sitePort) ? `<a class="btn btn-icon btn-primary" href="${absoluteUrlForPortAndPath(siteBaseForLinks, sitePort, sitePath)}" target="_blank" title="사이트 접속">🌐</a>` : ''}
                                </div>
                            </div>
                            <div class="env-metrics">
                                <div class="env-metric"><span class="label">상태</span><span class="value ${statusValueClass}">${statusValue}</span></div>
                                ${data.type === 'docker' ? `
                                    <div class="env-metric"><span class="label">메모리</span><span class="value">${env.memory_mb} MB</span></div>
                                    <div class="env-metric"><span class="label">CPU</span><span class="value">${env.cpu_percent}%</span></div>
                                ` : `
                                    <div class="env-metric"><span class="label">PID</span><span class="value">${env.pid || 'N/A'}</span></div>
                                    <div class="env-metric"><span class="label">메모리</span><span class="value">${env.memory_mb} MB</span></div>
                                `}
                                <div class="env-metric"><span class="label">포트</span><span class="value">${portForContainerCard(env)}</span></div>
                                <div class="env-metric"><span class="label">가동</span><span class="value">${env.uptime}</span></div>
                            </div>
                            <div class="env-target"><span class="label">Target:</span> ${env.target}</div>
                            ${data.type === 'docker' && env.target ? `
                            <div class="env-ctl-btns">
                                <button type="button" class="btn btn-sm btn-primary" onclick="dockerAction(${JSON.stringify(env.target)}, 'start', ${JSON.stringify(data.name)})">▶ 시작</button>
                                <button type="button" class="btn btn-sm" onclick="dockerAction(${JSON.stringify(env.target)}, 'stop', ${JSON.stringify(data.name)})">■ 중지</button>
                                <button type="button" class="btn btn-sm" onclick="dockerAction(${JSON.stringify(env.target)}, 'restart', ${JSON.stringify(data.name)})">↻ 재시작</button>
                            </div>
                            ` : ''}
                        </div>
                    `;
                };
                const renderBackendCard = (env) => {
                    const healthDotClass =
                        env.health === 'healthy' ? 'running' :
                        env.health === 'unhealthy' ? 'unhealthy' : 'warn';
                    const bhText = (() => {
                        const h = env.health;
                        if (h === 'healthy') return '정상';
                        if (h === 'unhealthy') return '비정상';
                        if (h === 'none' || h === 'unknown') return '미설정';
                        return String(h || '—');
                    })();
                    const bhValueClass =
                        env.health === 'healthy' ? 'success' :
                        env.health === 'unhealthy' ? 'error' : 'warning';
                    const bhCardClass =
                        env.health === 'unhealthy' ? 'unhealthy' :
                        env.health === 'healthy' ? 'running' : 'stopped';
                    const backendPort = portForBackendCard(env);
                    const backendPath = env.path || '';
                    return `
                        <div class="env-card ${bhCardClass}">
                            <div class="env-header">
                                <div class="env-name">
                                    <span class="env-dot ${healthDotClass}"></span>
                                    ${env.name} · 백엔드
                                </div>
                                <div class="env-actions">
                                    ${data.type === 'docker' ? `<button class="btn btn-icon" onclick="openLogModal('${env.target}', '${data.name} - ${env.name} · 백엔드')" title="콘솔 로그">📜</button>` : ''}
                                    ${backendPort ? `<a class="btn btn-icon btn-primary" href="${absoluteUrlForPortAndPath(siteBaseForLinks, backendPort, backendPath)}" target="_blank" title="백엔드 접속">🌐</a>` : ''}
                                </div>
                            </div>
                            <div class="env-metrics">
                                <div class="env-metric"><span class="label">상태</span><span class="value ${bhValueClass}">${bhText}</span></div>
                                <div class="env-metric"><span class="label">메모리</span><span class="value">${env.memory_mb} MB</span></div>
                                <div class="env-metric"><span class="label">CPU</span><span class="value">${env.cpu_percent}%</span></div>
                                <div class="env-metric"><span class="label">포트</span><span class="value">${portForBackendCard(env)}</span></div>
                                <div class="env-metric"><span class="label">가동</span><span class="value">${env.uptime}</span></div>
                            </div>
                            <div class="env-target"><span class="label">API</span> :${portForBackendCard(env)}${env.path || ''}</div>
                            ${data.type === 'docker' && env.target ? `
                            <div class="env-ctl-btns">
                                <button type="button" class="btn btn-sm btn-primary" onclick="dockerAction(${JSON.stringify(env.target)}, 'start', ${JSON.stringify(data.name)})">▶ 시작</button>
                                <button type="button" class="btn btn-sm" onclick="dockerAction(${JSON.stringify(env.target)}, 'stop', ${JSON.stringify(data.name)})">■ 중지</button>
                                <button type="button" class="btn btn-sm" onclick="dockerAction(${JSON.stringify(env.target)}, 'restart', ${JSON.stringify(data.name)})">↻ 재시작</button>
                            </div>
                            ` : ''}
                        </div>
                    `;
                };
                const envs = data.environments;
                const allProbe = envs.length > 0 && envs.every(hasBackendProbe);
                if (allProbe) {
                    envGridWrapClass = 'env-grid-stacked';
                    const mains = envs.map(e => renderMainCard(e, true));
                    const backs = envs.map(e => renderBackendCard(e));
                    envCards = `
                        <div class="env-grid-row" title="컨테이너(Docker)">${mains.join('')}</div>
                        <div class="env-grid-row" title="백엔드 API(헬스 프로브)">${backs.join('')}</div>
                    `;
                } else {
                    envCards = envs.flatMap(env => {
                        const splitBackend = hasBackendProbe(env);
                        const mainCard = renderMainCard(env, splitBackend);
                        if (!splitBackend) return [mainCard];
                        return [mainCard, renderBackendCard(env)];
                    }).join('');
                }
            }
            
            let docsHtml = '';
            if (data.files && data.files.length > 0) {
                // 폴더별로 그룹화
                const grouped = {};
                data.files.forEach(f => {
                    const folder = f.folder || '';
                    if (!grouped[folder]) grouped[folder] = [];
                    grouped[folder].push(f);
                });
                
                // 폴더 정렬 (루트 먼저, 나머지 알파벳순)
                const sortedFolders = Object.keys(grouped).sort((a, b) => {
                    if (a === '') return -1;
                    if (b === '') return 1;
                    return a.localeCompare(b);
                });
                
                let fileListHtml = '';
                sortedFolders.forEach(folder => {
                    const files = grouped[folder];
                    const folderLabel = folder || '📁 루트';
                    const folderIcon = folder ? '📂' : '📁';
                    
                    fileListHtml += `
                        <div class="folder-group">
                            <div class="folder-header">${folderIcon} ${folder || '루트'}</div>
                            <div class="folder-files">
                                ${files.map(f => 
                                    `<span class="file-chip" data-path="${f.path}" onclick="loadFile('${f.path}')">${f.name}</span>`
                                ).join('')}
                            </div>
                        </div>
                    `;
                });
                
                docsHtml = `
                    <div class="docs-section">
                        <div class="docs-header">
                            <h2>📝 프로젝트 문서</h2>
                        </div>
                        <div class="file-list-grouped">${fileListHtml}</div>
                        <div class="markdown-content" id="fileContent">
                            <p style="color: var(--text-muted);">📄 위에서 파일을 선택하세요.</p>
                        </div>
                    </div>
                `;
            }
            
            document.getElementById('mainContent').innerHTML = `
                <div class="panel-header">
                    <div class="panel-title">
                        📊 ${data.name}
                        <span class="status-badge ${statusClass}">${statusText}</span>
                    </div>
                    <div class="panel-actions">
                        <button class="btn" onclick="loadProject('${data.name}', true)">🔄 새로고침</button>
                    </div>
                </div>
                ${data.type === 'info' ? `
                    <div style="padding: 20px;">
                        <div class="markdown-content">
                            <p>${data.description || '정보 프로젝트입니다.'}</p>
                        </div>
                    </div>
                ` : `<div class="${envGridWrapClass}">${envCards}</div>`}
                ${docsHtml}
            `;
        }
        
        // 파일 로드
        async function loadFile(path) {
            currentFile = path;
            
            document.querySelectorAll('.file-chip').forEach(el => {
                el.classList.toggle('active', el.dataset.path === path);
            });
            
            const contentEl = document.getElementById('fileContent');
            contentEl.innerHTML = '<p style="color: var(--text-muted);">로딩 중...</p>';
            
            try {
                const response = await fetch(`/api/content?path=${encodeURIComponent(path)}`);
                const data = await response.json();
                
                if (data.error) {
                    contentEl.innerHTML = `<p style="color: var(--error);">${data.error}</p>`;
                } else {
                    contentEl.innerHTML = data.html;
                    contentEl.querySelectorAll('pre code').forEach(block => hljs.highlightElement(block));
                }
            } catch (error) {
                contentEl.innerHTML = `<p style="color: var(--error);">오류: ${error.message}</p>`;
            }
        }
        
        // 로그 모달
        async function openLogModal(target, title) {
            currentLogTarget = target;
            document.getElementById('modalTitle').textContent = `${title} 로그 (${target})`;
            document.getElementById('logContent').textContent = '로그를 불러오는 중...';
            document.getElementById('logContent').className = 'log-container loading';
            document.getElementById('logModal').classList.add('show');
            await fetchLogs(target);
        }
        
        async function fetchLogs(target) {
            const content = document.getElementById('logContent');
            try {
                const response = await fetch(`/api/logs/${target}`);
                const data = await response.json();
                if (data.error) {
                    content.textContent = `오류: ${data.error}`;
                    content.className = 'log-container error';
                } else {
                    content.textContent = data.logs || '(로그가 비어 있습니다)';
                    content.className = 'log-container';
                    content.scrollTop = content.scrollHeight;
                }
            } catch (error) {
                content.textContent = `로그를 불러올 수 없습니다: ${error.message}`;
                content.className = 'log-container error';
            }
        }
        
        function refreshLogs() {
            if (currentLogTarget) {
                document.getElementById('logContent').textContent = '로그를 불러오는 중...';
                document.getElementById('logContent').className = 'log-container loading';
                fetchLogs(currentLogTarget);
            }
        }
        
        function closeLogModal(event) {
            if (event && event.target !== event.currentTarget) return;
            document.getElementById('logModal').classList.remove('show');
            currentLogTarget = null;
        }
        
        document.addEventListener('keydown', e => { if (e.key === 'Escape') closeLogModal(); });
        
        // 설정 모달
        let configData = null;
        
        async function openConfigModal() {
            try {
                const response = await fetch('/api/config');
                const data = await response.json();
                if (data.status === 'ok') {
                    configData = data.config;
                    renderConfigMapping();
                    await renderProjectOrder();
                    renderProjectConfigs();
                    renderDashboardSettings();
                    document.getElementById('configModal').classList.add('show');
                } else {
                    alert('설정을 불러올 수 없습니다: ' + (data.error || '알 수 없는 오류'));
                }
            } catch (error) {
                alert('설정을 불러올 수 없습니다: ' + error.message);
            }
        }
        
        function switchConfigTab(tabName) {
            // 탭 활성화
            document.querySelectorAll('.config-tab').forEach(tab => {
                tab.classList.toggle('active', tab.dataset.tab === tabName);
            });
            // 컨텐츠 표시
            document.querySelectorAll('.config-tab-content').forEach(content => {
                content.classList.toggle('active', content.id === 'tab-' + tabName);
            });
        }
        
        function closeConfigModal(event) {
            if (event && event.target !== event.currentTarget) return;
            document.getElementById('configModal').classList.remove('show');
            configData = null;
        }

        function ensureDashboardSettings() {
            if (!configData.dashboard_settings || typeof configData.dashboard_settings !== 'object') {
                configData.dashboard_settings = {};
            }
            if (!Array.isArray(configData.dashboard_settings.quick_links)) {
                configData.dashboard_settings.quick_links = [];
            }
            return configData.dashboard_settings;
        }

        function renderDashboardSettings() {
            const settings = ensureDashboardSettings();
            document.getElementById('dashboardSiteBaseUrl').value = settings.site_base_url || '';
            document.getElementById('dashboardSiteHostLabel').value = settings.site_host_label || '';

            const container = document.getElementById('dashboardQuickLinksList');
            container.innerHTML = '';

            settings.quick_links.forEach((link, index) => {
                const row = document.createElement('div');
                row.style.cssText = 'padding: 14px; background: var(--bg-tertiary); border: 1px solid var(--border); border-radius: 8px; display: grid; gap: 10px;';
                row.innerHTML = `
                    <div style="display: grid; grid-template-columns: 100px 1fr 1fr auto; gap: 10px; align-items: center;">
                        <input type="text" value="${link.icon || ''}" placeholder="아이콘" style="padding: 8px; background: var(--bg-primary); border: 1px solid var(--border); border-radius: 6px; color: var(--text-primary);" onchange="updateQuickLink(${index}, 'icon', this.value)">
                        <input type="text" value="${link.title || ''}" placeholder="제목" style="padding: 8px; background: var(--bg-primary); border: 1px solid var(--border); border-radius: 6px; color: var(--text-primary);" onchange="updateQuickLink(${index}, 'title', this.value)">
                        <input type="text" value="${link.label || ''}" placeholder="표시 라벨 (선택)" style="padding: 8px; background: var(--bg-primary); border: 1px solid var(--border); border-radius: 6px; color: var(--text-primary);" onchange="updateQuickLink(${index}, 'label', this.value)">
                        <button class="btn btn-icon" onclick="removeQuickLink(${index})" style="color: var(--error);">🗑️</button>
                    </div>
                    <div style="display: grid; grid-template-columns: 1fr 120px 1fr; gap: 10px;">
                        <input type="text" value="${link.url || ''}" placeholder="절대 URL (예: https://github.com/...)" style="padding: 8px; background: var(--bg-primary); border: 1px solid var(--border); border-radius: 6px; color: var(--text-primary);" onchange="updateQuickLink(${index}, 'url', this.value)">
                        <input type="number" value="${link.port || ''}" placeholder="포트" style="padding: 8px; background: var(--bg-primary); border: 1px solid var(--border); border-radius: 6px; color: var(--text-primary);" onchange="updateQuickLink(${index}, 'port', this.value)">
                        <input type="text" value="${link.path || ''}" placeholder="경로 (예: /admin)" style="padding: 8px; background: var(--bg-primary); border: 1px solid var(--border); border-radius: 6px; color: var(--text-primary);" onchange="updateQuickLink(${index}, 'path', this.value)">
                    </div>
                `;
                container.appendChild(row);
            });

            if (settings.quick_links.length === 0) {
                container.innerHTML = '<div style="padding: 16px; border: 1px dashed var(--border); border-radius: 8px; color: var(--text-muted);">설정된 Quick Link가 없습니다.</div>';
            }
        }

        function updateDashboardSetting(key, value) {
            const settings = ensureDashboardSettings();
            settings[key] = String(value || '').trim();
        }

        function addQuickLinkRow() {
            const settings = ensureDashboardSettings();
            settings.quick_links.push({ icon: '🔗', title: '', url: '', label: '', path: '' });
            renderDashboardSettings();
        }

        function removeQuickLink(index) {
            const settings = ensureDashboardSettings();
            settings.quick_links.splice(index, 1);
            renderDashboardSettings();
        }

        function updateQuickLink(index, key, value) {
            const settings = ensureDashboardSettings();
            const link = settings.quick_links[index];
            if (!link) return;

            const normalized = String(value || '').trim();
            if (key === 'port') {
                if (!normalized) {
                    delete link.port;
                } else {
                    const port = Number(normalized);
                    if (Number.isFinite(port) && port > 0) {
                        link.port = port;
                    }
                }
                if (link.port) delete link.url;
            } else {
                if (normalized) {
                    link[key] = normalized;
                } else {
                    delete link[key];
                }
                if (key === 'url' && normalized) {
                    delete link.port;
                    delete link.path;
                }
            }
        }
        
        function renderConfigMapping() {
            const mapping = configData.project_name_mapping || {};
            const container = document.getElementById('configMappingList');
            
            container.innerHTML = '';
            
            Object.entries(mapping).forEach(([folder, display], index) => {
                const row = document.createElement('div');
                row.style.cssText = 'display: flex; gap: 10px; margin-bottom: 10px; align-items: center;';
                row.innerHTML = `
                    <input type="text" value="${folder}" placeholder="폴더명" 
                           style="flex: 1; padding: 8px; background: var(--bg-tertiary); border: 1px solid var(--border); border-radius: 6px; color: var(--text-primary);"
                           onchange="updateMapping(${index}, 'folder', this.value)">
                    <span style="color: var(--text-secondary);">→</span>
                    <input type="text" value="${display}" placeholder="표시할 이름" 
                           style="flex: 1; padding: 8px; background: var(--bg-tertiary); border: 1px solid var(--border); border-radius: 6px; color: var(--text-primary);"
                           onchange="updateMapping(${index}, 'display', this.value)">
                    <button class="btn btn-icon" onclick="removeMapping(${index})" style="color: var(--error);">🗑️</button>
                `;
                container.appendChild(row);
            });
        }
        
        function updateMapping(index, type, value) {
            const mapping = configData.project_name_mapping || {};
            const entries = Object.entries(mapping);
            if (index < entries.length) {
                const [folder, display] = entries[index];
                if (type === 'folder') {
                    delete mapping[folder];
                    mapping[value] = display;
                } else {
                    mapping[folder] = value;
                }
                configData.project_name_mapping = mapping;
            }
        }
        
        function removeMapping(index) {
            const mapping = configData.project_name_mapping || {};
            const entries = Object.entries(mapping);
            if (index < entries.length) {
                const [folder] = entries[index];
                delete mapping[folder];
                configData.project_name_mapping = mapping;
                renderConfigMapping();
            }
        }
        
        function addMappingRow() {
            if (!configData.project_name_mapping) {
                configData.project_name_mapping = {};
            }
            configData.project_name_mapping[''] = '';
            renderConfigMapping();
        }
        
        async function loadAvailableFolders() {
            const folderDiv = document.getElementById('availableFolders');
            const folderList = document.getElementById('folderList');
            
            folderDiv.style.display = 'block';
            folderList.innerHTML = '<span style="color: var(--text-muted);">로딩 중...</span>';
            
            try {
                const response = await fetch('/api/projects/list');
                const data = await response.json();
                if (data.status === 'ok') {
                    folderList.innerHTML = data.folders.map(folder => 
                        `<span style="padding: 4px 8px; background: var(--bg-primary); border: 1px solid var(--border); border-radius: 4px; font-size: 0.75rem; cursor: pointer;" 
                              onclick="document.querySelector('input[placeholder=\\'폴더명\\']').value='${folder}'">${folder}</span>`
                    ).join('');
                } else {
                    folderList.innerHTML = '<span style="color: var(--error);">폴더 목록을 불러올 수 없습니다.</span>';
                }
            } catch (error) {
                folderList.innerHTML = '<span style="color: var(--error);">오류: ' + error.message + '</span>';
            }
        }
        
        async function renderProjectOrder() {
            const order = configData.project_order || [];
            const mapping = configData.project_name_mapping || {};
            
            // 프로젝트 목록 가져오기 (매핑된 이름 기준)
            const projects = new Set();
            const config = configData;
            for (const [key, value] of Object.entries(config)) {
                if (!CONFIG_META_KEYS.includes(key) && typeof value === 'object' && value !== null) {
                    const displayName = mapping[key] || key;
                    projects.add(displayName);
                }
            }
            
            // 마크다운 파일에서 스캔된 프로젝트도 추가
            try {
                const response = await fetch('/api/projects/list');
                const data = await response.json();
                if (data.status === 'ok' && data.folders) {
                    data.folders.forEach(folder => {
                        const displayName = mapping[folder] || folder;
                        projects.add(displayName);
                    });
                }
            } catch (error) {
                console.error('프로젝트 목록 로드 실패:', error);
            }
            
            // 순서에 없는 프로젝트 추가
            const allProjects = Array.from(projects);
            const finalOrder = [...order.filter(p => allProjects.includes(p)), ...allProjects.filter(p => !order.includes(p))];
            
            const container = document.getElementById('projectOrderList');
            container.innerHTML = '';
            
            finalOrder.forEach((projectName, index) => {
                const row = document.createElement('div');
                row.className = 'draggable-item';
                row.draggable = true;
                row.style.cssText = 'display: flex; gap: 10px; margin-bottom: 8px; align-items: center; padding: 10px; background: var(--bg-tertiary); border-radius: 6px;';
                row.dataset.project = projectName;
                row.dataset.index = index;
                
                row.innerHTML = `
                    <span style="color: var(--text-muted); font-size: 0.85rem; min-width: 30px;">${index + 1}</span>
                    <span style="flex: 1; color: var(--text-primary);">${projectName}</span>
                    <button class="btn btn-icon" onclick="moveProjectOrder(${index}, 'up')" ${index === 0 ? 'disabled style="opacity: 0.3;"' : ''} title="위로">⬆️</button>
                    <button class="btn btn-icon" onclick="moveProjectOrder(${index}, 'down')" ${index === finalOrder.length - 1 ? 'disabled style="opacity: 0.3;"' : ''} title="아래로">⬇️</button>
                `;
                
                // 드래그 이벤트
                row.addEventListener('dragstart', (e) => {
                    e.dataTransfer.effectAllowed = 'move';
                    e.dataTransfer.setData('text/html', projectName);
                    row.classList.add('dragging');
                });
                row.addEventListener('dragend', () => {
                    row.classList.remove('dragging');
                    document.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over'));
                });
                row.addEventListener('dragover', (e) => {
                    e.preventDefault();
                    e.dataTransfer.dropEffect = 'move';
                    row.classList.add('drag-over');
                });
                row.addEventListener('dragleave', () => {
                    row.classList.remove('drag-over');
                });
                row.addEventListener('drop', (e) => {
                    e.preventDefault();
                    row.classList.remove('drag-over');
                    const draggedName = e.dataTransfer.getData('text/html');
                    const draggedIndex = finalOrder.indexOf(draggedName);
                    const targetIndex = finalOrder.indexOf(projectName);
                    
                    if (draggedIndex !== -1 && targetIndex !== -1 && draggedIndex !== targetIndex) {
                        finalOrder.splice(draggedIndex, 1);
                        finalOrder.splice(targetIndex, 0, draggedName);
                        configData.project_order = finalOrder;
                        renderProjectOrder();
                    }
                });
                
                container.appendChild(row);
            });
        }
        
        function moveProjectOrder(index, direction) {
            if (!configData.project_order) {
                configData.project_order = [];
            }
            
            const order = configData.project_order || [];
            const mapping = configData.project_name_mapping || {};
            const config = configData;
            
            // 전체 프로젝트 목록 구성
            const projects = [];
            for (const [key, value] of Object.entries(config)) {
                if (!CONFIG_META_KEYS.includes(key) && typeof value === 'object' && value !== null) {
                    const displayName = mapping[key] || key;
                    projects.push(displayName);
                }
            }
            
            // 순서에 없는 프로젝트 추가
            projects.forEach(p => {
                if (!order.includes(p)) {
                    order.push(p);
                }
            });
            
            if (direction === 'up' && index > 0) {
                [order[index - 1], order[index]] = [order[index], order[index - 1]];
            } else if (direction === 'down' && index < order.length - 1) {
                [order[index], order[index + 1]] = [order[index + 1], order[index]];
            }
            
            configData.project_order = order;
            renderProjectOrder();
        }
        
        function renderProjectConfigs() {
            const container = document.getElementById('projectConfigList');
            container.innerHTML = '';
            
            const mapping = configData.project_name_mapping || {};
            const projects = [];
            
            for (const [key, value] of Object.entries(configData)) {
                if (!CONFIG_META_KEYS.includes(key) && typeof value === 'object' && value !== null) {
                    const displayName = mapping[key] || key;
                    projects.push({key, displayName, config: value});
                }
            }
            
            if (projects.length === 0) {
                container.innerHTML = '<p style="color: var(--text-muted); text-align: center; padding: 20px;">설정된 프로젝트가 없습니다.</p>';
                return;
            }
            
            projects.forEach(({key, displayName, config}) => {
                const card = document.createElement('div');
                card.style.cssText = 'background: var(--bg-tertiary); border: 1px solid var(--border); border-radius: 8px; padding: 15px; margin-bottom: 15px;';
                card.innerHTML = `
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px;">
                        <div>
                            <div style="font-weight: 600; color: var(--text-primary); margin-bottom: 4px;">${displayName}</div>
                            <div style="font-size: 0.75rem; color: var(--text-muted);">키: ${key}</div>
                        </div>
                        <div style="display: flex; gap: 8px;">
                            <button class="btn btn-icon" onclick="editProjectConfig('${key}')" title="편집">✏️</button>
                            <button class="btn btn-icon" onclick="deleteProjectConfig('${key}')" style="color: var(--error);" title="삭제">🗑️</button>
                        </div>
                    </div>
                    <div style="font-size: 0.85rem; color: var(--text-secondary);">
                        <div><strong>타입:</strong> ${config.type || 'info'}</div>
                        ${config.environments ? `<div><strong>환경:</strong> ${config.environments.length}개</div>` : ''}
                        ${config.description ? `<div><strong>설명:</strong> ${config.description}</div>` : ''}
                    </div>
                `;
                container.appendChild(card);
            });
        }
        
        function addProjectConfig() {
            const name = prompt('프로젝트 이름을 입력하세요:');
            if (!name) return;
            
            const type = prompt('프로젝트 타입을 선택하세요 (docker/process/info):', 'info');
            if (!type) return;
            
            if (!configData[name]) {
                configData[name] = {
                    type: type,
                    environments: type === 'info' ? [] : []
                };
                renderProjectConfigs();
            } else {
                alert('이미 존재하는 프로젝트입니다.');
            }
        }
        
        function editProjectConfig(key) {
            const config = configData[key];
            if (!config) return;
            
            const newType = prompt('프로젝트 타입 (docker/process/info):', config.type || 'info');
            if (newType) {
                config.type = newType;
                if (!config.environments) config.environments = [];
                renderProjectConfigs();
            }
        }
        
        function deleteProjectConfig(key) {
            if (confirm(`프로젝트 "${key}"를 삭제하시겠습니까?`)) {
                delete configData[key];
                // 매핑에서도 제거
                const mapping = configData.project_name_mapping || {};
                for (const [k, v] of Object.entries(mapping)) {
                    if (v === key || k === key) {
                        delete mapping[k];
                    }
                }
                // 순서에서도 제거
                if (configData.project_order) {
                    configData.project_order = configData.project_order.filter(p => p !== key);
                }
                renderProjectConfigs();
                renderConfigMapping();
                renderProjectOrder();
            }
        }
        
        async function saveConfig() {
            try {
                const response = await fetch('/api/config', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({config: configData})
                });
                
                const data = await response.json();
                if (data.status === 'ok') {
                    alert('설정이 저장되었습니다. 페이지를 새로고침합니다.');
                    location.reload();
                } else {
                    alert('저장 실패: ' + (data.error || '알 수 없는 오류'));
                }
            } catch (error) {
                alert('저장 중 오류가 발생했습니다: ' + error.message);
            }
        }
        
        document.addEventListener('keydown', e => { 
            if (e.key === 'Escape') {
                closeLogModal();
                closeConfigModal();
            }
        });
    </script>
</body>
</html>
'''

# ============================================================
# 라우트
# ============================================================
@app.route('/')
def index():
    config = load_config()
    dashboard_settings = get_dashboard_settings(config)
    # 프로젝트 설정을 매핑된 이름으로 정규화
    project_configs = normalize_project_configs(config)
    # 첫 화면은 사이드바 요약만 필요하므로 가벼운 상태 조회를 사용한다.
    all_status = get_all_project_status(project_configs, include_metrics=False)
    markdown_projects = scan_markdown_project_names()
    
    projects = {}
    running_count = 0
    issue_count = 0
    
    for project_name, project_config in project_configs.items():
        status = all_status.get(project_name, {})
        ptype = project_config.get('type', 'info')
        
        env_running = status.get('running_count', 0)
        env_total = status.get('total_count', 0)
        has_issues = status.get('has_issues', False)
        
        status_class = 'info'
        if ptype in ('docker', 'process'):
            if env_running > 0:
                status_class = 'unhealthy' if has_issues else 'running'
                running_count += env_running
            else:
                status_class = 'stopped'
            if has_issues:
                issue_count += 1
        
        projects[project_name] = {
            'status_class': status_class,
            'running_count': env_running,
            'total_count': env_total
        }
    
    # 설정에 없는 프로젝트도 추가
    for project_name in markdown_projects:
        if project_name not in projects:
            projects[project_name] = {
                'status_class': 'info',
                'running_count': 0,
                'total_count': 0
            }
    
    # 순서 적용: project_order가 있으면 그 순서대로, 없으면 알파벳순
    project_order = config.get('project_order', [])
    if project_order:
        ordered_projects = {}
        # 순서에 있는 프로젝트부터 추가
        for name in project_order:
            if name in projects:
                ordered_projects[name] = projects[name]
        # 순서에 없는 프로젝트 추가
        for name, value in projects.items():
            if name not in ordered_projects:
                ordered_projects[name] = value
        projects = ordered_projects
    
    return render_template_string(
        HTML_TEMPLATE,
        projects=projects,
        running_count=running_count,
        issue_count=issue_count,
        site_base_url=dashboard_settings['site_base_url'],
        site_host_label=dashboard_settings['site_host_label'],
        default_public_site=dashboard_settings['default_public_site'],
        dashboard_port=PORT,
        quick_links=dashboard_settings['quick_links'],
    )


@app.route('/api/system')
def api_system():
    force_refresh = request.args.get('refresh', '').lower() == 'true'
    return jsonify(get_system_stats(force_refresh))


@app.route('/api/project/<name>')
def api_project(name):
    force_refresh = request.args.get('refresh', '').lower() == 'true'
    config = load_config()
    dashboard_settings = get_dashboard_settings(config)
    # 프로젝트 설정을 매핑된 이름으로 정규화
    project_configs = normalize_project_configs(config)
    project_config = project_configs.get(name, {'type': 'info'})
    
    result = get_project_status(
        name,
        project_config,
        force_refresh=force_refresh,
        include_metrics=True,
    )
    # AJAX 렌더 시 초기 HTML과 같은 베이스를 사용한다.
    result['public_site_base'] = dashboard_settings['site_base_url'].rstrip('/')
    
    # 마크다운 파일 목록 추가
    md_files = scan_markdown_files().get(name, [])
    result['files'] = md_files
    
    return jsonify(result)


@app.route('/api/content')
def api_content():
    path = request.args.get('path', '')
    if not path:
        return jsonify({'error': '파일 경로가 필요합니다.'})
    
    file_path = BASE_DIR / path
    if not file_path.exists() or not file_path.suffix == '.md':
        return jsonify({'error': '파일을 찾을 수 없습니다.'})
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        html = render_markdown_content(content)
        return jsonify({'html': html})
    except Exception as e:
        return jsonify({'error': str(e)})


@app.route('/api/status')
def api_status():
    force_refresh = request.args.get('refresh', '').lower() == 'true'
    config = load_config()
    # 프로젝트 설정을 매핑된 이름으로 정규화
    project_configs = normalize_project_configs(config)
    return jsonify(get_all_project_status(project_configs, force_refresh))


@app.route('/api/logs/<target>')
def api_logs(target):
    if not docker_client:
        return jsonify({'error': 'Docker 연결 안됨'}), 503
    
    try:
        container = docker_client.containers.get(target)
        logs = container.logs(tail=100, timestamps=True)
        return jsonify({'status': 'ok', 'container': target, 'logs': logs.decode('utf-8', errors='replace')})
    except docker.errors.NotFound:
        return jsonify({'error': f'컨테이너 "{target}"을(를) 찾을 수 없습니다.'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/docker/action', methods=['POST'])
def api_docker_action():
    """설정에 등록된 컨테이너만 시작/중지/재시작 (화이트리스트)."""
    if not docker_client:
        return jsonify({'status': 'error', 'error': 'Docker 연결 안됨'}), 503
    data = request.get_json(silent=True) or {}
    target = (data.get('target') or '').strip()
    action = (data.get('action') or '').strip().lower()
    if action not in ('start', 'stop', 'restart'):
        return jsonify({'status': 'error', 'error': 'action은 start, stop, restart 중 하나'}), 400
    if not target:
        return jsonify({'status': 'error', 'error': 'target(컨테이너 이름) 필요'}), 400
    cfg = load_config()
    allowed = collect_docker_targets_from_config(cfg)
    if target not in allowed:
        return jsonify({'status': 'error', 'error': f'설정에 없는 컨테이너: {target}'}), 403
    try:
        c = docker_client.containers.get(target)
        if action == 'start':
            c.start()
        elif action == 'stop':
            c.stop(timeout=15)
        else:
            c.restart(timeout=15)
        cache.invalidate()
        return jsonify({'status': 'ok', 'message': f'{target} {action} 완료'})
    except docker.errors.NotFound:
        return jsonify({'status': 'error', 'error': f'컨테이너 없음: {target}'}), 404
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500


@app.route('/api/cache/invalidate', methods=['POST'])
def api_cache_invalidate():
    cache.invalidate()
    return jsonify({'status': 'ok', 'message': '캐시가 무효화되었습니다.'})


@app.route('/api/config', methods=['GET'])
def api_config_get():
    """설정 파일 읽기"""
    try:
        config = load_config()
        return jsonify({'status': 'ok', 'config': config})
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500


@app.route('/api/config', methods=['POST'])
def api_config_save():
    """설정 파일 저장"""
    try:
        data = request.get_json()
        if not data or 'config' not in data:
            return jsonify({'status': 'error', 'error': '설정 데이터가 필요합니다.'}), 400
        
        # 백업 생성
        if CONFIG_FILE.exists():
            backup_path = CONFIG_FILE.with_suffix('.json.backup')
            import shutil
            shutil.copy2(CONFIG_FILE, backup_path)
        
        # 설정 저장
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(data['config'], f, ensure_ascii=False, indent=2)
        
        # 캐시 무효화
        cache.invalidate()
        
        return jsonify({'status': 'ok', 'message': '설정이 저장되었습니다.'})
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500


@app.route('/api/projects/list')
def api_projects_list():
    """스캔된 프로젝트 폴더 목록 반환"""
    try:
        md_files_map = scan_markdown_files(force_refresh=True)
        folder_names = set()
        for root, dirs, files in os.walk(BASE_DIR):
            dirs[:] = [d for d in dirs if d not in IGNORE_PATTERNS]
            root_path = Path(root)
            if any(p in root_path.parts for p in IGNORE_PATTERNS):
                continue
            relative = root_path.relative_to(BASE_DIR)
            if len(relative.parts) == 1:
                folder_names.add(relative.parts[0])
            elif len(relative.parts) >= 2 and relative.parts[0] == APPS_ROOT:
                folder_names.add(relative.parts[1])
        
        return jsonify({'status': 'ok', 'folders': sorted(list(folder_names))})
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500


if __name__ == '__main__':
    dashboard_settings = get_dashboard_settings()
    print(f"\n{'='*60}")
    print(f"🚀 통합 DevOps 대시보드 v3.0 (AJAX)")
    print(f"{'='*60}")
    print(f"🌐 바인딩: http://0.0.0.0:{PORT} (로컬: http://localhost:{PORT})")
    print(f"🔗 사이트 링크 베이스: {dashboard_settings['site_base_url']} (config/env)")
    print(f"📁 스캔 경로: {BASE_DIR}")
    print(f"⚙️  설정 파일: {CONFIG_FILE}")
    print(f"⏱️  캐시 TTL: {CACHE_TTL}초")
    print(f"{'='*60}")
    print(f"📦 Docker: {'✅ 연결됨' if docker_client else '❌ 연결 안됨'}")
    print(f"📊 PSUtil: {'✅ 사용 가능' if PSUTIL_AVAILABLE else '❌ 없음'}")
    print(f"📝 Markdown: {'✅ 사용 가능' if MARKDOWN_AVAILABLE else '❌ 없음'}")
    print(f"{'='*60}")
    print(f"종료하려면 Ctrl+C를 누르세요.\n")
    
    app.run(host='0.0.0.0', port=PORT, debug=False)
