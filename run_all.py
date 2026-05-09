"""
run_all.py — Pipeline completo PDF → RAG en una sola ejecución.

Ejecuta en cadena:
  Etapa 1: convert_pdfs.py      (PDF → MD bruto)
  Etapa 1.5: refine_markdown.py (MD bruto → MD refinado con IA, solo si necesario)
  Etapa 2: rag_builder.py       (MD refinado → MD RAG)

Uso:
    python run_all.py --proyecto MuseIA
    python run_all.py --proyecto MuseIA --model deepseek/deepseek-v4-flash
    python run_all.py --proyecto MuseIA --solo-etapa 1    (solo conversión PDF→MD)
    python run_all.py --proyecto MuseIA --solo-etapa 1.5  (solo refinado con IA)
    python run_all.py --proyecto MuseIA --solo-etapa 2    (solo MD→RAG)
    python run_all.py --proyecto MuseIA --sin-refinado    (salta etapa 1.5)
"""

import argparse
import subprocess
import sys
from pathlib import Path


def run(cmd: list, descripcion: str) -> int:
    print(f"\n{'═' * 70}")
    print(f"▶  {descripcion}")
    print(f"   {' '.join(cmd)}")
    print(f"{'═' * 70}\n")
    result = subprocess.run(cmd)
    return result.returncode


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--proyecto", required=True, help="Nombre del proyecto (ej: MuseIA, LegacIA-Ruta3)")
    p.add_argument("--model", default="deepseek/deepseek-v4-flash",
                   help="Modelo OpenRouter para Etapa 2")
    p.add_argument("--solo-etapa", default=None, choices=["1", "1.5", "2"],
                   help="Ejecutar solo la etapa indicada")
    p.add_argument("--sin-refinado", action="store_true",
                   help="Saltar Etapa 1.5 (refinado con IA)")
    p.add_argument("--dry-run", action="store_true",
                   help="Solo Etapa 2 dry-run (no llama a la API)")
    args = p.parse_args()

    py = sys.executable  # python del venv actual

    # === Etapa 1: PDF → MD bruto ===
    if args.solo_etapa in (None, "1"):
        if not Path("pdfs").exists() or not list(Path("pdfs").glob("*.pdf")):
            print("ERROR: No hay PDFs en ./pdfs/")
            sys.exit(1)

        rc = run([py, "convert_pdfs.py"], "ETAPA 1 — Conversión PDF → MD bruto")
        if rc != 0:
            print(f"\nETAPA 1 falló (código {rc}). Abortando.")
            sys.exit(rc)

    # === Etapa 1.5: MD bruto → MD refinado (IA) ===
    if args.solo_etapa in (None, "1.5") and not args.sin_refinado:
        if not Path("md_brutos").exists() or not list(Path("md_brutos").glob("*.md")):
            print("ERROR: No hay .md en ./md_brutos/. ¿Ejecutaste la Etapa 1?")
            sys.exit(1)

        rc = run([py, "refine_markdown.py"], "ETAPA 1.5 — Refinado con IA (md_brutos → md_refinados)")
        if rc != 0:
            print(f"\nETAPA 1.5 falló (código {rc}). Abortando.")
            sys.exit(rc)

    # === Etapa 2: MD refinado → MD RAG ===
    if args.solo_etapa in (None, "2"):
        # Usar md_refinados/ si existe, si no md_brutos/ como fallback
        src_dir = "md_refinados" if Path("md_refinados").exists() and list(Path("md_refinados").glob("*.md")) else "md_brutos"
        if not list(Path(src_dir).glob("*.md")):
            print(f"ERROR: No hay .md en ./{src_dir}/")
            sys.exit(1)

        cmd = [py, "rag_builder_legacia.py", "--proyecto", args.proyecto,
               "--model", args.model, "--input-dir", src_dir]
        if args.dry_run:
            cmd.append("--dry-run")
        rc = run(cmd, f"ETAPA 2 — {src_dir}/ → MD RAG (LegacIA Edition)")
        if rc != 0:
            print(f"\nETAPA 2 falló (código {rc}).")
            sys.exit(rc)

    print(f"\n{'=' * 70}")
    print("✓ Pipeline completo terminado")
    print(f"{'=' * 70}")
    print("Outputs:")
    print("  - md_brutos/          (extracción PDF)")
    print("  - md_refinados/       (mejorado con IA)")
    print("  - md_rag/             (final, listos para Agentia)")
    print("  - informe_cobertura.txt")
    print("  - informe_refinado.txt")
    print("  - informe_global_legacia.txt")


if __name__ == "__main__":
    main()
