"""Provider NLP menerjemahkan bahasa pengguna ke schema dataset DS-PSO."""

import json
import os
import re
import urllib.error
import urllib.request

from .feature_schema import DEFAULT_FEATURES, FEATURE_OPTIONS, dataset_prompt_context, validate_features
from .env_loader import load_env_file

load_env_file()

SYSTEM_PROMPT = """Anda adalah lapisan NLP untuk model DS-PSO kerusakan iPhone.
Tugas Anda HANYA menerjemahkan makna keluhan ke 13 fitur kategorikal persis seperti dataset penelitian.
Gunakan konteks schema dan contoh berlabel dari dataset. Jangan mendiagnosis kerusakan, jangan membuat nama fitur,
dan jangan membuat nilai baru. Keluhan boleh memakai bahasa sehari-hari yang berbeda dari contoh dataset.
Makna eksplisit harus dipetakan: misalnya layar tidak merespons sentuhan adalah gejala Touchscreen,
meskipun kalimat persisnya tidak ada dalam contoh. Setiap fitur yang tidak disebut harus memakai nilai default.
Keluarkan seluruh 13 fitur dan ai_advice dalam JSON."""


def _schema():
    properties = {
        name: {"type": "string", "enum": list(values)}
        for name, values in FEATURE_OPTIONS.items()
    }
    return {
        "type": "object",
        "properties": {
            "features": {
                "type": "object",
                "properties": properties,
                "required": list(FEATURE_OPTIONS),
                "additionalProperties": False,
            },
            "ai_advice": {"type": "string"},
        },
        "required": ["features", "ai_advice"],
        "additionalProperties": False,
    }


def _prompt(keluhan, tipe_hp):
    context = json.dumps(dataset_prompt_context(), ensure_ascii=False, separators=(",", ":"))
    return f"{SYSTEM_PROMPT}\n\nKonteks artefak penelitian:\n{context}\n\nTipe: {tipe_hp or 'iPhone'}\nKeluhan: {keluhan}"


def _sumopod_prompt(keluhan, tipe_hp):
    """Prompt tanpa JSON karena gateway Sumopod menolak JSON-mode dan role system."""
    feature_lines = "\n".join(
        f"- {name}: pilihan {' | '.join(values)}; default {DEFAULT_FEATURES[name]}"
        for name, values in FEATURE_OPTIONS.items()
    )
    field_lines = "\n".join(f"{name}: <pilih satu nilai>" for name in FEATURE_OPTIONS)
    return f"""Anda adalah lapisan NLP untuk DS-PSO kerusakan iPhone.
Tugas Anda hanya menerjemahkan keluhan pelanggan menjadi nilai fitur yang tersedia.
Jangan mendiagnosis kerusakan dan jangan menciptakan nilai fitur baru. Gunakan default bila tidak disebut.

Pilihan fitur:
{feature_lines}

Tipe: {tipe_hp or 'iPhone'}
Keluhan: {keluhan}

Balas tepat dalam baris berikut, tanpa markdown atau penjelasan tambahan:
{field_lines}
ai_advice: <ringkasan pemeriksaan singkat>"""


def _request_json(url, payload, headers=None, timeout=None):
    if timeout is None:
        timeout = float(os.environ.get("NLP_PROVIDER_TIMEOUT_SECONDS", "8"))
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _clean_json(text):
    cleaned = re.sub(r"^```(?:json)?", "", str(text).strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"```$", "", cleaned.strip()).strip()
    return json.loads(cleaned)


def _parse_sumopod_lines(text):
    """Parse kontrak baris dari Sumopod; nilai tetap divalidasi oleh schema DS-PSO."""
    parsed = {}
    advice = ""
    expected = {name.lower(): name for name in FEATURE_OPTIONS}
    for raw_line in str(text).splitlines():
        line = raw_line.strip().lstrip("-• ").strip()
        if ":" not in line:
            continue
        name, value = (part.strip() for part in line.split(":", 1))
        canonical = expected.get(name.lower())
        if canonical:
            parsed[canonical] = value
        elif name.lower() == "ai_advice":
            advice = value
    missing = [name for name in FEATURE_OPTIONS if name not in parsed]
    if missing:
        raise ValueError(f"Output Sumopod tidak memuat fitur: {', '.join(missing)}")
    return {"features": parsed, "ai_advice": advice}


