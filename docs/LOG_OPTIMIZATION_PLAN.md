# Kế Hoạch Xử Lý Triệt Để Lỗi X11 BadLength (Tối Ưu Log UI & Phân Loại Storage)

Ứng dụng bị crash trên Ubuntu với lỗi `X Error of failed request: BadLength (RenderAddGlyphs)` do ô hiển thị Log trên giao diện (Widget `CTkTextbox`) tích tụ quá nhiều dòng văn bản (đặc biệt là các dòng thông báo thử lại / retry lặp đi lặp lại), vượt quá dung lượng gói tin request bộ đệm của X11 Server.

Tài liệu này lưu trữ quy tắc quản lý Log chi tiết được thống nhất để triển khai cho hệ thống:

---

## Các Quy Tắc Quản Lý Log Cốt Lõi

1. **Giữ nguyên Log Lỗi (`LOG_ERROR`) trên UI:**
   - Toàn bộ các dòng log báo lỗi sẽ **KHÔNG bị xóa khỏi UI**, giúp kiểm tra toàn bộ lịch sử lỗi trực tiếp trên giao diện bất cứ lúc nào.

2. **Cập nhật Retry In-Place (1/10 ➔ 2/10):**
   - Khi thử lại (Retry) các tác vụ (như Tải video, gọi API AI...), thay vì chèn dòng log mới, ứng dụng sẽ **sửa trực tiếp số lần thử từ 1/10 lên 2/10 ngay trên cùng 1 dòng log**.

3. **Giới hạn Log thông thường khoảng 1,000 dòng (`MAX_UI_LOGS = 1000`):**
   - Các dòng log tiến trình thông thường (Tải video, Tạo voice, Xử lý, Upload...) sẽ được giới hạn tối đa 1,000 dòng gần nhất.
   - Khi vượt quá 1,000 dòng, các log thông thường cũ nhất sẽ được tự động tỉa bớt để giải phóng bộ đệm X11.

4. **Sao lưu vĩnh viễn ra File Đĩa Cứng:**
   - 100% log lỗi và vết crash vẫn được tự động ghi append vào file `app_error.log` / `crash.log` trên đĩa cứng.

---

## Chi Tiết Các Thay Đổi Code Cần Thực Hiện

### 1. `main.py`
- **Cơ chế Cập nhật Log Retry In-Place (`_log_update_last` / `_log_add`)**:
  - Nhận diện các dòng thông báo thử lại (dạng `Tải lần X/Y` hoặc `thử lại X/Y`).
  - Nếu dòng log mới là thông báo retry cho tác vụ hiện tại, cập nhật trực tiếp nội dung dòng cuối cùng từ `1/10` thành `2/10` thay vì tạo dòng mới.
- **Logic Tỉa Log Phân Loại (`MAX_UI_LOGS = 1000`)**:
  - Phân tách danh sách `self._log_entries`:
    - Giữ lại **100% dòng thuộc `LOG_ERROR`**.
    - Giới hạn các dòng log khác (không phải lỗi) ở mức **tối đa 1,000 dòng mới nhất**.
  - Cập nhật hiển thị lên widget `self._log` (`CTkTextbox`) một cách tối ưu.
- **Ghi Log Lỗi ra File `app_error.log`**:
  - Tự động ghi tất cả log lỗi ra file UTF-8 trên đĩa cứng.

### 2. `core.py`
- **Chuẩn hóa thông báo Retry trong `download_video`**:
  - Định dạng chuỗi thông báo retry dạng `! Tải lần {attempt}/{retries}` để `main.py` nhận diện và cập nhật in-place mượt mà.

---

## Phương Án Kiểm Thứ & Xác Nhận

### Kiểm thử tự động (Automated Tests)
- Script test giả lập:
  - 50 dòng log lỗi `LOG_ERROR`.
  - 1,500 dòng log tiến trình thông thường.
  - 10 lần retry liên tiếp.
- Kết quả kỳ vọng:
  - Dòng retry chỉ hiển thị **1 dòng duy nhất** với chỉ số nhảy từ `1/10` ➔ `10/10`.
  - UI giữ nguyên **toàn bộ 50 dòng log lỗi**.
  - UI chỉ giữ lại **1,000 dòng log thường mới nhất** (tỉa 500 dòng log thường cũ).
  - Tệp `app_error.log` chứa đầy đủ thông tin log lỗi.

### Kiểm thử thực tế (Manual Verification)
- Chạy ứng dụng trên Ubuntu, xử lý hàng loạt video để xác minh UI mượt mà, không bị tràn bộ nhớ và không còn crash X11 `BadLength`.
