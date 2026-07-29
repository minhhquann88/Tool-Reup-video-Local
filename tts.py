"""
tts.py - Voice AI: prompt + dữ liệu CSV -> Gemini sinh lời thoại -> Google TTS -> mp3.

Port từ luồng của auto-video-grok-main (Node/Electron) sang Python.
Dùng cho Video Reup Tool: mỗi dòng CSV sinh ra một file giọng nói riêng,
file mp3 này được dùng làm audio thay thế khi ghép video (core.VideoProcessor).
"""

import base64
import os
import re
import subprocess
import time

import requests

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)
GOOGLE_TTS_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"
AUTOVOICE_TTS_URL = "https://autovoice.vn/rest/tts/synthesize"
DEFAULT_AUTOVOICE_VOICE = "pv-fdb7a34a-243d-42f2-bbcb-cb78cfd027fa"

VIDEOAI_TTS_URL = "http://videoai3.ddns.net:8000/v1/tts"
DEFAULT_VIDEOAI_VOICE = "vi_anh_duong_reviewer_female"

# Prompt mặc định. Hỗ trợ chèn BẤT KỲ cột nào trong CSV theo cú pháp ${tên_cột},
# ví dụ ${nd_video}. Riêng ${nd_video} sẽ fallback sang product_name nếu rỗng.
DEFAULT_PROMPT = (
    "Tạo nội dung review sản phẩm bằng tiếng Việt cho video ngắn bán hàng trên sàn Thương mại điện tử.\n"
    "YÊU CẦU BẮT BUỘC VỀ ĐỘ DÀI: Kết quả trả ra BẮT BUỘC phải là một đoạn kịch bản có độ dài CHÍNH XÁC từ 350 đến 370 ký tự (không được ngắn hơn 350 ký tự và không được dài quá 370 ký tự). Hãy đếm kỹ số ký tự trước khi xuất kết quả.\n"
    "Yêu cầu nội dung: Lời thoại tự nhiên, mạch lạc. Ngôn từ trung thực, khách quan, không phóng đại hoặc so sánh với sản phẩm khác, không kêu gọi mua hàng ngoài nền tảng, không nhắc tên nền tảng khác. Không chứa ký tự đặc biệt, hashtag, chú thích, câu chào hay lặp ý. Trả về DUY NHẤT một đoạn văn lời thoại bằng tiếng Việt, không kèm bất kỳ câu dẫn nào khác.\n\n"
    "Tên sản phẩm: ${product_name}"
)

# Giọng tiếng Việt của Google TTS: nhãn dễ hiểu -> mã giọng thật
VOICE_CHOICES = {
    "Nữ A (trẻ)":        "vi-VN-Standard-A",
    "Nam B":             "vi-VN-Standard-B",
    "Nữ C (truyền cảm)": "vi-VN-Standard-C",
    "Nam D (trầm)":      "vi-VN-Standard-D",
}
DEFAULT_VOICE_LABEL = "Nữ C (truyền cảm)"


def _strip_hashtags(text):
    """
    Bỏ các hashtag (#abc) khỏi chuỗi. nd_video thường có đuôi tag marketing
    như "#ShopeeVideo #KOLUyTin …" — không phải tên sản phẩm, đưa vào prompt
    chỉ gây nhiễu cho nội dung AI sinh ra.
    """
    cleaned = re.sub(r"#\S+", "", text or "")
    return re.sub(r"\s+", " ", cleaned).strip()


def replace_prompt_variables(prompt, row, language_name=None):
    """
    Thay placeholder trong prompt:
      - ${product_name}  -> row['product_name'] (cột chuẩn chứa tên sản phẩm)
      - ${productName}   -> row['product_name'] hoặc row['productName']
      - ${languageName}  -> language_name (truyền vào, không phải cột CSV)
      - ${tên_cột}      -> giá trị cột bất kỳ trong row (CSV)
    Chấp nhận cả ${ten} lẫn {ten}.
    Ném ValueError nếu prompt yêu cầu ${product_name} nhưng ô product_name bị rỗng.
    """
    if not prompt or not isinstance(prompt, str):
        return prompt or ""

    result = prompt

    product_name = ""
    if isinstance(row, dict):
        val = row.get("product_name") if row.get("product_name") is not None else row.get("productName")
        if val is not None and str(val).strip().lower() not in ("", "nan", "none"):
            product_name = str(val).strip()

    if re.search(r"\$?\{\s*(product_name|productName)\s*\}", prompt) and not product_name:
        raise ValueError("Ô 'product_name' bị rỗng hoặc không tồn tại trong dữ liệu CSV")

    result = re.sub(r"\$?\{\s*productName\s*\}", product_name, result)
    result = re.sub(r"\$?\{\s*product_name\s*\}", product_name, result)

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


