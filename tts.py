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

# Prompt mặc định. Hỗ trợ chèn BẤT KỲ cột nào trong CSV theo cú pháp ${tên_cột},
# ví dụ ${product_name}.
DEFAULT_PROMPT = (
    "Tạo nội dung review sản phẩm bằng tiếng Việt cho video ngắn "
    "bán hàng trên sàn Thương mại điện tử. Yêu cầu kết quả trả ra là một đoạn "
    "kịch bản lời thoại tự nhiên, mạch lạc, dài khoảng 370 ký tự. Ngôn từ trung "
    "thực, khách quan, không phóng đại hoặc so sánh với sản phẩm khác, không kêu "
    "gọi mua hàng ngoài nền tảng, không nhắc đến tên của nền tảng nào khác. Không "
    "chứa ký tự đặc biệt, hashtag, chú thích, câu chào hay lặp ý. Kết quả trả về "
    "là một đoạn lời thoại duy nhất bằng tiếng Việt.\n\n"
    "Tên sản phẩm: ${product_name}"
)

# Giọng tiếng Việt của Google TTS: nhãn dễ hiểu -> mã giọng thật
VOICE_CHOICES = {
    "👩 Nữ A (trẻ)":     "vi-VN-Standard-A",
    "👨 Nam B":          "vi-VN-Standard-B",
    "👩 Nữ C (truyền cảm)": "vi-VN-Standard-C",
    "👨 Nam D (trầm)":   "vi-VN-Standard-D",
}
DEFAULT_VOICE_LABEL = "👩 Nữ C (truyền cảm)"


def replace_prompt_variables(prompt, row, language_name=None):
    """
    Thay placeholder trong prompt:
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
    retries=6,
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
            resp = requests.post(url, headers=headers, json=body, timeout=120)
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
                log(f"⚠️ Gemini lần {attempt}/{retries} lỗi: {exc}")
            if attempt < retries:
                _sleep_or_stop(min(backoff * attempt, 20), should_stop)

    raise RuntimeError(f"Gemini thất bại sau {retries} lần: {last_err}")


def synthesize_voice(
    text,
    out_path,
    *,
    api_key,
    voice_name="vi-VN-Standard-C",
    language_code="vi-VN",
    speaking_rate=1.2,
    retries=6,
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
            resp = requests.post(url, headers=headers, json=body, timeout=120)
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
                log(f"⚠️ Google TTS lần {attempt}/{retries} lỗi: {exc}")
            if attempt < retries:
                _sleep_or_stop(min(backoff * attempt, 20), should_stop)

    raise RuntimeError(f"Google TTS thất bại sau {retries} lần: {last_err}")


def make_voice(
    row,
    out_path,
    *,
    gemini_key,
    tts_key,
    prompt=DEFAULT_PROMPT,
    model="gemini-3.1-flash-lite",
    voice_name="vi-VN-Standard-C",
    language_code="vi-VN",
    language_name="Tiếng Việt",
    speaking_rate=1.2,
    retries=6,
    log=None,
    should_stop=None,
):
    """
    Luồng đầy đủ cho 1 dòng CSV: prompt -> Gemini sinh text -> Google TTS -> mp3.
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
    if log:
        preview = script if len(script) <= 120 else script[:117] + "..."
        log(f"📝 Lời thoại: {preview}")
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
