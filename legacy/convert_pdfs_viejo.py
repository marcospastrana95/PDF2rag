import pymupdf4llm
import pymupdf
from pathlib import Path
import re
import sys
import os
import shutil

# === Configuración ===
BASE_DIR = Path(__file__).parent.resolve()
INPUT_DIR = BASE_DIR / "pdfs"
OUTPUT_DIR = BASE_DIR / "md"
IMAGES_DIR = OUTPUT_DIR / "images"
REPORT_FILE = BASE_DIR / "informe_cobertura.txt"

# Rutas comunes de Tesseract en Windows
TESSERACT_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Users\{}\AppData\Local\Tesseract-OCR\tesseract.exe".format(os.getlogin()),
]

# Crear carpetas si no existen
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
IMAGES_DIR.mkdir(parents=True, exist_ok=True)


def get_tesseract_path():
    """Busca tesseract en el PATH o en rutas comunes de Windows."""
    # 1. Intentar encontrarlo en el PATH
    path_in_env = shutil.which("tesseract")
    if path_in_env:
        return path_in_env
    
    # 2. Intentar rutas comunes en Windows
    for path in TESSERACT_PATHS:
        if Path(path).exists():
            return path
    
    return None


def count_words(text: str) -> int:
    """Cuenta palabras reales (ignora símbolos sueltos)."""
    if not text:
        return 0
    return len(re.findall(r"\b\w+\b", text))


def extract_raw_text(pdf_path: Path) -> str:
    """Texto plano del PDF para comparar contra el markdown generado."""
    try:
        doc = pymupdf.open(pdf_path)
        text = "\n".join(page.get_text() for page in doc)
        doc.close()
        return text
    except Exception as e:
        return ""


def convert_pdf(pdf_path: Path, use_ocr: bool = False) -> dict:
    """Convierte un PDF a markdown y devuelve métricas."""
    prefix = "[OCR] " if use_ocr else ""
    print(f"  Procesando: {prefix}{pdf_path.name}")
    
    try:
        # Configuración de conversión
        kwargs = {
            "write_images": True,
            "image_path": str(IMAGES_DIR.relative_to(OUTPUT_DIR)),
            "image_format": "png"
        }
        
        if use_ocr:
            kwargs["ocr"] = True # PyMuPDF4LLM soporta esto si Tesseract está en PATH
            # Si no está en PATH pero lo encontramos, lo añadimos temporalmente
            tess_path = get_tesseract_path()
            if tess_path:
                tess_dir = str(Path(tess_path).parent)
                if tess_dir not in os.environ["PATH"]:
                    os.environ["PATH"] += os.pathsep + tess_dir

        md_text = pymupdf4llm.to_markdown(str(pdf_path), **kwargs)

        # Si no es OCR y el resultado es vacío, reintentar con OCR si es posible
        if not use_ocr and count_words(md_text) < 10:
            tess_path = get_tesseract_path()
            if tess_path:
                print(f"    [!] Poco texto detectado. Reintentando con OCR...")
                return convert_pdf(pdf_path, use_ocr=True)
            else:
                print(f"    [!] Poco texto detectado, pero Tesseract no está instalado.")

        md_filename = f"{pdf_path.stem}.md"
        md_path = OUTPUT_DIR / md_filename
        md_path.write_text(md_text, encoding="utf-8")

        # Métricas
        raw_text = extract_raw_text(pdf_path)
        raw_words = count_words(raw_text)
        md_words = count_words(md_text)
        
        coverage = (md_words / raw_words * 100) if raw_words > 0 else 0
        
        status = "OK"
        if use_ocr:
            status = "OCR_OK"
        elif coverage < 85:
            status = "REVISAR"
        
        if md_words == 0:
            status = "VACÍO/REQUIERE_OCR"

        return {
            "file": pdf_path.name,
            "raw_words": raw_words,
            "md_words": md_words,
            "coverage": coverage,
            "status": status,
            "error": None
        }

    except Exception as e:
        print(f"    [ERROR] Falló la conversión de {pdf_path.name}: {str(e)}")
        return {
            "file": pdf_path.name,
            "raw_words": 0,
            "md_words": 0,
            "coverage": 0,
            "status": "ERROR",
            "error": str(e)
        }


def main():
    tess_path = get_tesseract_path()
    if not tess_path:
        print("\n[AVISO] Tesseract OCR no detectado. Los PDFs escaneados no se leerán bien.")
        print("Instálalo desde: https://github.com/UB-Mannheim/tesseract/wiki")
    else:
        print(f"\n[INFO] Tesseract detectado en: {tess_path}")

    if not INPUT_DIR.exists():
        print(f"\nERROR: No existe la carpeta {INPUT_DIR}")
        return

    pdfs = sorted(INPUT_DIR.glob("*.pdf"))
    if not pdfs:
        print(f"\nNo se encontraron archivos .pdf en {INPUT_DIR}")
        return

    print(f"\nIniciando conversión de {len(pdfs)} PDFs...\n")

    results = [convert_pdf(p) for p in pdfs]

    # === Informe ===
    total_files = len(results)
    success_count = sum(1 for r in results if r["status"] in ["OK", "OCR_OK", "REVISAR"])
    
    lines = [
        "=" * 85,
        "INFORME DE COBERTURA PDF -> MD (LegacIA + OCR)",
        "=" * 85,
        f"{'Archivo':<45} {'Pal. PDF':>10} {'Pal. MD':>10} {'%':>8}  Estado",
        "-" * 85
    ]
    
    for r in results:
        lines.append(
            f"{r['file'][:45]:<45} {r['raw_words']:>10} {r['md_words']:>10} "
            f"{r['coverage']:>7.1f}%  {r['status']}"
        )
    
    lines.append("-" * 85)
    lines.append(f"RESUMEN: Total: {total_files} | Procesados: {success_count}")
    lines.append("-" * 85)
    lines.append("\nNOTAS:")
    lines.append("- OCR_OK: Se usó reconocimiento óptico de caracteres con éxito.")
    lines.append("- VACÍO/REQUIERE_OCR: El archivo parece imagen y Tesseract no está instalado.")
    lines.append("- Las imágenes se han guardado en: ./md/images/")

    REPORT_FILE.write_text("\n".join(lines), encoding="utf-8")
    print("\n" + "\n".join(lines))
    print(f"\nProceso finalizado. Informe en: {REPORT_FILE}\n")


if __name__ == "__main__":
    main()
