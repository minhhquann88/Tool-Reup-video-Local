# Hướng dẫn build Ubuntu và bàn giao cho agent ở project khác

Tài liệu này mô tả luồng build Linux đang có trong project **Tool-Reup-video-Local**, các thành phần cần chuyển sang project khác và những điểm agent phải đánh giá trước khi áp dụng. Nội dung được đối chiếu với mã nguồn và script hiện có; chưa chạy build hoặc kiểm thử trong phiên tạo tài liệu.

## 1. Mục tiêu và phạm vi

Đóng gói ứng dụng desktop Python dùng Tkinter/CustomTkinter, xử lý video bằng FFmpeg và có tích hợp Google Drive, thành bản chạy trên Ubuntu x86_64.

Hai định dạng phân phối chính:

| Định dạng | Lệnh chọn | Nội dung | Máy người dùng |
| --- | --- | --- | --- |
| AppImage | Mặc định | Một file `.AppImage` chứa ứng dụng, Python và các tài nguyên được bundle | Cần desktop tương thích, quyền thực thi và thư viện hệ thống còn phụ thuộc |
| Thư mục nén | `PACKAGE=tar` | Toàn bộ thư mục PyInstaller `onedir` nén thành `.tar.gz` | Giải nén toàn bộ rồi chạy binary bên trong; không cần cơ chế AppImage/FUSE |

Ưu tiên build trong **Docker với base `ubuntu:22.04`** theo mặc định hiện tại của project. Nếu muốn hỗ trợ Ubuntu cũ hơn, phải đổi môi trường build và đánh giá lại toàn bộ dependency. Build thành công chưa chứng minh ứng dụng chạy được trên mọi máy Ubuntu.

Tài liệu là hướng dẫn chuyển giao kỹ thuật, không phải yêu cầu thay đổi nghiệp vụ, license hoặc OAuth của project đích.

## 2. Các file liên quan trong project nguồn

| File/thư mục | Vai trò | Khi chuyển sang project khác |
| --- | --- | --- |
| `build_appimage_docker.sh` | Tạo container Ubuntu, cài dependency hệ thống, gọi build Linux | Mang theo và sửa tên/path/env phù hợp |
| `build_linux.sh` | Tạo venv, cài Python dependency, build libXft, chạy PyInstaller, tạo AppImage hoặc tar | Thành phần build chính cần chuyển |
| `packaging/build-libxft.sh` | Tải và build libXft, mặc định phiên bản `2.3.8` | Giữ nếu project đích cũng dùng Tk/X11 hoặc cần cùng cơ chế khắc phục font |
| `packaging/linux-fontconfig-bundled.conf` | Chính sách font cho bản đóng gói, ưu tiên DejaVu và loại font màu | Chuyển cùng font bundle nếu tiếp tục dùng cơ chế này |
| `linux-fontconfig.conf` | Chính sách font khi chạy source; cũng được đưa vào bước PyInstaller | Cần có vì build script tham chiếu trực tiếp |
| `requirements.txt` | Dependency Python của ứng dụng | Dùng dependency thực tế của project đích |
| `setup_ffmpeg.py` | Chuẩn bị FFmpeg/FFprobe theo hệ điều hành | Cần nếu tiếp tục bundle FFmpeg bằng cơ chế hiện tại |
| `main.py` | Entry point, metadata ứng dụng, khởi tạo GUI và xử lý font Linux | Project đích thay bằng entry point của nó; chuyển phần hỗ trợ Linux khi cần |
| Các module ứng dụng | Hiện có `core.py`, `drive.py`, `license.py`, `tts.py`, `single_instance.py` | Mang đủ module mà entry point import, không chỉ một danh sách cố định |
| `client_secret.json` | Cấu hình OAuth desktop được bundle | Cung cấp cấu hình của project đích nếu còn dùng Google Drive |
| `video.png` | Icon dùng để dựng AppImage | Bắt buộc cho nhánh AppImage hiện tại; thay bằng icon PNG của project đích |
| `video.ico` | Icon Windows | Không dùng thay PNG trong AppImage; chỉ chuyển nếu code/source package còn cần |
| `bin/ffmpeg`, `bin/ffprobe` | Binary Linux được bundle | Phải đúng Linux x86_64, có quyền thực thi và là binary thực |
| `run_linux.sh` | Cài dependency và chạy từ source | Chỉ cần khi muốn cung cấp thêm chế độ chạy source |
| `docs/BUILD_LINUX.md` | Hướng dẫn Linux cũ | Tham khảo; có thông tin tên output và cấu hình đã lệch script |
| `tests/` liên quan Linux | Công cụ đánh giá X11 và thư viện bundle | Chỉ chuyển/chạy khi có yêu cầu kiểm thử tương ứng |

