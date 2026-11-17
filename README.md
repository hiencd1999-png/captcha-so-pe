# captcha-so-pe

Mã nguồn server xử lý ảnh CAPTCHA và extension thử nghiệm.

## Server Python (`server1`)

Yêu cầu Python và các thư viện trong `server1/requirements.txt`.

```powershell
python -m pip install -r server1/requirements.txt
python server1/server.py
```

Server mặc định lắng nghe tại `http://localhost:10000` và dùng model
`server1/best.pt`. Đường dẫn model và dữ liệu được tính từ thư mục mã nguồn,
không phụ thuộc thư mục chạy lệnh.

- `GET /health`: kiểm tra server và trạng thái model.
- `POST /detect` hoặc `/api/detect`: nhận JSON có trường `image` chứa ảnh Base64.
  Server tra nhãn trên GitHub trước, sau đó tra CSV local; nếu chưa có nhãn thì
  dùng YOLO. Danh sách nhãn từ GitHub được cache trong 60 giây.
- `GET /api/captures`: liệt kê nhãn remote và local.
- `POST /api/captures`: nhận `backgroundImage` và `sliderX`, ghi nhãn vào CSV
  local. API này không tự commit hoặc đẩy dữ liệu lên GitHub.

Nguồn nhãn mặc định thuộc tài khoản `hiencd1999-png`:

<https://raw.githubusercontent.com/hiencd1999-png/captcha-so-pe/main/server1/data/dataset/labels.csv>

Biến môi trường có thể cấu hình: `PORT`, `HOST`, `DATASET_DIR`, `LABELS_URL`,
`YOLO_MODEL_PATH`, `YOLO_CONF`, `YOLO_OFFSET_X`, `YOLO_OFFSET_Y`.

Sau khi cập nhật nhãn local, commit và push `server1/data/dataset/labels.csv`
để những server khác đọc được dữ liệu mới từ GitHub.

## Server Node.js (`server1`)

Đây là một server thay thế cho bản Python, cũng mặc định dùng cổng `10000`.
Không chạy cả hai bản trên cùng cổng. Worker cần các thư viện Python của
`server1/requirements.txt`.

```powershell
node server1/server.js
```

Bản Node chuyển ảnh cho `yolo_worker.py`, không tra CSV trước khi nhận diện.
API lưu dữ liệu ghi cả ảnh và nhãn vào `server1/data/dataset`.

## Bản so ảnh (`source2`)

`source2/server.py` là server riêng dùng OpenCV để so ảnh với các PNG mẫu có
tên dạng `left_top.png`, chạy trên cổng `5000`. Extension trong
`source2/extension` gọi API `/solve` của bản này; chưa nối với `server1`.
URL server của extension nằm trong `content.js`, quyền truy cập nằm trong
`manifest.json` và cần khớp với địa chỉ server sử dụng.

## Chạy thử bằng một file Python

`test_shopee.py` khởi động server Python, mở Chromium, hỏi số điện thoại bằng
hộp thoại trên máy, điền form đăng ký Shopee Việt Nam và bấm Tiếp theo một lần.
Khi thấy CAPTCHA, script gửi ảnh gốc cho `/detect`, quy đổi tọa độ theo kích
thước hiển thị và thử kéo một lần. Script kết thúc ở bước OTP; không nhập OTP
hoặc hoàn tất tài khoản.

```powershell
python -m pip install -r server1/requirements.txt playwright
python -m playwright install chromium
python test_shopee.py
```

Nếu có `.venv` tại gốc dự án, script tự chạy lại bằng Python trong môi trường
đó. Test API bằng ảnh local, không cần số điện thoại hoặc gửi đăng ký:

```powershell
python test_shopee.py --smoke
```

Ảnh CAPTCHA, log server và `report.json` nằm trong `test-results/` và được
loại khỏi Git. Báo cáo không lưu số điện thoại. Mỗi lần chạy tự chọn cổng
server trống và dừng server do chính script mở khi kết thúc. Có thể dùng
`--server-url http://127.0.0.1:10000` để kiểm tra server đã chạy.

`server_smoke_passed` chỉ xác nhận API tra CSV và suy luận YOLO hoạt động.
`captcha_passed_otp_visible` xác nhận đã nhìn thấy bước OTP sau lần thử kéo;
`captcha_disappeared_unconfirmed` chưa đủ để kết luận vượt CAPTCHA. Shopee có
thể đổi giao diện hoặc hiện loại CAPTCHA khác; khi đó cần chỉ định
`--background-selector`, `--handle-selector`, `--verify-selector` theo giao
diện thực tế. Chạy `python test_shopee.py --help` để xem các tùy chọn.
