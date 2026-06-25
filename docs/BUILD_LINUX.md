# Build AppImage trên Ubuntu & phân phối sang máy Ubuntu khác

Đóng gói Video Reup Tool thành **1 file `.AppImage` duy nhất** chạy độc lập
(không cần cài Python), mang sang máy Ubuntu khác là chạy.

> PyInstaller **không cross-compile**: muốn có bản Linux phải build **trên
> Linux**. Build trên Windows chỉ ra file `.exe`.

---

## A. TRÊN MÁY UBUNTU DÙNG ĐỂ BUILD

### 1. Cài công cụ (chỉ 1 lần)
```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-full python3-tk curl
```
> `python3-venv` + `python3-full` là bắt buộc — thiếu nó sẽ gặp lỗi
> `externally-managed-environment` (PEP 668) trên Ubuntu 23.04+/24.04.

### 2. Chuẩn bị mã nguồn
Copy/clone project sang máy build. `client_secret.json` bị `.gitignore` loại —
nếu clone bằng git phải **chép tay** file này vào thư mục gốc (cùng cấp
`main.py`), nếu không script dừng ngay với lỗi `Không tìm thấy client_secret.json`.

> Nếu bạn copy nguyên thư mục từ Windows sang và có sẵn `.venv` (của Windows),
> không sao — `build_linux.sh` tự phát hiện venv hỏng/khác OS và tạo lại.

### 3. Build
```bash
cd tool_reup_video_local
chmod +x build_linux.sh
./build_linux.sh
```
Script tự: tạo `.venv` (chắc chắn có pip), cài thư viện, tải `ffmpeg`/`ffprobe`
Linux, chạy PyInstaller, dựng AppDir, tải `appimagetool` và đóng gói.

### 4. Kết quả
```
dist/VideoReupTool.AppImage   ← file duy nhất để gửi đi
```
Đã nhúng **runtime FUSE-less** → người dùng không cần cài `libfuse2`.

Chạy thử trên máy build:
```bash
./dist/VideoReupTool.AppImage
```

---

## B. GỬI CHO MÁY KHÁC

Gửi **đúng 1 file** `VideoReupTool.AppImage`.

> Để người dùng **double-click là mở**, file phải còn **quyền thực thi (+x)**.
> Đây là cơ chế bảo mật của Linux — file thiếu +x sẽ không chạy khi double-click.
>
> - **Cách giữ +x (khuyến nghị):** gửi qua `scp`/`rsync`, USB định dạng ext4,
>   hoặc nén `.tar.gz` (giữ nguyên +x). Người dùng nhận về double-click chạy luôn.
> - Tải qua **trình duyệt / Google Drive / Zalo** sẽ **mất +x** → người dùng phải
>   bật lại 1 lần (xem mục C).

---

## C. TRÊN MÁY UBUNTU NGƯỜI DÙNG (mục tiêu: chỉ double-click)

Không cần cài Python/thư viện/libfuse2 gì cả. Chỉ cần Ubuntu **có giao diện đồ
họa** (desktop).

**Trường hợp file còn +x** (gửi bằng tar/scp/USB ext4): chỉ cần **double-click**
vào `VideoReupTool.AppImage` → app mở.

**Trường hợp file mất +x** (tải qua trình duyệt/Drive/Zalo): bật quyền chạy 1 lần:
1. Chuột phải `VideoReupTool.AppImage` → **Properties**
2. Tab **Permissions** → tick **"Allow executing file as program"**
3. Đóng lại → **double-click** → app mở (các lần sau khỏi làm lại).

> Tương đương dòng lệnh: `chmod +x VideoReupTool.AppImage` rồi double-click.

### File dữ liệu (token.json / license.json)
Khi chạy dạng AppImage, app ghi `token.json` và `license.json` **cạnh file
`.AppImage`** (nếu thư mục đó ghi được), hoặc trong `~/.config/VideoReupTool/`.
→ Nên đặt file `.AppImage` ở thư mục ghi được (Desktop, Downloads, Home), đừng
để trong `/opt` hay `/usr` (chỉ-đọc).

---

## D. VÌ SAO "MÁY BUILD CHẠY ĐƯỢC, MÁY KHÁC KHÔNG"

### 1. GLIBC — build trên bản Ubuntu CŨ NHẤT cần hỗ trợ
AppImage/PyInstaller **không** đóng gói `libc`. Build trên Ubuntu mới (vd 24.04)
rồi chạy máy cũ (vd 20.04) → lỗi `version 'GLIBC_2.XX' not found`.
→ Build trên **bản cũ nhất** trong nhóm người dùng. Bản cũ chạy được trên bản
mới, KHÔNG ngược lại.

**Cách chắc ăn nhất — build trong Docker bằng `build_appimage_docker.sh`:**
```bash
bash build_appimage_docker.sh                  # mặc định ubuntu:22.04 (glibc 2.35), Python hệ thống 3.10
BASE_IMAGE=ubuntu:20.04 PYVER=3.11 bash build_appimage_docker.sh   # đỡ máy cũ hơn (glibc 2.31)
```
Script tự dựng Ubuntu trong Docker, cài Python + Tkinter, chạy `build_linux.sh`,
rồi xuất `dist/VideoReupTool.AppImage`. Container chạy glibc riêng của image nên
build trên máy 24.04 vẫn ra binary cho máy 22.04. Binary build trên glibc thấp
chạy được trên cả máy mới lẫn cũ.

> Cần Docker: `sudo apt install -y docker.io` rồi `sudo usermod -aG docker $USER`
> (đăng xuất/đăng nhập lại). Không cần FUSE — appimagetool chạy ở chế độ
> extract-and-run trong container.
>
> Chọn `BASE_IMAGE` theo bản Ubuntu **thấp nhất** mà người dùng của bạn đang
> chạy. Không chắc thì để mặc định `ubuntu:20.04` (phủ hầu hết máy từ 2020).

### 2. Máy đích phải có desktop
App là GUI (Tkinter) → cần X11/Wayland. Server headless hoặc SSH không
X-forwarding sẽ báo `no display name and no $DISPLAY`.

### 3. Khi không mở được — chẩn đoán bằng terminal
Double-click không hiện gì thì chạy từ terminal để thấy lỗi:
```bash
./VideoReupTool.AppImage
```
| Lỗi / hiện tượng | Khắc phục |
|---|---|
| Double-click không có gì xảy ra | File mất +x → bật "Allow executing file as program" (mục C) |
| `GLIBC_2.XX not found` | Build lại trên Ubuntu cũ hơn (mục D.1) |
| `error while loading shared libraries: libXXX` | `sudo apt install` lib đó trên máy đích |
| `no display name and no $DISPLAY` | Phải chạy trên máy có desktop |
| Vẫn nghi do FUSE | Chạy `./VideoReupTool.AppImage --appimage-extract-and-run` |

App cũng tự ghi `crash.log` (cạnh file .AppImage hoặc trong `~/.config/VideoReupTool/`)
mỗi khi crash khởi động — gửi file đó để chẩn đoán.
