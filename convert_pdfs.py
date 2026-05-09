import pymupdf4llm
import pymupdf
from pathlib import Path
import re
import os
import shutil
import subprocess
import tempfile
import pdfplumber
import pandas as pd
import base64
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

load_dotenv()
# === Configuracion ===
BASE_DIR = Path(__file__).parent.resolve()
INPUT_DIR = BASE_DIR / "pdfs"
OUTPUT_DIR = BASE_DIR / "md_brutos"
REPORT_FILE = BASE_DIR / "informe_cobertura.txt"

# Umbral: si el PDF tiene menos palabras que esto por pagina, se trata como escaneado
PALABRAS_POR_PAGINA_MINIMO = 30

# Rutas comunes de Tesseract en Windows
TESSERACT_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Users\{}\AppData\Local\Tesseract-OCR\tesseract.exe".format(os.getlogin()),
]

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# --- Utilidades ---

def get_tesseract_path() -> str | None:
    candidates = [shutil.which("tesseract")] + TESSERACT_PATHS
    for path in candidates:
        if not path or not Path(path).exists():
            continue
        # Verificar que el paquete de español está instalado
        try:
            check = subprocess.run(
                [path, "--list-langs"],
                capture_output=True, text=True, encoding="utf-8", errors="ignore",
                timeout=10,
            )
            langs = check.stdout + check.stderr
            if "spa" not in langs:
                print(f"[AVISO] Tesseract encontrado en {path} pero sin paquete 'spa' (español).")
                print("        Instala el paquete de idioma desde: https://github.com/tesseract-ocr/tessdata")
                return None
        except Exception:
            return None
        return path
    return None


def count_words(text: str) -> int:
    if not text:
        return 0
    return len(re.findall(r"\b\w+\b", text))


def es_escaneado(pdf_path: Path) -> bool:
    """Detecta si el PDF es escaneado."""
    try:
        doc = pymupdf.open(pdf_path)
        n_pages = len(doc)
        if n_pages == 0:
            doc.close()
            return True
        total_words = sum(count_words(page.get_text()) for page in doc)
        doc.close()
        return (total_words / n_pages) < PALABRAS_POR_PAGINA_MINIMO
    except Exception:
        return True


def _strip_long_citations(text: str) -> str:
    """Elimina citas bibliograficas inline largas (≥2 punto y coma dentro de parentesis)."""
    def _replace(m):
        content = m.group(0)
        if content.count(";") >= 2:
            return ""
        return content
    return re.sub(r"\([^)]{80,}\)", _replace, text)


