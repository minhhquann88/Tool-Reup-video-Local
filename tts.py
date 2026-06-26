"""
tts.py - Voice AI: prompt + dữ liệu CSV -> Gemini sinh lời thoại -> Google TTS -> mp3.

Port từ luồng của auto-video-grok-main (Node/Electron) sang Python.
Dùng cho Video Reup Tool: mỗi dòng CSV sinh ra một file giọng nói riêng,
file mp3 này được dùng làm audio thay thế khi ghép video (core.VideoProcessor).
"""

import base64
import re
import time

import requests

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)
GOOGLE_TTS_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"
VIDEOAI_TTS_URL = "https://videoai.ddns.net/v1/tts"

# Giọng mặc định cho Voice API (videoai)
DEFAULT_VIDEOAI_VOICE = "vi_anh_duong_reviewer_female"

# Prompt mặc định. Hỗ trợ chèn BẤT KỲ cột nào trong CSV theo cú pháp ${tên_cột},
# ví dụ ${nd_video}. Riêng ${nd_video} sẽ fallback sang product_name nếu rỗng.
DEFAULT_PROMPT = (
    "Tạo nội dung review sản phẩm bằng tiếng Việt cho video ngắn "
    "bán hàng trên sàn Thương mại điện tử. Yêu cầu kết quả trả ra là một đoạn "
    "kịch bản lời thoại tự nhiên, mạch lạc, dài khoảng 370 ký tự. Ngôn từ trung "
    "thực, khách quan, không phóng đại hoặc so sánh với sản phẩm khác, không kêu "
    "gọi mua hàng ngoài nền tảng, không nhắc đến tên của nền tảng nào khác. Không "
    "chứa ký tự đặc biệt, hashtag, chú thích, câu chào hay lặp ý. Kết quả trả về "
    "là một đoạn lời thoại duy nhất bằng tiếng Việt.\n\n"
    "Tên sản phẩm: ${nd_video}"
)

# Giọng tiếng Việt của Google TTS: nhãn dễ hiểu -> mã giọng thật
VOICE_CHOICES = {
    "Nữ A (trẻ)":        "vi-VN-Standard-A",
    "Nam B":             "vi-VN-Standard-B",
    "Nữ C (truyền cảm)": "vi-VN-Standard-C",
    "Nam D (trầm)":      "vi-VN-Standard-D",
}
DEFAULT_VOICE_LABEL = "Nữ C (truyền cảm)"


def replace_prompt_variables(prompt, row, language_name=None):
    """
    Thay placeholder trong prompt:
      - ${nd_video}     -> row['nd_video'], rỗng thì fallback product_name
      - ${productName}  -> row['product_name'] hoặc row['productName']
      - ${languageName} -> language_name (truyền vào, không phải cột CSV)
      - ${tên_cột}      -> giá trị cột bất kỳ trong row (CSV)
    Chấp nhận cả ${ten} lẫn {ten}.
    """
    if not prompt or not isinstance(prompt, str):
        return prompt or ""

    result = prompt

    product_name = ""
    if isinstance(row, dict):
        product_name = str(row.get("product_name") or row.get("productName") or "")
    result = re.sub(r"\$?\{\s*productName\s*\}", product_name, result)

    # ${nd_video}: ưu tiên cột nd_video, rỗng/không có thì fallback product_name.
    # Xử lý trước vòng lặp chèn cột bên dưới để tránh cột nd_video rỗng ghi đè "".
    nd_video = ""
    if isinstance(row, dict):
        nd_video = str(row.get("nd_video") or "").strip()
        if not nd_video:
            nd_video = product_name
    result = re.sub(r"\$?\{\s*nd_video\s*\}", nd_video, result)

    if language_name:
        result = re.sub(r"\$?\{\s*languageName\s*\}", language_name, result)

    # Thay mọi cột CSV theo tên
    if isinstance(row, dict):
        for key, value in row.items():
            if value is None:
                continue
            pattern = r"\$?\{\s*" + re.escape(str(key)) + r"\s*\}"
            result = re.sub(pattern, str(value), result)

    return result