def _validate_audio_file(path: str, min_duration: float = 3.0, min_bytes: int = 50 * 1024):
    """
    Kiểm tra file audio vừa nhận từ TTS API có hợp lệ không.

    Tiêu chí:
      - File phải tồn tại và kích thước >= min_bytes (mặc định 50KB).
        → Response lỗi/tiếng "pít" thường khoảng 5KB (< 50KB).
      - Duration >= min_duration giây (mặc định 3.0s) đo bằng ffprobe.
        → Lời thoại review 370 ký tự chuẩn dài khoảng 15-30s.

    Ném ValueError nếu không đạt để trigger retry trong vòng lặp gọi API.
    """
    # Kiểm tra kích thước file trước (nhanh, không cần spawn process)
    try:
        size = os.path.getsize(path)
    except OSError:
        raise ValueError(f"File audio không tồn tại sau khi ghi: {path}")

    if size < min_bytes:
        raise ValueError(f"File audio quá nhỏ ({size} byte < {min_bytes} byte)")

    # Kiểm tra duration bằng ffprobe
    try:
        from core import FFPROBE, _WIN_FLAGS   # import lazy để tránh circular
        cmd = [
            FFPROBE, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
        ]
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            creationflags=_WIN_FLAGS,
            timeout=15,
        )
        dur_str = (r.stdout or "").strip()
        if dur_str and dur_str not in ("N/A", ""):
            duration = float(dur_str)
            if duration < min_duration:
                raise ValueError(f"Audio quá ngắn ({duration:.2f}s < {min_duration}s)")
    except ValueError:
        raise   # re-raise lỗi duration
    except Exception:
        # ffprobe không khả dụng hoặc timeout: bỏ qua kiểm tra duration,
        # chỉ dựa vào kích thước file (đã kiểm tra ở trên).
        pass