_INDEX_HEADERS = re.compile(
    r"#{1,3}\s*\*{0,2}\s*(?:[ÍI]NDICE|[ÍI]ndice|Sumario|SUMARIO|Contenido|CONTENIDO|"
    r"Tabla de contenidos?|Table of contents?)\s*\*{0,2}\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_NEXT_HEADER = re.compile(r"^#{1,3}\s", re.MULTILINE)
# Línea de tabla Markdown (empieza con |) o separador de tabla (|---|)
_TABLE_LINE = re.compile(r"^\|", re.MULTILINE)


def _remove_index_block(text: str) -> str:
    """Elimina el bloque de índice/sumario completo (desde el encabezado hasta el siguiente ##).
    Maneja tanto índices en texto plano como índices formateados como tablas Markdown."""
    m = _INDEX_HEADERS.search(text)
    if not m:
        return text
    start = m.start()
    rest = text[m.end():]
    next_h = _NEXT_HEADER.search(rest)
    end = m.end() + next_h.start() if next_h else len(text)
    return text[:start] + "\n" + text[end:]


def clean_markdown_text(text: str) -> str:
    """Limpieza basica del texto bruto para RAG."""
    # 1. Quitar bibliografía (todo lo que venga después de ciertos encabezados)
    bib_pattern = r"\n#+\s*\*{0,2}\s*(?:Referencias|Bibliografía|BIBLIOGRAFÍA|Referencias bibliográficas|Fuentes|Fuentes consultadas|Notas y referencias)\s*\*{0,2}.*"
    match = re.search(bib_pattern, text, flags=re.IGNORECASE | re.DOTALL)
    if match:
        text = text[:match.start()]

    # 2. Eliminar bloques de índice/sumario completos (texto plano o tabla Markdown)
    text = _remove_index_block(text)

    # 3a. Eliminar líneas de índice sueltas: puntos de relleno (.... o . . . .) + número de página
    text = re.sub(r"^.+?(?:\.{2,}|(?:\. ){3,}\.?)\s*\d+\s*$", "", text, flags=re.MULTILINE)

    # 3b. Eliminar bloques de tabla Markdown que sean índices (celdas con puntos de relleno)
    # Detecta grupos de líneas de tabla donde alguna celda contiene puntos de relleno o solo números
    def _strip_index_tables(t: str) -> str:
        lines = t.split("\n")
        result = []
        i = 0
        while i < len(lines):
            line = lines[i]
            # ¿Es inicio de bloque de tabla?
            if line.startswith("|"):
                # Recoger todo el bloque de tabla
                block = []
                while i < len(lines) and lines[i].startswith("|"):
                    block.append(lines[i])
                    i += 1
                # ¿Es un índice? Solo contar filas de datos (excluir separadores |---|)
                # y ver qué ratio tiene puntos de relleno
                data_lines = [l for l in block if not re.match(r"^\|[-: |]+\|$", l)]
                dot_lines = sum(1 for l in data_lines if re.search(r"\.{3,}|(?:\. ){3,}", l))
                ratio_dots = dot_lines / max(len(data_lines), 1)
                if ratio_dots > 0.3:
                    pass  # tabla-índice, descartar
                else:
                    result.extend(block)
            else:
                result.append(line)
                i += 1
        return "\n".join(result)

    text = _strip_index_tables(text)

    # 4. Eliminar imágenes (patrón genérico y placeholders de pymupdf4llm)
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
    text = re.sub(r"\*\*==>.*?intentionally omitted.*?<==\*\*", "", text, flags=re.IGNORECASE)

    # 5. Eliminar leyendas de figura sueltas (línea que empieza con "Figura N.")
    text = re.sub(r"^Figura\s+\d+[\.:].*$", "", text, flags=re.MULTILINE | re.IGNORECASE)

    # 6. Eliminar notas al pie intercaladas (blockquotes con número al inicio: "> 1 texto")
    text = re.sub(r"^>\s*\d+\s+.+$", "", text, flags=re.MULTILINE)

    # 7. Eliminar notas a pie de página como texto plano (línea que empieza con dígito + texto largo)
    # Patrón: inicio de línea, número, espacio/tab, texto — solo si la línea parece nota (no titular)
    text = re.sub(r"^\d{1,2}(?:También|Además|Para|Véase|Ver |Cf\.|Ibid|Op\. ?cit|Referido|Nota)\b.+$",
                  "", text, flags=re.MULTILINE | re.IGNORECASE)

    # 8. Eliminar citas bibliográficas inline largas que interrumpen el flujo
    text = _strip_long_citations(text)

    # 9. Reparar palabras con guión de corte de línea
    text = re.sub(r"(\w+)-\n(\w+)", r"\1\2", text)

    # 10. Eliminar líneas que solo son números de página
    text = re.sub(r"^\s*\d+\s*$", "", text, flags=re.MULTILINE)

    # 11. Colapsar espacios en blanco excesivos
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# --- Conversion nativa (pymupdf4llm) ---

MAX_CELL_LEN = 120
MAX_TABLE_ROWS = 40


def table_to_markdown(table_data):
    """Convierte una lista de listas en una tabla Markdown usando pandas."""
    if not table_data or len(table_data) < 2:
        return ""

    # Normalizar celdas: None → "", truncar largas, uniformar ancho de filas
    n_cols = max(len(row) for row in table_data)
    cleaned = []
    for row in table_data:
        padded = list(row) + [""] * (n_cols - len(row))
        normalized = []
        for c in padded:
            s = str(c).strip() if c is not None else ""
            if len(s) > MAX_CELL_LEN:
                s = s[:MAX_CELL_LEN] + "…"
            normalized.append(s)
        cleaned.append(normalized)

    header = cleaned[0]
    body = cleaned[1:]
    total_rows = len(body)
    truncated = total_rows > MAX_TABLE_ROWS
    if truncated:
        body = body[:MAX_TABLE_ROWS]

    try:
        # Desambiguar columnas con nombre vacío o duplicado
        seen = {}
        safe_header = []
        for i, c in enumerate(header):
            key = c if c else f"Col_{i}"
            if key in seen:
                seen[key] += 1
                key = f"{key}_{seen[key]}"
            else:
                seen[key] = 0
            safe_header.append(key)

        df = pd.DataFrame(body, columns=safe_header)
        md = df.to_markdown(index=False)
    except Exception:
        rows_str = [" | ".join(row) for row in [header] + body]
        separator = "|---" * n_cols + "|"
        md = rows_str[0] + "\n" + separator + "\n" + "\n".join(rows_str[1:])

    result = "\n" + md + "\n"
    if truncated:
        result += f"\n> **Tabla truncada** — se muestran {MAX_TABLE_ROWS} de {total_rows} filas.\n"
    return result

def _merge_continuation_tables(pages_tables: list[list]) -> list[list | None]:
    """Fusiona tablas consecutivas de páginas distintas si comparten el mismo encabezado.

    pages_tables: lista de (table_data | None) por página.
    Devuelve la misma lista pero con tablas de continuación marcadas como None
    y sus filas absorbidas en la primera tabla del grupo.
    """
    result = list(pages_tables)
    i = 0
    while i < len(result) - 1:
        curr = result[i]
        nxt = result[i + 1]
        if curr is None or nxt is None:
            i += 1
            continue
        # Si los encabezados coinciden, la siguiente es continuación
        if curr[0] == nxt[0]:
            result[i] = curr + nxt[1:]  # absorber filas de continuación
            result[i + 1] = None        # marcar como ya absorbida
        i += 1
    return result


def convert_native(pdf_path: Path) -> str:
    """Extrae texto nativo con pymupdf4llm e intercala tablas de pdfplumber por pagina."""
    kwargs = {
        "write_images": False,
        "extract_tables": False,
        "header": False,
        "footer": False,
    }

    try:
        with pdfplumber.open(pdf_path) as pdf:
            n_pages = len(pdf.pages)
            # Recopilar texto y tablas por página en dos pasadas
            pages_text = []
            pages_first_table = []  # primera tabla de cada página (para fusión multi-pág)

            for i in range(n_pages):
                if i % 10 == 0:
                    print(f"      - Procesando pagina {i+1}/{n_pages}...")
                page_md = pymupdf4llm.to_markdown(str(pdf_path), pages=[i], **kwargs)
                tables = pdf.pages[i].extract_tables()
                pages_text.append(page_md)
                # Solo guardamos la primera tabla por página para la detección de continuación
                pages_first_table.append(tables[0] if tables else None)

            # Fusionar tablas multi-página por encabezado común
            merged_tables = _merge_continuation_tables(pages_first_table)

            full_md = []
            for i in range(n_pages):
                page_md = pages_text[i]
                table_data = merged_tables[i]
                if table_data is not None:
                    md_table = table_to_markdown(table_data)
                    if md_table.strip():
                        page_md += "\n\n" + md_table
                full_md.append(page_md)

    except Exception as e:
        print(f"    [!] Error en extraccion hibrida: {e}")
        return pymupdf4llm.to_markdown(str(pdf_path), **kwargs)

    return "\n\n".join(full_md)


# --- Conversion OCR (Gemini Vision) ---

GEMINI_OCR_MODEL = "google/gemini-2.5-flash-lite"
GEMINI_OCR_PROMPT = (
    "Extrae todo el texto visible en esta imagen de página escaneada. "
    "Mantén la estructura original: párrafos, títulos, listas. "
    "No añadas explicaciones ni comentarios. Solo el texto extraído."
)


def _page_to_base64(page: pymupdf.Page) -> str:
    mat = pymupdf.Matrix(300 / 72, 300 / 72)
    pix = page.get_pixmap(matrix=mat, colorspace=pymupdf.csGRAY)
    return base64.b64encode(pix.tobytes("png")).decode("utf-8")


def _gemini_ocr_page(b64_img: str, api_key: str) -> str:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://desarrolloia.com",
        "X-Title": "Convert PDFs LegacIA",
    }
    payload = {
        "model": GEMINI_OCR_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": GEMINI_OCR_PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_img}"}},
                ],
            }
        ],
        "temperature": 0.0,
        "max_tokens": 4096,
    }
    r = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers=headers, json=payload, timeout=120,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def convert_ocr_gemini(pdf_path: Path, api_key: str, workers: int = 6) -> tuple[str, int, int]:
    """OCR con Gemini Vision en paralelo. Devuelve (markdown, págs_con_texto, total_págs)."""
    doc = pymupdf.open(pdf_path)
    total_paginas = len(doc)

    # Pre-renderizar todas las páginas a base64
    pages_b64 = []
    for i in range(total_paginas):
        page = doc.load_page(i)
        # Dividir páginas dobles
        if page.rect.width > page.rect.height * 1.1:
            mid = page.rect.width / 2
            clips = [
                pymupdf.Rect(0, 0, mid, page.rect.height),
                pymupdf.Rect(mid, 0, page.rect.width, page.rect.height),
            ]
            for clip in clips:
                mat = pymupdf.Matrix(300 / 72, 300 / 72)
                pix = page.get_pixmap(matrix=mat, colorspace=pymupdf.csGRAY, clip=clip)
                pages_b64.append((i, base64.b64encode(pix.tobytes("png")).decode("utf-8")))
        else:
            pages_b64.append((i, _page_to_base64(page)))
    doc.close()

    results = {}

    def ocr_page(item):
        idx, b64 = item
        text = _gemini_ocr_page(b64, api_key)
        return idx, text

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(ocr_page, item): item for item in pages_b64}
        for future in as_completed(futures):
            idx, text = future.result()
            results.setdefault(idx, []).append(text)

    paginas_con_texto = 0
    parts = []
    for i in sorted(results.keys()):
        page_text = "\n\n".join(results[i])
        cleaned = clean_markdown_text(page_text)
        if count_words(cleaned) >= 20:
            paginas_con_texto += 1
        parts.append(cleaned)
        parts.append("\n\n---\n\n")

    texto_final = "".join(parts)
    md = f"# {pdf_path.stem}\n\n{texto_final}"
    return md, paginas_con_texto, total_paginas


