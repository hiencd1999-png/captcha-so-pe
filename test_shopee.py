r"""Local server/extension diagnostics and YOLO image tests.

    python test_shopee.py
    python test_shopee.py --integration-smoke --port 0
    python test_shopee.py --image path/to/image.png
"""

import argparse
import base64
import csv
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import unquote_to_bytes, urlsplit

ROOT = Path(__file__).resolve().parent
LOCAL_TZ = timezone(timedelta(hours=7))
CAPTCHA_ROOT = '.shopee-captcha, .shopee-captcha-slider__main, .er1GfA, [class*="captcha" i]'
BACKGROUND = ', '.join(f'{selector} img' for selector in CAPTCHA_ROOT.split(', '))
HANDLE = '.shopee-captcha-slider__button, .shopee-captcha-slider__btn, [class*="slider" i][class*="button" i], [role="slider"], img.j5BVKH'


class DiagnosticError(RuntimeError):
    def __init__(self, code, message, hint='', **details):
        super().__init__(message)
        self.code, self.hint, self.details = code, hint, details


class RunReport(dict):
    def __init__(self, output, mode):
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        super().__init__(mode=mode, status='starting', events=[])
        self.output = output
        self.stage('initializing', 'Khởi tạo chẩn đoán local.')

    def stage(self, stage, message, **details):
        self['stage'] = stage
        self['updated_at'] = datetime.now(LOCAL_TZ).isoformat(timespec='seconds')
        self['events'].append({'at': self['updated_at'], 'stage': stage, 'message': message, **details})
        save_report(self.output, self)
        print(f'[{stage}] {message}', flush=True)