Không chuyển `.venv` của Windows, cache build, output cũ, token đăng nhập, license người dùng hoặc log cá nhân vào gói phát hành.

## 3. Kiến trúc build hiện tại

```text
Mã nguồn project + cấu hình OAuth + icon + dependency
    |
    v
build_appimage_docker.sh
    |-- Ubuntu container, mặc định 22.04
    |-- Python/Tkinter và thư viện phát triển font/X11
    v
build_linux.sh
    |-- Tạo venv và cài requirements + PyInstaller
    |-- Build libXft 2.3.8 từ source
    |-- Chuẩn bị FFmpeg và FFprobe Linux
    |-- PyInstaller --onedir
    |-- Thay libXft được thu thập bằng bản tự build
    |-- Bundle font DejaVu và fontconfig
    |
    +--> PACKAGE=tar: dist/<APP>-linux.tar.gz
    |
    +--> Mặc định: AppDir + AppRun + desktop entry + icon
                  -> appimagetool + type2-runtime
                  -> dist/<APP>.AppImage
```

PyInstaller chạy **bên trong Linux**. Không chạy PyInstaller trên Windows rồi đổi đuôi `.exe` thành AppImage. Có thể dùng máy Windows làm host, nhưng môi trường thực hiện build vẫn phải là Linux, ví dụ WSL2 với Docker hoặc một máy Ubuntu.

## 4. Môi trường cần chuẩn bị

### 4.1. Máy build

- Kiến trúc x86_64/amd64 là mục tiêu hiện tại: script hardcode `ARCH=x86_64`, công cụ AppImage x86_64 và FFmpeg Linux amd64.
- Có Bash và Docker nếu dùng luồng container.
- Có mạng để tải image Ubuntu, gói apt, dependency pip, source libXft, FFmpeg và công cụ AppImage.
- Source và thư mục output phải ghi được.
- Python của ứng dụng nên theo mức **tối thiểu 3.10** mà `run_linux.sh` đang kiểm tra. Không suy ra hỗ trợ Python 3.8 từ comment trong các script cũ.

### 4.2. Máy Ubuntu chạy ứng dụng

- Có phiên desktop và X11 hoặc XWayland hoạt động; GUI hiện tại phụ thuộc Tk/X11.
- Không dùng một phiên SSH/server headless thông thường để kết luận GUI bị lỗi đóng gói.
- Có quyền chạy file và ghi dữ liệu người dùng.
- Cần mạng cho những tính năng vốn gọi dịch vụ ngoài như license, OAuth, Drive và dịch vụ tạo giọng nói; đóng gói không biến các tính năng này thành offline.

### 4.3. Tương thích Ubuntu/glibc

Theo cấu hình và chú thích của script nguồn:

| Môi trường build | Baseline glibc được script mô tả | Python |
| --- | --- | --- |
| `ubuntu:22.04` mặc định | `2.35` | Python hệ thống, dự kiến `3.10` |
| `ubuntu:20.04` tùy chọn | `2.31` | Cần chọn Python phù hợp, ví dụ `PYVER=3.11` theo ví dụ trong script |

Chọn bản Ubuntu build theo **bản cũ nhất thực sự cần hỗ trợ**. Glibc thấp hơn là một điều kiện giúp tương thích; vẫn phải xét kiến trúc CPU, shared library, wheel Python, FFmpeg và desktop của máy đích.

Lệnh dùng Ubuntu 20.04/PPA là tùy chọn có trong mã nguồn, chưa được xác nhận hoạt động ở thời điểm dùng tài liệu. Nếu cần hướng này, agent phải xác minh nguồn Python còn cung cấp gói tương ứng trước khi triển khai.

## 5. Chuẩn bị source trước khi build

1. Đặt project trong một thư mục làm việc riêng cho build, có đầy đủ module và tài nguyên thực tế.
2. Đặt `client_secret.json` cùng cấp với `main.py` nếu dùng build script nguyên bản. Cả wrapper Docker và script Linux đều dừng nếu thiếu file này.
3. Đảm bảo `video.png`, hai file fontconfig và helper `packaging/build-libxft.sh` tồn tại.
4. Dùng shell script lưu **UTF-8, LF**, tránh CRLF từ Windows gây lỗi `bash\r`, `$'\r': command not found` hoặc option chứa ký tự `\r`.
5. Không mang venv Windows sang môi trường Linux để sử dụng lại.
6. Nếu repo dùng Git LFS cho binary, đảm bảo file nhận được là binary thật, không phải file text con trỏ LFS. Không cần kéo binary Windows chỉ để build Linux.
7. Đảm bảo có cả `bin/ffmpeg` và `bin/ffprobe` đúng Linux, hoặc để cơ chế tải chuẩn bị chúng.