# --- Conversion OCR (Tesseract directo) ---

def convert_ocr(pdf_path: Path, tess_path: str) -> tuple[str, int, int]:
    """Extrae texto usando Tesseract dividiendo paginas dobles.

    Devuelve (markdown, paginas_con_texto, total_paginas).
    """
    doc = pymupdf.open(pdf_path)
    full_text = []
    total_paginas = len(doc)
    paginas_con_texto = 0

    try:
        for i in range(total_paginas):
            page = doc.load_page(i)

            es_doble = page.rect.width > (page.rect.height * 1.1)
            clips = []
            if es_doble:
                mid = page.rect.width / 2
                clips.append(pymupdf.Rect(0, 0, mid, page.rect.height))
                clips.append(pymupdf.Rect(mid, 0, page.rect.width, page.rect.height))
            else:
                clips.append(page.rect)

            page_words = 0
            for clip in clips:
                mat = pymupdf.Matrix(300 / 72, 300 / 72)
                pix = page.get_pixmap(matrix=mat, colorspace=pymupdf.csGRAY, clip=clip)
                tmp_name = f"tmp_page_{i}_{clips.index(clip)}.png"
                tmp_path = os.path.join(tempfile.gettempdir(), tmp_name)
                pix.save(tmp_path)

                try:
                    cmd = [tess_path, tmp_path, "stdout", "-l", "spa", "--psm", "3"]
                    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
                    if result.stdout:
                        page_words += count_words(result.stdout)
                        full_text.append(clean_markdown_text(result.stdout))
                finally:
                    if os.path.exists(tmp_path):
                        try:
                            os.remove(tmp_path)
                        except:
                            pass

            if page_words >= 20:
                paginas_con_texto += 1

            full_text.append("\n\n---\n\n")

    finally:
        doc.close()

    texto_final = "".join(full_text)
    md = f"# {pdf_path.stem}\n\n{texto_final}"
    return md, paginas_con_texto, total_paginas


