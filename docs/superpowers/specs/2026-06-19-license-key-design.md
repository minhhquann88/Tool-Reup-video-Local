# Khóa bản quyền (license key) cho tool — Thiết kế

Ngày: 2026-06-19
Nguồn: `HUONG_DAN_TICH_HOP_KEY_EXTENSION.md` (đặc tả API), điều chỉnh cho app Python desktop.

## Quyết định đã chốt
- Chặn **ngay khi mở app** (chưa kích hoạt → không vào được giao diện chính).
- `deviceId` theo **MachineGuid (phần cứng Windows)** — ổn định, không đổi khi xoá file/cài lại.
- Mất mạng / server không gọi được = **khoá (chặt)**, giống tài liệu.
- Kiểm tra key **chỉ lúc mở app** (không timer định kỳ, không check trước mỗi batch).
- `app_id = "tool_reup_video"` (đã đăng ký trong bảng apps — giữ đúng chữ thường).

## 1. Module `license.py` (logic thuần + gọi API, không GUI → test được)
Dùng `requests` (đã là dependency của core.py).

- Hằng:
  - `API_URL = "https://key.byscom.vn/api/validate-key"`
  - `APP_ID  = "tool_reup_video"`
- `_exe_dir()` (sao theo drive.py) → `get_license_path()` = `license.json` cạnh exe
  (cùng chỗ token.json). Nội dung: `{"key": "...", "expire_date": "..."}`.
  deviceId KHÔNG lưu (suy từ phần cứng mỗi lần).
- `get_device_id()`: đọc registry `HKLM\SOFTWARE\Microsoft\Cryptography` value
  `MachineGuid` (ép `KEY_WOW64_64KEY`) → `"win_" + guid.lower()`. Lỗi → fallback
  `"mac_" + hex(uuid.getnode())`.
- `load_saved_key()` / `save_key(key, expire_date)` / `clear_key()`.
- `validate_key(key)`: POST JSON `{key, app_id, deviceId}` (deviceId **camelCase**),
  timeout 15s. Lỗi mạng/timeout → `{"status":0,"message":"Không thể kết nối máy chủ bản quyền."}`.
  Trả `res.json()` (đọc `status` trong body, KHÔNG dựa HTTP code).
- `check_license()` → `{"is_valid": bool, "data": dict|None, "message": str}`:
  - Không có key đã lưu → invalid ("Chưa kích hoạt bản quyền.").
  - Có → `validate_key`; `status==1 & data.key` → valid, lưu lại key+expire; ngược lại
    xoá key, invalid (kèm message từ server).
- `activate(key)`: `validate_key`; hợp lệ thì `save_key` + trả `{is_valid, data, message}`.

## 2. Cổng chặn ở `main()` (main.py)
```
def main():
    st = license.check_license()                 # check mạng lúc mở (chặt)
    if st["is_valid"]:
        data = st["data"]
    else:
        dlg = LicenseDialog()                    # cửa sổ nhập key
        dlg.mainloop()
        if not dlg.activated:                     # đóng mà chưa kích hoạt
            return                                # KHÔNG mở tool
        data = dlg.activated_data
    App(license_data=data).mainloop()
```
- `LicenseDialog(ctk.CTk)`: tiêu đề "🔑 Kích hoạt bản quyền", ô nhập key, nút "Kích hoạt"
  (gọi `license.activate` trong thread để không treo UI), nhãn lỗi, hiển thị `deviceId`
  (để báo admin khi đổi máy). Kích hoạt OK → `self.activated=True`, lưu `activated_data`,
  `self.destroy()`. Đóng cửa sổ khi chưa kích hoạt → `activated=False` → main() thoát.
- Có key đã lưu + online ⇒ vào thẳng. Mất mạng ⇒ check fail ⇒ phải nhập key nhưng
  validate cũng fail ⇒ không vào được (đúng "chặt").

## 3. Trong App (nhẹ)
`App.__init__(self, license_data=None)` lưu `self._license_data`. Cuối sidebar thêm:
- Nhãn "🔑 Bản quyền: ••••<4 ký tự cuối> · HH: <expire_date>".
- Nút **"Hủy kích hoạt"** → `license.clear_key()` rồi `self.destroy()` (mở lại app sẽ
  phải nhập key mới). Đây là đường để đổi key.

## 4. Kiểm thử
- `get_device_id()` trả chuỗi khác rỗng, ổn định qua 2 lần gọi.
- Round-trip `license.json`: save → load → clear.
- `check_license` với `validate_key` được monkeypatch (valid / invalid / lỗi mạng).
- **Gọi API thật với key GIẢ** (vd "FAKE-TEST-0000") → kỳ vọng `status 0`
  ("Key không tồn tại"): xác minh kết nối + đúng request/response. Key giả KHÔNG bind gì → an toàn.
- `python -m py_compile main.py license.py`; import main.py không lỗi.

## Ngoài phạm vi (YAGNI)
Không obfuscate mã, không timer kiểm tra định kỳ, không grace offline, không notification,
không sửa logic Drive/Local hiện có. Không tạo giá trị key thật (việc của Dashboard byscom.vn).

## Bảo mật (ghi nhận)
License phía client có thể bị đọc/bypass (mục 12 tài liệu). Đây là rào cơ bản; muốn chặt
hơn phải đặt logic "đắt giá" sau server tự gọi validate-key.