**Lưu ý tác động của wrapper hiện tại:** trước khi build, `build_appimage_docker.sh` gọi `rm -rf .venv build` trên thư mục `/app` đang mount từ host. Hai thư mục này có thể bị xóa ngay trong project host. Khi chuyển script, nên đổi sang cache/container path riêng và chỉ dọn thư mục build do chính script quản lý. Nếu giữ nguyên, thực hiện trong bản sao source dùng riêng cho build.

## 6. Build bằng Docker trên Ubuntu hoặc WSL2

Các lệnh dưới đây là hướng dẫn cho người thực hiện build; không có lệnh nào đã được chạy để kiểm thử trong phiên viết tài liệu này.

### 6.1. Cài Docker trên máy Ubuntu nếu chưa có

```bash
sudo apt update
sudo apt install -y docker.io
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

Đăng xuất/đăng nhập lại để áp dụng nhóm `docker`. Với WSL2/Docker Desktop, dùng Docker engine đã cấu hình tích hợp vào distro WSL; không mặc định chạy thêm engine thứ hai.

### 6.2. Vào thư mục project

```bash
cd /duong-dan/toi/project
```

Nếu source xuất phát từ Windows, chuẩn hóa LF trước khi chạy. Ví dụ khi đã có `dos2unix`:

```bash
dos2unix build_appimage_docker.sh build_linux.sh packaging/build-libxft.sh
```

### 6.3. Tạo AppImage

```bash
bash build_appimage_docker.sh
```

Wrapper tự cài trong container:

```text
ca-certificates curl file binutils build-essential pkg-config
libfontconfig1-dev libfreetype6-dev libxrender-dev fonts-dejavu-core
python3 python3-venv python3-tk python3-dev
```

Nếu có `PYVER`, wrapper dùng PPA `deadsnakes` và cài các gói Python tương ứng thay nhánh Python hệ thống.

### 6.4. Tạo gói thư mục `.tar.gz`

```bash
PACKAGE=tar bash build_appimage_docker.sh
```

`PACKAGE=dir` hiện cũng đi vào cùng nhánh tạo `.tar.gz`; nó không phải chế độ chỉ xuất thư mục riêng.

### 6.5. Chọn baseline khác khi có yêu cầu

```bash
BASE_IMAGE=ubuntu:20.04 PYVER=3.11 bash build_appimage_docker.sh
```

Không chọn baseline cũ chỉ vì một comment khuyên dùng. Chốt theo máy đích, dependency và khả năng cung cấp Python thực tế.

### 6.6. Build từ máy Windows

Dùng terminal Bash trong WSL2 và Docker Linux containers. Các lệnh `PACKAGE=tar ...`, `BASE_IMAGE=...` là cú pháp Bash, không phải PowerShell. Có thể truy cập source qua `/mnt/c/...`, nhưng build trên filesystem Linux của WSL thường giảm vấn đề quyền/file lock; chép artifact cuối về thư mục project cần bàn giao.

Nếu chạy wrapper từ Git Bash, cần xử lý riêng đường dẫn mount Docker và UID/GID. Không coi đây là cách đã được project xác nhận.

## 7. Build trực tiếp trên Ubuntu

Đây là phương án thay thế. Nếu cần baseline ổn định, ưu tiên container đã chọn ở trên.

Các gói nền tảng phù hợp với luồng Ubuntu 22.04:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-tk python3-dev \
  ca-certificates curl file binutils build-essential pkg-config \
  libfontconfig1-dev libfreetype6-dev libxrender-dev fonts-dejavu-core \
  xz-utils
```

Có thể cần thêm gói venv tương ứng nếu dùng Python khác. Pip phải chạy qua interpreter trong venv; không dùng `sudo pip` hoặc `--break-system-packages` để chữa lỗi PEP 668.

**Điểm cần sửa trước khi dùng build native:** `build_linux.sh` đặt `BUILD_DIR="build"`, rồi truyền prefix tương đối như `build/libxft-2.3.8-install` vào helper `configure --prefix=...`. Helper còn đổi thư mục sang source libXft trước khi configure. Agent cần chuẩn hóa `PREFIX` và `WORK_DIR` thành đường dẫn tuyệt đối trước mọi thao tác/đổi thư mục. Ví dụ về ý tưởng sửa trong helper:

```bash
PREFIX="$(realpath -m -- "$PREFIX")"
WORK_DIR="$(realpath -m -- "$WORK_DIR")"
# Tính ARCHIVE, SOURCE_DIR và OUTPUT sau khi chuẩn hóa hai biến trên.
```