# --- Funcion principal de conversion ---

def convert_pdf(pdf_path: Path, tess_path: str | None) -> dict:
    print(f"  Procesando: {pdf_path.name}")

    try:
        scaneado = es_escaneado(pdf_path)
        metodo = "native"

        ocr_pags_con_texto = 0
        ocr_total_pags = 0

        if scaneado:
            gemini_api_key = os.getenv("OPENROUTER_API_KEY")
            if gemini_api_key:
                print(f"    [OCR] Escaneado detectado -> Gemini Vision (paralelo)")
                try:
                    md_text, ocr_pags_con_texto, ocr_total_pags = convert_ocr_gemini(pdf_path, gemini_api_key)
                    metodo = "ocr_gemini"
                except Exception as e:
                    print(f"    [!] Gemini OCR falló ({e}), usando Tesseract como fallback")
                    if tess_path:
                        md_text, ocr_pags_con_texto, ocr_total_pags = convert_ocr(pdf_path, tess_path)
                        metodo = "ocr_tess_fallback"
                    else:
                        md_text = convert_native(pdf_path)
                        metodo = "native_fallback"
            elif tess_path:
                print(f"    [OCR] Escaneado detectado -> Tesseract directo")
                md_text, ocr_pags_con_texto, ocr_total_pags = convert_ocr(pdf_path, tess_path)
                metodo = "ocr"
            else:
                print(f"    [!] Escaneado detectado pero sin OCR disponible -> native")
                md_text = convert_native(pdf_path)
                metodo = "native_fallback"
        else:
            md_text = convert_native(pdf_path)
            metodo = "native"

        md_words = count_words(md_text)

        # Cobertura preliminar (solo texto nativo, sin líneas de tabla) para decidir reintento
        doc = pymupdf.open(pdf_path)
        raw_text_check = "\n".join(page.get_text() for page in doc)
        doc.close()
        raw_words_check = count_words(raw_text_check)
        md_text_only = re.sub(r"^\|.*\|$", "", md_text, flags=re.MULTILINE)
        md_words_text = count_words(md_text_only)
        coverage_pre = (md_words_text / raw_words_check * 100) if raw_words_check > 0 else 100

        if md_words < 100 or coverage_pre < 10:
            if raw_words_check > 500:
                gemini_api_key = os.getenv("OPENROUTER_API_KEY")
                if gemini_api_key:
                    print(f"    [!] Cobertura muy baja ({coverage_pre:.1f}%) -> Reintentando con Gemini OCR")
                    try:
                        md_text, ocr_pags_con_texto, ocr_total_pags = convert_ocr_gemini(pdf_path, gemini_api_key)
                        metodo = "ocr_gemini_retry"
                    except Exception as e:
                        print(f"    [!] Gemini OCR retry falló ({e})")
                        if tess_path:
                            md_text, ocr_pags_con_texto, ocr_total_pags = convert_ocr(pdf_path, tess_path)
                            metodo = "ocr_retry"
                elif tess_path:
                    print(f"    [!] Cobertura muy baja ({coverage_pre:.1f}%) -> Reintentando con OCR")
                    md_text, ocr_pags_con_texto, ocr_total_pags = convert_ocr(pdf_path, tess_path)
                    metodo = "ocr_retry"
                md_words = count_words(md_text)

        md_text = clean_markdown_text(md_text)
        md_path = OUTPUT_DIR / f"{pdf_path.stem}.md"
        md_path.write_text(md_text, encoding="utf-8")

        if metodo in ("ocr_gemini", "ocr_gemini_retry", "ocr", "ocr_tess_fallback", "ocr_retry"):
            raw_words = ocr_total_pags  # número de páginas como referencia
            coverage = min((ocr_pags_con_texto / ocr_total_pags * 100) if ocr_total_pags > 0 else 0, 100.0)
            status = "OCR_OK" if coverage >= 80 else "OCR_PARCIAL"
        else:
            doc = pymupdf.open(pdf_path)
            raw_text = "\n".join(page.get_text() for page in doc)
            doc.close()
            raw_words = count_words(raw_text)
            md_text_only_final = re.sub(r"^\|.*\|$", "", md_text, flags=re.MULTILINE)
            md_words_final = count_words(md_text_only_final)
            coverage = min((md_words_final / raw_words * 100) if raw_words > 0 else 0, 100.0)
            status = "OK" if coverage >= 85 else "REVISAR"

        if md_words == 0:
            status = "VACIO"

        return {
            "file": pdf_path.name,
            "metodo": metodo,
            "raw_words": raw_words,
            "md_words": md_words,
            "coverage": coverage,
            "status": status,
            "error": None,
        }

    except Exception as e:
        # Limpiar mensaje de error por si tiene caracteres raros
        error_msg = str(e).encode('ascii', 'ignore').decode('ascii')
        print(f"    [ERROR] {error_msg}")
        return {
            "file": pdf_path.name,
            "metodo": "error",
            "raw_words": 0,
            "md_words": 0,
            "coverage": 0,
            "status": "ERROR",
            "error": error_msg,
        }


