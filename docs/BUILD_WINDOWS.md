# Build Windows: một file EXE

Chạy trên Windows với Python 3.12 64-bit:

| Lệnh | Kết quả trong `dist/` |
|---|---|
| `build.bat` hoặc `python build_windows.py` | **`RenderVideoReupPro_v1.0.3.exe`** (bản Pro) |
| `build.bat khach` hoặc `python build_windows.py --edition khach` | **`RenderVideoReup_v1.0.3.exe`** (bản thường) |
| `build.bat all` | cả 2 file |

Tên và `app_id` theo bản trong `edition.py`, phiên bản theo `APP_VERSION` trong
`license.py`. Bản được đóng cứng vào EXE (`_edition_stamp.py`), không đổi được
bằng biến môi trường trên máy khách. Chỉ gửi file EXE đúng bản cho từng khách.

Đây là bản portable cho Windows 10/11 64-bit: mở EXE để chạy, không cần cài
Python, pip, FFmpeg hay các thư viện Python trên máy người dùng. Không cần quyền
administrator. Khi chạy, PyInstaller tự giải nén runtime vào thư mục tạm.
Các file key/token/log phát sinh khi sử dụng là dữ liệu runtime, không phải
file thư viện cần gửi kèm.

Mỗi lần build tự xóa EXE cũ **cùng bản** trong `dist/` (build bản thường không
xóa EXE Pro và ngược lại), nên mỗi bản chỉ còn đúng một file EXE. Nếu EXE cũ đang mở (bị khóa), build báo lỗi:
hãy đóng app rồi build lại. Không có bộ cài setup.exe: cùng một file EXE dùng để
gửi khách và làm gói cập nhật.

## Phát hành bản mới qua GitHub Release

1. Tăng `APP_VERSION` trong `license.py` (ví dụ `1.0.3`), commit và push lên `main`.
2. Tạo tag trùng phiên bản rồi push: `git tag v1.0.3` và `git push origin v1.0.3`.
   CI dừng nếu tag khác `v` + `APP_VERSION`. Bản EXE có phiên bản sai sẽ bị updater
   khôi phục về bản cũ.
3. Workflow `build-windows.yml` build song song 2 bản, kiểm tra rồi tạo Release
   `v1.0.3` đính kèm `RenderVideoReupPro_v1.0.3.exe` và `RenderVideoReup_v1.0.3.exe`
   (mỗi file kèm `.sha256`). Summary của từng job ghi `app_id`, `download_url` và
   `sha256`.
4. Trên trang quản trị key, tạo phiên bản `1.0.3`, nền tảng `win` **cho từng app**:
   - `tool_reup_video_pro`: `download_url` dạng
     `https://github.com/<owner>/<repo>/releases/download/v1.0.3/RenderVideoReupPro_v1.0.3.exe`
   - `tool_reup_video`: `download_url` dạng
     `https://github.com/<owner>/<repo>/releases/download/v1.0.3/RenderVideoReup_v1.0.3.exe`

   và `sha256` tương ứng nếu có ô nhập. Dán nhầm link bản kia thì updater sẽ từ
   chối hoặc khôi phục bản cũ.

Nếu build trên máy, có thể tự tạo Release trên GitHub và kéo file EXE trong `dist/`
vào. Lấy link bằng chuột phải vào file đính kèm, chọn "Copy link address". Repo phải
public thì app của khách mới tải được. Không dùng link trang Release, link
"Source code" hay file `_Setup.exe`.

## Build và kiểm tra tự động

1. Script tạo môi trường riêng `.venv-build-windows`, cài các dependency trong
   `requirements-windows-build.txt`; không thay thư viện Python đang dùng để phát triển.
2. Kiểm tra Tcl/Tk thực sự tạo được cửa sổ, dependency không xung đột, OAuth
   Desktop và icon có đủ, FFmpeg/FFprobe là binary Windows thật.