Trong Docker, `BUILD_DIR=/tmp/build` đã là đường dẫn tuyệt đối nên không đi qua cùng vấn đề prefix này. Tài liệu này chỉ ghi nhận điểm cần sửa, chưa thay đổi script nguồn.

Sau khi xử lý prefix và các điều kiện đầu vào:

```bash
bash build_linux.sh
```

Hoặc:

```bash
PACKAGE=tar bash build_linux.sh
```

Khi dùng script Linux trực tiếp, có thể chọn interpreter bằng `PYTHON`, ví dụ `PYTHON=python3.11`, nhưng phải cài Tkinter và venv đúng interpreter đó.

## 8. Metadata, biến môi trường và tên output

Metadata hiện tại trong mã nguồn:

```text
main.APP_VERSION = "v1.0.1"
license.APP_ID = "tool_reup_video_pro"
```

Khi import metadata thành công, tên mặc định dự kiến là:

```text
RenderVideoReupPro_v1.0.1
```

Tuy nhiên `build_linux.sh` đọc metadata bằng **`python3` trước bước cài dependency vào venv**. Import `main` có thể thất bại vì thiếu CustomTkinter hoặc các dependency khác. Khi đó script dùng fallback:

```text
RenderVideoReup_v1.0.1
```

Vì vậy không mặc định hứa artifact luôn có chữ `Pro`. Tên `VideoReupTool.AppImage` trong hướng dẫn cũ và dòng thông báo cuối wrapper là tên cũ, không phải tên được cố định trong bước tạo artifact hiện tại.

| Biến | Ý nghĩa | Giới hạn hiện tại |
| --- | --- | --- |
| `BASE_IMAGE` | Image Docker dùng build | Wrapper mặc định `ubuntu:22.04` |
| `PYVER` | Chọn Python trong Docker | Wrapper cài từ PPA khi biến khác rỗng |
| `PYTHON` | Interpreter tạo venv khi gọi script Linux | Được wrapper thiết lập bên trong container |
| `PACKAGE` | Chọn `appimage`, `tar` hoặc `dir` | Wrapper có truyền vào container |
| `APP` | Tên ứng dụng/output khi gọi script Linux | Wrapper chưa truyền biến này vào container |
| `XFT_VERSION` | Phiên bản libXft, mặc định `2.3.8` | Wrapper chưa truyền biến này vào container |

Để chuyển sang project khác, nên định nghĩa tên/version một lần trong metadata không import GUI, rồi dùng cùng metadata cho binary, `.desktop`, icon và tên artifact. Nếu tiếp tục dùng env `APP`/`XFT_VERSION`, phải thêm `-e` tương ứng vào wrapper Docker. Đặt env ở host mà không truyền qua `docker run` sẽ không cấu hình được container như mong muốn.

Output cần tìm theo tên thực tế:

```text
dist/<APP>.AppImage
dist/<APP>-linux.tar.gz
```

Trong container, venv và output PyInstaller tạm nằm ở `/tmp/venv`, `/tmp/build`, `/tmp/dist`; artifact phát hành được ghi vào `/app/dist`, tức `dist/` của project host.

## 9. PyInstaller và tài nguyên bắt buộc

Luồng hiện tại dùng `--onedir`, không dùng `--onefile` cho binary nội bộ. Các cấu hình quan trọng:

```text
Entry point: main.py
--add-data "client_secret.json:."
--add-data "bin:bin"
--add-data "linux-fontconfig.conf:."
--collect-all customtkinter
--hidden-import PIL._tkinter_finder
--hidden-import google.auth.transport.requests
--hidden-import google.oauth2.credentials
--hidden-import google_auth_oauthlib.flow
--hidden-import googleapiclient.discovery
--hidden-import googleapiclient.http
--hidden-import googleapiclient._helpers
--hidden-import openpyxl
```

Khi đổi project, cập nhật entry point, tài nguyên và hidden import theo ứng dụng thực tế. Không giữ `client_secret.json`, module Google hoặc `openpyxl` chỉ vì project nguồn có chúng nếu project đích không dùng.

Script chính gọi PyInstaller bằng CLI và tạo spec theo tên `APP`. File `RenderVideoReupPro.spec` trong repo không phải cấu hình duy nhất của luồng này. Chạy spec trực tiếp sẽ không tự thực hiện các bước sau PyInstaller như thay libXft, bundle font và dựng AppImage.

Sau PyInstaller, script chọn thư mục thư viện:

```text
dist/<APP>/_internal/    nếu cấu trúc PyInstaller có _internal
dist/<APP>/              nếu không có
```