class Server:
    def __init__(self, args, output, report):
        self.args, self.output, self.report = args, output, report
        self.process = self.log = None
        self.url = args.server_url.rstrip('/') if args.server_url else ''
        self.health = None

    def probe(self, url, timeout=2):
        import requests

        response = requests.get(url + '/health', timeout=timeout)
        if not response.ok:
            raise DiagnosticError('health_http_error', f'/health trả HTTP {response.status_code}.', server_url=url)
        try:
            health = response.json()
        except ValueError as error:
            raise DiagnosticError('health_invalid_json', '/health không trả JSON.', 'Kiểm tra đúng cổng của server1.') from error
        if not isinstance(health, dict) or health.get('service') != 'captcha-so-pe-server1' or health.get('ok') is not True:
            raise DiagnosticError('wrong_service', 'Dịch vụ ở địa chỉ này không phải server1 hợp lệ.', 'Kiểm tra --server-url hoặc dùng --port 0.')
        return health

    def __enter__(self):
        import requests

        self.report.stage('server_starting', 'Kiểm tra cổng và khởi động server.')
        try:
            if not self.url:
                with socket.socket() as sock:
                    try:
                        sock.bind(('127.0.0.1', self.args.port))
                    except OSError as error:
                        candidate = f'http://127.0.0.1:{self.args.port}'
                        try:
                            existing = self.probe(candidate)
                        except (requests.RequestException, DiagnosticError) as cause:
                            raise DiagnosticError('port_in_use', f'Cổng {self.args.port} đang được dịch vụ khác sử dụng.', 'Chạy lại với --port 0 để chọn cổng trống.') from cause
                        if existing.get('diagnostics_version', 0) >= 2:
                            self.url = candidate
                        else:
                            sock.bind(('127.0.0.1', 0))
                            print('Server cũ đang chiếm cổng; mở bản chẩn đoán mới trên cổng trống.', flush=True)
                    port = self.args.port if self.url else sock.getsockname()[1]
                if not self.url:
                    self.url = f'http://127.0.0.1:{port}'
                    env = dict(os.environ, HOST='127.0.0.1', PORT=str(port), PYTHONUNBUFFERED='1', PYTHONIOENCODING='utf-8')
                    self.log = (self.output / 'server.log').open('w', encoding='utf-8')
                    self.process = subprocess.Popen(
                        [sys.executable, str(ROOT / 'server1/server.py')], cwd=ROOT,
                        env=env, stdout=self.log, stderr=subprocess.STDOUT,
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                    )
            self.report['server'] = {'url': self.url, 'managed': self.process is not None, 'pid': self.process.pid if self.process else None, 'active': False}
            self.report.stage('server_waiting', f'Đang chờ {self.url}/health', **self.report['server'])
            deadline = time.monotonic() + self.args.startup_timeout
            last_progress = time.monotonic()
            while time.monotonic() < deadline:
                if self.process and self.process.poll() is not None:
                    tail = (self.output / 'server.log').read_text(encoding='utf-8', errors='replace').splitlines()[-14:]
                    raise DiagnosticError('server_exited', 'Server dừng trước khi sẵn sàng.', 'Xem server.log; kiểm tra thư viện Python và đường dẫn model.', exit_code=self.process.returncode, log_tail=tail)
                try:
                    health = self.probe(self.url)
                    if health.get('diagnostics_version', 0) < 2:
                        raise DiagnosticError('server_outdated', 'Server đang chạy là phiên bản cũ.', 'Khởi động lại server mới hoặc bỏ --server-url và dùng --port 0.')
                    self.health = health
                    self.report['health'] = health
                    self.report['server']['active'] = True
                    self.report.stage('server_ready', f'Server đang chạy tại {self.url}; YOLO={"đã nạp" if health.get("model_loaded") else "chưa nạp"}.')
                    return self
                except requests.RequestException:
                    pass
                if time.monotonic() - last_progress >= 5:
                    print('Vẫn đang chờ server. Chi tiết khởi động nằm trong server.log.', flush=True)
                    last_progress = time.monotonic()
                time.sleep(0.25)
            raise DiagnosticError('server_timeout', f'Không kết nối được server sau {self.args.startup_timeout:g} giây.', 'Kiểm tra server.log, cổng và --server-url.')
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self.process and 'server' in self.report:
            self.report['server']['active'] = False
        if self.log:
            self.log.close()

    def detect(self, image):
        import requests

        try:
            response = requests.post(self.url + '/detect', json={'image': image}, timeout=60)
        except requests.RequestException as error:
            raise DiagnosticError('detect_connection_failed', 'Không gọi được API /detect.', 'Kiểm tra server.log và trang trạng thái extension.') from error
        try:
            result = response.json()
        except ValueError as error:
            raise DiagnosticError('detect_invalid_json', f'/detect trả HTTP {response.status_code}, không phải JSON.') from error
        if not response.ok or not isinstance(result, dict) or result.get('ok') is not True:
            raise DiagnosticError('detect_api_error', f'/detect báo lỗi: {result}')
        points = result.get('points')
        if not isinstance(points, list) or any(
            not isinstance(point, dict) or any(
                isinstance(point.get(axis), bool) or not isinstance(point.get(axis), (int, float)) or not math.isfinite(point[axis])
                for axis in ('x', 'y')
            ) for point in points
        ):
            raise DiagnosticError('detect_invalid_points', 'Server trả tọa độ không hợp lệ.')
        return result


def resolve_edge_path(override=None):
    if override:
        path = Path(override).expanduser().resolve()
        if not path.is_file():
            raise DiagnosticError('edge_not_found', f'Không tìm thấy Edge: {path}', 'Kiểm tra --edge-path.')
        return path
    candidates = [
        Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Microsoft/Edge/Application/msedge.exe',
        Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe',
        Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'Microsoft/Edge/Application/msedge.exe',
    ]
    found = shutil.which('msedge.exe')
    if found:
        candidates.append(Path(found))
    for path in candidates:
        if path.is_file():
            return path.resolve()
    raise DiagnosticError('edge_not_found', 'Không tìm thấy Microsoft Edge đã cài trên máy.', 'Chỉ định --edge-path tới msedge.exe; hoặc dùng --browser chromium cho kiểm thử local.')