def generate_script(
    prompt_template,
    row,
    *,
    api_key,
    model="gemini-3.1-flash-lite",
    language_name="Tiếng Việt",
    max_chars=600,
    retries=10,
    backoff=2,
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
            resp = requests.post(url, headers=headers, json=body, timeout=180)
            data = resp.json()
            if isinstance(data, dict) and data.get("error"):
                raise RuntimeError(data["error"].get("message", "Gemini API error"))
            text = (
                data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
            )
            text_str = text.strip()
            if not text_str:
                raise RuntimeError("Gemini trả về nội dung rỗng")
            if max_chars and len(text_str) > max_chars:
                raise RuntimeError(
                    f"Lời thoại quá dài ({len(text_str)} ký tự > {max_chars} ký tự)"
                )
            return text_str
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if log:
                log(f"! Gemini lần {attempt}/{retries} lỗi: {exc}")
            if attempt < retries:
                # Chờ cố định `backoff` giây (mặc định 5s) rồi thử lại,
                # không tăng dần để khỏi đợi lâu.
                _sleep_or_stop(backoff, should_stop)

    raise RuntimeError(f"Gemini thất bại sau {retries} lần: {last_err}")


def generate_script_openai(
    prompt_template,
    row,
    *,
    api_key,
    model="gpt-4o-mini",
    language_name="Tiếng Việt",
    max_chars=600,
    retries=10,
    backoff=2,
    log=None,
    should_stop=None,
):
    """
    Gọi OpenAI/ChatGPT sinh lời thoại từ prompt (đã chèn dữ liệu CSV).
    Trả về chuỗi text. Ném RuntimeError nếu thất bại sau `retries` lần.
    """
    if not api_key:
        raise ValueError("Thiếu API key OpenAI/ChatGPT")

    prompt = replace_prompt_variables(prompt_template, row, language_name)
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}]
    }

    last_err = None
    for attempt in range(1, retries + 1):
        if should_stop and should_stop():
            raise RuntimeError("Đã dừng theo yêu cầu")
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=180)
            data = resp.json()
            if isinstance(data, dict) and data.get("error"):
                raise RuntimeError(data["error"].get("message", "OpenAI API error"))
            text = (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            text_str = text.strip()
            if not text_str:
                raise RuntimeError("ChatGPT trả về nội dung rỗng")
            if max_chars and len(text_str) > max_chars:
                raise RuntimeError(
                    f"Lời thoại quá dài ({len(text_str)} ký tự > {max_chars} ký tự)"
                )
            return text_str
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if log:
                log(f"! ChatGPT lần {attempt}/{retries} lỗi: {exc}")
            if attempt < retries:
                _sleep_or_stop(backoff, should_stop)

    raise RuntimeError(f"ChatGPT thất bại sau {retries} lần: {last_err}")


def synthesize_voice(
    text,
    out_path,
    *,
    api_key,
    voice_name="vi-VN-Standard-C",
    language_code="vi-VN",
    speaking_rate=1.2,
    retries=20,
    backoff=2,
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
            resp = requests.post(url, headers=headers, json=body, timeout=180)
            data = resp.json()
            if isinstance(data, dict) and data.get("error"):
                raise RuntimeError(data["error"].get("message", "Google TTS API error"))
            audio = data.get("audioContent")
            if not audio:
                raise RuntimeError("Google TTS trả về audioContent rỗng")
            with open(out_path, "wb") as fh:
                fh.write(base64.b64decode(audio))
            # Kiểm tra audio vừa nhận có hợp lệ không (tránh tiếng 'pít')
            _validate_audio_file(out_path)
            return out_path
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            # Xóa file audio hỏng (nếu có) để lần retry ghi lại sạch
            try:
                if os.path.exists(out_path):
                    os.remove(out_path)
            except OSError:
                pass
            if log:
                log(f"! Google TTS lần {attempt}/{retries} lỗi: {exc}")
            if attempt < retries:
                # Chờ cố định `backoff` giây (mặc định 5s), không tăng dần.
                _sleep_or_stop(backoff, should_stop)

    raise RuntimeError(f"Google TTS thất bại sau {retries} lần: {last_err}")


def synthesize_voice_autovoice(
    text,
    out_path,
    *,
    api_key,
    voice_name=DEFAULT_AUTOVOICE_VOICE,
    speed=1.0,
    retries=20,
    backoff=2,
    log=None,
    should_stop=None,
):
    if not api_key:
        raise ValueError("Thiếu API Key AutoVoice")
    if not text or not str(text).strip():
        raise ValueError("Thiếu text để tạo voice")

    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
    body = {
        "text": str(text),
        "voiceId": voice_name or DEFAULT_AUTOVOICE_VOICE,
        "speed": float(speed),
    }

    last_err = None
    for attempt in range(1, retries + 1):
        if should_stop and should_stop():
            raise RuntimeError("Đã dừng theo yêu cầu")
        try:
            resp = requests.post(
                AUTOVOICE_TTS_URL, headers=headers, json=body, timeout=180)
            ctype = (resp.headers.get("Content-Type") or "").lower()

            if "application/json" in ctype:
                data = resp.json()
                if isinstance(data, dict) and data.get("error"):
                    err = data["error"]
                    msg = err.get("message", err) if isinstance(err, dict) else err
                    raise RuntimeError(f"AutoVoice API error: {msg}")
                audio_b64 = None
                if isinstance(data, dict):
                    audio_b64 = (data.get("audioContent")
                                 or data.get("audio")
                                 or data.get("data"))
                if not audio_b64:
                    raise RuntimeError(f"AutoVoice API trả JSON không có audio: {data}")
                audio_bytes = base64.b64decode(audio_b64)
            else:
                if resp.status_code >= 400:
                    raise RuntimeError(
                        f"AutoVoice API HTTP {resp.status_code}: "
                        f"{resp.text[:200]}")
                audio_bytes = resp.content

            if not audio_bytes:
                raise RuntimeError("AutoVoice API trả về audio rỗng")
            with open(out_path, "wb") as fh:
                fh.write(audio_bytes)
            _validate_audio_file(out_path)
            return out_path
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            try:
                if os.path.exists(out_path):
                    os.remove(out_path)
            except OSError:
                pass
            if log:
                log(f"! AutoVoice API lần {attempt}/{retries} lỗi: {exc}")
            if attempt < retries:
                _sleep_or_stop(backoff, should_stop)

    raise RuntimeError(f"AutoVoice API thất bại sau {retries} lần: {last_err}")


def synthesize_voice_videoai(
    text,
    out_path,
    *,
    api_key,
    voice_name=DEFAULT_VIDEOAI_VOICE,
    speed=1.0,
    retries=20,
    backoff=2,
    log=None,
    should_stop=None,
):
    if not api_key:
        raise ValueError("Thiếu API Key Voice API (videoai)")
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
                VIDEOAI_TTS_URL, headers=headers, json=body, timeout=180)
            ctype = (resp.headers.get("Content-Type") or "").lower()

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
                if resp.status_code >= 400:
                    raise RuntimeError(
                        f"Voice API HTTP {resp.status_code}: "
                        f"{resp.text[:200]}")
                audio_bytes = resp.content

            if not audio_bytes:
                raise RuntimeError("Voice API trả về audio rỗng")
            with open(out_path, "wb") as fh:
                fh.write(audio_bytes)
            _validate_audio_file(out_path)
            return out_path
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            try:
                if os.path.exists(out_path):
                    os.remove(out_path)
            except OSError:
                pass
            if log:
                log(f"! Voice API lần {attempt}/{retries} lỗi: {exc}")
            if attempt < retries:
                _sleep_or_stop(backoff, should_stop)

    raise RuntimeError(f"Voice API thất bại sau {retries} lần: {last_err}")


def make_voice(
    row,
    out_path,
    *,
    gemini_key=None,
    tts_key,
    ai_provider="Gemini",
    ai_key=None,
    prompt=DEFAULT_PROMPT,
    model=None,
    provider="google",
    voice_name="vi-VN-Standard-C",
    language_code="vi-VN",
    language_name="Tiếng Việt",
    speaking_rate=1.2,
    max_chars=600,
    retries=10,
    tts_retries=20,
    tts_backoff=2,
    log=None,
    on_script=None,
    should_stop=None,
):
    """
    Luồng đầy đủ cho 1 dòng CSV: prompt -> Gemini/ChatGPT sinh text -> TTS -> mp3.
    `provider`:
      - "google"  : Google TTS (tts_key = API key Google).
      - "videoai" : Voice API videoai.ddns.net (tts_key = X-API-Key).
    `on_script(script)` (tuỳ chọn): gọi ngay sau khi sinh xong lời thoại, trước
    bước TTS — để bên gọi log "tạo text xong" đúng thứ tự.
    Trả về (out_path, script_text). Ném RuntimeError nếu thất bại.
    """
    actual_ai_key = ai_key or gemini_key
    actual_model = model or ("gemini-3.1-flash-lite" if ai_provider == "Gemini" else "gpt-4o-mini")

    if ai_provider == "ChatGPT":
        script = generate_script_openai(
            prompt,
            row,
            api_key=actual_ai_key,
            model=actual_model,
            language_name=language_name,
            max_chars=max_chars,
            retries=retries,
            log=log,
            should_stop=should_stop,
        )
    else:
        script = generate_script(
            prompt,
            row,
            api_key=actual_ai_key,
            model=actual_model,
            language_name=language_name,
            max_chars=max_chars,
            retries=retries,
            log=log,
            should_stop=should_stop,
        )
    if on_script:
        on_script(script)
    elif log:
        preview = script if len(script) <= 120 else script[:117] + "..."
        log(f" Lời thoại: {preview}")
    if provider == "autovoice":
        synthesize_voice_autovoice(
            script,
            out_path,
            api_key=tts_key,
            voice_name=voice_name,
            speed=speaking_rate,
            retries=tts_retries,
            backoff=tts_backoff,
            log=log,
            should_stop=should_stop,
        )
    elif provider == "videoai":
        synthesize_voice_videoai(
            script,
            out_path,
            api_key=tts_key,
            voice_name=voice_name,
            speed=speaking_rate,
            retries=tts_retries,
            backoff=tts_backoff,
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
            retries=tts_retries,
            backoff=tts_backoff,
            log=log,
            should_stop=should_stop,
        )
    return out_path, script
