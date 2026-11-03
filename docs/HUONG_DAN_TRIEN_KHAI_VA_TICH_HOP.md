# VCAM API_KEY — Hướng dẫn Triển khai & Tích hợp Kiểm soát Bản quyền

> **Phiên bản tài liệu:** 2.2  
> **Ngày tạo:** 30/05/2026  
> **Cập nhật lần cuối:** 10/08/2026  
> **Tác giả:** Phân tích tĩnh toàn bộ mã nguồn dự án API_KEY  
> **Hệ thống:** Laravel 11 Backend (`vcamapi`) + Vue 3 / Vite SPA (`vcamadmin`)

> **Thay đổi v2.2:** Bổ sung **PHẦN D — Quy trình Tự động Build & Đẩy Bản Release Lên GitHub Public Repository** (GitHub Actions, GitHub CLI `gh release`,PAT Token, Tag Prefix đa dự án).
>
> **Thay đổi v2.1:** Bổ sung **Đặc tả UX bắt buộc cho Force Update** (ẩn cửa sổ chính, dialog độc lập, grab_set), cập nhật code mẫu Python (B7) với `APP_VERSION`, `get_platform_name()`, xử lý update response trong GUI, và Recheck Timer hoàn chỉnh.
>
> **Thay đổi v2.0:** Bổ sung tính năng **Quản lý Phiên bản Ứng dụng (App Versioning)**, **Kiểm tra Cập nhật Tự động (Auto-Update Check)** tích hợp vào luồng xác thực key, và cơ chế **Recheck Key & Version định kỳ mỗi 30 phút**.

---

# MỤC LỤC

