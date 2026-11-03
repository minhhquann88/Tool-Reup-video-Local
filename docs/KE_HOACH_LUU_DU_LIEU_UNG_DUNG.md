# Kế hoạch lưu license và token vào thư mục cố định

Trạng thái: đã triển khai trong mã ứng dụng; kiểm tra bằng test và EXE onefile.

## Mục tiêu

`license.json` và `token.json` thuộc tài khoản người dùng của hệ điều hành.
Đường dẫn của chúng phải giữ ổn định khi đổi tên, chuyển vị trí hoặc cập nhật
EXE, và khi chạy bằng mã nguồn. Bản Pro và bản thường dùng thư mục riêng.

## Vị trí lưu

Windows, bản Pro:

```text
%LOCALAPPDATA%\RenderVideoReupPro\
    license.json
    token.json
```

Trên tài khoản MQ, vị trí thông thường là
`C:\Users\MQ\AppData\Local\RenderVideoReupPro\`.
Bản thường dùng `%LOCALAPPDATA%\RenderVideoReup\`.

Linux dùng `${XDG_CONFIG_HOME:-~/.config}/RenderVideoReupPro/` hoặc
`RenderVideoReup/` tương ứng. Thư mục không chứa số phiên bản, tên EXE hay tên
thư mục cài đặt. Mỗi tài khoản hệ điều hành có dữ liệu riêng.

Các cửa sổ cùng bản ứng dụng, cùng tài khoản hệ điều hành sẽ dùng chung key và
tài khoản Google Drive. Thay đổi đường dẫn không thay đổi deviceId hoặc quy tắc
xác thực key trên server.

## 1. Thống nhất cách xác định đường dẫn

- Thêm `app_paths.py` để xác định thư mục dữ liệu và đường dẫn hai file.
- `license.get_license_path()` và `drive.get_token_path()` cùng dùng module này.
- Bỏ việc chọn thư mục dựa trên vị trí EXE, thư mục nguồn hoặc quyền ghi cạnh EXE.
- Nếu không tạo/ghi được thư mục dữ liệu, báo lỗi rõ ràng; không tự chuyển sang
  một vị trí khác khiến người dùng không biết file đang nằm ở đâu.
- Log lỗi hiện đang phụ thuộc `license._exe_dir()` cũng chuyển sang thư mục dữ
  liệu cố định. Tài nguyên nhúng như FFmpeg, icon và cấu hình OAuth vẫn dùng bộ
  phân giải tài nguyên đóng gói hiện có.

## 2. Chuyển dữ liệu cũ một lần

Thực hiện trước khi kiểm tra key lúc khởi động hoặc đăng nhập Drive:

1. Khóa việc chuyển dữ liệu giữa các tiến trình để nhiều cửa sổ không chuyển
   cùng lúc.
2. Nếu file ở thư mục mới đã tồn tại, ưu tiên file đó, không ghi đè bằng bản cũ.
3. Nếu file mới chưa có và chưa ghi nhận đã chuyển, tìm đúng file cùng thư mục
   EXE đang chạy; khi chạy nguồn thì tìm cùng thư mục nguồn. Vị trí AppImage cũ
   được xử lý tương ứng. Không quét toàn bộ ổ đĩa tìm key/token.
4. Kiểm tra cấu trúc JSON của từng loại, sao chép bằng thao tác ghi nguyên tử.
   Key vẫn phải được xác thực trên server; token vẫn đi qua luồng kiểm tra/làm
   mới hiện có khi thực sự dùng Drive.
5. Ghi dấu đã xử lý riêng cho từng file. Dấu này phải ngăn việc nhập lại bản cũ
   sau khi người dùng đăng xuất hoặc hủy kích hoạt.
6. Giữ file cũ làm dự phòng trong lần chuyển đầu; sau đó app chỉ đọc/ghi đường
   dẫn mới. Không ghi nội dung key hoặc token vào log.

Trường hợp người dùng đã chuyển EXE sang nơi khác trước lần chạy đầu của bản
mới, app không thể tự biết thư mục cũ. Khi triển khai cần hướng dẫn mở bản mới
tại thư mục đang chứa dữ liệu cũ một lần để chuyển dữ liệu trước khi di chuyển EXE.
Lần mở chưa tìm thấy dữ liệu cũ không khóa khả năng chuyển dữ liệu sau này;
đăng xuất/hủy kích hoạt vẫn ghi dấu chặn nhập lại, kể cả khi file đang không có.

## 3. Đồng bộ đọc, ghi và đăng xuất

- Ghi qua file tạm có tên riêng rồi thay thế nguyên tử; không ghi đè trực tiếp
  khiến JSON bị thiếu khi app đóng đột ngột.
- Dùng khóa giữa các tiến trình cho thao tác chuyển dữ liệu, cập nhật token,
  lưu key và xóa dữ liệu đăng nhập. Khóa trong một tiến trình hiện tại chưa đủ
  cho bản Pro mở nhiều cửa sổ.
- Đăng xuất Google chỉ xóa token của hồ sơ hiện tại; hủy kích hoạt chỉ xóa key.
- Chặn phản hồi xác thực/làm mới token cũ ghi dữ liệu trở lại sau khi người dùng
  đã đăng xuất/hủy kích hoạt, bằng trạng thái thay đổi được kiểm tra dưới khóa.
- Báo lỗi lưu dữ liệu thay vì báo kích hoạt/đăng nhập đã lưu thành công trong khi
  thao tác ghi thất bại.
- Giới hạn quyền file/thư mục theo tài khoản hệ điều hành; trên Linux dùng thư
  mục `0700`, file xác thực `0600`.

## 4. Cập nhật, đóng gói và tài liệu

- Kiểm tra updater chỉ thay file chương trình, giữ nguyên thư mục dữ liệu và
  không mang key/token vào thư mục helper hay gói phát hành.
- Bổ sung `app_paths.py` vào danh sách gói nguồn, installer và các fixture cần
  thiết; xác nhận PyInstaller thu thập module mới trong EXE onefile.
- Cập nhật self-test, hướng dẫn Windows và hướng dẫn kiểm tra phiên bản với
  đường dẫn mới.
- Có thể bổ sung nút mở thư mục dữ liệu tại phần cài đặt để người dùng dễ tìm.
  Việc này không phải điều kiện để hoàn thành thay đổi lưu trữ.

## 5. Kiểm thử và điều kiện hoàn thành

Toàn bộ test dùng thư mục tạm, không đọc/ghi dữ liệu đăng nhập thật.

- Chạy từ nguồn, EXE onefile và nhiều vị trí EXE vẫn trả cùng đường dẫn dữ liệu.
- Chuyển dữ liệu cũ khi chỉ có một file hoặc đủ hai file; JSON hỏng không làm
  hỏng file mới; dữ liệu mới sẵn có không bị ghi đè.
- Sau chuyển dữ liệu, đăng xuất/hủy kích hoạt rồi mở lại không khôi phục dữ liệu
  từ file cũ còn nằm cạnh EXE.
- Nhiều tiến trình làm mới/ghi/xóa không tạo JSON hỏng hoặc khôi phục phiên đã
  đăng xuất; lỗi ghi/thiếu quyền được báo đúng.
- Cập nhật và rollback giữ nguyên key/token ở vị trí cố định.
- Build lại một EXE, chạy kiểm tra với hồ sơ tạm và thư mục đặt EXE chỉ đọc;
  kiểm tra gói không chứa dữ liệu người dùng.

Kết quả mong muốn: người dùng có thể thay hoặc chuyển riêng file EXE; thông tin
kích hoạt và đăng nhập Drive vẫn gắn với hồ sơ người dùng, không gắn với chỗ đặt EXE.
