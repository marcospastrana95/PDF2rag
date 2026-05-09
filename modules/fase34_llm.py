"""
FASE 3+4 — Reescritura del contenido aplicando reglas de chunking semántico.

Es la ÚNICA fase que usa LLM. Recibe:
- Texto pre-limpio (FASE 1)
- Parámetros calculados (FASE 0)
- Estrategia decidida (FASE 2)

Para documentos grandes divide el texto antes de enviar al LLM:
  1. Por headings ##
  2. Si una sección supera el límite → por párrafos \n\n
  3. Si un párrafo supera el límite → por tamaño fijo (último recurso)
"""

import re
import time
import requests
from pathlib import Path

BASE_URL = "https://openrouter.ai/api/v1/chat/completions"

# Límite de chars de entrada por llamada LLM (~60k chars ≈ 15k tokens de entrada)
# Deja margen suficiente para el system prompt + 16k tokens de salida
CHUNK_CHAR_LIMIT = 60_000
FIXED_CHUNK_SIZE = 50_000  # fallback de último recurso


def _split_by_headings(text: str) -> list[str]:
    """Divide el texto en secciones por headings ##. Preserva el heading con su sección."""
    parts = re.split(r"(?=^#{1,3} )", text, flags=re.MULTILINE)
    return [p for p in parts if p.strip()]


def _split_by_paragraphs(text: str) -> list[str]:
    """Divide una sección larga por párrafos dobles."""
    parts = text.split("\n\n")
    return [p for p in parts if p.strip()]


def _agrupar_hasta_limite(fragmentos: list[str], limite: int) -> list[str]:
    """
    Agrupa fragmentos consecutivos mientras no superen el límite de chars.
    Si un fragmento solo ya supera el límite, va solo (se cortará en nivel 3).
    """
    grupos = []
    actual = ""
    for frag in fragmentos:
        if not actual:
            actual = frag
        elif len(actual) + len(frag) + 2 <= limite:
            actual += "\n\n" + frag
        else:
            grupos.append(actual)
            actual = frag
    if actual:
        grupos.append(actual)
    return grupos


def _split_fixed(text: str, size: int) -> list[str]:
    """Último recurso: corte por tamaño fijo."""
    return [text[i:i + size] for i in range(0, len(text), size)]


def preparar_chunks(texto: str) -> list[str]:
    """
    Divide el texto en chunks que caben en CHUNK_CHAR_LIMIT.
    Árbol de tres niveles: headings → párrafos → tamaño fijo.
    """
    if len(texto) <= CHUNK_CHAR_LIMIT:
        return [texto]

    # Nivel 1: dividir por headings y agrupar mientras quepan
    secciones = _split_by_headings(texto)
    grupos = _agrupar_hasta_limite(secciones, CHUNK_CHAR_LIMIT)

    # Nivel 2: secciones que siguen siendo demasiado grandes → subdividir por párrafos
    resultado = []
    for grupo in grupos:
        if len(grupo) <= CHUNK_CHAR_LIMIT:
            resultado.append(grupo)
        else:
            parrafos = _split_by_paragraphs(grupo)
            subgrupos = _agrupar_hasta_limite(parrafos, CHUNK_CHAR_LIMIT)
            # Nivel 3: párrafos que aún superan el límite → corte fijo
            for sub in subgrupos:
                if len(sub) <= CHUNK_CHAR_LIMIT:
                    resultado.append(sub)
                else:
                    resultado.extend(_split_fixed(sub, FIXED_CHUNK_SIZE))

    return resultado


def _build_user_prompt(texto_limpio: str, diagnostico, decision, proyecto: str, fecha: str,
                       chunk_info: str = "") -> str:
    """Empaqueta los parámetros calculados como contexto del usuario."""
    chunk_line = f"- Parte: {chunk_info}\n" if chunk_info else ""
    return f"""PARÁMETROS CALCULADOS (no decidir, ya están fijados):
- Proyecto: {proyecto}
- Fecha: {fecha}
- Estrategia: {decision.estrategia}
- max_chars: {diagnostico.max_chars}
- overlap: {diagnostico.overlap}
- min_chunk_size: {diagnostico.min_chunk_size}
- Tipo proyecto: {diagnostico.tipo_proyecto}
- Longitud entidades: {diagnostico.longitud_entidad}
{chunk_line}
INSTRUCCIONES:
Reescribe el siguiente texto bruto aplicando las reglas de escritura del system prompt.
Respeta los parámetros calculados (especialmente max_chars).
Si la estrategia es 'fixed', el documento original tiene poca estructura — añade `##` y `---` para mejorar el chunking.

TEXTO BRUTO:
─────────────────────────────────────────────────
{texto_limpio}
─────────────────────────────────────────────────

Devuelve SOLO el .md final."""