class ExtensionBrowser:
    def __init__(self, args, server, output, report):
        self.args, self.server, self.output, self.report = args, server, output, report
        self.context = self.playwright = None

    def __enter__(self):
        from playwright.sync_api import sync_playwright

        extension = self.output / 'extension'
        shutil.copytree(ROOT / 'server1/extension', extension)
        config = {'serverUrl': self.server.url, 'mode': 'fixture' if self.args.integration_smoke else 'diagnostics', 'autoDetect': self.args.integration_smoke, 'backgroundSelector': ''}
        (extension / 'config.js').write_text('globalThis.CAPTCHA_SERVER1_CONFIG = ' + json.dumps(config) + ';\n', encoding='utf-8')
        self.playwright = sync_playwright().start()
        try:
            options = {'channel': 'chromium'}
            if self.args.browser == 'edge':
                edge_path = resolve_edge_path(self.args.edge_path)
                options = {'channel': 'msedge', 'executable_path': str(edge_path)}
                self.report['browser'] = {'name': 'edge', 'executable': str(edge_path)}
            else:
                self.report['browser'] = {'name': 'chromium'}
            self.report.stage('browser_starting', f'Mở {self.args.browser} với profile chẩn đoán riêng.')
            self.context = self.playwright.chromium.launch_persistent_context(
                str(self.output / 'browser-profile'), **options,
                headless=self.args.integration_smoke, locale='vi-VN',
                viewport={'width': 1280, 'height': 850},
                args=[f'--disable-extensions-except={extension}', f'--load-extension={extension}'],
            )
            self.report['browser']['version'] = self.context.browser.version
            self.report.stage('extension_loading', 'Chờ service worker của extension.')
            workers = self.context.service_workers
            try:
                worker = workers[0] if workers else self.context.wait_for_event('serviceworker', timeout=10000)
            except Exception as error:
                raise DiagnosticError('extension_not_loaded', f'{self.args.browser} đã mở nhưng chưa nạp được extension.', 'Kiểm tra khả năng nạp extension của bản Edge này; dùng --browser chromium để chạy kiểm thử extension local.') from error
            if not worker.url.startswith('chrome-extension://'):
                raise DiagnosticError('extension_not_loaded', 'Không thấy service worker của extension server1.')
            extension_id = worker.url.split('/')[2]
            status_page = self.context.pages[0] if self.context.pages else self.context.new_page()
            status_page.goto(f'chrome-extension://{extension_id}/status.html')
            self.report.stage('extension_connecting', 'Kiểm tra extension gọi được /health.')
            try:
                status_page.locator('#server-state[data-ready="true"]').wait_for(timeout=20000)
            except Exception as error:
                detail = status_page.locator('#server-state').inner_text(timeout=2000)
                raise DiagnosticError('extension_connection_failed', detail, 'Kiểm tra trang trạng thái extension và server.log.') from error
            self.report['extension'] = {'loaded': True, 'background_connected': True, 'id': extension_id, 'server_url': self.server.url}
            self.report.stage('extension_ready', f'Extension đã nạp và nối server: {extension_id}')
            self.page = self.context.new_page()
            return self
        except Exception as error:
            self.__exit__(None, None, None)
            if "Executable doesn't exist" in str(error):
                if self.args.browser == 'edge':
                    raise DiagnosticError('edge_not_found', 'Không khởi động được Edge đã cài trên máy.', 'Kiểm tra --edge-path và quyền chạy msedge.exe.') from error
                raise DiagnosticError('missing_browser', 'Chưa cài Chromium cho Playwright.', f'"{sys.executable}" -m playwright install chromium') from error
            raise

    def __exit__(self, *_):
        try:
            if self.context:
                try:
                    self.context.close()
                except Exception as error:
                    self.report['cleanup_warning'] = type(error).__name__
        finally:
            if self.playwright:
                self.playwright.stop()

    def hold(self):
        from playwright.sync_api import Error

        print('Trình duyệt và server đang giữ mở. Đóng cửa sổ trình duyệt test để dừng.', flush=True)
        while self.context.pages:
            try:
                self.context.pages[0].wait_for_timeout(1000)
            except Error:
                break


def extension_result(page):
    for frame in page.frames:
        if frame.is_detached():
            continue
        bridge = frame.locator('#captcha-server1-bridge')
        if not bridge.count():
            continue
        state = json.loads(bridge.get_attribute('data-state') or '{}')
        if state.get('status') == 'error':
            raise RuntimeError(f'Extension báo lỗi: {state.get("error")}')
        if state.get('status') == 'detected':
            image = frame.locator(f'[data-captcha-server1-image="{int(state["requestId"])}"]')
            if image.count() and image.is_visible():
                return frame, image, state
    return None


