# Kiểm tra phiên bản ứng dụng

Phiên bản dùng chung nằm tại `APP_VERSION` trong `license.py`, dạng SemVer
`1.0.3`. Giao diện và tên gói build thêm tiền tố `v`.

Mỗi lần kích hoạt, mở ứng dụng hoặc kiểm tra định kỳ 30 phút, client gửi
`key`, `app_id`, `deviceId`, `current_version`, `platform` đến
`https://key.byscom.vn/api/validate-key`. `platform` là `win`, `linux` hoặc `mac`.
Hợp đồng response theo phần B2 của `HUONG_DAN_TRIEN_KHAI_VA_TICH_HOP.md`.

- `update: null`: tiếp tục sử dụng bình thường.
- `has_update: true`, `force_update: false`: hiện phiên bản mới, ghi chú và nút
  cập nhật trong ứng dụng; có thể bỏ qua. Một phiên bản chỉ được thông báo một lần mỗi phiên dùng.
- `has_update: true`, `force_update: true` **lúc mở app**: khóa vào ứng dụng,
  ẩn cửa sổ chính, hủy timer và hiện dialog độc lập có modal, không cho đóng.
  Người dùng bấm cập nhật để bắt đầu tải, cài đặt và tự mở lại bản mới.
- Nếu admin đăng hoặc chuyển sang bản **bắt buộc trong khi người dùng đang dùng**,
  các lần recheck không hiện dialog ép cập nhật, không dừng render và không khóa
  chức năng. Người dùng tiếp tục sử dụng phiên hiện tại. Khi đóng rồi mở lại,
  app hỏi lại server và áp dụng yêu cầu cập nhật mới nhất trước khi vào app.
- Khi đã vào ứng dụng, lỗi mạng/server giữ key và thử lại sau 5 phút, tối đa
  3 lần rồi trở về chu kỳ 30 phút. Key bị thu hồi/hết hạn vẫn khóa ứng dụng.
  Khi khởi động, vẫn cần xác thực key thành công trước khi vào app.

### Xác thực key định kỳ

Client gọi cùng endpoint với `key`, `app_id`, `deviceId`, `current_version`,
`platform`. Trên Windows, `deviceId` lấy từ MachineGuid, dự phòng theo MAC.
Client xét `status` trong JSON, không chỉ HTTP status và không tự kết luận key
hợp lệ dựa trên ngày hết hạn đã lưu.

- Mở app: key hợp lệ mới vào được; chưa có/sai key thì hiện kích hoạt; lỗi mạng
  thì yêu cầu thử lại trước khi vào app.
- Trong phiên: recheck mặc định 30 phút trên luồng nền. Key hợp lệ tiếp tục sử dụng
  và hẹn lần kiểm tra kế tiếp, kể cả response kèm bản cập nhật bắt buộc.
- Key bị server từ chối/hết hạn/thu hồi: xóa key đã lưu, dừng tác vụ và khóa app,
  yêu cầu kích hoạt lại. Chính sách trì hoãn cập nhật không trì hoãn việc này.
- Lỗi mạng/server/timeout: giữ key, không khóa phiên đang dùng; retry sau 5 phút,
  tối đa 3 lượt retry rồi quay lại chu kỳ 30 phút.

Các khoảng thời gian có thể đổi qua `LICENSE_RECHECK_SECONDS`,
`LICENSE_RETRY_SECONDS`, `LICENSE_MAX_RETRIES`. Quy tắc chỉ ép cập nhật lúc mở
app là chính sách của client dự án này, thay cho ép cập nhật ngay giữa phiên
trong mẫu tích hợp chung.

Để phát hành, tạo phiên bản active trên trang quản trị cho
`app_id=tool_reup_video_pro`, chọn đúng nền tảng, `download_url`, `changelog`
và `force_update`/`min_version`. Backend quyết định có cần cập nhật hay không.

## Cập nhật trong ứng dụng và cửa sổ tiến trình

Nút **Cập nhật ngay trong ứng dụng** khởi tạo một updater độc lập, chạy từ
thư mục tạm. Với bản Windows onefile, chỉ sao chép EXE và chạy với
`PYINSTALLER_RESET_ENVIRONMENT=1` để tự giải nén runtime riêng. Bản onedir cũ
sao chép cả runtime. Updater không khóa file cần thay và không thoát theo app chính.

1. App hiển thị tiến trình chuẩn bị, dừng render và lưu kết quả CSV.
2. Updater mở cửa sổ riêng trước khi app ẩn cửa sổ chuẩn bị.
3. Cửa sổ riêng hiển thị dung lượng tải, phần trăm (nếu server có tổng dung lượng),
   số file giải nén, bước cài đặt và thời gian đã chạy. Giao diện vẫn phản hồi
   trong lúc thao tác mạng/file chạy trên luồng nền.
4. Chỉ khi tải và kiểm tra gói xong, app mới đóng sau khi batch đã dừng hoàn toàn.
5. Updater thay file chương trình, giữ bản sao cũ, rồi mở bản mới. Cửa sổ tiến trình
   chỉ tự đóng sau khi bản mới báo đã mở được giao diện với đúng phiên bản.