# --- Main ---

def main():
    tess_path = get_tesseract_path()
    if not tess_path:
        print("\n[AVISO] Tesseract OCR no detectado.")
    else:
        print(f"\n[INFO] Tesseract detectado")

    if not INPUT_DIR.exists():
        print(f"\nERROR: No existe {INPUT_DIR}")
        return

    pdfs = sorted(INPUT_DIR.glob("*.pdf"))
    if not pdfs:
        print(f"\nNo hay PDFs en {INPUT_DIR}")
        return

    pdfs_to_process = []
    for p in pdfs:
        if (OUTPUT_DIR / f"{p.stem}.md").exists():
            print(f"  [SKIP] {p.name}")
        else:
            pdfs_to_process.append(p)

    if not pdfs_to_process:
        print("\nTodos los archivos ya estan convertidos.")
        return

    print(f"\nConvirtiendo {len(pdfs_to_process)} PDFs nuevos...\n")
    results = [convert_pdf(p, tess_path) for p in pdfs_to_process]

    lines = [
        "=" * 80,
        "INFORME DE COBERTURA PDF -> MD",
        "=" * 80,
        f"{'Archivo':<40} {'Metodo':<16} {'Pal.PDF':>8} {'Pal.MD':>8} {'%':>7}  Estado",
        "-" * 80,
    ]
    for r in results:
        lines.append(
            f"{r['file'][:40]:<40} {r['metodo']:<16} {r['raw_words']:>8} "
            f"{r['md_words']:>8} {r['coverage']:>6.1f}%  {r['status']}"
        )
    lines += [
        "-" * 80,
        f"Total: {len(results)} | "
        f"OK: {sum(1 for r in results if r['status'] == 'OK')} | "
        f"OCR_OK: {sum(1 for r in results if r['status'] == 'OCR_OK')} | "
        f"OCR_PARCIAL: {sum(1 for r in results if r['status'] == 'OCR_PARCIAL')} | "
        f"REVISAR: {sum(1 for r in results if r['status'] == 'REVISAR')} | "
        f"ERROR: {sum(1 for r in results if r['status'] == 'ERROR')}",
        "",
        "NOTAS:",
        "- native: texto extraido directamente (% = palabras texto / palabras PDF).",
        "- ocr_gemini: Gemini Vision en paralelo (% = paginas con >=20 palabras / total).",
        "- ocr / ocr_retry: Tesseract fallback (% = paginas con >=20 palabras / total).",
        "- OCR_PARCIAL: menos del 80% de paginas con texto legible.",
        "- native_fallback: PDF escaneado sin OCR disponible.",
    ]

    report = "\n".join(lines)
    REPORT_FILE.write_text(report, encoding="utf-8")
    print("\n" + report)


if __name__ == "__main__":
    main()