def first_visible(frame, selector):
    matches = frame.locator(selector)
    for index in range(min(matches.count(), 25)):
        candidate = matches.nth(index)
        if candidate.is_visible():
            return candidate
    return None


def find_background(page, selector=None):
    best = None
    for frame in page.frames:
        if frame.is_detached():
            continue
        matches = frame.locator(selector or BACKGROUND)
        for index in range(min(matches.count(), 30)):
            image = matches.nth(index)
            if not image.is_visible():
                continue
            box = image.bounding_box()
            if box and box['width'] >= 180 and box['height'] >= 70:
                if best is None or box['width'] * box['height'] > best[2]['width'] * best[2]['height']:
                    best = (frame, image, box)
    return best


def read_image(page, image):
    dimensions = image.evaluate('(el) => ({width: el.naturalWidth || el.width, height: el.naturalHeight || el.height})')
    source = image.evaluate('(el) => el.currentSrc || el.src || ""')
    if source.startswith('data:'):
        header, data = source.split(',', 1)
        raw = base64.b64decode(data) if ';base64' in header else unquote_to_bytes(data)
    elif source.startswith('https://') or source.startswith('http://'):
        response = page.context.request.get(source, timeout=15000)
        if not response.ok:
            raise RuntimeError(f'Không tải được ảnh CAPTCHA: HTTP {response.status}')
        raw = response.body()
    elif image.evaluate('(el) => el.tagName') == 'CANVAS':
        encoded = image.evaluate('(el) => el.toDataURL("image/png")')
        raw = base64.b64decode(encoded.split(',', 1)[1])
    else:
        raise RuntimeError('Không đọc được ảnh gốc CAPTCHA; dùng --background-selector nếu cấu trúc trang khác.')
    if not dimensions['width'] or not dimensions['height']:
        raise RuntimeError('Ảnh CAPTCHA chưa tải xong.')
    return raw, dimensions


def drag_to_point(page, frame, image, point, dimensions, handle_selector=None):
    background = image.bounding_box()
    handle = first_visible(frame, handle_selector or HANDLE)
    if handle is None:
        raise RuntimeError('Không tìm thấy nút kéo. Chỉ định --handle-selector theo trang hiện tại.')
    box = handle.bounding_box()
    if box is None or background is None:
        raise RuntimeError('CAPTCHA đã đổi trước khi kéo.')
    piece = first_visible(frame, 'img.j5BVKH, .shopee-captcha-slider__puzzle, img[style*="left"][style*="top"]')
    piece_box = piece.bounding_box() if piece is not None else None
    scale_x = background['width'] / dimensions['width']
    scale_y = background['height'] / dimensions['height']
    x = point['x'] * scale_x
    if not 0 <= x <= background['width']:
        raise RuntimeError('Tọa độ server nằm ngoài ảnh CAPTCHA.')
    is_piece = handle.evaluate('(el) => el.tagName === "IMG"')
    if is_piece:
        y = point['y'] * scale_y
        if not 0 <= y <= background['height']:
            raise RuntimeError('Tọa độ Y nằm ngoài ảnh CAPTCHA.')
        dx = background['x'] + x - box['x']
        dy = background['y'] + y - box['y']
    else:
        current_x = piece_box['x'] - background['x'] if piece_box else 0
        dx, dy = x - current_x, 0
        if piece_box:
            track = handle.evaluate('''el => {
                for (let parent = el.parentElement; parent; parent = parent.parentElement) {
                    const rect = parent.getBoundingClientRect();
                    if (rect.width > el.getBoundingClientRect().width * 2 && rect.height < 100)
                        return {width: rect.width};
                }
                return null;
            }''')
            piece_travel = background['width'] - piece_box['width']
            if track and piece_travel > 0:
                dx *= (track['width'] - box['width']) / piece_travel
    if abs(dx) > background['width'] * 1.5:
        raise RuntimeError('Quãng kéo không hợp lệ.')
    start_x, start_y = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
    page.mouse.move(start_x, start_y)
    page.mouse.down()
    try:
        page.mouse.move(start_x + dx, start_y + dy, steps=30)
    finally:
        page.mouse.up()
    return {'dx': round(dx, 2), 'dy': round(dy, 2), 'scale_x': scale_x, 'scale_y': scale_y}