Trong container, hai đường dẫn trên tương ứng nằm dưới `/tmp/dist`. Agent cần hỗ trợ cấu trúc thực tế của phiên bản PyInstaller đang dùng.

## 10. FFmpeg và FFprobe

Linux dùng binary không có đuôi `.exe`. `setup_ffmpeg.py` hiện tải bản Linux amd64 static từ URL được định nghĩa trong file; mặc dù docstring/log còn nhắc BtbN/GitHub, nhánh Linux thực tế dùng John Van Sickle.

Những điểm cần đánh giá khi chuyển:

- `build_linux.sh` chỉ quyết định tải dựa trên `-x bin/ffmpeg`, chưa kiểm tra đồng thời `ffprobe`.
- `setup_ffmpeg.py` bỏ qua tải khi cả hai đường dẫn đã tồn tại, chưa xác minh định dạng, kiến trúc hoặc quyền thực thi của file có sẵn.
- File LFS pointer, file sai OS hoặc binary hỏng vẫn có thể vượt qua kiểm tra tồn tại.
- `--add-data "bin:bin"` gom toàn bộ `bin/`, có thể mang cả binary Windows nếu thư mục có chúng.

Trong project đích, nên stage riêng hai binary Linux, kiểm tra đúng định dạng/kiến trúc và đặt `chmod +x` trước đóng gói. Nếu chọn bản FFmpeg khác, đánh giá các codec mà nghiệp vụ render đang dùng và điều kiện phân phối của bản đó.

## 11. Font, libXft và lỗi X11

Project nguồn có cơ chế giảm lỗi `X Error ... BadLength ... RenderAddGlyphs` theo nhiều lớp. Khi chuyển, giữ các lớp liên quan cùng nhau thay vì chỉ copy một vài biến môi trường.

### 11.1. Build và bundle libXft

Helper tải source libXft `2.3.8`, build trên cùng baseline Ubuntu rồi tạo shared library. `build_linux.sh` thay `libXft.so.2` mà PyInstaller thu thập bằng bản vừa build, và dừng nếu không tìm thấy chuỗi version mong đợi trong binary.

Kiểm tra bằng `strings` là chốt kiểm tra version của script, không chứng minh loader lúc chạy đã nạp đúng thư viện. Nếu được yêu cầu kiểm thử, cần đánh giá cả đường dẫn thư viện thực tế được nạp.

Không chữa lỗi glibc bằng cách chép tùy ý `libc.so.6` từ máy khác vào bundle.

### 11.2. Font đi kèm

Script copy bốn font từ `fonts-dejavu-core`:

```text
DejaVuSans.ttf
DejaVuSans-Bold.ttf
DejaVuSansMono.ttf
DejaVuSansMono-Bold.ttf
```

Fontconfig bundle ưu tiên DejaVu, ánh xạ `Consolas` sang DejaVu Sans Mono và loại font có thuộc tính `color=true`. File vẫn include `/etc/fonts/fonts.conf`; không mô tả đây là bundle font hoàn toàn độc lập với mọi cấu hình hệ thống.

### 11.3. Thời điểm áp dụng

`AppRun` đặt chính sách trước khi chạy binary. `main.py` cũng thiết lập fontconfig, biến XFT và cấu hình DPI Linux trước/khi khởi tạo GUI. Project đích cần áp dụng phần tương ứng trước khi Tk bắt đầu dùng font.

Các giá trị hiện có trong launcher AppImage:

```text
XFT_ANTIALIAS=0
XFT_HINTING=0
XFT_RGBA=none
XFT_MAX_GLYPH_MEMORY=10485760
XLIB_SKIP_ARGB_VISUALS=1
GDK_SCALE=1
GDK_DPI_SCALE=1
QT_SCALE_FACTOR=1
QT_AUTO_SCREEN_SCALE_FACTOR=0
TK_SCALING=1
WAYLAND_DISPLAY=
FONTCONFIG_FILE=<AppDir>/usr/share/render-video-fontconfig/fonts.conf
FONTCONFIG_PATH=<AppDir>/usr/share/render-video-fontconfig
```

Code GUI còn đặt scaling Tk/CustomTkinter về `1.0`, giảm font fallback không phù hợp và có xử lý một số ký tự ngoài BMP. Đây là lựa chọn của ứng dụng nguồn, có thể ảnh hưởng độ sắc nét, HiDPI và khả năng hiển thị emoji; agent cần đánh giá mức phù hợp với UI project đích.

Xóa/để rỗng `WAYLAND_DISPLAY` không tự tạo X server. Trên phiên Wayland, Tk vẫn cần đường kết nối X11/XWayland phù hợp.

