# Hướng dẫn Tích hợp Khóa Bản quyền vào Chrome Extension

> **Server bản quyền:** `https://key.byscom.vn`
> **Endpoint xác thực:** `POST https://key.byscom.vn/api/validate-key`
> **Áp dụng cho:** Chrome Extension (Manifest V3)
> **Nguồn:** Đặc tả khớp với mã nguồn backend `vcamapi` (`KeyController`, `KeyService`, `KeyRepository`)

---

## Mục lục

1. [Nguyên lý hoạt động](#1-nguyên-lý-hoạt-động)
2. [Đặc tả API `validate-key`](#2-đặc-tả-api-validate-key)
3. [Logic ràng buộc thiết bị](#3-logic-ràng-buộc-thiết-bị)
4. [Đăng ký `app_id` trước khi tích hợp](#4-đăng-ký-app_id-trước-khi-tích-hợp)
5. [Cấu trúc Extension](#5-cấu-trúc-extension)
6. [manifest.json](#6-manifestjson)
7. [lib/license.js — Thư viện xác thực](#7-liblicensejs--thư-viện-xác-thực)
8. [popup — Giao diện nhập key](#8-popup--giao-diện-nhập-key)
9. [content.js — Khóa tính năng](#9-contentjs--khóa-tính-năng)
10. [background.js — Kiểm tra định kỳ](#10-backgroundjs--kiểm-tra-định-kỳ)
11. [Kiểm thử nhanh bằng cURL / PowerShell](#11-kiểm-thử-nhanh)
12. [Lưu ý bảo mật & CORS production](#12-lưu-ý-bảo-mật--cors-production)
13. [Checklist tích hợp](#13-checklist-tích-hợp)

---

## 1. Nguyên lý hoạt động

Extension **bắt buộc nhập key hợp lệ mới mở khóa tính năng**. Cơ chế:

```
[Mở extension]
      │
      ▼
 Đã lưu key trong chrome.storage chưa?
      │
      ├─ Chưa ──────────────► Hiện form nhập key (khóa toàn bộ tính năng)
      │
      └─ Rồi ──► POST /api/validate-key { key, app_id, deviceId }
                       │
            ┌──────────┴──────────┐
            ▼                     ▼
      status === 1           status === 0
      (HỢP LỆ)               (KHÔNG HỢP LỆ)
            │                     │
            ▼                     ▼
   Lưu key + mở khóa      Xóa key cũ + yêu cầu nhập lại
```

**3 yếu tố cốt lõi:**

1. **`app_id` gán cứng (hardcode)** trong mã nguồn extension — người dùng không sửa được.
2. **`deviceId`** là mã định danh thiết bị do extension tự sinh và lưu cố định — mỗi trình duyệt một mã.
3. **Backend kiểm tra đồng thời `key` + `app_id` + `deviceId`** — sai bất kỳ yếu tố nào đều trả thất bại.

---

## 2. Đặc tả API `validate-key`

### Thông tin endpoint

| Thuộc tính | Giá trị |
|------------|---------|
| URL | `POST https://key.byscom.vn/api/validate-key` |
| Content-Type | `application/json` |
| Xác thực | **Không cần** token (API công khai) |
| HTTP status | **Luôn trả `200 OK`** — kể cả khi thất bại |

> ⚠️ **QUAN TRỌNG:** Phải kiểm tra trường `status` trong body JSON, **KHÔNG** dựa vào HTTP status code.

### Request body

```json
{
  "key": "ABCDEF-123456-GHIJKL-789012",
  "app_id": "APP_CUA_BAN",
  "deviceId": "550e8400-e29b-41d4-a716-446655440000"
}
```

| Trường | Kiểu | Bắt buộc | Ghi chú |
|--------|------|----------|---------|
| `key` | string | ✅ | License key người dùng nhập (nên `.trim()`) |
| `app_id` | string | ✅ | Mã ứng dụng đã đăng ký trong bảng `apps` |
| `deviceId` | string | ✅ | **camelCase** — KHÔNG phải `device_id` |

### Response — Thành công (`status: 1`)

Backend trả về **toàn bộ bản ghi key**:

```json
{
  "status": 1,
  "message": "Key hợp lệ",
  "data": {
    "id": 42,
    "app_id": "APP_CUA_BAN",
    "key": "ABCDEF-123456-GHIJKL-789012",
    "start_date": "2026-06-01 00:00:00",
    "expire_date": "2026-12-31 23:59:59",
    "device_id": "550e8400-e29b-41d4-a716-446655440000",
    "note": "Cấp cho khách hàng A",
    "author": 1,
    "created_at": "2026-06-01T00:00:00.000000Z",
    "updated_at": "2026-06-01T07:00:00.000000Z"
  }
}
```

Extension chỉ cần đọc 3 trường: `data.key`, `data.expire_date`, `data.device_id` (các trường còn lại bỏ qua).

### Response — Thất bại (`status: 0`)

```json
{
  "status": 0,
  "message": "<thông điệp lỗi>"
}
```

| Trường hợp | `message` trả về |
|------------|------------------|
| Key không có trong DB | `"Key không tồn tại"` |
| `app_id` chưa đăng ký trong bảng `apps` | `"App id không tồn tại"` |
| Sai app / sai thiết bị / hết hạn / chưa tới ngày bắt đầu | `"Key không hợp lệ hoặc đã được sử dụng trên một thiết bị khác"` |
| Thiếu trường bắt buộc | kèm mảng `errors` chi tiết |

---

## 3. Logic ràng buộc thiết bị

Logic chính xác trong backend (`KeyService::validateKey` + `KeyRepository::validateKey`):

```
B1. Tìm key trong bảng keys
     └─ Không thấy → "Key không tồn tại"

B2. Tìm app_id trong bảng apps
     └─ Không thấy → "App id không tồn tại"

B3. Xét device_id của bản ghi key:
     ├─ device_id == NULL (kích hoạt lần đầu):
     │     WHERE key=? AND app_id=? AND device_id IS NULL
     │     AND (start_date IS NULL OR start_date <= NOW())
     │     AND (expire_date IS NULL OR expire_date >= NOW())
     │
     └─ device_id != NULL (đã kích hoạt):
           WHERE key=? AND app_id=? AND device_id=?  (+ điều kiện ngày như trên)

B4. Khớp  → CẬP NHẬT device_id = deviceId gửi lên → trả status 1 + data
    Không khớp → "Key không hợp lệ hoặc đã được sử dụng trên một thiết bị khác"
```

**Hệ quả:**

- **Lần đầu:** bất kỳ thiết bị nào cũng kích hoạt được (vì `device_id` đang `NULL`) → key bị **khóa cứng vào thiết bị đó**.
- **Từ lần 2:** chỉ đúng thiết bị đã kích hoạt mới xác thực thành công.
- **Đổi máy cho khách:** Admin phải vào Dashboard/DB đặt lại `device_id = NULL` cho key đó.
- `start_date`/`expire_date` để trống = **không giới hạn** (bắt đầu ngay / vĩnh viễn).

---

## 4. Đăng ký `app_id` trước khi tích hợp

Backend chỉ chấp nhận `app_id` đã có trong bảng `apps`. Mặc định seeder chỉ tạo `APP001`–`APP005`, vì vậy **trước khi cấp key cho extension mới, phải đăng ký `app_id` riêng**.

Chọn 1 trong các cách trên Admin Dashboard hoặc server:

**Cách 1 — Giao diện quản trị (khuyên dùng):** Vào `/app-management` → **Thêm Ứng dụng** → nhập `App ID` (ví dụ `EXT_BYSCOM_TIKTOK`) và `App Name` → **Lưu**.

**Cách 2 — Tinker:**
```php
\App\Models\App::create([
    'app_id'   => 'EXT_BYSCOM_TIKTOK',
    'app_name' => 'Byscom TikTok Extension',
]);
```

**Cách 3 — SQL:**
```sql
INSERT INTO apps (app_id, app_name, created_at, updated_at)
VALUES ('EXT_BYSCOM_TIKTOK', 'Byscom TikTok Extension', NOW(), NOW());
```

> **Quy tắc đặt `app_id`:** chuỗi duy nhất, viết HOA, không dấu, không khoảng trắng. Giá trị này sẽ được hardcode vào `lib/license.js` ở bước sau.

---

## 5. Cấu trúc Extension

```
my-extension/
├── manifest.json
├── lib/
│   └── license.js         ← Thư viện xác thực bản quyền (gatekeeper)
├── popup/
│   ├── popup.html         ← Form nhập key
│   └── popup.js           ← Logic kích hoạt
├── content/
│   └── content.js         ← Khóa/mở tính năng trên trang mục tiêu
└── background/
    └── background.js      ← Service worker, kiểm tra định kỳ
```

---

## 6. manifest.json

```json
{
  "manifest_version": 3,
  "name": "Byscom Extension",
  "version": "1.0.0",
  "permissions": ["storage", "alarms", "notifications"],
  "host_permissions": [
    "https://key.byscom.vn/*"
  ],
  "action": {
    "default_popup": "popup/popup.html"
  },
  "content_scripts": [
    {
      "matches": ["https://*.tiktok.com/*"],
      "js": ["lib/license.js", "content/content.js"]
    }
  ],
  "background": {
    "service_worker": "background/background.js"
  }
}
```

| Quyền | Lý do |
|-------|-------|
| `storage` | Lưu key + deviceId vào bộ nhớ cục bộ |
| `host_permissions: https://key.byscom.vn/*` | Cho phép extension gọi API tới server bản quyền |
| `alarms` | Hẹn giờ kiểm tra lại key định kỳ |
| `notifications` | Báo khi key hết hạn (tùy chọn) |

---

## 7. lib/license.js — Thư viện xác thực

```javascript
// ════════════════════════════════════════════════════════════════
// license.js — License Gatekeeper cho server key.byscom.vn
// ════════════════════════════════════════════════════════════════

const LICENSE_CONFIG = {
  // Endpoint cố định của server bản quyền
  API_URL: "https://key.byscom.vn/api/validate-key",

  // ⚠️ Thay bằng app_id ĐÃ ĐĂNG KÝ trong bảng apps (xem mục 4)
  APP_ID: "EXT_BYSCOM_TIKTOK",

  // Khóa lưu trong chrome.storage.local
  STORAGE_KEY_LICENSE: "byscom_license_key",
  STORAGE_KEY_DEVICE:  "byscom_device_id",
  STORAGE_KEY_EXPIRY:  "byscom_key_expiry",
};

// ── Sinh & lưu deviceId cố định cho trình duyệt này ──
async function getOrCreateDeviceId() {
  return new Promise((resolve) => {
    chrome.storage.local.get([LICENSE_CONFIG.STORAGE_KEY_DEVICE], (res) => {
      const existing = res[LICENSE_CONFIG.STORAGE_KEY_DEVICE];
      if (existing) {
        resolve(existing);
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

// ── Gọi API xác thực key ──
async function validateLicenseKey(key) {
  const deviceId = await getOrCreateDeviceId();
  try {
    const res = await fetch(LICENSE_CONFIG.API_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Accept": "application/json",
      },
      body: JSON.stringify({
        key: key,
        app_id: LICENSE_CONFIG.APP_ID, // hardcode, không cho sửa
        deviceId: deviceId,            // camelCase
      }),
    });
    // API luôn trả 200 → đọc thẳng JSON, không check res.ok
    return await res.json(); // { status, message, data }
  } catch (err) {
    console.error("[LICENSE] Lỗi kết nối:", err);
    return { status: 0, message: "Không thể kết nối máy chủ bản quyền." };
  }
}

// ── Lưu / đọc / xóa key ──
async function getSavedLicenseKey() {
  return new Promise((resolve) => {
    chrome.storage.local.get([LICENSE_CONFIG.STORAGE_KEY_LICENSE], (res) =>
      resolve(res[LICENSE_CONFIG.STORAGE_KEY_LICENSE] || null)
    );
  });
}

async function saveLicenseKey(key, expiryDate) {
  return new Promise((resolve) => {
    chrome.storage.local.set({
      [LICENSE_CONFIG.STORAGE_KEY_LICENSE]: key,
      [LICENSE_CONFIG.STORAGE_KEY_EXPIRY]: expiryDate || null,
    }, resolve);
  });
}

async function clearLicenseKey() {
  return new Promise((resolve) => {
    chrome.storage.local.remove([
      LICENSE_CONFIG.STORAGE_KEY_LICENSE,
      LICENSE_CONFIG.STORAGE_KEY_EXPIRY,
    ], resolve);
  });
}

// ── Quy trình kiểm tra đầy đủ ──
// Trả về: { isValid, data?, message }
async function checkLicense() {
  const savedKey = await getSavedLicenseKey();
  if (!savedKey) {
    return { isValid: false, message: "Chưa kích hoạt bản quyền." };
  }

  const result = await validateLicenseKey(savedKey);

  if (result.status === 1 && result.data && result.data.key) {
    await saveLicenseKey(savedKey, result.data.expire_date);
    return { isValid: true, data: result.data, message: result.message };
  }

  await clearLicenseKey();
  return { isValid: false, message: result.message || "Key không hợp lệ." };
}
```

---

## 8. popup — Giao diện nhập key

### popup/popup.html

```html
<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="UTF-8" />
  <style>
    body { width: 340px; padding: 20px; font-family: 'Segoe UI', sans-serif;
           background: #1a1a2e; color: #eee; }
    h2 { text-align: center; color: #00d2ff; margin: 0 0 4px; }
    .subtitle { text-align: center; color: #888; font-size: 12px; margin-bottom: 18px; }
    input { width: 100%; padding: 10px; border: 1px solid #333; border-radius: 6px;
            background: #16213e; color: #fff; box-sizing: border-box; margin-bottom: 12px; }
    button { width: 100%; padding: 10px; border: none; border-radius: 6px; cursor: pointer;
             font-weight: bold; color: #fff;
             background: linear-gradient(135deg, #00d2ff, #3a7bd5); }
    button:disabled { opacity: .5; cursor: not-allowed; }
    .error { color: #ff4757; font-size: 12px; min-height: 16px; }
    .activated { text-align: center; }
    .activated .icon { font-size: 44px; }
    .activated .info { font-size: 12px; color: #888; margin: 4px 0; }
    .activated .info strong { color: #00d2ff; }
    .btn-deactivate { background: #333 !important; margin-top: 14px; }
  </style>
</head>
<body>
  <div id="screen-activate">
    <h2>🔑 Kích hoạt Bản quyền</h2>
    <p class="subtitle">Byscom Extension</p>
    <input type="text" id="input-key" placeholder="Nhập key bản quyền..." />
    <button id="btn-activate">Kích hoạt</button>
    <p id="msg-error" class="error"></p>
  </div>

  <div id="screen-activated" class="activated" style="display:none;">
    <div class="icon">✅</div>
    <h2>Đã kích hoạt</h2>
    <p class="info">Ứng dụng: <strong id="info-app"></strong></p>
    <p class="info">Hết hạn: <strong id="info-expiry"></strong></p>
    <button id="btn-deactivate" class="btn-deactivate">Hủy kích hoạt</button>
  </div>

  <script src="../lib/license.js"></script>
  <script src="popup.js"></script>
</body>
</html>
```

### popup/popup.js

```javascript
document.addEventListener("DOMContentLoaded", async () => {
  const inputKey       = document.getElementById("input-key");
  const btnActivate    = document.getElementById("btn-activate");
  const msgError       = document.getElementById("msg-error");
  const screenActivate = document.getElementById("screen-activate");
  const screenActive   = document.getElementById("screen-activated");

  // Khi mở popup: kiểm tra lại với server
  const status = await checkLicense();
  if (status.isValid) showActivated(status.data);

  btnActivate.addEventListener("click", async () => {
    const key = inputKey.value.trim();
    if (!key) { msgError.textContent = "Vui lòng nhập key!"; return; }

    btnActivate.disabled = true;
    btnActivate.textContent = "Đang kiểm tra...";
    msgError.textContent = "";

    const result = await validateLicenseKey(key);

    if (result.status === 1 && result.data && result.data.key) {
      await saveLicenseKey(key, result.data.expire_date);
      showActivated(result.data);
    } else {
      msgError.textContent = result.message || "Kích hoạt thất bại!";
      btnActivate.disabled = false;
      btnActivate.textContent = "Kích hoạt";
    }
  });

  document.getElementById("btn-deactivate").addEventListener("click", async () => {
    await clearLicenseKey();
    screenActive.style.display = "none";
    screenActivate.style.display = "block";
    inputKey.value = "";
    btnActivate.disabled = false;
    btnActivate.textContent = "Kích hoạt";
  });

  function showActivated(data) {
    screenActivate.style.display = "none";
    screenActive.style.display = "block";
    document.getElementById("info-app").textContent = data?.app_id || "N/A";
    document.getElementById("info-expiry").textContent =
      data?.expire_date || "Vô thời hạn";
  }
});
```

---

## 9. content.js — Khóa tính năng

```javascript
// content/content.js — chỉ chạy tính năng khi bản quyền hợp lệ
(async function () {
  "use strict";

  const license = await checkLicense();
  if (!license.isValid) {
    console.warn("[BYSCOM] ⛔ Chưa kích hoạt bản quyền — extension không chạy.");
    return; // KHÔNG inject bất kỳ tính năng nào
  }

  console.log("[BYSCOM] ✅ Bản quyền hợp lệ — khởi chạy tính năng.");
  initFeatures();

  function initFeatures() {
    // ... toàn bộ code tính năng thực tế của bạn đặt ở đây ...
  }
})();
```

---

## 10. background.js — Kiểm tra định kỳ

```javascript
// background/background.js — Service Worker
// (cần import license.js vì service worker không tự có)
importScripts("../lib/license.js");

// Kiểm tra lại key mỗi 6 giờ
chrome.alarms.create("license-check", { periodInMinutes: 360 });

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== "license-check") return;
  const result = await checkLicense();
  if (!result.isValid) {
    chrome.notifications.create({
      type: "basic",
      iconUrl: "icons/icon48.png",
      title: "Bản quyền hết hạn",
      message: "Key của bạn đã hết hạn hoặc không hợp lệ. Vui lòng gia hạn.",
    });
  }
});
```

> Nếu dùng `importScripts`, đảm bảo `lib/license.js` không gọi `chrome.storage` ở phạm vi top-level (code mẫu trên chỉ định nghĩa hàm nên an toàn).

---

## 11. Kiểm thử nhanh

### cURL

```bash
curl -X POST "https://key.byscom.vn/api/validate-key" \
  -H "Content-Type: application/json" \
  -d '{
    "key": "YOUR_LICENSE_KEY",
    "app_id": "EXT_BYSCOM_TIKTOK",
    "deviceId": "test-device-id-123"
  }'
```

### PowerShell

```powershell
Invoke-RestMethod -Uri "https://key.byscom.vn/api/validate-key" `
  -Method Post -ContentType "application/json" `
  -Body '{"key":"YOUR_LICENSE_KEY","app_id":"EXT_BYSCOM_TIKTOK","deviceId":"test-device-id-123"}'
```

**Diễn giải kết quả:**
- Lần đầu chạy với `deviceId` bất kỳ → key bị bind vào `test-device-id-123`.
- Đổi `deviceId` khác mà gọi lại → `"Key không hợp lệ hoặc đã được sử dụng trên một thiết bị khác"`.
- Muốn test lại từ đầu → đặt `device_id = NULL` cho key đó trong DB.

---

## 12. Lưu ý bảo mật & CORS production

### Client-side là không tuyệt đối

Mã JS của extension có thể bị đọc/sửa. Các biện pháp tăng cường:

| Biện pháp | Mức bảo vệ |
|-----------|-----------|
| Obfuscate mã (`javascript-obfuscator`) | ⭐⭐ |
| Kiểm tra lại định kỳ (mục 10) thay vì chỉ 1 lần | ⭐⭐⭐ |
| Đặt logic "đắt giá" ở server riêng, server đó tự gọi `validate-key` trước khi trả dữ liệu | ⭐⭐⭐⭐⭐ |

### CORS

Hiện tại `config/cors.php` đang để `'allowed_origins' => ['*']` → extension gọi được ngay, nhưng **nên siết khi production**:

```php
'allowed_origins' => [
    'https://admin.byscom.vn',
    'chrome-extension://<ID_EXTENSION_CUA_BAN>',
],
```

> Lấy `ID_EXTENSION` tại `chrome://extensions` (bật Developer mode). Sau khi publish lên Web Store, ID sẽ cố định.

### Lưu ý khác

- Backend route `validate-key` **chưa cấu hình rate-limit** → cân nhắc thêm `throttle` để chống dò key brute-force.
- `deviceId` do client tự sinh nên có thể bị giả lập; ràng buộc thiết bị chỉ thực sự chặt khi kết hợp kiểm tra lại định kỳ ở server.

---

## 13. Checklist tích hợp

- [ ] Đăng ký `app_id` riêng vào bảng `apps` (mục 4)
- [ ] Cấp key cho `app_id` đó trên Dashboard
- [ ] Đặt `LICENSE_CONFIG.APP_ID` trong `lib/license.js` khớp với `app_id` đã đăng ký
- [ ] Giữ `API_URL = https://key.byscom.vn/api/validate-key`
- [ ] Khai báo `host_permissions: https://key.byscom.vn/*` trong manifest
- [ ] Kiểm tra `status` trong body JSON (không dựa HTTP code)
- [ ] Gửi `deviceId` (camelCase) — không phải `device_id`
- [ ] Khóa tính năng trong `content.js` cho tới khi `checkLicense().isValid === true`
- [ ] Test bind thiết bị + test đổi `deviceId` báo lỗi đúng
- [ ] (Production) Siết CORS về `chrome-extension://<ID>` và thêm rate-limit