def otp_visible(page):
    for frame in page.frames:
        if frame.is_detached():
            continue
        if first_visible(frame, 'input[autocomplete="one-time-code"], input[placeholder*="OTP" i], input[placeholder*="mã xác minh" i]') is not None:
            return True
        matches = frame.get_by_text(re.compile(r'Nhập mã xác minh|Mã xác minh đã được gửi|Enter verification code|Verification code has been sent', re.I))
        for index in range(min(matches.count(), 5)):
            if matches.nth(index).is_visible():
                return True
    return False


def smoke_test(server, report):
    import cv2
    import numpy as np

    with (ROOT / 'server1/data/dataset/labels.csv').open(encoding='utf-8', newline='') as stream:
        labels = {row['image'].split('.')[0]: float(row['slider_x']) for row in csv.DictReader(stream)}
    for path in sorted((ROOT / 'server1/data/dataset/images').glob('*.png')):
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest in labels:
            break
    else:
        raise RuntimeError('Không tìm thấy ảnh mẫu có SHA-256 trong labels.csv.')
    result = server.detect(base64.b64encode(raw).decode())
    if result.get('source') != 'csv_cache' or result['points'][0]['x'] != int(labels[digest]):
        raise RuntimeError(f'Tra CSV trả kết quả không khớp: {result}')
    report['csv_test'] = {'passed': True, 'image': path.name, 'result': result}
    if not server.health.get('model_loaded'):
        raise RuntimeError('Tra CSV thành công nhưng YOLO chưa nạp được model.')
    image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    changed = image.copy()
    changed[0, 0] = (changed[0, 0].astype(int) + 1) % 256
    ok, encoded = cv2.imencode('.png', changed)
    if not ok:
        raise RuntimeError('Không tạo được ảnh kiểm tra YOLO.')
    result = server.detect(base64.b64encode(encoded.tobytes()).decode())
    if result.get('source') != 'yolo':
        raise RuntimeError(f'Ảnh mới chưa chạy qua nhánh YOLO: {result}')
    report['yolo_test'] = {'passed': True, 'points_count': len(result['points']), 'result': result}
    report['status'] = 'server_smoke_passed'


def integration_smoke(args, server, output, report, session):
    """Exercise the installed extension against local fixtures and the real API."""
    import html
    import cv2
    import numpy as np

    smoke_test(server, report)
    raw = (ROOT / 'server1/data/dataset/images' / report['csv_test']['image']).read_bytes()
    height, width = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR).shape[:2]
    target = report['csv_test']['result']['points'][0]['x']
    encoded = 'data:image/png;base64,' + base64.b64encode(raw).decode()
    report['integration_cases'] = []
    for index, (scale, iframe) in enumerate(((1, False), (0.75, True))):
        displayed_width, piece_width = width * scale, 40 * scale
        fixture = f'''<div class="shopee-captcha" style="position:relative;margin:25px;width:{displayed_width}px">
<div style="position:relative;width:{displayed_width}px;height:{height * scale}px">
<img src="{encoded}" style="width:{displayed_width}px;height:{height * scale}px">
<img class="shopee-captcha-slider__puzzle" src="{encoded}" style="position:absolute;left:0px;top:0px;width:{piece_width}px;height:{piece_width}px">
</div><div style="position:relative;width:{displayed_width}px;height:{piece_width}px">
<div role="slider" style="position:absolute;left:0px;top:0px;width:{piece_width}px;height:{piece_width}px;background:red"></div>
</div></div><script>
let active=false,start=0,dx=0;const knob=document.querySelector('[role=slider]');
knob.addEventListener('mousedown',e=>{{active=true;start=e.clientX}});
document.addEventListener('mousemove',e=>{{if(active){{dx=e.clientX-start;knob.style.left=dx+'px';document.querySelector('.shopee-captcha-slider__puzzle').style.left=dx+'px'}}}});
document.addEventListener('mouseup',()=>{{if(active){{active=false;if(Math.abs(dx-{target * scale})<2){{document.querySelector('.shopee-captcha').remove();const otp=document.createElement('input');otp.autocomplete='one-time-code';document.body.appendChild(otp)}}}}}});
</script>'''
        body = f'<iframe width="900" height="700" srcdoc="{html.escape(fixture, quote=True)}"></iframe>' if iframe else fixture
        url = f'{server.url}/diagnostics/fixture/{index}'
        session.context.route(url, lambda route, request, content=body: route.fulfill(status=200, content_type='text/html', body=content))
        page = session.page
        page.goto(url, wait_until='domcontentloaded')
        deadline = time.monotonic() + 30
        captured = None
        while time.monotonic() < deadline:
            captured = extension_result(page)
            if captured:
                break
            page.wait_for_timeout(200)
        if not captured:
            raise RuntimeError(f'Extension chưa gửi ảnh fixture {index} đến server.')
        frame, image, state = captured
        if state['result'].get('source') != 'csv_cache' or state['result']['points'][0]['x'] != target:
            raise RuntimeError('Kết quả extension khác đáp án CSV của ảnh fixture.')
        extracted, dimensions = read_image(page, image)
        if extracted != raw:
            raise RuntimeError('Extension/test đã thay đổi dữ liệu ảnh gốc.')
        drag = drag_to_point(page, frame, image, state['result']['points'][0], dimensions)
        if not otp_visible(page):
            raise RuntimeError('Fixture không nhận được thao tác kéo đúng từ kết quả extension.')
        report['integration_cases'].append({'iframe': iframe, 'scale': scale, 'source': state['result']['source'], 'drag': drag, 'fixture_otp_visible': True})
    report['status'] = 'extension_integration_passed'