## 12. AppDir, launcher và runtime AppImage

Nhánh AppImage dựng cấu trúc:

```text
<APP>.AppDir/
  AppRun
  <APP>.desktop
  <APP>.png
  usr/
    bin/
      <APP>
      _internal/ ... nếu có
    share/
      applications/<APP>.desktop
      icons/hicolor/256x256/apps/<APP>.png
      render-video-fontconfig/
        fonts.conf
        fonts/*.ttf
```

File `.desktop` dùng `Exec=<APP>`, `Icon=<APP>`, `Categories=AudioVideo;`, `Terminal=false`.

**Điểm bắt buộc sửa khi đổi tên:** `AppRun` hiện tìm binary có tên khớp `RenderVideoReup*`, sau đó mới fallback về `$APP`. Khi port, bỏ prefix nguồn và gọi binary đích bằng tên đã cấu hình, ví dụ `exec "$HERE/usr/bin/<TEN_BINARY>" "$@"` trong launcher được sinh ra.

Script tải `appimagetool-x86_64.AppImage` và `runtime-x86_64`, rồi gọi appimagetool với `--runtime-file`. `APPIMAGE_EXTRACT_AND_RUN=1` được đặt để **công cụ build** chạy trong container không cần mount FUSE.

Runtime đầu ra được script mô tả là FUSE-less/có phương án giải nén. Vì các URL tải dùng nhánh `continuous`, khi phát hành cần xác minh hành vi của runtime thực tế. Nếu cần chạy theo cơ chế giải nén:

```bash
./<APP>.AppImage --appimage-extract-and-run
```

Không khẳng định mọi máy double-click chạy ngay chỉ dựa vào việc có file AppImage. Quyền thực thi, filesystem, desktop/file manager và shared library còn ảnh hưởng.

## 13. Dữ liệu runtime và quyền ghi

Tách tài nguyên chỉ đọc với dữ liệu người dùng:

| Loại | Cách xử lý trong project nguồn |
| --- | --- |
| Resource bundle | Đọc từ `sys._MEIPASS` khi frozen, ví dụ OAuth config |
| Token/license khi chạy AppImage | Ghi cạnh file AppImage qua biến `APPIMAGE` nếu thư mục ghi được |
| Fallback AppImage | Ghi dưới `XDG_CONFIG_HOME` hoặc `~/.config`, với tên ứng dụng theo `APP_ID` |
| Token/license khi chạy onedir | Ghi cạnh executable |
| Crash log | `main.py` dùng thư mục dữ liệu do module license xác định |

Với bản Pro hiện tại, fallback tương ứng là `~/.config/RenderVideoReupPro/`, trừ khi có `XDG_CONFIG_HOME`. Tên `~/.config/VideoReupTool/` trong tài liệu/comment cũ không phản ánh nhánh code hiện tại.

Project đích nên dùng thư mục dữ liệu riêng, ghi được và ổn định. Không ghi token/license vào mount AppImage hoặc `_MEIPASS`. Nếu giữ hành vi ghi cạnh executable của bản tar, giải nén vào thư mục người dùng có quyền ghi.

Build script hiện xóa `token.json`/`license.json` ở cấp thư mục output chính; đây chưa phải kiểm tra toàn bộ artifact. Khi phát hành, bảo đảm không gom dữ liệu người dùng qua các thư mục resource khác.

## 14. Phân phối và lệnh chạy cho người nhận

### AppImage

Gửi `dist/<APP>.AppImage`, sau đó người dùng chạy:

```bash
chmod +x <APP>.AppImage
./<APP>.AppImage
```

Có thể bật quyền qua Properties → Permissions → Allow executing file as program. Double-click phụ thuộc file manager/desktop trên máy đích.

Nếu muốn giữ quyền Linux, đóng gói vào tar hoặc dùng cách truyền hỗ trợ giữ quyền. File tải qua trình duyệt/dịch vụ chia sẻ có thể cần cấp lại `+x`.

### Gói thư mục

```bash
tar -xzf <APP>-linux.tar.gz
cd <APP>
./<APP>
```

Phải giữ cả thư mục cùng `_internal`, tài nguyên và binary đi kèm. Không chỉ gửi riêng executable nằm trong thư mục PyInstaller.

## 15. Những điểm agent cần đánh giá trước khi port

Các điểm dưới đây là kết quả đọc source, chưa phải kết quả chạy ứng dụng:

1. **Metadata import quá sớm:** tên Pro/version/title có thể fallback vì `python3` chưa có dependency. Tách metadata khỏi GUI hoặc đọc sau khi môi trường đã sẵn sàng.
2. **Biến env chưa truyền vào Docker:** bổ sung `APP`/`XFT_VERSION` nếu muốn hỗ trợ override từ host; tên `PYTHON` ở host không thay thế cách wrapper dùng `PYVER`.
3. **Đường dẫn libXft native:** chuẩn hóa prefix/workdir tuyệt đối trước configure và trước tính các đường dẫn dẫn xuất.
4. **Dọn thư mục mount:** tránh xóa `.venv` và `build` đang dùng cho công việc khác trên host; giới hạn cleanup vào thư mục build sở hữu.
5. **FFmpeg/FFprobe:** kiểm tra cả hai binary, kiến trúc, quyền và file LFS pointer; tránh bundle thừa `.exe`.
6. **Launcher hardcode:** thay `RenderVideoReup*` và tên/icon/desktop entry bằng metadata project đích.
7. **Tính tái lập:** `requirements.txt` hiện dùng `>=`, PyInstaller không pin, công cụ AppImage dùng `continuous`, FFmpeg dùng URL release thay đổi theo thời gian. Nếu yêu cầu build lặp lại cùng dependency, cần pin version/nguồn và checksum phù hợp.
8. **ARM64:** luồng hiện tại chưa hỗ trợ. Không chỉ đổi `ARCH`; phải đổi FFmpeg, tool/runtime, môi trường build và dependency theo kiến trúc.
9. **Tài nguyên GUI khác:** `video.png` được copy vào AppDir, nhưng chưa được thêm riêng vào `--add-data` của PyInstaller. Nếu GUI project đích đọc icon qua resource loader, phải bundle icon đúng vị trí đó.
10. **Các gói source/installer cũ:** `package_source.sh` và `make_installer.sh` có danh sách file cố định chưa chứa `single_instance.py` và `linux-fontconfig.conf`. Không dùng chúng nguyên trạng cho source hiện tại nếu các file này được code tham chiếu.
11. **Đường dẫn installer không khớp:** `make_installer.sh` stage payload vào `RenderVideoReupPro`, nhưng header dùng `APP_DIR=.../VideoReupTool`. Cần đồng bộ trước khi dùng nhánh `.run`.
12. **Comment hỗ trợ Python cũ:** các lời mô tả Ubuntu 20.04/Python 3.8 trong installer không thay thế yêu cầu Python 3.10 mà launcher đang áp dụng.

Vì các điểm 10–12, tài liệu này ưu tiên AppImage/tar từ PyInstaller. Gói `.run` hoặc source là một nhánh riêng cần sửa trước khi lựa chọn làm deliverable.

## 16. Chẩn đoán lỗi thường gặp

| Lỗi/hiện tượng | Nguyên nhân cần xét | Hướng xử lý |
| --- | --- | --- |
| Thiếu `client_secret.json` | Script nguyên bản yêu cầu OAuth config | Cung cấp file của project đích, hoặc sửa cả wrapper và build nếu bỏ tính năng OAuth |
| `externally-managed-environment` | Pip chạy vào Python hệ thống | Dùng interpreter của venv và cài đúng gói venv |
| Không import được Tkinter | Python thiếu gói Tk tương ứng | Cài `python3-tk` hoặc `python<version>-tk` |
| `expected an absolute directory name` khi configure | Prefix libXft là đường dẫn tương đối | Chuẩn hóa prefix/workdir như mục 7 |
| Không tải/cài được gói | Mạng, proxy, TLS hoặc nguồn apt/pip/PPA | Đọc log bước lỗi và xử lý đúng nguồn, không bỏ qua dependency bắt buộc |
| `Permission denied` | Mất `+x` hoặc filesystem mount `noexec` | Cấp quyền và chuyển sang filesystem cho phép thực thi |
| `GLIBC_2.xx not found` | Baseline build/dependency mới hơn máy đích | Build lại trong baseline phù hợp và kiểm tra dependency thực tế |
| `Exec format error` | Binary sai OS/CPU hoặc file không phải binary thực | Chuẩn bị binary Linux đúng kiến trúc |
| Thiếu shared library | Bundle thiếu hoặc còn phụ thuộc hệ thống | Xác định thư viện thiếu, điều chỉnh gói hoặc ghi rõ dependency máy đích |
| `no display name and no $DISPLAY` | Không có phiên desktop/X11 phù hợp | Chạy trong phiên GUI hoặc môi trường kiểm thử được cấu hình display |
| `BadLength ... RenderAddGlyphs` | LibXft/font/DPI hoặc bundle cũ | Xét đúng libXft được nạp, font policy, thời điểm cấu hình Tk và scaling |
| Thiếu `ffprobe` | Bước tải chỉ xét FFmpeg hoặc binary có sẵn không đầy đủ | Chuẩn bị đủ hai binary và quyền thực thi |
| Artifact có tên không đúng Pro | Import metadata fallback trước khi cài dependency | Sửa nguồn metadata và cách truyền tên vào container |
| Không lưu được token/license | Ghi vào mount/read-only hoặc thư mục không có quyền | Dùng thư mục dữ liệu người dùng ghi được |

