"""
refine_markdown.py — Paso 1.5: post-procesado con IA de MD brutos.

Lee cada .md de md_brutos/, detecta si necesita refinado por IA,
y guarda el resultado en md_refinados/.

Uso:
    python refine_markdown.py
    python refine_markdown.py --forzar          (re-procesa aunque ya exista en md_refinados/)
    python refine_markdown.py --archivo "Embalsamamiento de cadáveres.md"
"""

import argparse
import json
import os
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from dotenv import load_dotenv
from tqdm import tqdm

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
load_dotenv()
API_KEY = os.getenv("OPENROUTER_API_KEY")

BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "deepseek/deepseek-v4-flash"
FALLBACK_MODELS = [
    "google/gemini-2.5-flash-lite",
    "google/gemini-2.5-flash",
    "deepseek/deepseek-chat-v3",
]
TEMPERATURE = 0.1
MAX_TOKENS = 16000
TIMEOUT = 300

DIR_BRUTOS = Path("md_brutos")
DIR_REFINADOS = Path("md_refinados")
PROMPT_PATH = Path("prompts/refine_md.md")
INFORME_PATH = Path("informe_refinado.txt")
ESTADO_PATH = Path("refine_estado.json")

# Umbral de palabras para aplicar chunking
CHUNK_WORD_LIMIT = 12_000
CHUNK_SIZE_WORDS = 5_000
CHUNK_OVERLAP_WORDS = 150

# ---------------------------------------------------------------------------
# Estado persistente (qué archivos terminaron correctamente)
# ---------------------------------------------------------------------------
_estado_lock = threading.Lock()