3. Đóng gói onefile bằng `packaging/windows-onefile.spec`. Chỉ kèm hai binary
   FFmpeg Windows; kèm Python runtime, Tcl/Tk, theme/font CustomTkinter, Pillow,
   Excel, Google OAuth/Drive discovery, chứng chỉ HTTPS và Visual C runtime cần thiết.
   Không kèm `token.json`, `license.json`, key hoặc video của người dùng.
4. Chép duy nhất EXE sang thư mục thử nghiệm có tên tiếng Việt, đặt PATH chỉ
   còn thư mục Windows, bỏ biến môi trường Python/Tcl của máy build.
5. Chạy EXE bằng `--self-test <report.json>`: tạo các widget GUI ẩn, đọc ảnh/icon,
   chuẩn bị Drive/OAuth offline, kiểm tra CA TLS, đọc/ghi XLSX, tạo video H264/AAC
   và render logo bằng chính FFmpeg/FFprobe nhúng. Không gọi server bản quyền,
   không đăng nhập Google và không thay key/token đang dùng.

Self-test còn kiểm tra EXE đã đóng cứng đúng bản yêu cầu (`edition_source` =
`stamp`, đúng `app_id`). Build chỉ báo thành công nếu EXE qua đủ kiểm tra. Báo cáo
và SHA-256 nằm tại `build/windows-onefile-report-<pro|khach>.json`; kết quả từng kiểm tra ở
`build/windows-onefile-check/Máy khác - kiểm thử/self-test.json`.

Workflow `.github/workflows/build-windows.yml` thực hiện cùng quy trình trên
Windows runner mới. Windows/Linux vẫn có quy trình build riêng.

## Lưu dữ liệu và cập nhật

Key, token và log luôn được ghi vào `%LOCALAPPDATA%/RenderVideoReupPro`
(hoặc `RenderVideoReup` cho bản thường), chung cho chạy nguồn và EXE.
Đổi tên, di chuyển hoặc cập nhật EXE không đổi nơi lưu dữ liệu. Không xóa thư mục
này khi thay EXE.

Lần đầu dùng bản mới, đặt EXE cạnh `license.json`/`token.json` cũ để tự chuyển
dữ liệu một lần. File mới sẵn có được ưu tiên. File cũ được giữ dự phòng nhưng
không được nhập lại sau khi đăng xuất/hủy kích hoạt. Xem
`KE_HOACH_LUU_DU_LIEU_UNG_DUNG.md` để biết các tình huống chuyển dữ liệu.

Self-test còn kiểm tra lưu cố định, chuyển dữ liệu và chống khôi phục phiên đã
đăng xuất bằng dữ liệu giả. Thư mục chứa EXE thử nghiệm bị chặn quyền tạo file/
thư mục bằng ACL Windows; thư mục profile và báo cáo nằm riêng. ACL được khôi
phục sau khi kiểm tra.

Updater nhận trực tiếp EXE standalone mới hoặc ZIP chứa EXE đó. Nó sao chép EXE
cũ sang thư mục tạm để chạy cửa sổ tiến trình độc lập, chờ cả app lẫn bootloader
onefile thoát rồi thay file và khởi động lại. Tên EXE cũ được giữ để lối tắt vẫn
hoạt động. Nếu cài/khởi động lỗi thì khôi phục bản cũ.

Để cập nhật trong app, đặt EXE ở thư mục người dùng có quyền ghi; vị trí chỉ đọc
vẫn chạy được nhưng không cho updater tự ghi đè. `download_url` trên server phải
trỏ tới EXE standalone hoặc ZIP của bản mới, không phải trang web hay bộ cài đặt.
Xem thêm `KIEM_TRA_PHIEN_BAN.md`.

## Giới hạn của việc xác minh

Self-test kiểm tra EXE độc lập với PATH và thư viện Python của máy build.
Nó không thay cho kiểm thử trên mọi phiên bản Windows, phần mềm antivirus,
proxy hay cấu hình mạng của khách hàng. Nên thử artifact trên máy Windows 10/11
sạch trước khi phát hành rộng. Lần mở đầu có thể lâu do giải nén FFmpeg và runtime.