- [PHẦN A — TRIỂN KHAI DỰ ÁN API_KEY](#phần-a--triển-khai-dự-án-api_key)
  - [A1. Tổng quan Kiến trúc Hệ thống](#a1-tổng-quan-kiến-trúc-hệ-thống)
  - [A2. Yêu cầu Môi trường](#a2-yêu-cầu-môi-trường)
  - [A3. Triển khai Backend (vcamapi)](#a3-triển-khai-backend-vcamapi)
  - [A4. Triển khai Frontend (vcamadmin)](#a4-triển-khai-frontend-vcamadmin)
  - [A5. Đăng ký Ứng dụng mới vào Hệ thống](#a5-đăng-ký-ứng-dụng-mới-vào-hệ-thống)
  - [A6. Tạo và Quản lý Khóa Bản quyền](#a6-tạo-và-quản-lý-khóa-bản-quyền)
  - [A7. Quản lý Phiên bản Ứng dụng (App Versioning) — MỚI](#a7-quản-lý-phiên-bản-ứng-dụng-app-versioning--mới)
- [PHẦN B — TÍCH HỢP KIỂM SOÁT BẢN QUYỀN VÀO ỨNG DỤNG CLIENT](#phần-b--tích-hợp-kiểm-soát-bản-quyền-vào-ứng-dụng-client)
  - [B1. Nguyên lý Hoạt động](#b1-nguyên-lý-hoạt-động)
  - [B2. Đặc tả API Xác thực Khóa (Kèm Check Update)](#b2-đặc-tả-api-xác-thực-khóa-kèm-check-update)
  - [B3. Logic Ràng buộc Thiết bị (Device Binding)](#b3-logic-ràng-buộc-thiết-bị-device-binding)
  - [B4. Cơ chế Recheck Key & Version định kỳ (30 phút)](#b4-cơ-chế-recheck-key--version-định-kỳ-30-phút)
  - [B5. Tích hợp cho Chrome Extension](#b5-tích-hợp-cho-chrome-extension)
  - [B6. Tích hợp cho Desktop App (C# / .NET)](#b6-tích-hợp-cho-desktop-app-c--net)
  - [B7. Tích hợp cho Python](#b7-tích-hợp-cho-python)
  - [B8. Tích hợp cho Mobile App (Flutter / React Native)](#b8-tích-hợp-cho-mobile-app-flutter--react-native)
  - [B9. Lưu ý Bảo mật khi Tích hợp](#b9-lưu-ý-bảo-mật-khi-tích-hợp)
- [PHẦN C — CHECKLIST TRIỂN KHAI PRODUCTION](#phần-c--checklist-triển-khai-production)
- [PHẦN D — QUY TRÌNH TỰ ĐỘNG BUILD & ĐẨY BẢN RELEASE LÊN GITHUB PUBLIC REPOSITORY](#phần-d--quy-trình-tự-động-build--đẩy-bản-release-lên-github-public-repository)
  - [D1. Mô Hình Hoạt Động Trung Tâm](#d1-mô-hình-hoạt-động-trung-tâm)
  - [D2. Quy Ước Đặt Tên Tag (Prefix Bắt Buộc)](#d2-quy-ước-đặt-tên-tag-prefix-bắt-buộc)
  - [D3. Hướng Dẫn Thiết Lập Từng Bước](#d3-hướng-dẫn-thiết-lập-từng-bước)
  - [D4. Quy Trình Phát Hành Bản Mới Hàng Ngày](#d4-quy-trình-phát-hành-bản-mới-hàng-ngày)
  - [D5. Cấu Trúc URL Tải Trực Tiếp Dành Cho Admin Dashboard](#d5-cấu-trúc-url-tải-trực-tiếp-dành-cho-admin-dashboard)
  - [D6. Xử Lý Sự Cố Thường Gặp](#d6-xử-lý-sự-cố-thường-gặp)

---

# PHẦN A — TRIỂN KHAI DỰ ÁN API_KEY

## A1. Tổng quan Kiến trúc Hệ thống

Dự án API_KEY bao gồm 2 thành phần chính:

| Thành phần | Thư mục | Công nghệ | Vai trò |
|------------|---------|------------|---------|
| **Backend API** | `vcamapi/` | Laravel 11, PHP 8.2+, MySQL | Xử lý logic nghiệp vụ: xác thực key, ràng buộc thiết bị, quản lý CRUD |
| **Admin Dashboard** | `vcamadmin/` | Vue 3, TypeScript, Vite 6, TailwindCSS 4 | Giao diện quản trị: tạo key, quản lý ứng dụng, quản lý tài khoản |

```
┌─────────────────────────────────────────────────────────────────┐
│                    CÁC ỨNG DỤNG CLIENT                         │
│  (Chrome Extension, Desktop App, Mobile App, Python Script)     │
│                                                                 │
│  Mỗi app được gán cứng một app_id duy nhất trong mã nguồn      │
│  Ví dụ: app_id = "APP001" cho Extension Shopee Check Live      │
└──────────────────────────┬──────────────────────────────────────┘
                           │ POST /api/validate-key
                           │ {key, app_id, deviceId}
                           │ (Không cần xác thực token)
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│              BACKEND API  (vcamapi - Laravel 11)                │
│                     http://localhost:8000                        │
│                                                                 │
│  ┌─────────────┐    ┌──────────────┐    ┌────────────────────┐  │
│  │ routes/     │    │ Controllers/ │    │ Services/          │  │
│  │ api.php     │───▶│ KeyController│───▶│ KeyService         │  │
│  │ (public)    │    │              │    │ .validateKey()     │  │
│  └─────────────┘    └──────────────┘    └────────┬───────────┘  │
│                                                  │              │
│  ┌─────────────┐    ┌──────────────┐    ┌────────▼───────────┐  │
│  │ routes/     │    │ Admin/       │    │ Repositories/      │  │
│  │ api-admin   │───▶│ KeyController│    │ KeyRepository      │  │
│  │ (sanctum)   │    │ AppController│    │ AppRepository      │  │
│  └─────────────┘    │ AuthController    └────────┬───────────┘  │
│                     └──────────────┘             │              │
└──────────────────────────────────────────────────┼──────────────┘
                                                   │
                                                   ▼
                                        ┌─────────────────────┐
                                        │   MySQL Database    │
                                        │   vcam_apikey       │
                                        │                     │
                                        │   Bảng apps ◄───┐   │
                                        │   Bảng keys ────┘   │
                                        │   Bảng admins       │
                                        └─────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│              ADMIN DASHBOARD  (vcamadmin - Vue 3)               │
│                     http://localhost:5173                        │
│                                                                 │
│  Quản trị viên đăng nhập → Tạo key → Chọn app → Cấp cho KH    │
│  Gọi API backend qua Bearer Token (Sanctum)                    │
└─────────────────────────────────────────────────────────────────┘
```

### Bảng Ánh xạ Đường dẫn API (Route Prefix)

Cấu hình tại `app/Providers/RouteServiceProvider.php`:

| Tiền tố | Tệp Route | Xác thực | Mục đích |
|---------|-----------|----------|----------|
| `/api` | `routes/api.php` | **Không** (Công khai) | API xác thực khóa bản quyền cho Client |
| `/admin` | `routes/api-admin.php` | Sanctum Bearer Token | API quản trị Dashboard |

---

## A2. Yêu cầu Môi trường

| Phần mềm | Phiên bản | Ghi chú |
|-----------|-----------|---------|
| PHP | `≥ 8.2` | Bắt buộc bởi Laravel 11 |
| Composer | `2.x` (mới nhất) | Trình quản lý thư viện PHP |
| MySQL | `≥ 8.0` | Cơ sở dữ liệu chính |
| Node.js | `≥ 18.x` (LTS) | Vite 6 yêu cầu tối thiểu Node 18 |
| npm | `≥ 9.x` | Đi kèm Node 18+ |

---

## A3. Triển khai Backend (vcamapi)

### Bước 1: Cài đặt thư viện phụ thuộc

```bash
cd vcamapi

# Cài đặt thư viện PHP
composer install

# Cài đặt thư viện Node.js (cho Vite)
npm install
```

### Bước 2: Cấu hình môi trường

```bash
cp .env.example .env
```

Mở tệp `.env` và chỉnh sửa các thông tin sau:

```dotenv
APP_NAME=VcamAPIKey
APP_ENV=local
APP_DEBUG=true
APP_TIMEZONE=UTC
APP_URL=http://localhost:8000

# ── Cơ sở dữ liệu ──────────────────────────────
DB_CONNECTION=mysql
DB_HOST=127.0.0.1
DB_PORT=3306
DB_DATABASE=vcam_apikey
DB_USERNAME=root
DB_PASSWORD=MAT_KHAU_MYSQL_CUA_BAN

# ── Session & Queue ─────────────────────────────
SESSION_DRIVER=database
QUEUE_CONNECTION=database
CACHE_STORE=database

# ── Cấu hình gửi Mail (SMTP qua Gmail) ─────────
MAIL_MAILER=smtp
MAIL_HOST=smtp.gmail.com
MAIL_PORT=587
MAIL_USERNAME=email_cua_ban@gmail.com
MAIL_PASSWORD=mat_khau_ung_dung
MAIL_ENCRYPTION=tls
MAIL_FROM_ADDRESS=email_cua_ban@gmail.com
MAIL_FROM_NAME="${APP_NAME}"

# ── Tên miền Frontend (CORS) ────────────────────
DOMAIN_FRONTEND=http://localhost:5173
```

### Bước 3: Tạo khóa ứng dụng

```bash
php artisan key:generate
```

### Bước 4: Tạo cơ sở dữ liệu MySQL

```bash
mysql -u root -p -e "CREATE DATABASE vcam_apikey CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
```

### Bước 5: Chạy Migration

```bash
php artisan migrate
```

Các bảng sẽ được tạo:

| Bảng | Mục đích |
|------|----------|
| `apps` | Danh sách ứng dụng đã đăng ký (`app_id`, `app_name`) |
| `keys` | Khóa bản quyền (`key`, `app_id`, `device_id`, `start_date`, `expire_date`, `note`, `author`) |
| `app_versions` | **MỚI** — Quản lý phiên bản ứng dụng (`version`, `download_url`, `changelog`, `force_update`, `min_version`, `platform`) |
| `admins` | Tài khoản quản trị viên (`name`, `email`, `password`, `role`) |
| `personal_access_tokens` | Token xác thực API (Laravel Sanctum) |
| `sessions`, `cache`, `jobs` | Các bảng hỗ trợ hệ thống Laravel |

### Bước 6: Chạy Seeder (dữ liệu mẫu)

```bash
# Seed danh sách ứng dụng mặc định vào bảng apps
php artisan db:seed

# (Tùy chọn) Seed vai trò & quyền hạn
php artisan db:seed --class=PermissionSeeder
```

Danh sách ứng dụng mặc định được tạo bởi `AppSeeder`:

| app_id | app_name |
|--------|----------|
| APP001 | Shopee Check Live |
| APP002 | Shopee Auto Like |
| APP003 | Shopee Copy Product Live |
| APP004 | Shopee Analytics |
| APP005 | Shopee GMV Affiliate |

### Bước 7: Tạo tài khoản Admin đầu tiên

> **Lưu ý:** Hệ thống không tự động tạo tài khoản Admin. Bạn phải tạo thủ công.

```bash
php artisan tinker
```

```php
use App\Models\Admin;

Admin::create([
    'name'     => 'Super Admin',
    'email'    => 'admin@vcam.com',
    'password' => 'admin123456',   // Tự động hash bởi cast 'hashed' trong Model
    'role'     => 1,               // 1 = Admin (toàn quyền)
]);
```

**Giá trị vai trò (Role):**

| Giá trị | Hằng số | Quyền hạn |
|---------|---------|-----------|
| `0` | `ROLE_SUPPER_ADMIN` | Super Admin |
| `1` | `ROLE_ADMIN` | Admin — toàn quyền Dashboard, quản lý Analyst |
| `2` | `ROLE_ANALYST` | Analyst — chỉ xem/quản lý key do mình tạo |

### Bước 8: Khởi chạy Backend

```bash
# Cách 1: Chỉ chạy API server
php artisan serve
# → http://127.0.0.1:8000

# Cách 2: Chạy đầy đủ (server + queue + logs + vite)
composer run dev
```

Lệnh `composer run dev` khởi chạy đồng thời:
1. `php artisan serve` — HTTP server
2. `php artisan queue:listen --tries=1` — Xử lý hàng đợi
3. `php artisan pail --timeout=0` — Nhật ký thời gian thực
4. `npm run dev` — Vite biên dịch asset

---

## A4. Triển khai Frontend (vcamadmin)

### Bước 1: Cài đặt thư viện

```bash
cd vcamadmin
npm install
```

### Bước 2: Cấu hình URL API Backend

Tạo hoặc chỉnh sửa tệp `.env` trong thư mục `vcamadmin/`:

```dotenv
VITE_URL_API=http://127.0.0.1:8000/
```

> **Quan trọng:** URL phải có dấu `/` ở cuối. Đây là điểm cấu hình duy nhất để Frontend kết nối Backend, được sử dụng tại `src/services/api.ts`:
> ```typescript
> const instance = axios.create({
>     baseURL: import.meta.env.VITE_URL_API
> });
> ```

### Bước 3: Khởi chạy Dev Server

```bash
npm run dev
# → http://localhost:5173
```

### Bước 4: Build cho Production

```bash
npm run build
# Thư mục đầu ra: vcamadmin/dist/
```

### Đăng nhập Admin Dashboard

Truy cập `http://localhost:5173/login` và nhập:
- **Email:** `admin@vcam.com`
- **Mật khẩu:** `admin123456`

---

## A5. Đăng ký Ứng dụng mới vào Hệ thống

Khi bạn muốn cấp key cho một App hoặc Extension mới (ngoài 5 app mặc định), bạn cần đăng ký `app_id` mới vào bảng `apps` trước.

### Cách 1: Qua Giao Diện Quản Trị /app-management (Khuyên dùng)

Hệ thống đã tích hợp giao diện **Quản lý ứng dụng (Apps Management)** tại đường dẫn `/app-management` trên Admin Dashboard giúp thực hiện trực quan và nhanh chóng:

1. Đăng nhập vào trang quản trị `vcamadmin` (ví dụ: `http://localhost:5173`).
2. Truy cập vào menu hoặc gõ trực tiếp đường dẫn **`/app-management`**.
3. Nhấp chọn nút **Thêm Ứng dụng (Add App)**.
4. Nhập đầy đủ các thông tin:
   * **App ID:** Mã định danh duy nhất (ví dụ: `EXT_SHOPEE_SCANNER`).
   * **App Name:** Tên hiển thị của ứng dụng (ví dụ: `Shopee Scanner Extension`).
5. Nhấn **Lưu (Save)**. Ứng dụng mới sẽ lập tức được ghi nhận vào cơ sở dữ liệu và hiển thị trong danh mục lựa chọn khi cấp Key bản quyền mới.

---

### Cách 2: Qua Laravel Tinker

```bash
php artisan tinker
```

```php
\App\Models\App::create([
    'app_id'   => 'EXT_SHOPEE_SCANNER',
    'app_name' => 'Shopee Scanner Extension'
]);
```

---

### Cách 3: Qua SQL trực tiếp

```sql
INSERT INTO apps (app_id, app_name, created_at, updated_at)
VALUES ('EXT_SHOPEE_SCANNER', 'Shopee Scanner Extension', NOW(), NOW());
```

---

### Cách 4: Thêm vào Seeder (Khuyên dùng cho cấu trúc đồng bộ dài hạn)

Mở tệp `database/seeders/AppSeeder.php` và thêm phần tử mới vào mảng `$apps`:

```php
[
    'app_id' => 'EXT_SHOPEE_SCANNER',
    'app_name' => 'Shopee Scanner Extension',
],
```

> **Quy tắc đặt app_id:**
> - Chuỗi ký tự duy nhất, viết hoa, không dấu, không khoảng trắng
> - Ví dụ: `APP001`, `EXT_SHOPEE_SCANNER`, `DESKTOP_AUTO_LIKE`, `MOBILE_ANALYTICS`
> - Mã này sẽ được **gán cứng (hardcode)** vào mã nguồn của ứng dụng Client tương ứng

---

## A6. Tạo và Quản lý Khóa Bản quyền

### Qua giao diện Admin Dashboard

1. Đăng nhập tại `http://localhost:5173/login`
2. Vào menu **Key Management** → **Create**
3. Chọn ứng dụng (dropdown tự động tải từ bảng `apps`)
4. Nhập chuỗi key hoặc để hệ thống sinh tự động
5. Thiết lập ngày bắt đầu / ngày hết hạn (để trống = vĩnh viễn)
6. Nhấn **Tạo**

### Qua API (dành cho tự động hóa)

```bash
# 1. Đăng nhập lấy token
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/admin/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@vcam.com","password":"admin123456"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['access_token'])")

# 2. Tạo key mới
curl -X POST http://127.0.0.1:8000/admin/keys \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "app_id": "EXT_SHOPEE_SCANNER",
    "key": "ABCDEF-123456-GHIJKL-789012",
    "start_date": "2026-06-01 00:00:00",
    "expire_date": "2026-12-31 23:59:59",
    "note": "Cấp cho khách hàng Nguyễn Văn A"
  }'
```

---

## A7. Quản lý Phiên bản Ứng dụng (App Versioning) — MỚI

Hệ thống hỗ trợ **quản lý phiên bản** và **kiểm tra cập nhật tự động (Auto-Update Check)** cho mỗi ứng dụng Client. Tính năng này được tích hợp trực tiếp vào API xác thực key (`/api/validate-key`), không cần thêm API riêng.

### Quy trình phát hành bản cập nhật mới

```
1. Build file cài đặt (VD: CrawlTikTok_v1.1.0.exe)
2. Upload file lên Cloudflare R2 / GitHub Releases / VPS
3. Vào Admin Dashboard → Quản lý phiên bản → Thêm phiên bản
4. Dán link tải, ghi chú thay đổi, chọn bắt buộc/tùy chọn
5. Khách hàng mở app → Tự động nhận thông báo cập nhật
```

### Qua giao diện Admin Dashboard

1. Đăng nhập tại `http://localhost:5173/login`
2. Vào menu **Quản lý phiên bản** → **Thêm phiên bản**
3. Điền thông tin:

| Trường | Bắt buộc | Mô tả | Ví dụ |
|--------|----------|-------|-------|
| **App** | Có | Chọn ứng dụng từ danh sách | `CRAWL_TIKTOK` |
| **Version** | Có | Định dạng SemVer `x.y.z` | `1.1.0` |
| **Download URL** | Có | Đường dẫn tải file cài đặt (Cloudflare R2 / Direct Link) | `https://download.vcam.com/apps/tiktok/v1.1.0/setup.exe` |
| **Changelog** | Không | Ghi chú các thay đổi (plain text) | `- Thêm tính năng X\n- Sửa lỗi Y` |
| **Platform** | Có | Nền tảng: `all`, `win`, `mac`, `linux` | `win` |
| **Force Update** | Không | Nếu tích: mọi phiên bản cũ hơn bắt buộc phải cập nhật | `true/false` |
| **Min Version** | Không | Nếu client đang dùng phiên bản thấp hơn giá trị này → ép cập nhật | `1.0.0` |
| **Published At** | Không | Ngày phát hành (mặc định hôm nay) | `2026-08-08` |

4. Nhấn **Lưu**. Bản phiên bản mới lập tức có hiệu lực — mọi Client gọi API sẽ nhận được thông tin cập nhật.

### Qua API (dành cho CI/CD tự động hóa)

```bash
# Tạo phiên bản mới
curl -X POST http://127.0.0.1:8000/admin/app-versions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "app_id": "CRAWL_TIKTOK",
    "version": "1.1.0",
    "download_url": "https://download.vcam.com/apps/tiktok/v1.1.0/CrawlTikTok_v1.1.0.exe",
    "changelog": "- Thêm tính năng quét video theo Hashtag\n- Sửa lỗi Bio",
    "force_update": false,
    "min_version": "1.0.0",
    "platform": "win",
    "is_active": true
  }'
```

### Logic kiểm tra cập nhật trên Server

Khi Client gọi `/api/validate-key` kèm `current_version`:
1. Server xác thực key bình thường (giữ nguyên luồng cũ)
2. Nếu key hợp lệ → Server query bản `app_versions` mới nhất (active, cùng platform hoặc `all`)
3. So sánh SemVer: nếu `current_version < latest_version` → trả thông tin update
4. Xác định `force_update`: `true` nếu bản mới đặt `force_update = true` **HOẶC** `current_version < min_version`
5. Nếu Client **không gửi** `current_version` (app cũ chưa hỗ trợ) → `"update": null` (backward compatible 100%)

---

# PHẦN B — TÍCH HỢP KIỂM SOÁT BẢN QUYỀN VÀO ỨNG DỤNG CLIENT

## B1. Nguyên lý Hoạt động

Để một ứng dụng (Extension, Desktop App, Mobile App...) **bắt buộc nhập key mới dùng được**, bạn cần nhúng **License Check Gatekeeper** vào mã nguồn Client. Cơ chế hoạt động như sau:

```
┌──────────────────────────────────────────────────────────────────────┐
│                    MÃ NGUỒN ỨNG DỤNG CLIENT                         │
│                                                                      │
│  ┌──────────────┐                                                    │
│  │ Khởi động    │                                                    │
│  │ ứng dụng     │                                                    │
│  └──────┬───────┘                                                    │
│         │                                                            │
│         ▼                                                            │
│  ┌──────────────────┐    Chưa có     ┌─────────────────────────────┐ │
│  │ Key đã lưu trong │──────────────▶│ Hiển thị FORM NHẬP KEY      │ │
│  │ bộ nhớ cục bộ?   │               │ (Khóa toàn bộ tính năng)    │ │
│  └──────┬───────────┘               └────────────┬────────────────┘ │
│         │ Đã có                                  │ Người dùng nhập  │
│         ▼                                        ▼                  │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │        GỌI API:  POST /api/validate-key                      │    │
│  │        Body: { key, app_id (gán cứng), deviceId }            │    │
│  └──────────────────────────┬───────────────────────────────────┘    │
│                             │                                        │
│              ┌──────────────┴──────────────┐                        │
│              ▼                             ▼                        │
│     ┌────────────────┐           ┌──────────────────┐               │
│     │ status == 1    │           │ status == 0      │               │
│     │ KEY HỢP LỆ    │           │ KEY KHÔNG HỢP LỆ │               │
│     └───────┬────────┘           └────────┬─────────┘               │
│             │                             │                          │
│             ▼                             ▼                          │
│     ┌────────────────┐           ┌──────────────────┐               │
│     │ Lưu key vào    │           │ Xóa key cũ       │               │
│     │ bộ nhớ cục bộ  │           │ Hiện thông báo   │               │
│     │ MỞ KHÓA        │           │ lỗi + yêu cầu   │               │
│     │ TÍNH NĂNG      │           │ nhập key mới     │               │
│     └────────────────┘           └──────────────────┘               │
└──────────────────────────────────────────────────────────────────────┘
```

**3 yếu tố cốt lõi cần hiểu:**

1. **`app_id` được gán cứng (hardcode) trong mã nguồn Client.** Mỗi app/extension có một `app_id` riêng. Người dùng không thể tự ý thay đổi giá trị này.

2. **`deviceId` là mã định danh phần cứng của máy người dùng.** Được tự động thu thập bởi Client (Machine GUID, Browser Fingerprint, v.v.). Khi key được kích hoạt lần đầu, hệ thống sẽ **ràng buộc key đó vào đúng thiết bị** → không thể dùng key trên máy khác.

3. **Backend kiểm tra đồng thời cả 3 yếu tố:** `key` + `app_id` + `deviceId` đều phải khớp với bản ghi trong database thì mới trả về thành công. Điều này đảm bảo:
   - Key của Extension A không dùng được cho Extension B (khác `app_id`)
   - Key đã kích hoạt trên máy X không dùng được trên máy Y (khác `deviceId`)

---

## B2. Đặc tả API Xác thực Khóa (Kèm Check Update)

### Thông tin Endpoint

| Thuộc tính | Giá trị |
|------------|---------|
| **URL** | `POST {BACKEND_URL}/api/validate-key` |
| **Phương thức** | `POST` |
| **Content-Type** | `application/json` |
| **Xác thực** | **Không cần** (API công khai) |
| **Giới hạn** | 60 requests/phút/IP |
| **Mục đích** | Xác thực key **VÀ** kiểm tra bản cập nhật trong cùng 1 request |

### Dữ liệu gửi lên (Request Body)

```json
{
    "key": "ABCDEF-123456-GHIJKL-789012",
    "app_id": "EXT_SHOPEE_SCANNER",
    "deviceId": "550e8400-e29b-41d4-a716-446655440000",
    "current_version": "1.0.0",
    "platform": "win"
}
```

| Trường | Kiểu | Bắt buộc | Mô tả |
|--------|------|----------|-------|
| `key` | `string` | **Có** | Chuỗi khóa bản quyền (do Admin cấp) |
| `app_id` | `string` | **Có** | Mã ứng dụng (gán cứng trong mã nguồn Client) |
| `deviceId` | `string` | **Có** | Mã định danh thiết bị (**camelCase**, không phải `device_id`) |
| `current_version` | `string` | Không | **MỚI** — Phiên bản hiện tại của Client (SemVer: `"1.0.0"`). Nếu không gửi → Server trả `"update": null` |
| `platform` | `string` | Không | **MỚI** — Nền tảng: `"win"`, `"mac"`, `"linux"`, `"all"` (mặc định: `"all"`) |

### Phản hồi Thành công (status = 1) — Không có bản cập nhật

```json
{
    "status": 1,
    "message": "Key hợp lệ",
    "data": {
        "id": 42,
        "app_id": "EXT_SHOPEE_SCANNER",
        "key": "ABCDEF-123456-GHIJKL-789012",
        "start_date": "2026-06-01 00:00:00",
        "expire_date": "2026-12-31 23:59:59",
        "device_id": "550e8400-e29b-41d4-a716-446655440000",
        "note": "Cấp cho khách hàng Nguyễn Văn A",
        "author": 1
    },
    "update": null
}
```

### Phản hồi Thành công — Có bản cập nhật tùy chọn

```json
{
    "status": 1,
    "message": "Key hợp lệ",
    "data": { "...key info..." },
    "update": {
        "has_update": true,
        "force_update": false,
        "current_version": "1.0.0",
        "latest_version": "1.1.0",
        "min_version": "1.0.0",
        "download_url": "https://download.vcam.com/apps/CRAWL_TIKTOK/v1.1.0/setup.exe",
        "changelog": "- Thêm tính năng quét theo Hashtag\n- Sửa lỗi Bio",
        "published_at": "2026-08-07 15:00:00"
    }
}
```

### Phản hồi Thành công — Bắt buộc cập nhật (`force_update = true`)

```json
{
    "status": 1,
    "message": "Key hợp lệ",
    "data": { "...key info..." },
    "update": {
        "has_update": true,
        "force_update": true,
        "current_version": "0.9.0",
        "latest_version": "1.1.0",
        "min_version": "1.0.0",
        "download_url": "https://download.vcam.com/apps/.../setup.exe",
        "changelog": "Bản cập nhật bắt buộc. Phiên bản cũ ngưng hoạt động.",
        "published_at": "2026-08-07 15:00:00"
    }
}
```

### Trường `update` — Chi tiết

| Trường | Kiểu | Mô tả |
|--------|------|-------|
| `has_update` | `boolean` | Luôn `true` khi có object `update` |
| `force_update` | `boolean` | `true` = ép cập nhật (chặn dùng app), `false` = tùy chọn |
| `current_version` | `string` | Phiên bản mà Client đang gửi lên |
| `latest_version` | `string` | Phiên bản mới nhất trên Server |
| `min_version` | `string\|null` | Phiên bản tối thiểu. Nếu `current < min` → tự động `force_update = true` |
| `download_url` | `string` | Đường dẫn tải file cài đặt mới |
| `changelog` | `string\|null` | Ghi chú thay đổi |
| `published_at` | `string\|null` | Ngày phát hành |

### Phản hồi Thất bại (status = 0)

```json
{
    "status": 0,
    "message": "<thông_điệp_lỗi>"
}
```

| Trường hợp lỗi | Giá trị `message` |
|----------------|-------------------|
| Key không tồn tại trong database | `"Key không tồn tại"` |
| Mã `app_id` không tồn tại trong bảng `apps` | `"App id không tồn tại"` |
| Key sai ứng dụng / đã dùng trên máy khác / hết hạn | `"Key không hợp lệ hoặc đã được sử dụng trên một thiết bị khác"` |
| Key bị quản trị viên khóa thủ công (nghi ngờ chia sẻ) | `"Key đã bị khóa"` |
| Thiếu trường bắt buộc | `""` (kèm mảng `errors` chi tiết) |

> **QUAN TRỌNG:** Với các lỗi nghiệp vụ ở bảng trên, API trả về HTTP 200 OK — Client phải kiểm tra trường `status` trong body JSON, KHÔNG dựa vào HTTP status code.
>
> **Ngoại lệ — Giới hạn tần suất (Rate limit):** Endpoint bị giới hạn **60 request/phút cho mỗi key**. Khi vượt ngưỡng, API trả về **HTTP 429** với body `{"message": "Too Many Attempts."}` (KHÔNG có trường `status`). Client **PHẢI** coi 429 là "thử lại sau" — giữ nguyên key đã lưu, chờ rồi gọi lại — **KHÔNG** được xóa key đã lưu như trường hợp `status = 0`.

---

## B3. Logic Ràng buộc Thiết bị (Device Binding)

Dưới đây là logic nghiệp vụ chính xác trong mã nguồn Backend (tệp `KeyService.php` và `KeyRepository.php`):

```
Bước 1: Tìm key trong bảng keys
         └─ Không tìm thấy → Trả lỗi "Key không tồn tại"

Bước 2: Tìm app_id trong bảng apps
         └─ Không tìm thấy → Trả lỗi "App id không tồn tại"

Bước 3: Kiểm tra trường device_id của bản ghi key
         ├─ device_id == NULL (Lần kích hoạt đầu tiên):
         │    Truy vấn: WHERE key = ? AND app_id = ? AND device_id IS NULL
         │    AND (start_date IS NULL OR start_date <= NOW())
         │    AND (expire_date IS NULL OR expire_date >= NOW())
         │
         └─ device_id != NULL (Đã kích hoạt trước đó):
              Truy vấn: WHERE key = ? AND app_id = ? AND device_id = ?
              AND (start_date IS NULL OR start_date <= NOW())
              AND (expire_date IS NULL OR expire_date >= NOW())

Bước 4: Nếu truy vấn trả về kết quả
         → CẬP NHẬT device_id = deviceId gửi lên
         → Kiểm tra bản cập nhật (nếu Client gửi current_version)
         → Trả kết quả thành công kèm dữ liệu key + thông tin update (nếu có)

         Nếu truy vấn không trả về kết quả
         → Trả lỗi "Key không hợp lệ hoặc đã được sử dụng trên một thiết bị khác"
```

**Hệ quả quan trọng:**
- **Lần đầu tiên:** Bất kỳ thiết bị nào cũng có thể kích hoạt key (vì `device_id` đang NULL)
- **Từ lần thứ 2:** Chỉ đúng thiết bị đã kích hoạt mới xác thực thành công
- **Muốn chuyển máy:** Admin phải vào database hoặc Dashboard đặt lại `device_id = NULL`

---

## B4. Cơ chế Recheck Key & Version định kỳ (30 phút)

Để đảm bảo ứng dụng Client luôn cập nhật trạng thái key (bị khóa giữa phiên, hết hạn giữa phiên) và nhận được bản cập nhật mới nhất, Client cần tích hợp cơ chế **gọi lại API `/api/validate-key` định kỳ mỗi 30 phút** trong khi ứng dụng đang chạy.

### Nguyên lý hoạt động

```
┌──────────────────────────────────────────────────────────────────┐
│                    VÒNG ĐỜI CỦA CLIENT APP                      │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  1. MỞ APP → Gọi /validate-key (lần đầu)                       │
│     ├── ❌ Key fail → Chặn vào App                               │
│     └── ✅ Key OK → Vào App + Xử lý Update (nếu có)             │
│              └── Khởi động Background Timer (30 phút)            │
│                                                                  │
│  2. SỬ DỤNG APP (Liên tục)                                      │
│     └── ⏱️ Timer mỗi 30 phút gọi /validate-key                  │
│         ├── 🚫 Key bị khóa/hết hạn → Thông báo & Exit App      │
│         ├── ✅ OK, không có bản mới → Tiếp tục bình thường       │
│         ├── 💡 OK, có bản mới (Optional) → Popup nhẹ nhàng      │
│         └── ⚠️ OK, Force Update → Chặn & Ép cập nhật            │
│                                                                  │
│  3. CẬP NHẬT (Khi user đồng ý hoặc bị ép)                      │
│     ├── Tải file từ download_url                                 │
│     ├── Chạy Updater → Ghi đè file mới                          │
│     └── Restart App → Quay lại Bước 1                            │
│                                                                  │
│  4. ĐÓNG APP → Timer tự hủy theo tiến trình                     │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### Quy tắc hoạt động của Timer

| Quy tắc | Mô tả |
|---|---|
| **Chu kỳ mặc định** | 30 phút (1.800 giây) |
| **Thời điểm bắt đầu** | Ngay sau khi xác thực Key thành công lần đầu tiên |
| **Khi recheck thành công** | Timer lên lịch lại sau đúng 30 phút |
| **Khi lỗi mạng / timeout** | Retry sớm hơn: sau **5 phút**. Tối đa **3 lần liên tiếp** rồi quay lại chu kỳ 30 phút |
| **Khi đang tải bản cập nhật** | Timer tạm dừng cho đến khi App restart |
| **Khi App bị đóng/crash** | Timer tự hủy theo tiến trình. Lần mở kế tiếp sẽ gọi validate-key lại từ đầu |

### Xử lý kết quả Recheck

| Kết quả | Hành động Client |
|---|---|
| **Lỗi mạng** | Bỏ qua, retry sau 5 phút. **KHÔNG kick user ra** |
| `status: 0` (Key bị khóa/hết hạn) | Hiển thị dialog thông báo → Đóng App |
| `status: 1`, `update: null` | Tiếp tục bình thường |
| `status: 1`, `update.force_update: false` | Hiện popup nhẹ nhàng, user tự chọn |
| `status: 1`, `update.force_update: true` | Hiện dialog bắt buộc, chặn mọi thao tác |

> **Lưu ý:** Request payload cho recheck **hoàn toàn giống** lần đầu. Server không cần phân biệt đây là lần gọi đầu hay recheck.

### ⚠️ Đặc tả UX bắt buộc cho Force Update (QUAN TRỌNG)

Khi `force_update = true`, Client **PHẢI** đảm bảo các hành vi UX sau đây để
ngăn người dùng tiếp tục sử dụng phiên bản cũ:

#### Khi `force_update = true` (Bắt buộc cập nhật):

| Hành vi | Yêu cầu |
|---------|---------|
| **Cửa sổ chính** | **ẨN HOÀN TOÀN** (withdraw/hide) — người dùng không được nhìn thấy hoặc tương tác với giao diện phiên bản cũ |
| **Tất cả nút/chức năng** | **VÔ HIỆU HÓA** (disable/lock UI) |
| **Tác vụ đang chạy** | **DỪNG NGAY** (stop flag = true) |
| **Dialog cập nhật** | **Hiện dạng cửa sổ ĐỘC LẬP** — không bị ẩn theo cửa sổ chính |
| **Nút đóng dialog** | **KHÔNG CHO ĐÓNG** (chặn sự kiện close) |
| **Grab/Modal** | **BẮT BUỘC** — chặn tương tác với mọi cửa sổ khác trong ứng dụng |
| **Nội dung dialog** | Hiện phiên bản hiện tại → phiên bản mới, changelog, nút tải bản cập nhật |

```
┌─── force_update = true ───────────────────────────────────────┐
│                                                                │
│  1. Dừng mọi tác vụ đang chạy (download, scan, ...)          │
│  2. Khóa toàn bộ UI (disable tất cả các nút)                  │
│  3. ẨN HOÀN TOÀN cửa sổ chính (withdraw/hide)                │
│  4. Tạo dialog CẬP NHẬT BẮT BUỘC dạng cửa sổ ĐỘC LẬP       │
│     - Không cho đóng dialog (chặn nút X)                       │
│     - Chỉ có nút "Tải bản cập nhật bắt buộc"                  │
│     - Hiện thông báo: "Phiên bản hiện tại đã ngưng hỗ trợ"   │
│  5. Người dùng CHỈ THẤY dialog cập nhật, KHÔNG THẤY app cũ   │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

#### Khi `force_update = false` (Tùy chọn cập nhật):

| Hành vi | Yêu cầu |
|---------|---------|
| **Cửa sổ chính** | Vẫn hiện bình thường, người dùng tiếp tục sử dụng được |
| **Tất cả nút/chức năng** | Vẫn hoạt động bình thường |
| **Dialog cập nhật** | Hiện popup nhẹ, là cửa sổ con (transient) của cửa sổ chính |
| **Nút đóng dialog** | **CHO PHÉP ĐÓNG** — có nút "Bỏ qua lần này" |

```
┌─── force_update = false ──────────────────────────────────────┐
│                                                                │
│  1. App vẫn chạy bình thường                                  │
│  2. Hiện popup nhẹ nhàng thông báo có bản mới                 │
│     - Nút "Tải bản cập nhật" → mở link download               │
│     - Nút "Bỏ qua lần này" → đóng popup                      │
│  3. Người dùng tự quyết định có cập nhật hay không            │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

> **Lỗi UX thường gặp khi tích hợp:** Khi `force_update = true`, nếu chỉ hiện
> dialog mà **không ẩn cửa sổ chính**, người dùng vẫn nhìn thấy toàn bộ giao diện
> app cũ với các nút vẫn active phía sau dialog → vi phạm mục đích chặn sử dụng
> phiên bản cũ. **PHẢI ẩn hoàn toàn cửa sổ chính** khi force update.

---

## B5. Tích hợp cho Chrome Extension

### Cấu trúc thư mục Extension

```
my-shopee-extension/
├── manifest.json          ← Khai báo Extension
├── lib/
│   └── license.js         ← Thư viện xác thực bản quyền (TẠO MỚI)
├── popup/
│   ├── popup.html         ← Giao diện nhập key
│   └── popup.js           ← Logic xử lý kích hoạt
├── content/
│   └── content.js         ← Script chạy trên trang web mục tiêu
└── background/
    └── background.js      ← Service worker
```

### Bước 1: Khai báo quyền trong `manifest.json`

```json
{
  "manifest_version": 3,
  "name": "Shopee Scanner Extension",
  "version": "1.0.0",
  "permissions": [
    "storage",
    "downloads",
    "notifications",
    "alarms"
  ],
  "host_permissions": [
    "http://localhost:8000/*",
    "https://api.your-domain.com/*"
  ],
  "action": {
    "default_popup": "popup/popup.html"
  },
  "content_scripts": [
    {
      "matches": ["https://shopee.vn/*"],
      "js": ["lib/license.js", "content/content.js"]
    }
  ],
  "background": {
    "service_worker": "background/background.js"
  }
}
```

> **Giải thích quyền:**
> - `storage`: Lưu key, deviceId và thông tin update vào local storage.
> - `downloads`: Tải file `.zip` cập nhật từ Cloudflare R2 về máy người dùng.
> - `notifications`: Hiển thị thông báo Chrome khi có bản mới hoặc khi key bị khóa.
> - `alarms`: Hẹn giờ 30 phút tự động recheck key & version ngầm.

---

### Bước 2: Tạo thư viện xác thực `lib/license.js`

```javascript
// ═══════════════════════════════════════════════════════════════════
// license.js — Thư viện kiểm soát bản quyền & Check Update
// ═══════════════════════════════════════════════════════════════════

const LICENSE_CONFIG = {
    API_URL: "http://localhost:8000/api/validate-key",
    APP_ID: "APP001",
    STORAGE_KEY_LICENSE: "vcam_license_key",
    STORAGE_KEY_DEVICE: "vcam_device_id",
    STORAGE_KEY_EXPIRY: "vcam_key_expiry",
    STORAGE_KEY_UPDATE: "vcam_pending_update",
};

// Lấy version hiện tại động từ manifest.json
function getCurrentVersion() {
    return chrome.runtime.getManifest().version;
}

// Sinh hoặc lấy Device ID duy nhất
async function getOrCreateDeviceId() {
    return new Promise((resolve) => {
        chrome.storage.local.get([LICENSE_CONFIG.STORAGE_KEY_DEVICE], (result) => {
            if (result[LICENSE_CONFIG.STORAGE_KEY_DEVICE]) {
                resolve(result[LICENSE_CONFIG.STORAGE_KEY_DEVICE]);
            } else {
                const newId = "ext_" + crypto.randomUUID();
                chrome.storage.local.set(
                    { [LICENSE_CONFIG.STORAGE_KEY_DEVICE]: newId },
                    () => resolve(newId)
                );
            }
        });
    });
}

// Gọi API xác thực key + Check Version
async function validateLicenseKey(key) {
    const deviceId = await getOrCreateDeviceId();
    const currentVersion = getCurrentVersion();

    try {
        const response = await fetch(LICENSE_CONFIG.API_URL, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            body: JSON.stringify({
                key: key,
                app_id: LICENSE_CONFIG.APP_ID,
                deviceId: deviceId,
                current_version: currentVersion, // 👈 Gửi version hiện tại
                platform: "win",
            }),
        });

        const data = await response.json();
        return data; // { status: 1|0, message: "...", data: {...}, update: {...} }

    } catch (error) {
        console.error("[LICENSE] Lỗi kết nối máy chủ:", error);
        return {
            status: 0,
            message: "Không thể kết nối đến máy chủ xác thực bản quyền."
        };
    }
}

async function getSavedLicenseKey() {
    return new Promise((resolve) => {
        chrome.storage.local.get([LICENSE_CONFIG.STORAGE_KEY_LICENSE], (result) => {
            resolve(result[LICENSE_CONFIG.STORAGE_KEY_LICENSE] || null);
        });
    });
}

async function saveLicenseKey(key, expiryDate) {
    return new Promise((resolve) => {
        chrome.storage.local.set({
            [LICENSE_CONFIG.STORAGE_KEY_LICENSE]: key,
            [LICENSE_CONFIG.STORAGE_KEY_EXPIRY]: expiryDate,
        }, resolve);
    });
}

async function clearLicenseKey() {
    return new Promise((resolve) => {
        chrome.storage.local.remove([
            LICENSE_CONFIG.STORAGE_KEY_LICENSE,
            LICENSE_CONFIG.STORAGE_KEY_EXPIRY,
            LICENSE_CONFIG.STORAGE_KEY_UPDATE,
        ], resolve);
    });
}

async function checkLicense() {
    const savedKey = await getSavedLicenseKey();

    if (!savedKey) {
        return { isValid: false, message: "Chưa có key. Vui lòng kích hoạt bản quyền." };
    }

    const result = await validateLicenseKey(savedKey);

    if (result.status === 1) {
        await saveLicenseKey(savedKey, result.data?.expire_date);
        
        // Lưu thông tin update (nếu có)
        if (result.update && result.update.has_update) {
            await chrome.storage.local.set({ [LICENSE_CONFIG.STORAGE_KEY_UPDATE]: result.update });
        } else {
            await chrome.storage.local.remove([LICENSE_CONFIG.STORAGE_KEY_UPDATE]);
        }

        return { isValid: true, data: result.data, update: result.update, message: result.message };
    } else {
        await clearLicenseKey();
        return { isValid: false, message: result.message };
    }
}
```

---

### Bước 3: Tạo giao diện `popup/popup.html` (Có màn hình Update)

```html
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <style>
        body {
            width: 360px;
            padding: 16px;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: #1a1a2e;
            color: #eee;
            margin: 0;
        }
        h2 { text-align: center; color: #00d2ff; margin: 0 0 5px 0; font-size: 18px; }
        .subtitle { text-align: center; color: #888; font-size: 12px; margin-bottom: 16px; }
        input {
            width: 100%;
            padding: 10px;
            border: 1px solid #333;
            border-radius: 6px;
            background: #16213e;
            color: #fff;
            font-size: 14px;
            box-sizing: border-box;
            margin-bottom: 12px;
        }
        button {
            width: 100%;
            padding: 10px;
            border: none;
            border-radius: 6px;
            background: linear-gradient(135deg, #00d2ff, #3a7bd5);
            color: #fff;
            font-size: 14px;
            cursor: pointer;
            font-weight: bold;
        }
        button:hover { opacity: 0.9; }
        button:disabled { opacity: 0.5; cursor: not-allowed; }
        .error { color: #ff4757; font-size: 12px; margin-top: 8px; }

        /* Màn hình đã kích hoạt */
        .activated { text-align: center; }
        .activated .status-icon { font-size: 40px; margin-bottom: 8px; }
        .activated .info { font-size: 12px; color: #aaa; margin: 4px 0; }
        .activated .info strong { color: #00d2ff; }
        .btn-deactivate { background: #333 !important; margin-top: 12px; }

        /* Banner & Modal Cập nhật */
        .update-banner {
            background: #0f3460;
            border: 1px solid #00d2ff;
            border-radius: 8px;
            padding: 12px;
            margin-bottom: 14px;
            text-align: left;
        }
        .update-banner.force {
            background: #4a0e17;
            border-color: #ff4757;
        }
        .update-banner h4 { margin: 0 0 6px 0; font-size: 14px; color: #00d2ff; }
        .update-banner.force h4 { color: #ff4757; }
        .update-banner p { margin: 0 0 10px 0; font-size: 12px; color: #ccc; white-space: pre-line; }
        .btn-update {
            background: linear-gradient(135deg, #11998e, #38ef7d) !important;
            color: #000 !important;
        }
        .btn-update.force {
            background: linear-gradient(135deg, #ff416c, #ff4b2b) !important;
            color: #fff !important;
        }
        .guide-box {
            font-size: 11px;
            color: #aaa;
            background: rgba(255,255,255,0.05);
            padding: 8px;
            border-radius: 4px;
            margin-top: 8px;
            line-height: 1.4;
        }
    </style>
</head>
<body>
    <!-- Khối thông báo Cập nhật (Hiển thị ở trên cùng nếu có bản mới) -->
    <div id="update-box" style="display:none;" class="update-banner">
        <h4 id="update-title">💡 Đã có phiên bản mới!</h4>
        <p id="update-changelog"></p>
        <button id="btn-download-update" class="btn-update">📥 Tải bản cập nhật (.zip)</button>
        <div class="guide-box">
            💡 <strong>Hướng dẫn 5 giây:</strong> Tải file .zip ➔ Giải nén <strong>đè vào thư mục cũ</strong> ➔ Mở <code>chrome://extensions</code> bấm nút <strong>Reload (🔄)</strong>.
        </div>
    </div>

    <!-- Màn hình 1: Nhập Key -->
    <div id="screen-activate">
        <h2>🔑 Kích hoạt Bản quyền</h2>
        <p class="subtitle">Shopee Scanner Extension (v<span class="current-ver"></span>)</p>
        <input type="text" id="input-key" placeholder="Nhập Key bản quyền của bạn..." />
        <button id="btn-activate">Kích hoạt</button>
        <p id="msg-error" class="error"></p>
    </div>

    <!-- Màn hình 2: Đã kích hoạt -->
    <div id="screen-activated" style="display:none;" class="activated">
        <div class="status-icon">✅</div>
        <h2>Đã Kích hoạt!</h2>
        <p class="info">Phiên bản: <strong class="current-ver"></strong></p>
        <p class="info">Ứng dụng: <strong id="info-app"></strong></p>
        <p class="info">Hết hạn: <strong id="info-expiry"></strong></p>
        <button id="btn-deactivate" class="btn-deactivate">Hủy kích hoạt</button>
    </div>

    <script src="../lib/license.js"></script>
    <script src="popup.js"></script>
</body>
</html>
```

---

### Bước 4: Logic Xử lý kích hoạt & Tải Update trong `popup/popup.js`

```javascript
document.addEventListener("DOMContentLoaded", async () => {
    const inputKey = document.getElementById("input-key");
    const btnActivate = document.getElementById("btn-activate");
    const msgError = document.getElementById("msg-error");
    const screenActivate = document.getElementById("screen-activate");
    const screenActivated = document.getElementById("screen-activated");
    const updateBox = document.getElementById("update-box");
    const updateTitle = document.getElementById("update-title");
    const updateChangelog = document.getElementById("update-changelog");
    const btnDownloadUpdate = document.getElementById("btn-download-update");

    // Hiển thị version hiện tại
    const ver = getCurrentVersion();
    document.querySelectorAll(".current-ver").forEach(el => el.textContent = ver);

    // ── 1. Kiểm tra bản quyền & thông tin Update khi mở Popup ──
    const licenseStatus = await checkLicense();

    if (licenseStatus.isValid) {
        showActivatedScreen(licenseStatus.data);
    }

    // Xử lý hiển thị thông báo Update (nếu Server trả về có bản mới)
    if (licenseStatus.update && licenseStatus.update.has_update) {
        renderUpdateBanner(licenseStatus.update);
    }

    // ── 2. Logic Nút Tải Cập Nhật (.zip) ──
    function renderUpdateBanner(update) {
        updateBox.style.display = "block";
        const isForce = update.force_update;

        if (isForce) {
            updateBox.classList.add("force");
            updateTitle.textContent = `⚠️ Yêu cầu nâng cấp v${update.latest_version} (Bắt buộc)`;
            btnDownloadUpdate.classList.add("force");
            // Bắt buộc: Ẩn màn hình chính, ép tải update
            screenActivate.style.display = "none";
            screenActivated.style.display = "none";
        } else {
            updateTitle.textContent = `💡 Đã có phiên bản v${update.latest_version} mới!`;
        }

        updateChangelog.textContent = update.changelog || "Bản nâng cấp tối ưu tính năng và sửa lỗi.";

        btnDownloadUpdate.onclick = () => {
            // Tải file .zip từ Cloudflare R2 về máy
            chrome.downloads.download({
                url: update.download_url,
                filename: `Extension_v${update.latest_version}.zip`,
                saveAs: true
            }, () => {
                alert(`✅ Đã tải file Extension_v${update.latest_version}.zip!\n\nHướng dẫn cập nhật:\n1. Giải nén đè file zip vào thư mục Extension cũ.\n2. Mở chrome://extensions bấm nút Reload (🔄).`);
            });
        };
    }

    // ── 3. Nút Kích hoạt Key ──
    btnActivate.addEventListener("click", async () => {
        const key = inputKey.value.trim();
        if (!key) {
            msgError.textContent = "Vui lòng nhập key!";
            return;
        }

        btnActivate.disabled = true;
        btnActivate.textContent = "Đang kiểm tra...";
        msgError.textContent = "";

        const result = await validateLicenseKey(key);

        if (result.status === 1) {
            await saveLicenseKey(key, result.data?.expire_date);
            showActivatedScreen(result.data);

            if (result.update && result.update.has_update) {
                renderUpdateBanner(result.update);
            }
        } else {
            msgError.textContent = result.message || "Kích hoạt thất bại!";
            btnActivate.disabled = false;
            btnActivate.textContent = "Kích hoạt";
        }
    });

    // ── 4. Nút Hủy kích hoạt ──
    document.getElementById("btn-deactivate").addEventListener("click", async () => {
        await clearLicenseKey();
        screenActivated.style.display = "none";
        screenActivate.style.display = "block";
        updateBox.style.display = "none";
        inputKey.value = "";
        btnActivate.disabled = false;
        btnActivate.textContent = "Kích hoạt";
    });

    function showActivatedScreen(data) {
        screenActivate.style.display = "none";
        screenActivated.style.display = "block";
        document.getElementById("info-app").textContent = data?.app_id || "N/A";
        document.getElementById("info-expiry").textContent = data?.expire_date || "Vô thời hạn";
    }
});
```

---

### Bước 5: Khóa tính năng chính trong `content/content.js`

```javascript
// ═══════════════════════════════════════════════════════════════════
// content.js — Script chạy trên trang shopee.vn / tiktok.com
// ═══════════════════════════════════════════════════════════════════

(async function () {
    "use strict";

    const license = await checkLicense();

    if (!license.isValid) {
        console.warn("[VCAM EXTENSION] ⛔ Key chưa kích hoạt hoặc đã hết hạn.");
        return;
    }

    // Nếu là Force Update → Không chạy tính năng cũ
    if (license.update && license.update.force_update) {
        console.warn("[VCAM EXTENSION] ⚠️ Yêu cầu cập nhật phiên bản bắt buộc trước khi dùng.");
        alert(`[VCAM EXTENSION] Phiên bản v${getCurrentVersion()} đã hết hạn hỗ trợ. Vui lòng mở Extension và nhấn "Tải bản cập nhật" để tiếp tục!`);
        return;
    }

    console.log("[VCAM EXTENSION] ✅ Bản quyền hợp lệ. Khởi chạy tính năng...");
    initAppFeatures();

    function initAppFeatures() {
        // Mã nguồn tính năng cào dữ liệu của bạn ở đây...
    }
})();
```

---

### Bước 6: Recheck Key & Version định kỳ 30 phút trong `background/background.js`

```javascript
// ═══════════════════════════════════════════════════════════════════
// background.js — Service Worker
// Recheck Key + Version mỗi 30 phút
// ═══════════════════════════════════════════════════════════════════

importScripts('../lib/license.js');

const RECHECK_INTERVAL_MINUTES = 30;
const RETRY_INTERVAL_MINUTES = 5;
const MAX_CONSECUTIVE_FAILURES = 3;

let consecutiveFailures = 0;

chrome.runtime.onInstalled.addListener(() => {
    chrome.alarms.create("license-recheck", { periodInMinutes: RECHECK_INTERVAL_MINUTES });
});

chrome.runtime.onStartup.addListener(() => {
    chrome.alarms.create("license-recheck", { periodInMinutes: RECHECK_INTERVAL_MINUTES });
});

chrome.alarms.onAlarm.addListener(async (alarm) => {
    if (alarm.name !== "license-recheck") return;

    const result = await checkLicense();

    if (result === null || result.message?.includes("kết nối")) {
        consecutiveFailures++;
        if (consecutiveFailures <= MAX_CONSECUTIVE_FAILURES) {
            chrome.alarms.create("license-recheck", { delayInMinutes: RETRY_INTERVAL_MINUTES });
        } else {
            consecutiveFailures = 0;
            chrome.alarms.create("license-recheck", { delayInMinutes: RECHECK_INTERVAL_MINUTES });
        }
        return;
    }

    consecutiveFailures = 0;

    if (!result.isValid) {
        chrome.notifications.create({
            type: "basic",
            iconUrl: "icons/icon48.png",
            title: "⚠️ Key bản quyền không hợp lệ",
            message: result.message || "Key đã hết hạn hoặc bị khóa. Vui lòng gia hạn."
        });
        return;
    }

    // Phát hiện có bản cập nhật mới
    if (result.update && result.update.has_update) {
        const title = result.update.force_update
            ? "⚠️ Yêu cầu cập nhật bắt buộc"
            : "💡 Có bản cập nhật mới";
        const message = result.update.force_update
            ? `Phiên bản v${result.update.latest_version} bắt buộc cập nhật. Mở Extension để tải.`
            : `Phiên bản v${result.update.latest_version} đã sẵn sàng. Mở Extension để tải.`;

        chrome.notifications.create({
            type: "basic",
            iconUrl: "icons/icon48.png",
            title: title,
            message: message
        });
    }
});
```

---

## B6. Tích hợp cho Desktop App (C# / Electron / Python / Cross-Platform)

Khác với Extension, **Desktop App (.exe / .app / binary)** có khả năng **tự động cập nhật 100% ngầm** (Tự tải file từ Cloudflare R2 ➔ Tự gọi Updater ➔ Ghi đè file ➔ Tự mở lại phiên bản mới) mà người dùng không cần giải nén hay copy thủ công.

> 💡 **Hỗ trợ Đa nền tảng (Cross-Platform Win / Mac / Linux):**
> - **Luồng API & Recheck Timer 30p:** Giống nhau 100% trên mọi HĐH.
> - **Trường `platform` gửi lên Server:** Gửi `"win"`, `"mac"`, hoặc `"linux"` tùy HĐH của máy khách.
> - **Mã DeviceId:** 
>   - *Windows:* Registry `MachineGuid`
>   - *macOS:* Lệnh `ioreg -rd1 -c IOPlatformExpertDevice` (lấy `IOPlatformUUID`)
>   - *Linux:* Đọc tệp `/etc/machine-id`
> - **Script Auto-Updater ghi đè:**
>   - *Windows:* Script Batch `.bat` / PowerShell
>   - *macOS / Linux:* Shell script `.sh` (`unzip -o`, `chmod +x`, `open` / `./app`)

### Mã nguồn C# (.NET 6 / 8 / Framework 4.8+)

```csharp
using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Net.Http;
using System.Reflection;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace VcamLicenseClient
{
    // ── 1. CẤU HÌNH ─────────────────────────────────────────────
    public static class LicenseConfig
    {
        public const string ApiUrl = "http://localhost:8000/api/validate-key";
        public const string AppId = "CRAWL_TIKTOK"; // Thay bằng app_id của bạn
        public const string Platform = "win";
        public const int RecheckIntervalMinutes = 30; // Recheck định kỳ mỗi 30 phút
    }

    // ── 2. MODEL PHẢN HỒI API ──────────────────────────────────
    public class UpdateData
    {
        [JsonPropertyName("has_update")] public bool HasUpdate { get; set; }
        [JsonPropertyName("force_update")] public bool ForceUpdate { get; set; }
        [JsonPropertyName("current_version")] public string CurrentVersion { get; set; } = "";
        [JsonPropertyName("latest_version")] public string LatestVersion { get; set; } = "";
        [JsonPropertyName("download_url")] public string DownloadUrl { get; set; } = "";
        [JsonPropertyName("changelog")] public string? Changelog { get; set; }
    }

    public class LicenseData
    {
        [JsonPropertyName("id")] public int Id { get; set; }
        [JsonPropertyName("app_id")] public string AppId { get; set; } = "";
        [JsonPropertyName("key")] public string Key { get; set; } = "";
        [JsonPropertyName("expire_date")] public string? ExpireDate { get; set; }
        [JsonPropertyName("device_id")] public string? DeviceId { get; set; }
    }

    public class ApiResponse
    {
        [JsonPropertyName("status")] public int Status { get; set; }
        [JsonPropertyName("message")] public string Message { get; set; } = "";
        [JsonPropertyName("data")] public LicenseData? Data { get; set; }
        [JsonPropertyName("update")] public UpdateData? Update { get; set; }
    }

    // ── 3. LẤY MÃ THIẾT BỊ (Machine GUID) ──────────────────────
    public static class DeviceHelper
    {
        public static string GetDeviceId()
        {
            try
            {
                var key = Microsoft.Win32.Registry.LocalMachine.OpenSubKey(@"SOFTWARE\Microsoft\Cryptography");
                return key?.GetValue("MachineGuid")?.ToString() ?? Environment.MachineName;
            }
            catch
            {
                return Environment.MachineName + "-" + Environment.OSVersion;
            }
        }

        public static string GetCurrentVersion()
        {
            // Lấy version từ Assembly (AssemblyVersion / FileVersion)
            var version = Assembly.GetExecutingAssembly().GetName().Version;
            return $"{version.Major}.{version.Minor}.{version.Build}";
        }
    }

    // ── 4. DỊCH VỤ XÁC THỰC BẢN QUYỀN & CHECK UPDATE ──────────
    public class LicenseService
    {
        private readonly HttpClient _client = new() { Timeout = TimeSpan.FromSeconds(15) };
        private System.Threading.Timer? _recheckTimer;

        public async Task<(bool IsValid, ApiResponse Response)> ValidateAsync(string licenseKey)
        {
            var payload = new
            {
                key = licenseKey,
                app_id = LicenseConfig.AppId,
                deviceId = DeviceHelper.GetDeviceId(),
                current_version = DeviceHelper.GetCurrentVersion(), // 👈 Gửi version hiện tại
                platform = LicenseConfig.Platform
            };

            try
            {
                var json = JsonSerializer.Serialize(payload);
                var content = new StringContent(json, Encoding.UTF8, "application/json");
                var httpResp = await _client.PostAsync(LicenseConfig.ApiUrl, content);
                var body = await httpResp.Content.ReadAsStringAsync();
                var result = JsonSerializer.Deserialize<ApiResponse>(body);

                if (result == null)
                    return (false, new ApiResponse { Status = 0, Message = "Phản hồi không hợp lệ từ máy chủ." });

                return (result.Status == 1, result);
            }
            catch (Exception ex)
            {
                return (false, new ApiResponse { Status = 0, Message = $"Lỗi kết nối: {ex.Message}" });
            }
        }

        // Khởi động Timer Recheck 30 phút một lần
        public void StartRecheckTimer(string licenseKey, Action<ApiResponse> onKeyInvalid, Action<UpdateData> onUpdateFound)
        {
            _recheckTimer = new System.Threading.Timer(async _ =>
            {
                var (isValid, response) = await ValidateAsync(licenseKey);

                if (!isValid)
                {
                    // Key bị khóa hoặc hết hạn giữa phiên
                    onKeyInvalid?.Invoke(response);
                }
                else if (response.Update != null && response.Update.HasUpdate)
                {
                    // Phát hiện có bản cập nhật mới
                    onUpdateFound?.Invoke(response.Update);
                }
            }, null, TimeSpan.FromMinutes(LicenseConfig.RecheckIntervalMinutes), TimeSpan.FromMinutes(LicenseConfig.RecheckIntervalMinutes));
        }
    }

    // ── 5. AUTO UPDATER: TỰ ĐỘNG TẢI & GHI ĐÈ FILE (.EXE) ──────
    public static class AutoUpdater
    {
        public static async Task ExecuteUpdateAsync(UpdateData update)
        {
            try
            {
                string tempPath = Path.Combine(Path.GetTempPath(), $"update_v{update.LatestVersion}.zip");
                string appDir = AppDomain.CurrentDomain.BaseDirectory;
                string exePath = Process.GetCurrentProcess().MainModule?.FileName ?? "";

                // Bước 1: Tải file update từ Cloudflare R2 về thư mục tạm %TEMP%
                using (var client = new HttpClient())
                {
                    var data = await client.GetByteArrayAsync(update.DownloadUrl);
                    await File.WriteAllBytesAsync(tempPath, data);
                }

                // Bước 2: Tạo script batch tạm để thực hiện ghi đè và khởi động lại app
                string scriptPath = Path.Combine(Path.GetTempPath(), "update_installer.bat");
                string batContent = $@"
@echo off
timeout /t 2 /nobreak > NUL
powershell -Command ""Expand-Archive -Path '{tempPath}' -DestinationPath '{appDir}' -Force""
del /f /q ""{tempPath}""
start "" "" ""{exePath}""
del /f /q ""%~f0""
";
                await File.WriteAllTextAsync(scriptPath, batContent, Encoding.Default);

                // Bước 3: Chạy script installer ngầm và tắt App hiện tại
                Process.Start(new ProcessStartInfo
                {
                    FileName = "cmd.exe",
                    Arguments = $"/c \"{scriptPath}\"",
                    WindowStyle = ProcessWindowStyle.Hidden,
                    CreateNoWindow = true
                });

                // Tắt App chính để giải phóng file cho script ghi đè
                Application.Exit();
                Environment.Exit(0);
            }
            catch (Exception ex)
            {
                MessageBox.Show($"Lỗi tự động cập nhật: {ex.Message}\nVui lòng tải thủ công tại: {update.DownloadUrl}", "Lỗi Cập Nhật", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }
    }

    // ── 6. TÍCH HỢP VÀO FORM CHÍNH (MAINFORM) ──────────────────
    public partial class MainForm : Form
    {
        private readonly LicenseService _licenseService = new();
        private string _userKey = "";

        private async void MainForm_Load(object sender, EventArgs e)
        {
            _userKey = Properties.Settings.Default.LicenseKey;

            if (string.IsNullOrEmpty(_userKey))
            {
                ShowActivationForm();
                return;
            }

            // Gọi kiểm tra khi mở App
            var (isValid, response) = await _licenseService.ValidateAsync(_userKey);

            if (!isValid)
            {
                MessageBox.Show($"Lỗi bản quyền: {response.Message}", "Thông Báo", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                ShowActivationForm();
                return;
            }

            // Xử lý bản cập nhật nếu có từ lần mở đầu tiên
            if (response.Update != null && response.Update.HasUpdate)
            {
                await HandleUpdateCheck(response.Update);
            }

            // Đăng ký Timer Recheck 30 phút một lần
            _licenseService.StartRecheckTimer(_userKey, 
                onKeyInvalid: (res) =>
                {
                    Invoke(new Action(() =>
                    {
                        MessageBox.Show($"Phiên làm việc đã kết thúc: {res.Message}", "Thông Báo", MessageBoxButtons.OK, MessageBoxIcon.Error);
                        Application.Exit();
                    }));
                },
                onUpdateFound: async (upd) =>
                {
                    Invoke(new Action(async () =>
                    {
                        await HandleUpdateCheck(upd);
                    }));
                }
            );
        }

        private async Task HandleUpdateCheck(UpdateData update)
        {
            if (update.ForceUpdate)
            {
                MessageBox.Show($"Bản cập nhật v{update.LatestVersion} là BẮT BUỘC.\n\nNội dung thay đổi:\n{update.Changelog}\n\nỨng dụng sẽ tự động tải và cài đặt ngay bây giờ.", "Yêu Cầu Cập Nhật Bắt Buộc", MessageBoxButtons.OK, MessageBoxIcon.Information);
                await AutoUpdater.ExecuteUpdateAsync(update);
            }
            else
            {
                var choice = MessageBox.Show($"Đã có phiên bản mới v{update.LatestVersion}!\n\nNội dung thay đổi:\n{update.Changelog}\n\nBạn có muốn cập nhật ngay không?", "Cập Nhật Phần Mềm", MessageBoxButtons.YesNo, MessageBoxIcon.Question);
                if (choice == DialogResult.Yes)
                {
                    await AutoUpdater.ExecuteUpdateAsync(update);
                }
            }
        }

        private void ShowActivationForm()
        {
            // Hiển thị Form nhập key bản quyền...
        }
    }
}
```

---

## B7. Tích hợp cho Python

### Module `license_client.py`

```python
"""
license_client.py — Module kiểm soát bản quyền cho ứng dụng Python
Tích hợp với hệ thống VCAM API_KEY (kèm Auto-Update Check)
"""

import json
import os
import platform
import subprocess
import requests
from pathlib import Path

# ═══════════════════════════════════════════════════════════════════
# CẤU HÌNH — THAY ĐỔI THEO DỰ ÁN CỦA BẠN
# ═══════════════════════════════════════════════════════════════════
API_URL = "http://localhost:8000/api/validate-key"
APP_ID = "PYTHON_YOUR_APP"   # Thay bằng app_id đã đăng ký trong bảng apps
APP_VERSION = "1.0.0"        # Phiên bản hiện tại của ứng dụng (SemVer)
LICENSE_FILE = Path.home() / ".vcam_license"  # Tệp lưu key trên máy người dùng
REQUEST_TIMEOUT = 15


# ═══════════════════════════════════════════════════════════════════
# XÁC ĐỊNH NỀN TẢNG (Platform)
# ═══════════════════════════════════════════════════════════════════
def get_platform_name() -> str:
    """Trả về tên nền tảng để server xác định download_url phù hợp."""
    s = platform.system()
    if s == "Windows":
        return "win"
    elif s == "Darwin":
        return "mac"
    return "linux"


# ═══════════════════════════════════════════════════════════════════
# LẤY MÃ ĐỊNH DANH THIẾT BỊ
# ═══════════════════════════════════════════════════════════════════
def get_device_id() -> str:
    """Lấy mã phần cứng duy nhất của máy tính."""
    system = platform.system()
    try:
        if system == "Windows":
            result = subprocess.run(
                ["reg", "query", r"HKLM\SOFTWARE\Microsoft\Cryptography",
                 "/v", "MachineGuid"],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.strip().split("\n"):
                if "MachineGuid" in line:
                    return line.split()[-1]

        elif system == "Linux":
            with open("/etc/machine-id", "r") as f:
                return f.read().strip()

        elif system == "Darwin":  # macOS
            result = subprocess.run(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.split("\n"):
                if "IOPlatformUUID" in line:
                    return line.split('"')[-2]
    except Exception:
        pass

    return f"{platform.node()}-{platform.machine()}-{system}"


# ═══════════════════════════════════════════════════════════════════
# GỌI API XÁC THỰC (KÈM CHECK UPDATE)
# ═══════════════════════════════════════════════════════════════════
def validate_key(license_key: str) -> dict:
    """
    Gửi key lên server API_KEY để xác thực.
    Payload kèm current_version + platform để server kiểm tra cập nhật.
    Trả về dict: { "status": 1|0, "message": "...", "data": {...}, "update": {...} }
    """
    try:
        response = requests.post(
            API_URL,
            json={
                "key": license_key,
                "app_id": APP_ID,
                "deviceId": get_device_id(),
                "current_version": APP_VERSION,     # ← Server so sánh với latest
                "platform": get_platform_name(),     # ← Server trả download_url đúng OS
            },
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=REQUEST_TIMEOUT,
        )
        return response.json()
    except requests.exceptions.ConnectionError:
        return {"status": 0, "message": "Không thể kết nối máy chủ bản quyền."}
    except requests.exceptions.Timeout:
        return {"status": 0, "message": "Hết thời gian kết nối."}
    except Exception as e:
        return {"status": 0, "message": f"Lỗi: {str(e)}"}


# ═══════════════════════════════════════════════════════════════════
# LƯU / ĐỌC KEY TỪ TỆP CỤC BỘ
# ═══════════════════════════════════════════════════════════════════
def save_key(key: str):
    LICENSE_FILE.write_text(key)

def load_key() -> str | None:
    if LICENSE_FILE.exists():
        return LICENSE_FILE.read_text().strip()
    return None

def clear_key():
    if LICENSE_FILE.exists():
        LICENSE_FILE.unlink()
```

### Xử lý Update Response trong GUI (Desktop App)

Sau khi `validate_key()` trả về thành công (`status: 1`), cần kiểm tra trường `update`
trong response và xử lý theo đặc tả UX ở mục B4:

```python
import webbrowser

def handle_update_response(app_window, update_info: dict | None):
    """
    Xử lý thông tin cập nhật từ server.
    app_window: cửa sổ chính của ứng dụng (tkinter/customtkinter)
    update_info: dict từ response["update"] (có thể None)
    """
    if not update_info or not isinstance(update_info, dict):
        return
    if not update_info.get("has_update"):
        return

    is_force = bool(update_info.get("force_update"))
    latest_ver = update_info.get("latest_version", "?")
    current_ver = update_info.get("current_version", APP_VERSION)
    changelog = update_info.get("changelog") or ""
    download_url = update_info.get("download_url", "")

    show_update_dialog(app_window, is_force, current_ver, latest_ver,
                       changelog, download_url)


def show_update_dialog(app_window, is_force, current_ver, latest_ver,
                        changelog, download_url):
    """
    Hiện dialog cập nhật phiên bản.

    ⚠️ QUY TẮC UX BẮT BUỘC (xem chi tiết tại mục B4):
    - force_update=True:  ẨN cửa sổ chính, dialog ĐỘC LẬP, KHÔNG cho đóng
    - force_update=False: App vẫn chạy, dialog dạng popup, CHO PHÉP đóng
    """

    if is_force:
        # ── FORCE UPDATE: Ẩn hoàn toàn app, chỉ hiện dialog ──
        app_window._stop_flag = True          # Dừng tác vụ đang chạy
        lock_ui(app_window)                   # Vô hiệu hóa tất cả nút
        app_window.withdraw()                 # ẨN HOÀN TOÀN cửa sổ chính

    # Tạo dialog
    import customtkinter as ctk
    dlg = ctk.CTkToplevel(app_window)
    dlg.title("⚠️ Yêu cầu cập nhật bắt buộc" if is_force else "Cập nhật phiên bản")
    dlg.geometry("520x420")
    dlg.resizable(False, False)

    if is_force:
        dlg.transient("")           # Dialog ĐỘC LẬP — không bị ẩn theo app
        dlg.protocol("WM_DELETE_WINDOW", lambda: None)  # Không cho đóng
        dlg.grab_set()              # Chặn tương tác cửa sổ khác
    else:
        dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)

    # ── Nội dung dialog ──
    ctk.CTkLabel(dlg, text="⚠️" if is_force else "💡", font=("", 36)).pack(pady=(20, 4))
    ctk.CTkLabel(dlg,
        text="Yêu cầu cập nhật bắt buộc!" if is_force else "Đã có phiên bản mới!",
        font=("", 18, "bold")).pack(pady=(0, 4))
    ctk.CTkLabel(dlg,
        text=f"v{current_ver}  →  v{latest_ver}",
        font=("", 14, "bold")).pack(pady=(0, 12))

    if changelog:
        ctk.CTkLabel(dlg, text=changelog, font=("", 11),
                     wraplength=440, justify="left").pack(padx=24, pady=(0, 12))

    # ── Nút download ──
    def do_download():
        if download_url:
            webbrowser.open(download_url)

    ctk.CTkButton(dlg,
        text="📥 Tải bản cập nhật bắt buộc" if is_force else "📥 Tải bản cập nhật",
        width=360, height=42, font=("", 14, "bold"),
        command=do_download).pack(pady=(4, 6))

    # ── Nút bỏ qua (chỉ khi KHÔNG phải force) ──
    if not is_force:
        ctk.CTkButton(dlg, text="Bỏ qua lần này",
            width=360, height=34, font=("", 12),
            command=dlg.destroy).pack(pady=(0, 8))
    else:
        ctk.CTkLabel(dlg,
            text="⚠ Phiên bản hiện tại đã ngưng hỗ trợ.\n"
                 "Vui lòng tải bản mới để tiếp tục sử dụng.",
            font=("", 11)).pack(pady=(0, 10))

    dlg.after(100, lambda: (dlg.lift(), dlg.focus_force()))
```

### Tích hợp Recheck Timer (30 phút)

```python
import threading

class RecheckTimer:
    """
    Timer gọi lại validate_key mỗi 30 phút.
    Xử lý kết quả: key hết hạn → đóng app, có update → hiện dialog.
    """
    RECHECK_INTERVAL = 1800   # 30 phút
    RETRY_INTERVAL = 300      # 5 phút khi lỗi mạng
    MAX_RETRIES = 3

    def __init__(self, app_window, license_key: str):
        self.app = app_window
        self.key = license_key
        self._timer_id = None
        self._retry_count = 0

    def start(self):
        self._schedule(self.RECHECK_INTERVAL)

    def stop(self):
        if self._timer_id:
            self.app.after_cancel(self._timer_id)
            self._timer_id = None

    def _schedule(self, delay_seconds):
        self._timer_id = self.app.after(delay_seconds * 1000, self._do_recheck)

    def _do_recheck(self):
        def work():
            result = validate_key(self.key)
            self.app.after(0, lambda: self._handle_result(result))
        threading.Thread(target=work, daemon=True).start()

    def _handle_result(self, result):
        if result.get("status") == 1:
            # Key hợp lệ — kiểm tra update
            self._retry_count = 0
            handle_update_response(self.app, result.get("update"))
            self._schedule(self.RECHECK_INTERVAL)

        elif result.get("status") == 0:
            # Key bị khóa/hết hạn → thông báo + đóng app
            show_error_and_exit(self.app, result.get("message", "Key không hợp lệ"))

        else:
            # Lỗi mạng → retry sớm hơn
            self._retry_count += 1
            if self._retry_count <= self.MAX_RETRIES:
                self._schedule(self.RETRY_INTERVAL)
            else:
                self._retry_count = 0
                self._schedule(self.RECHECK_INTERVAL)
```

### Luồng sử dụng tổng thể

```python
# Trong class App chính (customtkinter / tkinter):

class MyApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(f"My App v{APP_VERSION}")
        self.withdraw()    # Ẩn app cho đến khi key hợp lệ
        # ... tạo UI ...
        self._check_license()

    def _check_license(self):
        key = load_key()
        if key:
            result = validate_key(key)
            if result["status"] == 1:
                self._on_license_valid(result)
                return
        self._show_activation_dialog()

    def _on_license_valid(self, result):
        # 1. Hiện cửa sổ chính
        self.deiconify()

        # 2. Xử lý update (nếu có)
        #    ⚠️ Nếu force_update=True, hàm này sẽ tự ẩn lại cửa sổ chính
        handle_update_response(self, result.get("update"))

        # 3. Khởi động recheck timer
        self._recheck = RecheckTimer(self, load_key())
        self._recheck.start()
```

---




## B8. Tích hợp cho Mobile App (Flutter / React Native)

### Flutter (Dart)

```dart
import 'dart:convert';
import 'dart:io';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import 'package:device_info_plus/device_info_plus.dart';

class LicenseService {
  // ── CẤU HÌNH ──
  static const String apiUrl = 'http://YOUR_SERVER/api/validate-key';
  static const String appId = 'MOBILE_YOUR_APP';  // Thay bằng app_id của bạn
  static const String _storageKey = 'vcam_license_key';

  // ── Lấy Device ID ──
  static Future<String> getDeviceId() async {
    final deviceInfo = DeviceInfoPlugin();
    if (Platform.isAndroid) {
      final info = await deviceInfo.androidInfo;
      return info.id;  // Android ID
    } else if (Platform.isIOS) {
      final info = await deviceInfo.iosInfo;
      return info.identifierForVendor ?? 'unknown';
    }
    return 'unknown';
  }

  // ── Xác thực key ──
  static Future<Map<String, dynamic>> validateKey(String key) async {
    try {
      final deviceId = await getDeviceId();
      final response = await http.post(
        Uri.parse(apiUrl),
        headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
        body: jsonEncode({
          'key': key,
          'app_id': appId,
          'deviceId': deviceId,
        }),
      ).timeout(const Duration(seconds: 15));

      return jsonDecode(response.body);
    } catch (e) {
      return {'status': 0, 'message': 'Không thể kết nối máy chủ: $e'};
    }
  }

  // ── Lưu / Đọc / Xóa key ──
  static Future<void> saveKey(String key) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_storageKey, key);
  }

  static Future<String?> loadKey() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getString(_storageKey);
  }

  static Future<void> clearKey() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_storageKey);
  }

  // ── Kiểm tra đầy đủ ──
  static Future<Map<String, dynamic>> checkLicense() async {
    final savedKey = await loadKey();
    if (savedKey == null) {
      return {'isValid': false, 'message': 'Chưa kích hoạt bản quyền'};
    }
    final result = await validateKey(savedKey);
    if (result['status'] == 1) {
      return {'isValid': true, 'data': result['data']};
    } else {
      await clearKey();
      return {'isValid': false, 'message': result['message']};
    }
  }
}
```

### Sử dụng trong Flutter App

```dart
// Ở màn hình chính (main.dart hoặc SplashScreen):
class SplashScreen extends StatefulWidget { ... }

class _SplashScreenState extends State<SplashScreen> {
  @override
  void initState() {
    super.initState();
    _checkLicense();
  }

  Future<void> _checkLicense() async {
    final result = await LicenseService.checkLicense();
    if (result['isValid'] == true) {
      Navigator.pushReplacementNamed(context, '/home');  // Vào app chính
    } else {
      Navigator.pushReplacementNamed(context, '/activate');  // Vào form nhập key
    }
  }
}
```

---

## B9. Lưu ý Bảo mật khi Tích hợp

### 1. Không tin tưởng hoàn toàn vào Client

Vì mã nguồn Client (JavaScript, Dart) có thể bị đọc và sửa bởi người dùng nâng cao, hãy áp dụng các biện pháp sau:

| Biện pháp | Mô tả | Mức độ bảo vệ |
|-----------|-------|---------------|
| **Làm rối mã nguồn (Obfuscation)** | Sử dụng `javascript-obfuscator`, ProGuard (Android), hay Webpack để biến mã thành khó đọc | ⭐⭐ Trung bình |
| **Kiểm tra định kỳ** | Không chỉ kiểm tra 1 lần khi khởi động, mà kiểm tra lại mỗi vài giờ | ⭐⭐⭐ Tốt |
| **Xác thực kép qua Server riêng** | Nếu app có backend riêng, backend đó gọi API_KEY để kiểm tra thay vì Client gọi trực tiếp | ⭐⭐⭐⭐⭐ Rất tốt |

### 2. Kiến trúc Xác thực Kép (Khuyên dùng cho Extension cào dữ liệu)

```
┌──────────────┐     ┌─────────────────────┐     ┌──────────────────┐
│  Extension   │────▶│  Crawler Server     │────▶│  API_KEY Server  │
│  (Client)    │     │  (Backend của bạn)   │     │  (vcamapi)       │
│              │     │                     │     │                  │
│ Gửi kèm     │     │ Nhận key từ Client  │     │ Trả status 1/0  │
│ license_key  │     │ Gọi API validate    │     │                  │
│ trong mỗi   │     │ Chỉ xử lý nếu      │     │                  │
│ request      │     │ key hợp lệ          │     │                  │
└──────────────┘     └─────────────────────┘     └──────────────────┘
```

Với mô hình này, dù người dùng bẻ khóa Extension để bỏ qua kiểm tra ở Client, họ vẫn không thể lấy dữ liệu vì Crawler Server của bạn sẽ từ chối request không có key hợp lệ.

### 3. Cấu hình CORS trên Production

Hiện tại file `config/cors.php` cho phép mọi nguồn gốc (`'allowed_origins' => ['*']`). Trên production, hãy giới hạn chỉ cho phép các domain của bạn:

```php
'allowed_origins' => [
    'https://your-admin-domain.com',
    'chrome-extension://YOUR_EXTENSION_ID',
],
```

---

# PHẦN C — CHECKLIST TRIỂN KHAI PRODUCTION

## C1. Server & Backend

- [ ] Cập nhật `.env`: `APP_ENV=production`, `APP_DEBUG=false`
- [ ] Tạo mới `APP_KEY` (không dùng lại key từ môi trường dev)
- [ ] Sử dụng tài khoản MySQL riêng (không dùng `root`)
- [ ] Cập nhật `DOMAIN_FRONTEND` thành tên miền production
- [ ] Thêm tên miền vào `SANCTUM_STATEFUL_DOMAINS`
- [ ] Cấu hình web server (Nginx/Apache) + PHP-FPM
- [ ] Chạy `php artisan config:cache` và `php artisan route:cache`
- [ ] Chạy `php artisan migrate --force` trên production (bao gồm bảng `app_versions`)
- [ ] Cấu hình SSL/TLS (HTTPS) cho cả Frontend và Backend
- [ ] Cài đặt Supervisor để duy trì `queue:work`

## C2. Admin Dashboard

- [ ] Build frontend: `cd vcamadmin && npm run build`
- [ ] Phục vụ `vcamadmin/dist/` qua Nginx hoặc CDN
- [ ] Cập nhật `VITE_URL_API` trỏ về API production
- [ ] Kiểm tra menu **Quản lý phiên bản** hiển thị và hoạt động

## C3. Client App & Tích hợp

- [ ] Cập nhật `LICENSE_CONFIG.API_URL` trong mã nguồn Client trỏ về production
- [ ] Tích hợp gửi `current_version` và `platform` trong request `/api/validate-key`
- [ ] Tích hợp cơ chế **Recheck Timer 30 phút** (xem mục B4)
- [ ] Tích hợp xử lý trường `update` trong response (hiển thị dialog cập nhật)
- [ ] Tích hợp luồng tải file từ `download_url` và auto-update

## C4. Lưu trữ File cài đặt (Cloudflare R2)

- [ ] Tạo Cloudflare R2 Bucket (VD: `vcam-releases`)
- [ ] Bật Public Access và gắn Custom Domain (VD: `download.vcam.com`)
- [ ] Upload file cài đặt theo cấu trúc: `apps/{app_id}/{version}/{filename}`
- [ ] Dán Direct Link vào trường `download_url` khi tạo phiên bản trên Admin Dashboard

## C5. Bảo mật

- [ ] Giới hạn CORS trong `config/cors.php` (xóa wildcard `*`)
- [ ] Xóa thông tin email/password mẫu khỏi `.env.example`
- [ ] Làm rối mã nguồn Client (obfuscation) nếu cần

---

# PHẦN D — QUY TRÌNH TỰ ĐỘNG BUILD & ĐẨY BẢN RELEASE LÊN GITHUB PUBLIC REPOSITORY

Tài liệu này hướng dẫn cách thiết lập tự động hóa 100%: Khi bạn tạo và push **Git Tag** (ví dụ `crawltiktok-v2.1.4`, `tagsp-v1.2.1`) ở repository mã nguồn **Private**, hệ thống GitHub Actions sẽ tự động biên dịch ứng dụng (`.zip` cho Windows/Linux) và đăng bài **Release** đính kèm file nén lên repository **Public** chung (`VersionManagement`) để phân phối cho người dùng & dán link vào Admin Dashboard `vcamadmin`.

---

## D1. Mô Hình Hoạt Động Trung Tâm

Tất cả các dự án mã nguồn (Private) đều sử dụng **1 Repo Public duy nhất** (`VersionManagement`) làm trung tâm phân phối các bản build release. Để tránh trùng lặp release giữa các ứng dụng, mọi Git Tag phải có **prefix tên app**.

```
┌────────────────────────────────┐
│  Repo Private: CrawlTiktok 🔒  │──▶ git push origin crawltiktok-v2.1.4
└────────────────────────────────┘                │
                                                  │
┌────────────────────────────────┐                │
│  Repo Private: TagSP 🔒        │──▶ git push origin tagsp-v1.2.1
└────────────────────────────────┘                │
                                                  ▼
┌────────────────────────────────┐    ┌─────────────────────────────────────────┐
│  Repo Private: ZaloCRM 🔒      │──▶ │  Repo Public: VersionManagement 🌐      │
└────────────────────────────────┘    │                                         │
                                      │  Release crawltiktok-v2.1.4             │
                                      │    ├─ CrawlTikTok_v2.1.4_Windows.zip    │
                                      │    └─ CrawlTikTok_v2.1.4_Linux.zip      │
                                      │                                         │
                                      │  Release tagsp-v1.2.1                   │
                                      │    └─ TagSP_v1.2.1_Windows.zip         │
                                      └─────────────────────────────────────────┘
```

---

## D2. Quy Ước Đặt Tên Tag (Prefix Bắt Buộc)

```
⟨tên-app⟩-v⟨X.Y.Z⟩
```

| Dự án | Ví dụ Tag | Pattern Trigger Workflow |
|-------|-----------|--------------------------|
| **CrawlTikTok** | `crawltiktok-v2.1.4` hoặc `v2.1.4` | `tags: [ "v*", "crawltiktok-v*" ]` |
| **TagSP** | `tagsp-v1.2.1` | `tags: [ "tagsp-v*" ]` |
| **ZaloCRM** | `zalocrm-v3.0.1` | `tags: [ "zalocrm-v*" ]` |

---

## D3. Hướng Dẫn Thiết Lập Từng Bước

### Bước 0: Khởi tạo Repo Public (Chỉ làm 1 lần duy nhất)

> ⚠️ **QUAN TRỌNG:** Repo public (`VersionManagement`) **phải có ít nhất 1 commit** trước khi GitHub API cho phép tạo Release/Tag trên đó. Nếu repo còn trống (trang "Quick setup"), mọi lệnh upload Release sẽ **thất bại âm thầm**.

```bash
git clone https://github.com/eirapham2410/VersionManagement.git
cd VersionManagement
echo "# 📦 Version Management — Bản phát hành ứng dụng" > README.md
git add README.md
git commit -m "init: initialize repository for release hosting"
git branch -M main
git push -u origin main
```

---

### Bước 1: Tạo GitHub Personal Access Token (Classic)

`GITHUB_TOKEN` mặc định của GitHub Actions **chỉ có quyền ghi trên repo hiện tại**. Để tự động tạo Release sang repo public khác, ta cần tạo 1 Personal Access Token (PAT).

1. Truy cập: 👉 **https://github.com/settings/tokens/new**
2. Điền thông tin:
   - **Note**: `Auto Release to VersionManagement`
   - **Expiration**: `No expiration`
   - **Select scopes**: ✅ Tích chọn vào mục **`repo`** (Full control of private and public repositories).
3. Bấm **Generate token** và sao chép ngay chuỗi token `ghp_xxxxxxxxxxxxxxxxxxxx` (chỉ hiển thị 1 lần).

---

### Bước 2: Thêm Secret vào Repo Private của từng Dự án

Vào phần Cài đặt Secret của từng repo mã nguồn private:
1. Truy cập: `https://github.com/⟨owner⟩/⟨private-repo⟩/settings/secrets/actions`
2. Bấm **New repository secret**:
   - **Name**: `PUBLIC_REPO_TOKEN`
   - **Secret**: Dán chuỗi Token `ghp_...` tạo ở Bước 1.

---

### Bước 3: Cấu hình Workflow File (`.github/workflows/build.yml`)

Trong file workflow `.github/workflows/build.yml` của repo mã nguồn private, sử dụng **GitHub CLI (`gh`)** để tạo và tải file nén lên bài Release của repo public.

> ⚠️ **Tại sao dùng GitHub CLI (`gh`) thay vì `softprops/action-gh-release`?**  
> Action `softprops/action-gh-release@v2` (từ v2.5.0+) có bug regression gây lỗi `Too many retries` khi đẩy release cross-repo. GitHub CLI (`gh`) là công cụ chính thức của GitHub, hoạt động ổn định 100%.

```yaml
name: Build and Package App

on:
  push:
    tags: [ "v*", "crawltiktok-v*" ]  # Thay bằng pattern tag của dự án
  workflow_dispatch:

permissions:
  contents: write

jobs:
  build:
    name: Build for ${{ matrix.os }}
    runs-on: ${{ matrix.os }}
    strategy:
      fail-fast: false
      matrix:
        os: [windows-latest, ubuntu-latest]

    steps:
      - name: Checkout Code
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.10"
          cache: "pip"

      - name: Install Python Dependencies
        run: |
          python -m pip install --upgrade pip
          pip install pyinstaller
          pip install -r requirements.txt

      - name: Build Executable with PyInstaller
        run: |
          pyinstaller --noconfirm app.spec

      - name: Package Dist Folder to ZIP (Windows)
        if: matrix.os == 'windows-latest'
        shell: pwsh
        run: |
          $ver = "${{ github.ref_name }}"
          $archive = "App_${ver}_Windows.zip"
          Compress-Archive -Path dist/App -DestinationPath $archive
          Add-Content -Path $env:GITHUB_ENV -Value "ARCHIVE_NAME=$archive"

      - name: Package Dist Folder to ZIP (Linux)
        if: matrix.os == 'ubuntu-latest'
        shell: bash
        run: |
          ver="${{ github.ref_name }}"
          archive="App_${ver}_Linux.zip"
          zip -r "$archive" dist/App
          echo "ARCHIVE_NAME=$archive" >> $GITHUB_ENV

      # ── Upload lên Release của repo public VersionManagement bằng GitHub CLI ──
      - name: Create or Upload to Release (VersionManagement)
        shell: bash
        env:
          GH_TOKEN: ${{ secrets.PUBLIC_REPO_TOKEN }}
        run: |
          TAG="${{ github.ref_name }}"
          REPO="eirapham2410/VersionManagement"
          FILE="${{ env.ARCHIVE_NAME }}"

          # Kiểm tra Release đã tồn tại chưa
          if gh release view "$TAG" --repo "$REPO" > /dev/null 2>&1; then
            echo "✅ Release $TAG đã tồn tại, upload thêm file..."
            gh release upload "$TAG" "$FILE" --repo "$REPO" --clobber
          else
            echo "📦 Tạo Release $TAG mới và upload file..."
            gh release create "$TAG" "$FILE" \
              --repo "$REPO" \
              --title "Release $TAG" \
              --notes "Bản phát hành $TAG"
          fi
```

---

## D4. Quy Trình Phát Hành Bản Mới Hàng Ngày

Khi có bản cập nhật mới, nhà phát triển chỉ cần chạy các lệnh trong terminal:

```bash
# 1. Commit và Push mã nguồn
git add .
git commit -m "feat: release version 2.1.4"
git push origin main

# 2. Gắn Git Tag theo quy ước prefix và Push Tag
git tag crawltiktok-v2.1.4
git push origin crawltiktok-v2.1.4
```

Sau khi push tag, GitHub Actions tự chạy -> tự biên dịch -> tự xuất bản bài Release lên `https://github.com/eirapham2410/VersionManagement/releases`.

---

## D5. Cấu Trúc URL Tải Trực Tiếp Dành Cho Admin Dashboard

Link download trực tiếp thu được để dán vào trường `download_url` ở trang **Quản lý phiên bản (`vcamadmin`)**:

```
https://github.com/eirapham2410/VersionManagement/releases/download/⟨tag⟩/⟨File_Archive_Name⟩.zip
```

**Ví dụ:**
- `https://github.com/eirapham2410/VersionManagement/releases/download/crawltiktok-v2.1.4/CrawlTikTok_v2.1.4_Windows.zip`
- `https://github.com/eirapham2410/VersionManagement/releases/download/crawltiktok-v2.1.4/CrawlTikTok_v2.1.4_Linux.zip`

---

