# Chế độ lưu Local (cạnh Google Drive) — Thiết kế

Ngày: 2026-06-19

## Mục tiêu
Thêm chế độ **Local** bên cạnh Google Drive: thay vì upload video đã xử lý lên Drive
rồi ghi link Drive vào CSV, lưu video vào thư mục local và ghi **đường dẫn local** vào
cùng cột `video_url` của CSV output.

## Giao diện (main.py — thay khối "☁️ Google Drive")
```
[ Google Drive | Local ]        ← CTkSegmentedButton, mặc định "Google Drive"

· Khối Drive (hiện ở chế độ Drive):
    Chưa đăng nhập
    [🔑 Đăng nhập] [🚪 Đăng xuất]
· Khối Local (hiện ở chế độ Local):
    Chưa chọn thư mục
    [📂 Chọn thư mục lưu]

Tên folder: [______]            ← LUÔN hiện. Drive: tên folder Drive;
                                   Local: tên thư mục con tạo trong thư mục đã chọn
```
- Gạt chế độ → `pack`/`pack_forget` khối tương ứng (pattern giống switch Voice AI / Tốc độ logo).
- Thêm `self._local_dir` (StringVar) + `_pick_local_dir()` dùng `filedialog.askdirectory`.
- `_toggle_save_mode()` ẩn/hiện 2 khối theo giá trị segmented button.

## Dữ liệu (settings dict)
Thêm:
- `save_mode`: `"drive"` | `"local"`
- `local_dir`: đường dẫn thư mục đã chọn (str)

Giữ nguyên `folder_name` (Local dùng làm tên thư mục con).

## Kiểm tra đầu vào (`_start_processing`)
- `folder_name` bắt buộc ở cả 2 chế độ (Drive: tên folder Drive; Local: tên thư mục con).
- Chế độ Local: bắt buộc đã chọn `local_dir`.
- `csv_out` bắt buộc như cũ.

## Pipeline (`_run_batch`) — rẽ nhánh theo `save_mode`
Đổi tên biến `drive_links` → `out_refs` (chứa link Drive *hoặc* path local), key vẫn là
`id(vid)`.

### Drive (giữ nguyên)
Init Drive → tạo folder → mỗi video upload → `out_refs[id(vid)] = link`.

### Local
- Bỏ qua đăng nhập/tạo folder Drive.
- `dest_dir = os.path.join(local_dir, folder_name)`, `os.makedirs(dest_dir, exist_ok=True)`.
  Lỗi tạo thư mục → dừng batch kèm thông báo (giống lỗi kết nối Drive: bật lại nút, return).
- Mỗi video: tải → xử lý ra `tmp_out` → **move** `tmp_out` sang `dest_dir` với tên
  `{item_id}.mp4`. Lưu **đường dẫn tuyệt đối** vào `out_refs[id(vid)]`.
- **Trùng tên**: `_reserve_local_path(dest_dir, "<item_id>.mp4")` chạy trong `lock`,
  dò `os.path.exists` + set `_reserved` để tránh đua giữa worker; trả tên duy nhất
  `123.mp4 → 123_1.mp4 → 123_2.mp4 …` rồi mới `shutil.move`.

## Output CSV
Dùng lại `_write_output_csv` không đổi (chỉ đổi tham số tên `drive_links` → `out_refs`):
ghi CSV giống file gốc, cột `video_url` thay bằng giá trị trong `out_refs`
(link Drive hoặc path local). Vẫn xuất `.csv`.

## Thông báo hoàn thành
- Drive: như cũ ("☁️ Folder: …").
- Local: "📁 Thư mục: <dest_dir>" và hỏi **"Mở thư mục chứa video?"**
  (`messagebox.askyesno`) → nếu đồng ý `os.startfile(dest_dir)` (Windows).

## Phạm vi (YAGNI)
Không sửa logic Drive. Không thêm openpyxl (giữ CSV). Không tự mở thư mục — chỉ hỏi sau khi xong.