6. Nếu lỗi, cửa sổ không tự biến mất: hiện nguyên nhân và nút thử lại. Nếu thay file
   hoặc khởi động thất bại, khôi phục bản cũ; có nút mở lại bản cũ khi app đã đóng.
   Trường hợp hệ điều hành không cho khôi phục, log và bản sao được giữ để xử lý.

Key (`license.json`), token Drive (`token.json`), OAuth client của người dùng,
log, video và dữ liệu khác trong thư mục app được giữ nguyên. Cập nhật chỉ thay
các module/asset đã cho phép, hoặc executable và `_internal` của bản đóng gói.
Tên executable đang dùng được giữ ổn định để lối tắt cũ vẫn hoạt động.

`license.json`/`token.json` hiện dùng thư mục dữ liệu cố định theo tài khoản:
Windows là `%LOCALAPPDATA%/RenderVideoReupPro`, Linux là
`${XDG_CONFIG_HOME:-~/.config}/RenderVideoReupPro`. Bản thường dùng thư mục
`RenderVideoReup`. Dữ liệu cũ cạnh EXE được chuyển một lần khi được tìm thấy;
đăng xuất/hủy kích hoạt chặn việc nhập lại. Updater và rollback không thay thư
mục này. Việc đổi nơi lưu không thay đổi lịch xác thực key hoặc chính sách chỉ
ép cập nhật lúc mở app.

`download_url` phải là liên kết trực tiếp tới đúng loại gói:

| Bản đang chạy | Gói cập nhật |
|---|---|
| Windows onefile | File EXE standalone trực tiếp hoặc ZIP chỉ chứa EXE standalone |
| Windows onedir | ZIP chứa `RenderVideoReup*.exe` và `_internal/` |
| Linux onedir | tar.gz/ZIP chứa executable `RenderVideoReup*` và `_internal/` |
| Linux AppImage | File `.AppImage` trực tiếp |
| Chạy Python từ mã nguồn | Gói mã nguồn ZIP/tar.gz có đầy đủ module, `APP_ID` và `APP_VERSION` khớp |

Không dùng trang HTML, file setup/installer `.exe` hoặc gói của nền tảng khác.
Gói mới phải được build từ mã có updater và cơ chế báo giao diện đã sẵn sàng.
Với onefile, updater chờ cả PID app và bootloader thoát trước khi thay EXE,
và cho bản mới tối đa 240 giây để giải nén và báo đã mở giao diện.
Bản nguồn cần môi trường đã có các dependency của bản mới; nếu không khởi động
được, updater khôi phục mã cũ. Nếu metadata có trường `sha256`, client kiểm tra
mã này trước khi giải nén. ZIP/tar bị chặn ghi ra ngoài thư mục staging.

Log chẩn đoán nằm trong thư mục tạm `reup-update-*`: `helper.log`, `update.log`,
`startup.log`. Cache của lần cập nhật thành công/hủy được dọn sau khi updater
đã thoát; cache và log của lần bị lỗi được giữ lại. Nếu quá trình rollback bị hệ điều hành chặn, bản sao còn trong
`.reup-install-*` ở thư mục ứng dụng; không xóa khi chưa khôi phục xong.

## Kiểm thử cục bộ

Mock dựa trên tài liệu B2; không dùng key thật. Postman CLI hiện chưa đăng nhập,
nên chưa đối chiếu với mock do phía server phát hành. Các key giả `CURRENT`,
`OPTIONAL`, `FORCED`, `INVALID` chọn tình huống; `SERVER_ERROR`, `BAD_JSON`,
`BAD_BODY` kiểm tra giữ key khi phản hồi server lỗi.

Terminal 1 (Postman CLI >= 1.70):

```powershell
postman simulation run postman/simulations/license-version.sim.yaml --port 4917 --output ndjson --no-history
```

Terminal 2:

```powershell
$env:LICENSE_TEST_API_URL = 'http://127.0.0.1:4917/license/api/validate-key'
python -m pytest tests -q
```

Không có Postman CLI thì chạy thẳng mock bằng Node (`node postman/mocks/license-version/default.js`)
và đặt `LICENSE_TEST_API_URL = 'http://127.0.0.1:4917/api/validate-key'`.

Không đặt biến trên thì các ca HTTP được bỏ qua; các ca logic giao diện vẫn chạy.
Các test đổi nơi lưu key sang thư mục tạm, không chạm `license.json` thật.
Log Postman phải có sự kiện `request` tại `/api/validate-key`.

Mock còn phục vụ `/releases/source.tar.gz`, `/releases/unknown-size`,
`/releases/fail`, `/releases/corrupt`. `tests/test_updater.py` tải gói qua HTTP,
cài vào thư mục tạm và khởi động một tiến trình Python thật để kiểm tra handshake;
không thay mã nguồn hay dữ liệu đang dùng. Có các ca giữ dữ liệu người dùng,
rollback khi file bị khóa/khởi động sai phiên bản hoặc sai bản (Pro/thường) và chặn archive traversal.
Link tải mang tên bản khác (vd bản thường nhận `RenderVideoReupPro_…`) bị từ chối trước khi tải.
