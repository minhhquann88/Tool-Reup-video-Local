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
2. Chọn **Get started**
3. Chọn **External** → **Create**
4. Điền thông tin bắt buộc:
   - **App name**: `VideoReupTool` (tuỳ ý)
   - **User support email**: email của bạn
   - Chọn **External**
   - **Developer contact email**: email của bạn
5. Bấm **Create** qua các bước tiếp theo
6. Quay về trang **OAuth consent screen** chọn **Create OAuth client**
7. Chọn **Application type** (Desktop App), nhập tên App, chọn **Create**
8. Popup hiện ra → bấm **Download JSON**
9. Đổi tên file vừa tải thành `client_secret.json`

### Publish lên Production

Mặc định app ở chế độ **Testing**. Để người dùng đăng nhập mà không cần được thêm thủ công, cần publish:

1. vào Tab **OAuth consent screen** chọn Tab **Audience** → mục **Publishing status**
2. Bấm **Publish App** → xác nhận

> App chuyển sang **In production** — bất kỳ tài khoản Google nào cũng có thể đăng nhập.

### OAuth user cap — Giới hạn 100 người dùng

Sau khi publish, mục **OAuth user cap** hiển thị giới hạn **100 users** (ví dụ: `3 users / 100 user cap`).

- Đây là giới hạn **trọn đời của project**, không thể reset hay tăng lên
- Áp dụng cho các scope **nhạy cảm** (Drive API thuộc loại này)
- Khi đạt 100 users, tài khoản mới sẽ không thể cấp quyền cho app

**Nếu cần hơn 100 users:** tạo project Google Cloud mới → lặp lại từ bước 1.

---

## Đặt file vào đúng vị trí

| Môi trường                 | Vị trí đặt `client_secret.json`      |
| -------------------------- | ------------------------------------ |
| Chạy từ source (`run.bat`) | Cùng thư mục với `main.py`           |
| Chạy từ file `.exe`        | Cùng thư mục với `VideoReupTool.exe` |

> `token.json` sẽ tự tạo ra sau lần đăng nhập đầu tiên — không cần tạo thủ công.

---

## Lưu ý khi Clone Project (Git LFS)

Project này sử dụng **Git LFS** để lưu trữ các file thực thi nặng trong thư mục `bin/` (như `ffmpeg.exe`, `ffprobe.exe`). Khi clone về máy mới, bạn cần làm theo các bước sau:

1. **Cài đặt Git LFS trước**: Truy cập [git-lfs.com](https://git-lfs.com/) để tải và cài đặt Git LFS cho hệ điều hành của bạn.
2. Mở Terminal/CMD và chạy lệnh: `git lfs install` (chỉ cần chạy 1 lần trên máy).
3. Sau đó clone project như bình thường: `git clone <url_repo>`. Git sẽ tự động kéo các file binary thực tế về thư mục `bin/`.

> **Lưu ý**: Nếu bạn đã lỡ clone trước khi cài Git LFS, thư mục `bin/` sẽ chỉ chứa các file text con trỏ (dung lượng rất nhỏ) và tool sẽ bị lỗi. Cách khắc phục: cài Git LFS, sau đó mở terminal ở thư mục project và chạy lệnh `git lfs pull`.