def _sleep_or_stop(seconds, should_stop):
    """Ngủ theo từng giây, thoát sớm nếu should_stop() trả về True."""
    for _ in range(int(seconds)):
        if should_stop and should_stop():
            return
        time.sleep(1)


def generate_script(
    prompt_template,
    row,
    *,
    api_key,
    model="gemini-3.1-flash-lite",
    language_name="Tiếng Việt",
    retries=10,
    backoff=5,
    log=None,
    should_stop=None,
):
    """
    Gọi Gemini sinh lời thoại từ prompt (đã chèn dữ liệu CSV).
    Trả về chuỗi text. Ném RuntimeError nếu thất bại sau `retries` lần.
    """
    if not api_key:
        raise ValueError("Thiếu API key Gemini")

    prompt = replace_prompt_variables(prompt_template, row, language_name)
    url = GEMINI_URL.format(model=model) + f"?key={api_key}"
    headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
    body = {"contents": [{"parts": [{"text": prompt}]}]}

    last_err = None
    for attempt in range(1, retries + 1):
        if should_stop and should_stop():
            raise RuntimeError("Đã dừng theo yêu cầu")
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=30)
            data = resp.json()
            if isinstance(data, dict) and data.get("error"):
                raise RuntimeError(data["error"].get("message", "Gemini API error"))
            text = (
                data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
            )
            if not text or not text.strip():
                raise RuntimeError("Gemini trả về nội dung rỗng")
            return text.strip()
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if log:
                log(f"! Gemini lần {attempt}/{retries} lỗi: {exc}")
            if attempt < retries:
                # Chờ cố định `backoff` giây (mặc định 5s) rồi thử lại,
                # không tăng dần để khỏi đợi lâu.
                _sleep_or_stop(backoff, should_stop)

    raise RuntimeError(f"Gemini thất bại sau {retries} lần: {last_err}")


def synthesize_voice(
    text,
    out_path,
    *,
    api_key,
    voice_name="vi-VN-Standard-C",
    language_code="vi-VN",
    speaking_rate=1.2,
    retries=10,
    backoff=5,
    log=None,
    should_stop=None,
):
    """
    Gọi Google TTS chuyển text -> file mp3 tại out_path.
    Trả về out_path. Ném RuntimeError nếu thất bại sau `retries` lần.
    """
    if not api_key:
        raise ValueError("Thiếu API key Google TTS")
    if not text or not str(text).strip():
        raise ValueError("Thiếu text để tạo voice")

    url = GOOGLE_TTS_URL + f"?key={api_key}"
    headers = {"Content-Type": "application/json; charset=utf-8"}
    body = {
        "input": {"text": str(text)},
        "voice": {"languageCode": language_code, "name": voice_name},
        "audioConfig": {"audioEncoding": "MP3", "speakingRate": float(speaking_rate)},
    }

    last_err = None
    for attempt in range(1, retries + 1):
        if should_stop and should_stop():
            raise RuntimeError("Đã dừng theo yêu cầu")
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=30)
            data = resp.json()
            if isinstance(data, dict) and data.get("error"):
                raise RuntimeError(data["error"].get("message", "Google TTS API error"))
            audio = data.get("audioContent")
            if not audio:
                raise RuntimeError("Google TTS trả về audioContent rỗng")
            with open(out_path, "wb") as fh:
                fh.write(base64.b64decode(audio))
            return out_path
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if log:
                log(f"! Google TTS lần {attempt}/{retries} lỗi: {exc}")
            if attempt < retries:
                # Chờ cố định `backoff` giây (mặc định 5s), không tăng dần.
                _sleep_or_stop(backoff, should_stop)

    raise RuntimeError(f"Google TTS thất bại sau {retries} lần: {last_err}")