Nếu được phép chạy chẩn đoán, mở từ terminal để thấy stderr. Crash X11/native có thể không tạo traceback Python, nên không mặc định sẽ luôn có `crash.log`.

## 17. Bàn giao và tiêu chí đánh giá

Agent ở project đích cần xác định được:

- Entry point, tên/version, kiến trúc CPU, Ubuntu tối thiểu và định dạng cần phát hành.
- Các dependency/module/resource thực sự được ứng dụng dùng.
- File build, helper font và launcher nào cần chuyển; chỗ nào cần đổi theo project đích.
- Các lỗi/giới hạn ở mục 15 có áp dụng vào project đích hay không, và đã xử lý thế nào.
- Artifact cuối nằm trong `dist/` hoặc thư mục bàn giao của project đích; không để bản duy nhất trong `/tmp` container.
- Hướng dẫn chạy khớp chính xác tên artifact được tạo.
- Kết quả nào chỉ được đánh giá bằng source, kết quả nào đã build/test thực tế, và điều gì chưa xác nhận.

Nếu người dùng chỉ yêu cầu đọc/đánh giá hoặc sửa source, không tự chạy build, test, app launch hay stress test. Nếu người dùng yêu cầu build Ubuntu, thực hiện đúng deliverable đã yêu cầu. Chỉ chạy kiểm thử tính năng/X11 khi được yêu cầu kiểm thử; không coi tài liệu này là quyền tự động mở rộng phạm vi.

Khi được yêu cầu xác minh, ưu tiên kiểm tra đúng phần liên quan: artifact đủ tài nguyên, hai binary FFmpeg, libXft thực tế được nạp, quyền ghi dữ liệu và khả năng khởi động trên baseline desktop mục tiêu. Repo nguồn có các công cụ `tests/check_bundled_xft.py`, `tests/linux_x11_render_stress.py` và `tests/test_linux_x11_packaging.py`; đọc giao diện/điều kiện chạy của chúng trước khi sử dụng, không đoán tham số.

## 18. Prompt mẫu đưa cho agent project đích

Sao chép đoạn dưới và điền thông tin trong dấu `<...>`. Nếu chỉ đánh giá, ghi rõ chế độ đánh giá; nếu cần triển khai/build, ghi rõ deliverable tương ứng.

```text
Hãy đọc BUILD_UBUNTU_AGENT_GUIDE.md và áp dụng vào project hiện tại.

Yêu cầu của tôi:
- Chế độ: <chỉ đánh giá / triển khai script / triển khai và build artifact>.
- Entry point: <file chính hoặc tự xác định từ source liên quan>.
- Tên ứng dụng: <tên>.
- Version: <version hoặc nguồn metadata hiện có>.
- CPU: <x86_64, hoặc kiến trúc khác cần đánh giá riêng>.
- Ubuntu tối thiểu: <phiên bản>.
- Deliverable: <AppImage / tar.gz / cả hai nếu thực sự cần>.
- Tính năng cần giữ: <GUI, render FFmpeg, OAuth/Drive nếu có>.
- Kiểm thử: <không chạy; tôi tự test / phạm vi test tôi yêu cầu>.

Đầu tiên đọc những file thực sự liên quan đến build và entry point.
Đối chiếu hướng dẫn với project này, không copy nguyên tên/module của project nguồn.
Xử lý các điểm phù hợp ở mục 15, đặc biệt metadata, đường dẫn native,
biến Docker, FFmpeg/FFprobe, launcher và dữ liệu người dùng.

Nếu tôi yêu cầu triển khai, sửa trực tiếp trong workspace hiện tại,
giữ nguyên thay đổi có sẵn và nghiệp vụ ứng dụng.
Nếu tôi yêu cầu build, tạo đúng artifact trong thư mục output của project.
Không tự chạy test, mở app, tạo branch/worktree hoặc commit nếu tôi chưa yêu cầu.

Kết quả trả về: file đã sửa, lệnh build/chạy với tên thật,
các giới hạn còn lại và trạng thái build/test thực tế.
```

Nếu muốn agent đánh giá đúng implementation hiện tại thay vì chỉ lấy ý tưởng, gửi kèm các script/helper/fontconfig ở mục 2. Một file hướng dẫn không thay thế toàn bộ source mà script phụ thuộc.