_TRUNCATION_ENDINGS = re.compile(r'[.!?\n#\-]$')
MAX_TOKENS_CAP = 32000
MAX_TOKENS_MIN = 4000
MAX_TOKENS_RETRY = 32000


def _calcular_max_tokens(chunk: str) -> int:
    """max_tokens proporcional al chunk: mínimo 4k, máximo 32k, ~50% del tamaño de entrada."""
    estimado = max(MAX_TOKENS_MIN, min(MAX_TOKENS_CAP, len(chunk) // 2))
    return estimado


def _esta_truncado(content: str) -> bool:
    """Detecta si la respuesta del LLM se cortó antes de terminar."""
    last = content.rstrip()
    if not last:
        return True
    return not bool(_TRUNCATION_ENDINGS.search(last[-1]))


def _llamar_api(system_prompt: str, user_prompt: str, model: str, api_key: str,
                max_tokens: int = 16000) -> dict:
    """Una llamada a la API. Devuelve content + usage + truncado."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://desarrolloia.com",
        "X-Title": "RAG Builder Agentia",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "max_tokens": max_tokens,
        "models": [
            "google/gemini-2.5-flash-lite",
            "google/gemini-2.5-flash",
            "deepseek/deepseek-chat-v3",
        ],
    }
    r = requests.post(BASE_URL, headers=headers, json=payload, timeout=300)
    r.raise_for_status()
    data = r.json()
    usage = data.get("usage", {})
    content = data["choices"][0]["message"]["content"]
    return {
        "content": content,
        "tokens_in": usage.get("prompt_tokens", 0),
        "tokens_out": usage.get("completion_tokens", 0),
        "model_used": data.get("model", model),
        "truncado": _esta_truncado(content),
    }


def reescribir(
    texto_limpio: str,
    diagnostico,
    decision,
    prompt_path: Path,
    model: str,
    api_key: str,
    proyecto: str = "Proyecto sin nombrar",
    fecha: str = "2026-04-28",
) -> dict:
    """
    Reescribe el texto completo. Si es demasiado largo, divide en chunks y
    procesa cada uno por separado. Devuelve el resultado concatenado.
    """
    system_prompt = prompt_path.read_text(encoding="utf-8")
    chunks = preparar_chunks(texto_limpio)
    total = len(chunks)

    if total > 1:
        print(f"  [CHUNKING] Documento grande — dividido en {total} partes")

    partes_resultado = []
    tokens_in_total = 0
    tokens_out_total = 0
    model_used = model

    truncados = []

    for i, chunk in enumerate(chunks, start=1):
        chunk_info = f"{i}/{total}" if total > 1 else ""
        if total > 1:
            print(f"  [PARTE {i}/{total}] {len(chunk):,} chars...", end=" ", flush=True)

        user_prompt = _build_user_prompt(
            chunk, diagnostico, decision, proyecto, fecha, chunk_info
        )
        max_tokens = _calcular_max_tokens(chunk)
        result = _llamar_api(system_prompt, user_prompt, model, api_key, max_tokens)

        # Reintento si respuesta truncada con tokens ampliados
        if result["truncado"] and max_tokens < MAX_TOKENS_RETRY:
            print(f"\n  [REINTENTO] Respuesta truncada — ampliando a {MAX_TOKENS_RETRY} tokens...")
            result = _llamar_api(system_prompt, user_prompt, model, api_key, MAX_TOKENS_RETRY)

        if result["truncado"]:
            truncados.append(i)
            print(f"\n  [AVISO] Parte {i}/{total} posiblemente truncada tras reintento")

        if total > 1:
            print(f"{result['tokens_out']} tokens salida")

        partes_resultado.append(result["content"])
        tokens_in_total += result["tokens_in"]
        tokens_out_total += result["tokens_out"]
        model_used = result["model_used"]

        if i < total:
            time.sleep(1)

    contenido_final = "\n\n".join(partes_resultado)

    return {
        "content": contenido_final,
        "tokens_in": tokens_in_total,
        "tokens_out": tokens_out_total,
        "model_used": model_used,
        "chunks": total,
        "truncados": truncados,
    }