def synthesize_voice_videoai(
    text,
    out_path,
    *,
    api_key,
    voice_name=DEFAULT_VIDEOAI_VOICE,
    speed=1.0,
    retries=10,
    backoff=5,
    log=None,
    should_stop=None,
):
    """
    Gọi Voice API (videoai.ddns.net) chuyển text -> file mp3 tại out_path.
    Trả về out_path. Ném RuntimeError nếu thất bại sau `retries` lần.

    API trả về có thể là:
      - audio nhị phân (Content-Type: audio/*)  -> ghi thẳng
      - JSON chứa base64 (audioContent/audio/data) -> decode rồi ghi
    Xử lý phòng thủ cho cả hai trường hợp.
    """
    if not api_key:
        raise ValueError("Thiếu API Key Voice API")
    if not text or not str(text).strip():
        raise ValueError("Thiếu text để tạo voice")

    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
    body = {
        "text": str(text),
        "voice_name": voice_name or DEFAULT_VIDEOAI_VOICE,
        "speed": float(speed),
    }

    last_err = None
    for attempt in range(1, retries + 1):
        if should_stop and should_stop():
            raise RuntimeError("Đã dừng theo yêu cầu")
        try:
            resp = requests.post(
                VIDEOAI_TTS_URL, headers=headers, json=body, timeout=30)
            ctype = (resp.headers.get("Content-Type") or "").lower()

            # Trường hợp lỗi: thường trả JSON kèm field "error"
            if "application/json" in ctype:
                data = resp.json()
                if isinstance(data, dict) and data.get("error"):
                    err = data["error"]
                    msg = err.get("message", err) if isinstance(err, dict) else err
                    raise RuntimeError(f"Voice API error: {msg}")
                audio_b64 = None
                if isinstance(data, dict):
                    audio_b64 = (data.get("audioContent")
                                 or data.get("audio")
                                 or data.get("data"))
                if not audio_b64:
                    raise RuntimeError(f"Voice API trả JSON không có audio: {data}")
                audio_bytes = base64.b64decode(audio_b64)
            else:
                # Không phải JSON -> coi như audio nhị phân
                if resp.status_code >= 400:
                    raise RuntimeError(
                        f"Voice API HTTP {resp.status_code}: "
                        f"{resp.text[:200]}")
                audio_bytes = resp.content

            if not audio_bytes:
                raise RuntimeError("Voice API trả về audio rỗng")
            with open(out_path, "wb") as fh:
                fh.write(audio_bytes)
            return out_path
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if log:
                log(f"! Voice API lần {attempt}/{retries} lỗi: {exc}")
            if attempt < retries:
                # Chờ cố định `backoff` giây (mặc định 5s), không tăng dần.
                _sleep_or_stop(backoff, should_stop)

    raise RuntimeError(f"Voice API thất bại sau {retries} lần: {last_err}")


def make_voice(
    row,
    out_path,
    *,
    gemini_key,
    tts_key,
    prompt=DEFAULT_PROMPT,
    model="gemini-3.1-flash-lite",
    provider="google",
    voice_name="vi-VN-Standard-C",
    language_code="vi-VN",
    language_name="Tiếng Việt",
    speaking_rate=1.2,
    retries=10,
    log=None,
    on_script=None,
    should_stop=None,
):
    """
    Luồng đầy đủ cho 1 dòng CSV: prompt -> Gemini sinh text -> TTS -> mp3.
    `provider`:
      - "google"  : Google TTS (tts_key = API key Google).
      - "videoai" : Voice API videoai.ddns.net (tts_key = X-API-Key).
    `on_script(script)` (tuỳ chọn): gọi ngay sau khi sinh xong lời thoại, trước
    bước TTS — để bên gọi log "tạo text xong" đúng thứ tự.
    Trả về (out_path, script_text). Ném RuntimeError nếu thất bại.
    """
    script = generate_script(
        prompt,
        row,
        api_key=gemini_key,
        model=model,
        language_name=language_name,
        retries=retries,
        log=log,
        should_stop=should_stop,
    )
    if on_script:
        on_script(script)
    elif log:
        preview = script if len(script) <= 120 else script[:117] + "..."
        log(f" Lời thoại: {preview}")
    if provider == "videoai":
        synthesize_voice_videoai(
            script,
            out_path,
            api_key=tts_key,
            voice_name=voice_name,
            speed=speaking_rate,
            retries=retries,
            log=log,
            should_stop=should_stop,
        )
    else:
        synthesize_voice(
            script,
            out_path,
            api_key=tts_key,
            voice_name=voice_name,
            language_code=language_code,
            speaking_rate=speaking_rate,
            retries=retries,
            log=log,
            should_stop=should_stop,
        )
    return out_path, script