def _extract_openai_compatible(
    keluhan,
    tipe_hp,
    *,
    api_key_env,
    model_env,
    base_url_env,
    default_model,
    default_base_url,
    supports_system_message=True,
    supports_json_mode=True,
    prompt_builder=_prompt,
    response_parser=_clean_json,
    timeout_env=None,
):
    api_key = os.environ.get(api_key_env, "")
    if not api_key:
        raise RuntimeError(f"{api_key_env} belum dikonfigurasi.")
    model = os.environ.get(model_env, default_model)
    base_url = os.environ.get(base_url_env, default_base_url).rstrip("/")
    prompt = prompt_builder(keluhan, tipe_hp)
    messages = (
        [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
        if supports_system_message
        else [{"role": "user", "content": prompt}]
    )
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
    }
    if supports_json_mode:
        payload["response_format"] = {"type": "json_object"}
    data = _request_json(
        f"{base_url}/chat/completions",
        payload,
        {"Authorization": f"Bearer {api_key}"},
        timeout=float(os.environ.get(timeout_env, os.environ.get("NLP_PROVIDER_TIMEOUT_SECONDS", "8"))) if timeout_env else None,
    )
    text = data["choices"][0]["message"]["content"]
    return response_parser(text), model


def _extract_qwen(keluhan, tipe_hp):
    return _extract_openai_compatible(
        keluhan,
        tipe_hp,
        api_key_env="QWEN_API_KEY",
        model_env="QWEN_MODEL",
        base_url_env="QWEN_BASE_URL",
        default_model="qwen-plus",
        default_base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    )


def _extract_sumopod(keluhan, tipe_hp):
    return _extract_openai_compatible(
        keluhan,
        tipe_hp,
        api_key_env="SUMOPOD_API_KEY",
        model_env="SUMOPOD_MODEL",
        base_url_env="SUMOPOD_BASE_URL",
        default_model="glm-5",
        default_base_url="https://ai.sumopod.com/v1",
        supports_system_message=False,
        supports_json_mode=False,
        prompt_builder=_sumopod_prompt,
        response_parser=_parse_sumopod_lines,
        timeout_env="SUMOPOD_TIMEOUT_SECONDS",
    )


PROVIDERS = {"qwen": _extract_qwen, "sumopod": _extract_sumopod}


def extract_features_llm(keluhan, tipe_hp=""):
    """Ekstrak fitur dengan provider berantai; tidak ada tebakan mapper lokal."""
    configured_chain = os.environ.get("NLP_PROVIDER_CHAIN", "").strip()
    if configured_chain:
        chain = [item.strip().lower() for item in configured_chain.split(",") if item.strip()]
    else:
        chain = [os.environ.get("NLP_PROVIDER", "qwen").strip().lower()]

    attempts = []
    for provider in chain:
        if provider not in PROVIDERS:
            attempts.append({"provider": provider, "success": False, "error": "provider_not_supported"})
            continue
        try:
            parsed, model = PROVIDERS[provider](keluhan, tipe_hp)
            submitted = parsed.get("features") if isinstance(parsed, dict) else None
            if submitted is None and isinstance(parsed, dict):
                submitted = {name: parsed[name] for name in FEATURE_OPTIONS if name in parsed}
            if not isinstance(submitted, dict):
                raise ValueError("Output provider tidak memuat object fitur.")
            features, rejected = validate_features(submitted)
            if rejected:
                raise ValueError(f"Output provider memiliki nilai fitur tidak valid: {rejected}")
            advice = str(parsed.get("ai_advice", "")).strip()[:1000]
            attempts.append({"provider": provider, "success": True})
            if features == DEFAULT_FEATURES:
                return None, advice, "Provider NLP belum menemukan evidence yang dikenali DS-PSO. Jelaskan komponen dan gejalanya lebih spesifik.", {
                    "provider": provider,
                    "model": model,
                    "fallback": False,
                    "validated": True,
                    "no_evidence": True,
                    "provider_chain": chain,
                    "attempts": attempts,
                }
            return features, advice, None, {
                "provider": provider,
                "model": model,
                "fallback": len(attempts) > 1,
                "validated": True,
                "rejected": [],
                "provider_chain": chain,
                "attempts": attempts,
            }
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError, KeyError, RuntimeError) as exc:
            error_code = getattr(exc, "code", None)
            attempts.append({
                "provider": provider,
                "success": False,
                "error": "rate_limited" if error_code == 429 else type(exc).__name__,
                "status_code": error_code,
                "detail": str(exc)[:240],
            })

    return None, None, "Provider NLP gagal memahami keluhan. Periksa API key/kuota lalu coba kembali; diagnosis tidak dijalankan.", {
        "provider": None,
        "fallback": False,
        "provider_chain": chain,
        "attempts": attempts,
    }
