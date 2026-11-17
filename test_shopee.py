r"""Run one signup/CAPTCHA test; stop when Shopee shows the OTP step.

    .venv\Scripts\python.exe test_shopee.py
    .venv\Scripts\python.exe test_shopee.py --smoke

The phone number is requested locally and is never included in the report.
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
import socket
import subprocess
import sys
import time
from datetime import datetime
from urllib.parse import unquote_to_bytes


ROOT = Path(__file__).resolve().parent
CAPTCHA_ROOT = '.shopee-captcha, .shopee-captcha-slider__main, .er1GfA, [class*="captcha" i]'
BACKGROUND = ', '.join(f'{selector} img' for selector in CAPTCHA_ROOT.split(', '))
HANDLE = (
    '.shopee-captcha-slider__button, .shopee-captcha-slider__btn, '
    '[class*="slider" i][class*="button" i], [role="slider"], img.j5BVKH'
)


def phone_number():
    import tkinter as tk
    from tkinter import simpledialog, messagebox

    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    try:
        while True:
            value = simpledialog.askstring(
                'Test Shopee',
                'Nhập số điện thoại của bạn để thử đăng ký Shopee.\n'
                'Script bấm Tiếp theo, thử CAPTCHA một lần và dừng ở bước OTP.\n'
                'Số điện thoại không được lưu vào báo cáo.', parent=root,
            )
            if value is None:
                raise InterruptedError('Đã hủy nhập số điện thoại.')
            value = re.sub(r'[\s().-]', '', value)
            if value.startswith('+84'):
                value = '0' + value[3:]
            if re.fullmatch(r'0[35789]\d{8}', value):
                return value
            messagebox.showerror('Số chưa hợp lệ', 'Nhập số di động Việt Nam gồm 10 chữ số.', parent=root)
    finally:
        root.destroy()


class Server:
    def __init__(self, args, output):
        self.args, self.output = args, output
        self.process = self.log = None
        self.url = args.server_url.rstrip('/') if args.server_url else ''

    def __enter__(self):
        import requests

        if not self.url:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', self.args.port))
                port = sock.getsockname()[1]
            self.url = f'http://127.0.0.1:{port}'
            env = dict(os.environ, HOST='127.0.0.1', PORT=str(port), PYTHONUNBUFFERED='1', PYTHONIOENCODING='utf-8')
            self.log = (self.output / 'server.log').open('w', encoding='utf-8')
            self.process = subprocess.Popen(
                [sys.executable, str(ROOT / 'server1/server.py')], cwd=ROOT,
                env=env, stdout=self.log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
        try:
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if self.process and self.process.poll() is not None:
                    raise RuntimeError(f'Server dừng khi khởi động. Xem {self.output / "server.log"}')
                try:
                    response = requests.get(self.url + '/health', timeout=2)
                    response.raise_for_status()
                    health = response.json()
                    if health.get('ok'):
                        print(f'Server sẵn sàng: {self.url}; model_loaded={health.get("model_loaded")}', flush=True)
                        self.health = health
                        return self
                except (requests.RequestException, ValueError):
                    pass
                time.sleep(0.5)
            raise TimeoutError('Không nhận được /health sau 90 giây.')
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
        if self.log:
            self.log.close()

    def detect(self, image):
        import requests

        response = requests.post(self.url + '/detect', json={'image': image}, timeout=60)
        try:
            result = response.json()
        except ValueError as exc:
            raise RuntimeError(f'/detect trả HTTP {response.status_code}, không phải JSON.') from exc
        if not response.ok or not result.get('ok'):
            raise RuntimeError(f'/detect lỗi: {result}')
        points = result.get('points')
        if not isinstance(points, list) or any(
            not isinstance(point, dict) or any(
                not isinstance(point.get(axis), (int, float)) or not math.isfinite(point[axis])
                for axis in ('x', 'y')
            ) for point in points
        ):
            raise RuntimeError('Server trả tọa độ không hợp lệ.')
        return result


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


def live_test(args, server, output, report):
    from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        try:
            page = browser.new_page(locale='vi-VN', viewport={'width': 1280, 'height': 850})
            report['stage'] = 'open_signup'
            try:
                page.goto('https://shopee.vn/buyer/signup', wait_until='commit', timeout=60000)
            except BrowserTimeout:
                # A slow subresource must not block an already visible signup form.
                if page.get_by_placeholder(re.compile(r'Số điện thoại|Phone number', re.I)).count() == 0:
                    raise
            phone_input = page.get_by_placeholder(re.compile(r'Số điện thoại|Phone number', re.I)).first
            report['stage'] = 'find_phone_input'
            phone_input.wait_for(state='visible', timeout=60000)
            print('Nhập số trong hộp thoại trên máy để bắt đầu.', flush=True)
            report['stage'] = 'await_phone_input'
            phone = phone_number()
            try:
                report['stage'] = 'fill_phone'
                phone_input.fill(phone)
                phone_input.press('Tab')
                report['stage'] = 'click_next'
                page.get_by_role('button', name=re.compile(r'^(Tiếp theo|Next)$', re.I)).first.click(timeout=15000)
            finally:
                phone = None
            print('Đã bấm Tiếp theo. Đang chờ CAPTCHA...', flush=True)
            report['signup_submitted'] = True
            report['stage'] = 'wait_captcha'
            deadline = time.monotonic() + args.timeout
            captured = None
            while time.monotonic() < deadline:
                if otp_visible(page):
                    report['status'] = 'otp_without_captcha'
                    return
                captured = find_background(page, args.background_selector)
                if captured:
                    break
                page.wait_for_timeout(250)
            if not captured:
                raise TimeoutError('Không thấy CAPTCHA trong thời gian chờ. Có thể trang báo lỗi hoặc đổi giao diện.')
            frame, image, _ = captured
            report['stage'] = 'read_captcha_image'
            raw, dimensions = read_image(page, image)
            (output / 'captcha-image.bin').write_bytes(raw)
            image.screenshot(path=str(output / 'captcha.png'))
            encoded = 'data:image/png;base64,' + base64.b64encode(raw).decode()
            report['stage'] = 'detect'
            result = server.detect(encoded)
            report['detection'] = result
            report['image_sha256'] = hashlib.sha256(raw).hexdigest()
            report['image_dimensions'] = dimensions
            print(f'Server: source={result.get("source")}; points={result["points"]}', flush=True)
            if len(result['points']) != 1:
                raise RuntimeError(f'Server trả {len(result["points"])} điểm; chưa xác định được một vị trí kéo duy nhất.')
            report['stage'] = 'drag'
            report['drag'] = drag_to_point(page, frame, image, result['points'][0], dimensions, args.handle_selector)
            page.wait_for_timeout(700)
            if args.verify_selector:
                verify = first_visible(frame, args.verify_selector)
            else:
                verify = first_visible(frame, 'button.rb6XLo')
                if verify is None:
                    for root in frame.locator(CAPTCHA_ROOT).all():
                        buttons = root.get_by_role('button', name=re.compile(r'^(Xác minh|Xác nhận|Verify|Confirm|驗證)$', re.I))
                        if buttons.count() and buttons.first.is_visible():
                            verify = buttons.first
                            break
            report['stage'] = 'verify'
            if verify is not None:
                verify.click(timeout=5000)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if otp_visible(page):
                    report['status'] = 'captcha_passed_otp_visible'
                    return
                page.wait_for_timeout(250)
            report['status'] = 'captcha_still_visible' if find_background(page, args.background_selector) else 'captcha_disappeared_unconfirmed'
        finally:
            browser.close()


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


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--smoke', action='store_true', help='Test CSV và YOLO bằng ảnh local, không gửi đăng ký.')
    parser.add_argument('--server-url', help='Dùng server đã chạy, ví dụ http://127.0.0.1:10000.')
    parser.add_argument('--port', type=int, default=0, help='Cổng server local; mặc định chọn cổng trống.')
    parser.add_argument('--timeout', type=float, default=60, help='Số giây chờ CAPTCHA.')
    parser.add_argument('--background-selector', help='CSS selector ảnh nền nếu giao diện đã thay đổi.')
    parser.add_argument('--handle-selector', help='CSS selector nút kéo nếu giao diện đã thay đổi.')
    parser.add_argument('--verify-selector', help='CSS selector nút xác minh nếu có.')
    args = parser.parse_args()
    if args.timeout <= 0 or not 0 <= args.port <= 65535:
        parser.error('--timeout phải dương; --port nằm trong 0..65535.')
    if args.server_url and not re.fullmatch(r'https?://[^/?#]+', args.server_url.rstrip('/')):
        parser.error('--server-url phải là URL gốc của server, không kèm đường dẫn.')
    required = ['requests'] + ([] if args.server_url else ['flask', 'flask_cors', 'cv2', 'ultralytics'])
    required += ['cv2', 'numpy'] if args.smoke else ['playwright']
    missing = sorted({name for name in required if importlib.util.find_spec(name) is None})
    if missing:
        print('Thiếu thư viện: ' + ', '.join(missing))
        print(f'Chạy: "{sys.executable}" -m pip install -r "{ROOT / "server1/requirements.txt"}" playwright')
        return 2
    output = ROOT / 'test-results' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    output.mkdir(parents=True)
    report = {'mode': 'smoke' if args.smoke else 'shopee_signup', 'status': 'started'}
    code = 1
    try:
        with Server(args, output) as server:
            report['health'] = server.health
            if args.smoke:
                smoke_test(server, report)
            else:
                live_test(args, server, output, report)
        code = 0 if report['status'] in {'server_smoke_passed', 'captcha_passed_otp_visible'} else 3
    except KeyboardInterrupt:
        report.update(status='cancelled', error='Đã dừng bằng Ctrl+C.')
    except Exception as exc:
        # Playwright call logs can include fill() values; redact phone digits.
        report.update(status='failed', error_type=type(exc).__name__)
        report['error'] = re.sub(r'\d{6,}', '[redacted]', str(exc))[:3000]
    finally:
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print(f'Báo cáo: {output / "report.json"}', flush=True)
    return code


if __name__ == '__main__':
    # A double click can use the prepared project venv without changing PATH.
    venv_python = ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if sys.prefix == sys.base_prefix and venv_python.is_file():
        raise SystemExit(subprocess.call([str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]]))
    raise SystemExit(main())
