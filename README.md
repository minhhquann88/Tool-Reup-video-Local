# Video Reup Tool — Hướng dẫn cài đặt Google Cloud

## 1. Tạo Project

1. Truy cập [console.cloud.google.com](https://console.cloud.google.com)
2. Góc trên bên trái → **Select a project** → **New Project**
3. Đặt tên (ví dụ: `VideoReupTool`) → **Create**

---

## 2. Bật Google Drive API

1. Menu trái → **APIs & Services** → **Library**
2. Tìm **Google Drive API** → chọn → **Enable**

---

## 3. Cấu hình OAuth Consent Screen

1. Menu trái → **APIs & Services** → **OAuth consent screen**
2. Chọn **External** → **Create**
3. Điền thông tin bắt buộc:
   - **App name**: `VideoReupTool` (tuỳ ý)
   - **User support email**: email của bạn
   - **Developer contact email**: email của bạn
4. Bấm **Save and Continue** qua các bước tiếp theo — không cần điền thêm
5. Quay về trang **OAuth consent screen**

### Publish lên Production

Mặc định app ở chế độ **Testing**. Để người dùng đăng nhập mà không cần được thêm thủ công, cần publish:

1. Tab **Audience** (hoặc **OAuth consent screen**) → mục **Publishing status**
2. Bấm **Publish App** → xác nhận

> App chuyển sang **In production** — bất kỳ tài khoản Google nào cũng có thể đăng nhập.

### OAuth user cap — Giới hạn 100 người dùng

Sau khi publish, mục **OAuth user cap** hiển thị giới hạn **100 users** (ví dụ: `3 users / 100 user cap`).

- Đây là giới hạn **trọn đời của project**, không thể reset hay tăng lên
- Áp dụng cho các scope **nhạy cảm** (Drive API thuộc loại này)
- Khi đạt 100 users, tài khoản mới sẽ không thể cấp quyền cho app

**Nếu cần hơn 100 users:** tạo project Google Cloud mới → lặp lại từ bước 1.

---

## 4. Tạo OAuth Client ID

1. Menu trái → **APIs & Services** → **Credentials**
2. Bấm **+ Create Credentials** → **OAuth client ID**
3. **Application type**: chọn **Desktop app**
4. **Name**: tuỳ ý → **Create**
5. Popup hiện ra → bấm **Download JSON**
6. Đổi tên file vừa tải thành `client_secret.json`

---

## 5. Đặt file vào đúng vị trí

| Môi trường | Vị trí đặt `client_secret.json` |
|---|---|
| Chạy từ source (`run.bat`) | Cùng thư mục với `main.py` |
| Chạy từ file `.exe` | Cùng thư mục với `VideoReupTool.exe` |

> `token.json` sẽ tự tạo ra sau lần đăng nhập đầu tiên — không cần tạo thủ công.