def local_yolo(args, output, report):
    model_path = Path(args.model).expanduser().resolve()
    if not model_path.is_file():
        raise DiagnosticError('model_not_found', f'Không tìm thấy model local: {model_path}', 'Chỉ định --model tới file model đã có trên máy.')
    if args.image:
        paths = [Path(args.image).expanduser().resolve()]
        if not paths[0].is_file():
            raise DiagnosticError('image_not_found', f'Không tìm thấy ảnh: {paths[0]}')
    else:
        directory = Path(args.images_dir).expanduser().resolve()
        if not directory.is_dir():
            raise DiagnosticError('images_directory_not_found', f'Không tìm thấy thư mục ảnh: {directory}')
        paths = sorted(path for path in directory.iterdir() if path.is_file() and path.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp', '.bmp'})[:args.limit]
        if not paths:
            raise DiagnosticError('no_images', 'Thư mục không có ảnh được hỗ trợ.')
    import cv2
    import numpy as np
    from ultralytics import YOLO

    report.stage('model_loading', f'Nạp model local: {model_path.name}')
    try:
        model = YOLO(str(model_path))
    except Exception as error:
        raise DiagnosticError('model_load_failed', str(error), 'Kiểm tra file model và phiên bản ultralytics/torch.') from error
    report['local_yolo'] = {'model': str(model_path), 'confidence_threshold': args.conf, 'images': []}
    for index, path in enumerate(paths):
        report.stage('local_predicting', f'Kiểm tra ảnh {index + 1}/{len(paths)}: {path.name}')
        item = {'image': str(path), 'boxes': []}
        try:
            raw = path.read_bytes()
            image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                raise DiagnosticError('image_decode_failed', 'Không giải mã được ảnh.')
            result = model(image, conf=args.conf, device='cpu', verbose=False)[0]
            annotated = image.copy()
            for box in result.boxes:
                coords = [float(value) for value in box.xyxy[0].tolist()]
                class_id = int(box.cls.item())
                confidence = float(box.conf.item())
                label = str(result.names[class_id])
                item['boxes'].append({'xyxy': coords, 'confidence': confidence, 'class_id': class_id, 'class_name': label})
                x1, y1, x2, y2 = map(int, coords)
                cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 190, 0), 2)
                cv2.putText(annotated, f'{label} {confidence:.3f}', (max(0, x1), max(15, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 150, 0), 1)
            destination = output / f'{index + 1:04d}-{path.stem}-prediction.png'
            ok, encoded = cv2.imencode('.png', annotated)
            if not ok:
                raise DiagnosticError('annotation_failed', 'Không xuất được ảnh kết quả.')
            destination.write_bytes(encoded.tobytes())
            item.update(sha256=hashlib.sha256(raw).hexdigest(), detection_count=len(item['boxes']), annotated_image=str(destination), status='predicted' if item['boxes'] else 'no_detections')
        except Exception as error:
            item.update(status='failed', error_code=getattr(error, 'code', 'prediction_error'), error=str(error))
        report['local_yolo']['images'].append(item)
        save_report(output, report)
    items = report['local_yolo']['images']
    report['local_yolo']['summary'] = {'tested': len(items), 'with_detections': sum(item['status'] == 'predicted' for item in items), 'without_detections': sum(item['status'] == 'no_detections' for item in items), 'failed': sum(item['status'] == 'failed' for item in items)}
    report['status'] = 'local_yolo_failed' if any(item['status'] == 'failed' for item in items) else 'local_yolo_completed'
    report['note'] = 'Kết quả là dự đoán model trên ảnh local, chưa đánh giá độ chính xác bằng nhãn chuẩn.'


def record_error(report, error):
    report.update(status='failed', error_type=type(error).__name__, error_code=getattr(error, 'code', 'unexpected_error'))
    report['error'] = re.sub(r'\d{6,}', '[redacted]', str(error))[:3000]
    if isinstance(error, DiagnosticError):
        report['hint'] = error.hint
        report['error_details'] = error.details
    report.stage('failed', report['error'])


def save_report(output, report):
    temporary = output / 'report.json.tmp'
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(output / 'report.json')


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--smoke', action='store_true', help='Test API CSV và YOLO bằng ảnh local.')
    modes.add_argument('--integration-smoke', action='store_true', help='Test extension và server bằng hai trang mẫu local.')
    modes.add_argument('--setup-only', action='store_true', help='Mở bảng chẩn đoán kết nối local (chế độ mặc định).')
    modes.add_argument('--image', help='Chạy YOLO trực tiếp trên một ảnh local; không bật server/trình duyệt.')
    modes.add_argument('--images-dir', help='Kiểm tra YOLO trên thư mục ảnh local.')
    parser.add_argument('--limit', type=int, default=12, help='Số ảnh tối đa khi dùng --images-dir; mặc định 12.')
    parser.add_argument('--model', default=str(ROOT / 'server1/best.pt'), help='File model local.')
    parser.add_argument('--conf', type=float, default=0.8, help='Ngưỡng tin cậy cho kiểm tra YOLO local.')
    parser.add_argument('--server-url', help='URL gốc của server1 đang chạy.')
    parser.add_argument('--browser', choices=['edge', 'chromium'], default='edge', help='Trình duyệt chẩn đoán, mặc định là Edge đã cài trên máy.')
    parser.add_argument('--edge-path', help='Đường dẫn msedge.exe nếu cần chỉ định vị trí Edge.')
    parser.add_argument('--port', type=int, default=10000, help='Cổng server; dùng 0 để chọn cổng trống.')
    parser.add_argument('--startup-timeout', type=float, default=90, help='Số giây tối đa chờ server khởi động.')
    parser.add_argument('--close-after-test', action='store_true', help='Đóng cửa sổ chẩn đoán và server khi kiểm tra xong.')
    args = parser.parse_args()
    if not 0 <= args.port <= 65535 or not math.isfinite(args.startup_timeout) or args.startup_timeout <= 0:
        parser.error('Cổng phải nằm trong 0..65535; thời gian chờ phải là số dương hữu hạn.')
    if args.limit < 1 or not math.isfinite(args.conf) or not 0 <= args.conf <= 1:
        parser.error('--limit phải dương; --conf phải nằm trong 0..1.')
    if args.server_url:
        parts = urlsplit(args.server_url)
        try:
            parts.port
        except ValueError:
            parser.error('Cổng trong --server-url không hợp lệ.')
        if parts.scheme not in {'http', 'https'} or parts.hostname not in {'127.0.0.1', 'localhost'} or parts.username or parts.password or parts.path not in {'', '/'} or parts.query or parts.fragment:
            parser.error('--server-url phải là URL gốc của server local, không kèm tài khoản hoặc đường dẫn.')
    offline = bool(args.image or args.images_dir)
    mode = 'local_yolo' if offline else 'smoke' if args.smoke else 'extension_integration' if args.integration_smoke else 'diagnostics'
    output = ROOT / 'test-results' / datetime.now(LOCAL_TZ).strftime('%Y%m%d-%H%M%S-%f')
    output.mkdir(parents=True)
    report = RunReport(output, mode)
    print(f'Báo cáo cập nhật trực tiếp: {output / "report.json"}', flush=True)
    try:
        required = ['cv2', 'numpy', 'ultralytics'] if offline else ['requests']
        if not offline and not args.server_url:
            required += ['flask', 'flask_cors', 'cv2', 'ultralytics']
        if not offline and not args.smoke:
            required.append('playwright')
        if args.smoke or args.integration_smoke:
            required += ['cv2', 'numpy']
        missing = sorted({name for name in required if importlib.util.find_spec(name) is None})
        if missing:
            raise DiagnosticError('missing_dependencies', 'Thiếu thư viện: ' + ', '.join(missing), f'"{sys.executable}" -m pip install -r "{ROOT / "server1/requirements.txt"}" playwright')
        if offline:
            local_yolo(args, output, report)
        else:
            with Server(args, output, report) as server:
                if args.smoke:
                    report.stage('api_testing', 'Kiểm tra API bằng ảnh local.')
                    smoke_test(server, report)
                else:
                    with ExtensionBrowser(args, server, output, report) as session:
                        try:
                            if args.integration_smoke:
                                report.stage('integration_testing', 'Kiểm tra extension trên trang mẫu local.')
                                integration_smoke(args, server, output, report, session)
                            else:
                                session.page.goto(server.url + '/diagnostics', wait_until='domcontentloaded', timeout=15000)
                                session.page.locator('#captcha-server1-bridge').wait_for(state='attached', timeout=15000)
                                session.page.wait_for_function('JSON.parse(document.getElementById("captcha-server1-bridge").getAttribute("data-state") || "{}").serverReady === true', timeout=15000)
                                session.page.wait_for_function('document.getElementById("health").dataset.ready === "true"', timeout=15000)
                                session.page.wait_for_function('document.getElementById("bridge-state").textContent.includes("thành công")', timeout=15000)
                                report['extension']['content_connected'] = True
                                session.page.screenshot(path=str(output / 'diagnostics.png'))
                                report['status'] = 'environment_ready'
                                report.stage('environment_ready', 'Server và extension đã nối trên trang chẩn đoán local.')
                        except Exception as error:
                            record_error(report, error)
                        save_report(output, report)
                        if not args.integration_smoke and not args.close_after_test:
                            print(f'Trạng thái: {report["status"]}. Đóng trình duyệt chẩn đoán để dừng.', flush=True)
                            session.hold()
    except KeyboardInterrupt:
        report.update(status='cancelled', error_code='cancelled', error='Đã dừng bằng Ctrl+C.')
    except Exception as error:
        record_error(report, error)
    finally:
        if report['status'] not in {'failed', 'cancelled'}:
            report.stage('completed', f'Hoàn tất: {report["status"]}.')
        report['finished_at'] = datetime.now(LOCAL_TZ).isoformat(timespec='seconds')
        save_report(output, report)
        print(f'Trạng thái: {report["status"]}', flush=True)
        if 'error_code' in report:
            print(f'Mã lỗi: {report["error_code"]}', flush=True)
        if report.get('hint'):
            print(f'Gợi ý: {report["hint"]}', flush=True)
        if 'local_yolo' in report and 'summary' in report['local_yolo']:
            summary = report['local_yolo']['summary']
            print(f'Ảnh đã kiểm tra: {summary["tested"]}; có vùng: {summary["with_detections"]}; không có vùng: {summary["without_detections"]}; lỗi: {summary["failed"]}', flush=True)
        print(f'Báo cáo: {output / "report.json"}', flush=True)
    return 0 if report['status'] in {'environment_ready', 'server_smoke_passed', 'extension_integration_passed', 'local_yolo_completed'} else 1



if __name__ == '__main__':
    venv_python = ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if sys.prefix == sys.base_prefix and venv_python.is_file():
        raise SystemExit(subprocess.call([str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]]))
    raise SystemExit(main())