def _cargar_estado() -> dict:
    if ESTADO_PATH.exists():
        try:
            return json.loads(ESTADO_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _guardar_estado_entry(filename: str, entry: dict) -> None:
    with _estado_lock:
        data = _cargar_estado()
        data[filename] = entry
        ESTADO_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _guardar_chunk(filename: str, chunk_idx: int, texto: str, t_in: int, t_out: int, modelo: str) -> None:
    """Persiste un chunk completado sin esperar a que termine el archivo."""
    with _estado_lock:
        data = _cargar_estado()
        entry = data.get(filename) if isinstance(data.get(filename), dict) else {}
        chunks_done = entry.get("chunks_done", [])
        # chunks_done es lista de {idx, texto, t_in, t_out}
        chunks_done = [c for c in chunks_done if c["idx"] != chunk_idx]  # evitar duplicados
        chunks_done.append({"idx": chunk_idx, "texto": texto, "t_in": t_in, "t_out": t_out, "modelo": modelo})
        entry["chunks_done"] = chunks_done
        data[filename] = entry
        ESTADO_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _chunks_guardados(filename: str, estado_map: dict) -> list[dict]:
    """Devuelve los chunks ya procesados de una ejecución anterior."""
    entry = estado_map.get(filename)
    if isinstance(entry, dict):
        return entry.get("chunks_done", [])
    return []


def _estado_completado(filename: str, estado_map: dict) -> bool:
    """True si el archivo terminó con éxito en una ejecución anterior."""
    entry = estado_map.get(filename)
    if isinstance(entry, dict):
        return entry.get("estado") in ("OK", "CHUNKED", "COPY", "SIN_CAMBIO")
    # compatibilidad con formato antiguo (string)
    return entry in ("OK", "CHUNKED", "COPY", "SIN_CAMBIO")

# ---------------------------------------------------------------------------
# Detección de necesidad de refinado
# ---------------------------------------------------------------------------

def _word_count(text: str) -> int:
    return len(text.split())


def needs_refinement(text: str, filename: str) -> tuple[bool, str]:
    """
    Devuelve (necesita_refinado, motivo).
    Evalúa cuatro señales independientes; basta con que una sea True.
    """
    # 1. Mojibake: chars 'Ã' son síntoma de Latin-1 leído como UTF-8
    mojibake_count = text.count("Ã")
    mojibake_ratio = mojibake_count / max(len(text), 1)
    if mojibake_ratio > 0.003:
        return True, f"mojibake ({mojibake_count} ocurrencias, ratio {mojibake_ratio:.4f})"

    # 2. OCR corrupto: palabras con '~' o dígito mezclado con letras (ej. MUSE6, ~f0~)
    words = text.split()
    ocr_corrupt = sum(
        1 for w in words
        if "~" in w or re.search(r"[A-Za-záéíóúñ]{2,}\d|\d[A-Za-záéíóúñ]{2,}", w)
    )
    ocr_ratio = ocr_corrupt / max(len(words), 1)
    if ocr_ratio > 0.01:
        return True, f"OCR corrupto ({ocr_corrupt} palabras, ratio {ocr_ratio:.4f})"

    # 3. Encabezados repetidos de revista: "EL MUSEO CANARIO" > 3 veces
    revista_header_count = len(re.findall(r"EL MUSEO CANARIO", text, re.IGNORECASE))
    if revista_header_count > 3:
        return True, f"encabezados de revista repetidos ({revista_header_count} veces)"

    # 4. Artículo enterrado: documento largo y título ausente en las primeras 500 palabras
    total_words = len(words)
    if total_words > 5_000:
        stem = Path(filename).stem.lower()
        # Extraer 2-3 palabras clave del título (ignorar artículos/preposiciones cortos)
        stopwords = {"de", "del", "la", "el", "en", "los", "las", "un", "una", "y", "a"}
        keywords = [w for w in re.split(r"\W+", stem) if len(w) > 3 and w not in stopwords]
        first_500 = " ".join(words[:500]).lower()
        if keywords and not any(kw in first_500 for kw in keywords):
            return True, f"artículo enterrado (título ausente en primeras 500 palabras, doc={total_words} palabras)"

    return False, "limpio"


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def split_into_chunks(text: str) -> list[str]:
    """Divide el texto en chunks de ~CHUNK_SIZE_WORDS palabras con solape."""
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk_words = words[i: i + CHUNK_SIZE_WORDS]
        chunks.append(" ".join(chunk_words))
        i += CHUNK_SIZE_WORDS - CHUNK_OVERLAP_WORDS
    return chunks


# ---------------------------------------------------------------------------
# Llamada a la API
# ---------------------------------------------------------------------------

def call_api(system_prompt: str, user_prompt: str, model: str = DEFAULT_MODEL) -> tuple[str, int, int, str]:
    """
    Llama a OpenRouter. Devuelve (texto, tokens_in, tokens_out, modelo_usado).
    """
    if not API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY no encontrada en .env")

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://desarrolloia.com",
        "X-Title": "Refine Markdown LegacIA",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "models": FALLBACK_MODELS,
    }

    r = requests.post(BASE_URL, headers=headers, json=payload, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()

    usage = data.get("usage", {})
    tokens_in = usage.get("prompt_tokens", 0)
    tokens_out = usage.get("completion_tokens", 0)
    modelo_usado = data.get("model", model)
    content = data["choices"][0]["message"]["content"]
    if content is None:
        content = ""
    return content, tokens_in, tokens_out, modelo_usado


# ---------------------------------------------------------------------------
# Refinado de un archivo
# ---------------------------------------------------------------------------

def refine_file(md_path: Path, system_prompt: str, forzar: bool, estado_map: dict) -> dict:
    """
    Procesa un archivo .md. Devuelve dict con métricas para el informe.
    """
    out_path = DIR_REFINADOS / md_path.name
    filename = md_path.name

    # SKIP solo si terminó correctamente en ejecución anterior (estado persistente)
    if not forzar and _estado_completado(filename, estado_map):
        return {"archivo": filename, "estado": "SKIP", "chunks": 0,
                "tokens_in": 0, "tokens_out": 0, "modelo": "-"}

    text = md_path.read_text(encoding="utf-8", errors="replace")
    necesita, motivo = needs_refinement(text, filename)

    if not necesita:
        shutil.copy2(md_path, out_path)
        _guardar_estado_entry(filename, {"estado": "COPY"})
        return {"archivo": filename, "estado": "COPY", "chunks": 0,
                "tokens_in": 0, "tokens_out": 0, "modelo": "-", "motivo": motivo}

    # Decidir si chunking
    total_words = _word_count(text)
    if total_words > CHUNK_WORD_LIMIT:
        chunks = split_into_chunks(text)
    else:
        chunks = [text]

    title = Path(filename).stem
    short = title[:35]
    total_in = 0
    total_out = 0
    modelo_usado = DEFAULT_MODEL

    # Recuperar chunks ya completados en ejecuciones anteriores
    previos = {c["idx"]: c for c in _chunks_guardados(filename, estado_map)}
    n_previos = len(previos)

    bar = tqdm(
        total=len(chunks),
        initial=n_previos,
        desc=f"{short:<35}",
        unit="chunk",
        bar_format="{desc} {bar:20} {n}/{total} chunks  [{elapsed}<{remaining}]",
        leave=True,
        dynamic_ncols=False,
        ncols=90,
    )
    if n_previos:
        tqdm.write(f"  RESUME {filename}  ({n_previos}/{len(chunks)} chunks ya completados)")

    refined_parts = [""] * len(chunks)
    tokens_per_chunk: dict[int, tuple[int, int, str]] = {}

    # Rellenar los chunks ya procesados
    for i, c in previos.items():
        refined_parts[i] = c["texto"]
        tokens_per_chunk[i] = (c["t_in"], c["t_out"], c.get("modelo", DEFAULT_MODEL))

    # Indices pendientes de llamada API
    pending_indices = [i for i in range(len(chunks)) if i not in previos]

    def _process_chunk(i: int) -> None:
        chunk = chunks[i]
        chunk_header = f"(chunk {i+1}/{len(chunks)})\n\n" if len(chunks) > 1 else ""
        user_prompt = f"TÍTULO DEL ARTÍCULO DE INTERÉS: {title}\n\n{chunk_header}{chunk}"
        t0 = time.time()
        content, t_in, t_out, mod = call_api(system_prompt, user_prompt)
        elapsed = time.time() - t0
        refined_parts[i] = content
        tokens_per_chunk[i] = (t_in, t_out, mod)
        _guardar_chunk(filename, i, content, t_in, t_out, mod)
        bar.set_postfix_str(f"{t_out} tok  {elapsed:.0f}s", refresh=True)
        bar.update(1)

    # Chunks en paralelo (máx 4 simultáneos por archivo para no saturar)
    max_chunk_workers = min(4, len(pending_indices)) if pending_indices else 1
    with ThreadPoolExecutor(max_workers=max_chunk_workers) as chunk_exec:
        chunk_futures = {chunk_exec.submit(_process_chunk, i): i for i in pending_indices}
        for fut in as_completed(chunk_futures):
            fut.result()  # propaga excepciones

    bar.close()

    for t_in, t_out, mod in tokens_per_chunk.values():
        total_in += t_in
        total_out += t_out
        modelo_usado = mod

    refined_text = "\n\n---\n\n".join(p for p in refined_parts if p)
    out_path.write_text(refined_text, encoding="utf-8")

    words_bruto = _word_count(text)
    words_refinado = _word_count(refined_text)
    ratio = round(words_refinado / max(words_bruto, 1) * 100, 1)

    # Estado según ratio de retención
    if len(chunks) > 1:
        estado = "CHUNKED"
    elif ratio < 30:
        estado = "REVISAR"   # se descartó demasiado contenido
    elif ratio > 98:
        estado = "SIN_CAMBIO"  # el LLM no extrajo nada, devolvió casi todo
    else:
        estado = "OK"

    _guardar_estado_entry(filename, {"estado": estado, "chunks": len(chunks),
                                     "tokens_in": total_in, "tokens_out": total_out})
    return {"archivo": filename, "estado": estado, "chunks": len(chunks),
            "tokens_in": total_in, "tokens_out": total_out, "modelo": modelo_usado,
            "motivo": motivo, "ratio": ratio}


# ---------------------------------------------------------------------------
# Informe
# ---------------------------------------------------------------------------

def escribir_informe(resultados: list[dict]) -> None:
    col_w = 50
    lineas = [
        "INFORME REFINADO DE MARKDOWN",
        "=" * 100,
        f"{'Archivo':<{col_w}} {'Estado':<12} {'Ratio':>6} {'Chunks':>6} {'Tok.In':>8} {'Tok.Out':>8}  Modelo",
        "-" * 100,
    ]
    total_in = total_out = 0
    for r in resultados:
        ratio_str = f"{r.get('ratio', '-'):>5}%" if r.get('ratio') is not None else "     -"
        lineas.append(
            f"{r['archivo']:<{col_w}} {r['estado']:<12} {ratio_str} {r['chunks']:>6} "
            f"{r['tokens_in']:>8} {r['tokens_out']:>8}  {r['modelo']}"
        )
        total_in += r["tokens_in"]
        total_out += r["tokens_out"]
    lineas += [
        "-" * 90,
        f"{'TOTAL':<{col_w}} {'':<10} {'':>6} {total_in:>8} {total_out:>8}",
    ]
    INFORME_PATH.write_text("\n".join(lineas), encoding="utf-8")
    print(f"\nInforme guardado en {INFORME_PATH}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Refina MD brutos con IA.")
    parser.add_argument("--forzar", action="store_true",
                        help="Re-procesa aunque ya exista en md_refinados/")
    parser.add_argument("--archivo", default=None,
                        help="Procesar solo este archivo (nombre con extensión)")
    parser.add_argument("--workers", type=int, default=8,
                        help="Archivos en paralelo (default: 8)")
    args = parser.parse_args()

    if not API_KEY:
        print("ERROR: OPENROUTER_API_KEY no encontrada en .env")
        return

    if not PROMPT_PATH.exists():
        print(f"ERROR: Prompt no encontrado en {PROMPT_PATH}")
        return

    DIR_REFINADOS.mkdir(exist_ok=True)
    system_prompt = PROMPT_PATH.read_text(encoding="utf-8")

    # Seleccionar archivos a procesar
    if args.archivo:
        archivos = [DIR_BRUTOS / args.archivo]
        if not archivos[0].exists():
            print(f"ERROR: No existe {archivos[0]}")
            return
    else:
        archivos = sorted(DIR_BRUTOS.glob("*.md"))

    if not archivos:
        print("No hay archivos .md en md_brutos/")
        return

    estado_map = _cargar_estado()
    completados = [p for p in archivos if not args.forzar and _estado_completado(p.name, estado_map)]
    pendientes = [p for p in archivos if args.forzar or not _estado_completado(p.name, estado_map)]

    # Resumen inicial limpio
    print(f"\n{'─' * 60}")
    print(f"  Total: {len(archivos)} archivos  |  Pendientes: {len(pendientes)}  |  Ya completados: {len(completados)}")
    print(f"{'─' * 60}")
    if completados:
        for p in completados:
            est = estado_map.get(p.name, "?")
            print(f"  SKIP  {p.name}  [{est}]")
        print(f"{'─' * 60}")
    if pendientes:
        print(f"  Procesando {len(pendientes)} archivo(s) con {args.workers} workers en paralelo:\n")
        for p in pendientes:
            print(f"  PEND  {p.name}")
        print(f"{'─' * 60}\n")
    else:
        print("  Nada que procesar.\n")
        return

    def procesar(md_path: Path) -> dict:
        nombre = md_path.name
        try:
            r = refine_file(md_path, system_prompt, args.forzar, estado_map)
            motivo = r.pop("motivo", "")
            estado = r["estado"]
            ratio = r.get("ratio")
            ratio_str = f"  retención {ratio}%" if ratio is not None else ""
            if estado == "COPY":
                tqdm.write(f"  COPY  {nombre}  ({motivo})")
            elif estado == "REVISAR":
                tqdm.write(f"  WARN  {nombre}  → REVISAR ⚠{ratio_str}")
            elif estado == "SIN_CAMBIO":
                tqdm.write(f"  DONE  {nombre}  → SIN_CAMBIO{ratio_str}")
            elif estado in ("OK", "CHUNKED"):
                tqdm.write(f"  DONE  {nombre}  → {estado}{ratio_str}  "
                           f"{r['tokens_in']:,}in / {r['tokens_out']:,}out tok")
            return r
        except Exception as e:
            tqdm.write(f"  ERR   {nombre}  → {e}")
            return {"archivo": nombre, "estado": "ERROR", "chunks": 0,
                    "tokens_in": 0, "tokens_out": 0, "modelo": "-"}

    resultados = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(procesar, p): p for p in pendientes}
        for future in as_completed(futures):
            resultados.append(future.result())

    escribir_informe(resultados)

    ok = sum(1 for r in resultados if r["estado"] in ("OK", "CHUNKED", "SIN_CAMBIO"))
    copy = sum(1 for r in resultados if r["estado"] == "COPY")
    revisar = sum(1 for r in resultados if r["estado"] == "REVISAR")
    errors = sum(1 for r in resultados if r["estado"] == "ERROR")
    total_in = sum(r["tokens_in"] for r in resultados)
    total_out = sum(r["tokens_out"] for r in resultados)

    print(f"\n{'─' * 60}")
    print(f"  Refinados: {ok}  |  Copiados: {copy}  |  A revisar: {revisar}  |  Errores: {errors}")
    print(f"  Tokens: {total_in:,} entrada  /  {total_out:,} salida")
    print(f"{'─' * 60}")
    print(f"  Informe completo: {INFORME_PATH}")


if __name__ == "__main__":
    main()
