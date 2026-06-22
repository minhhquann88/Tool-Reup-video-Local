# Logo: Chuyển động + Độ mờ — Thiết kế

Ngày: 2026-06-19

## Mục tiêu
Bổ sung cho khối Logo/Watermark: logo có thể **chuyển động** (ngoài vị trí cố định) và
có **độ mờ** 3 nấc. Phục vụ reup video (watermark khó crop/khó trùng khớp khi quét).

## Giao diện (main.py — khối Logo)
```
Vị trí (điểm xuất phát):  [Top-Right ▾]
Chuyển động:              [Cố định ▾]     → Cố định | Chạy vòng theo viền | Nảy DVD
Tốc độ:                   [Vừa ▾]         ← CHỈ hiện khi Chuyển động ≠ Cố định
Độ mờ:                    [Rõ ▾]          → Rõ | Mờ vừa | Mờ nhiều   (luôn hiện)
Kích thước (%):           [15]
```
- Dropdown **Vị trí** giữ nguyên, giờ đóng vai trò *điểm xuất phát* của chuyển động.
- **Tốc độ** ẩn/hiện động qua callback của dropdown Chuyển động (pack / pack_forget),
  giống cơ chế toggle Voice AI sẵn có. "Cố định" → ẩn; kiểu chuyển động khác → hiện.
- **Độ mờ** áp dụng cho logo cả khi đứng yên lẫn khi chuyển động.

## Dữ liệu (settings dict truyền vào VideoProcessor.process_video)
Thêm 3 key (giữ nguyên các key cũ `logo_path`, `logo_position`, `logo_size`):
- `logo_motion`: `"static"` | `"perimeter"` | `"bounce"`
- `logo_speed` : `"slow"` | `"normal"` | `"fast"`
- `logo_opacity`: `"opaque"` | `"medium"` | `"light"`

main.py map nhãn tiếng Việt → token tiếng Anh trước khi đưa vào settings.

## Xử lý FFmpeg (core.py)
Biểu thức `overlay` viết theo biến `W,H,w,h,t` nên độc lập độ phân giải & cỡ logo,
thêm `eval=frame` để cập nhật toạ độ mỗi khung hình.

### Tốc độ → pixel/giây (theo chiều rộng video)
- slow   = `0.05*W` /s
- normal = `0.10*W` /s
- fast   = `0.20*W` /s

S (biểu thức) = ví dụ `(0.10*W)`.

### Chạy vòng theo viền (perimeter) — xuôi kim đồng hồ
Đặt `a=(W-w)`, `b=(H-h)`, chu vi `P=2*a+2*b`, `d=mod(S*t + d0, P)`.
Bốn đoạn: trên (T→P) → phải (T→D) → dưới (P→T) → trái (D→T), lặp vô hạn.
`d0` theo Vị trí xuất phát: Top-Left=0, Top-Right=a, Bottom-Right=a+b,
Bottom-Left=2*a+b, Center=0 (mặc định Top-Left).

```
x = if(lt(d,a), d, if(lt(d,a+b), a, if(lt(d,2*a+b), a-(d-a-b), 0)))
y = if(lt(d,a), 0, if(lt(d,a+b), d-a, if(lt(d,2*a+b), b, b-(d-2*a-b))))
```

### Nảy DVD (bounce)
Sóng tam giác trên mỗi trục, đập cạnh dội ngược:
```
x = abs(mod(S*t + px0, 2*(W-w)) - (W-w))
y = abs(mod(S*t + py0, 2*(H-h)) - (H-h))
```
Phase `px0/py0` đặt sao cho t=0 logo nằm ở góc tương ứng Vị trí
(trái→px0=(W-w), phải→px0=0; trên→py0=(H-h), dưới→py0=0; Center→Top-Left).

### Cố định (static)
Dùng lại `_POSITIONS` như hiện tại → tương thích ngược hoàn toàn.

### Độ mờ
Thêm vào chuỗi lọc logo trước overlay: `...,format=rgba,colorchannelmixer=aa={alpha}`.
- Rõ      → alpha 1.0 → KHÔNG thêm filter (giữ nguyên hành vi cũ)
- Mờ vừa  → alpha 0.6
- Mờ nhiều → alpha 0.3

### Escape
Biểu thức x/y chứa dấu phẩy → escape `\,` trước khi nhúng vào `-filter_complex`.
Overlay buộc re-encode (đã đúng sẵn vì có logo).

## Phạm vi (YAGNI)
Chỉ animate vị trí + chỉnh độ mờ. Không xoay/nhấp nháy, không chọn chiều xuôi/ngược,
không đổi kích thước theo thời gian.
