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

## Chẩn đoán khởi động và kết nối

Chạy từ thư mục gốc dự án:

```powershell
python -m pip install -r server1/requirements.txt playwright
python test_shopee.py
```

Lệnh mặc định mở bảng chẩn đoán **local**, khởi động server1, xác minh
`/health`, nạp extension vào profile Microsoft Edge riêng và kiểm tra content
script trên `/diagnostics`. Trang hiển thị riêng trạng thái server, model
và extension. Chế độ này không gửi đăng ký và không chạy nhận diện ảnh trên
trang. Tùy chọn `--setup-only` tương đương chế độ mặc định.

Mặc định sử dụng **Microsoft Edge đã cài trên máy**, tự tìm `msedge.exe`
trong các thư mục cài đặt Windows và `PATH`. Có thể chỉ định vị trí:

```powershell
python test_shopee.py --edge-path "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
```

Không cần tải Chromium để dùng Edge. Profile chẩn đoán nằm trong thư mục
kết quả của lần chạy, tách với profile Edge cá nhân. Báo cáo ghi tên trình
duyệt, đường dẫn thực thi và phiên bản đã chạy. Để kiểm thử local bằng
Chromium thay vì Edge:

```powershell
python -m playwright install chromium
python test_shopee.py --browser chromium
```

Khả năng nạp extension phụ thuộc bản trình duyệt đang cài; nếu không nạp
được, tool báo `extension_not_loaded` thay vì báo kết nối thành công.

Trình duyệt và server được giữ mở để quan sát. Đóng cửa sổ chẩn đoán để dừng
server do script mở; server có sẵn được giữ nguyên. Dùng `--close-after-test`
để tự đóng sau khi kiểm tra.

Mặc định dùng cổng 10000. Khi cổng này bị dịch vụ khác chiếm, thông báo lỗi
nêu rõ nguyên nhân và gợi ý `--port 0`. Nếu đó là server1 phiên bản cũ, script
mở phiên bản mới trên cổng trống. Có thể kết nối server local đã chạy bằng
`--server-url http://127.0.0.1:10000`.

`report.json` được cập nhật sau từng giai đoạn, không phải chờ test kết thúc.
Lỗi có `error_code`, `error`, `hint` và chi tiết liên quan. Các lỗi chính:

- `missing_dependencies`: thiếu thư viện Python.
- `port_in_use` / `wrong_service`: cổng đang bận hoặc sai dịch vụ.
- `server_exited` / `server_timeout`: server dừng hoặc quá thời gian chờ.
- `health_invalid_json`: địa chỉ server không trả dữ liệu JSON hợp lệ.
- `missing_browser`: thiếu Chromium cho Playwright.
- `edge_not_found`: không tìm thấy Edge hoặc đường dẫn chỉ định không đúng.
- `extension_connection_failed`: extension chưa nối được API server.

Dùng `--startup-timeout 30` để thay đổi thời gian chờ khởi động.

## Kiểm thử trên ảnh và trang mẫu local

Chạy YOLO trực tiếp trên một ảnh; chế độ này không mở server, trình duyệt
hoặc tra CSV/GitHub:

```powershell
python test_shopee.py --image server1/data/dataset/images/00ae58ca21c85376f499699f6c3fb816.png
```

Kiểm tra tối đa 12 ảnh trong thư mục:

```powershell
python test_shopee.py --images-dir server1/data/dataset/images --limit 12
```

Có thể chỉ định `--model đường_dẫn_model.pt` và `--conf 0.8`. Model phải có
sẵn trên máy. Báo cáo ghi từng khung nhận diện, lớp và độ tin cậy, kèm ảnh
được vẽ khung. `no_detections` nghĩa là model không tìm thấy vùng ở ngưỡng
đang dùng, không phải lỗi kết nối. Dự đoán chưa được coi là nhãn chuẩn.

Kiểm tra API bằng ảnh local:

```powershell
python test_shopee.py --smoke --port 0
```

Kiểm tra extension thật, API và thao tác kéo trên hai trang mẫu do test phục
vụ tại địa chỉ local (ảnh gốc và ảnh thu nhỏ trong iframe):

```powershell
python test_shopee.py --integration-smoke --port 0
```

`server_smoke_passed` xác nhận API chạy được; `extension_integration_passed`
xác nhận cả luồng trang mẫu. Các trạng thái này không đánh giá độ chính xác
của model trên CAPTCHA bên ngoài.

Ảnh kết quả, log server, báo cáo, profile trình duyệt và cấu hình extension
theo từng lần chạy nằm trong `test-results/`, được loại khỏi Git. Nếu có
`.venv` trong dự án, entry point tự sử dụng môi trường đó.

Kiểm tra hồi quy cho các lỗi khởi động và báo cáo:

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -v
```
