"""
RAG Builder Agentia — Pipeline híbrido.

Arquitectura:
  FASE 0 → CÓDIGO (diagnóstico + parámetros)
  FASE 1 → CÓDIGO (limpieza preclean)
  FASE 2 → CÓDIGO (decisión de estrategia)
  FASE 3+4 → LLM BARATO (única llamada)
  FASE 5 → CÓDIGO (parámetros recuperación)
  FASE 6 → CÓDIGO (validación checklist)
  FASE 7 → CÓDIGO (entrega + informe)

Uso básico:
    python rag_builder.py --proyecto MuseIA
    python rag_builder.py --proyecto MuseIA --model deepseek/deepseek-v4-flash
    python rag_builder.py --proyecto MuseIA --dry-run
    python rag_builder.py --proyecto MuseIA --only doc1.md
"""

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from dataclasses import asdict

from dotenv import load_dotenv

from modules.fase0_diagnostico import diagnosticar, imprimir_diagnostico
from modules.fase1_extraccion import preclean
from modules.fase2_estrategia import decidir_estrategia, imprimir_decision
from modules.fase34_llm import reescribir
from modules.fase6_checklist import validar, imprimir_checklist, fix_obvios
from modules.fase7_entrega import generar_nombre, recomendar_recuperacion, generar_informe


load_dotenv()
API_KEY = os.getenv("OPENROUTER_API_KEY")

DEFAULT_MODEL = "google/gemini-2.5-flash-lite"

COST_PER_M = {
    "deepseek/deepseek-v4-flash":     {"in": 0.13, "out": 0.26},
    "deepseek/deepseek-chat-v3":      {"in": 0.30, "out": 0.83},
    "google/gemini-2.5-flash-lite":   {"in": 0.23, "out": 1.40},
    "google/gemini-2.5-flash":        {"in": 0.28, "out": 2.33},
    "anthropic/claude-sonnet-4.5":    {"in": 2.80, "out": 14.0},
}


def estimar_coste(t_in: int, t_out: int, model: str) -> float:
    rates = COST_PER_M.get(model, {"in": 1, "out": 5})
    return (t_in * rates["in"] + t_out * rates["out"]) / 1_000_000


def procesar_archivo(
    md_path: Path,
    output_dir: Path,
    prompt_path: Path,
    model: str,
    proyecto: str,
    indice: int,
    fecha: str,
) -> dict:
    """Pipeline completo para un archivo."""
    print(f"\n{'═' * 60}")
    print(f"Procesando: {md_path.name}")
    print(f"{'═' * 60}")

    # === FASE 0: Diagnóstico (código) ===
    diag = diagnosticar(md_path)
    print(imprimir_diagnostico(diag))

    # === FASE 1: Preclean (código) ===
    raw_text = md_path.read_text(encoding="utf-8")
    cleaned, stats_clean = preclean(raw_text)
    print(f"[FASE 1] Preclean: -{stats_clean['chars_eliminados']} chars "
          f"({stats_clean['lineas_repetidas_eliminadas']} líneas repetidas eliminadas)")

    # Re-diagnosticar tras limpieza para que FASE 2 tenga datos correctos
    md_path_temp = md_path.parent / f".tmp_{md_path.name}"
    md_path_temp.write_text(cleaned, encoding="utf-8")
    diag = diagnosticar(md_path_temp)
    md_path_temp.unlink()
    diag.archivo = md_path.name  # mantener nombre real

    # === FASE 2: Estrategia (código) ===
    dec = decidir_estrategia(diag)
    print(imprimir_decision(dec))

    # === FASE 3+4: LLM ===
    print(f"[FASE 3+4] Llamando a {model}...")
    t0 = time.time()
    try:
        result = reescribir(
            texto_limpio=cleaned,
            diagnostico=diag,
            decision=dec,
            prompt_path=prompt_path,
            model=model,
            api_key=API_KEY,
            proyecto=proyecto,
            fecha=fecha,
        )
    except Exception as e:
        print(f"  ERROR: {e}")
        return {"file": md_path.name, "status": "ERROR", "error": str(e)[:120]}

    elapsed = time.time() - t0
    output_text = fix_obvios(result["content"])
    print(f"  → {result['tokens_in']} in / {result['tokens_out']} out / {elapsed:.1f}s")

    # === FASE 6: Checklist (código) ===
    check = validar(output_text, diag, dec)
    print(imprimir_checklist(check))

    # === FASE 5+7: Recuperación + Entrega (código) ===
    
    # Extraer lagunas detectadas para el informe y limpiar el MD final
    lagunas = ""
    if "## Lagunas detectadas" in output_text:
        partes = output_text.split("## Lagunas detectadas")
        output_text = partes[0].strip()
        # Quitar el separador --- si quedó al final
        if output_text.endswith("---"):
            output_text = output_text[:-3].strip()
        lagunas = partes[1].strip()

    nombre_final = generar_nombre(diag, indice=indice, version=1)
    out_path = output_dir / nombre_final
    out_path.write_text(output_text, encoding="utf-8")

    params_rec = recomendar_recuperacion(diag.tipo_proyecto)
    coste = estimar_coste(result["tokens_in"], result["tokens_out"], result["model_used"])

    informe = generar_informe(
        nombre_final=nombre_final,
        diagnostico=diag,
        decision=dec,
        checklist=check,
        params_recuperacion=params_rec,
        tokens_in=result["tokens_in"],
        tokens_out=result["tokens_out"],
        coste_eur=coste,
    )
    
    # Añadir lagunas al informe individual de consola si existen
    if lagunas:
        informe += f"\n⚠️ LAGUNAS DETECTADAS (Extraídas del MD):\n{lagunas}\n"
    
    print(informe)

    return {
        "file_origen": md_path.name,
        "file_final": nombre_final,
        "estado": check.estado_general,
        "estrategia": dec.estrategia,
        "max_chars": diag.max_chars,
        "tipo_proyecto": diag.tipo_proyecto,
        "tokens_in": result["tokens_in"],
        "tokens_out": result["tokens_out"],
        "coste_eur": coste,
        "tiempo_s": round(elapsed, 1),
        "incidencias": len(check.incidencias),
        "model_used": result["model_used"],
        "lagunas": lagunas,
    }


def informe_global(results: list, model: str, log_path: Path):
    lines = [
        "═" * 90,
        f"INFORME GLOBAL — RAG Builder Agentia",
        f"Modelo: {model}    Fecha: {datetime.now().isoformat(timespec='seconds')}",
        "═" * 90,
        "",
        f"{'Origen':<25} {'Final':<35} {'Estado':<8} {'Estr':<10} €",
        "─" * 90,
    ]
    total_coste = 0
    total_in = total_out = 0
    for r in results:
        if r.get("estado") == "ERROR":
            lines.append(f"{r.get('file_origen', '?'):<25} ERROR: {r.get('error', '')}")
            continue
        lines.append(
            f"{r['file_origen']:<25} {r['file_final']:<35} "
            f"{r['estado']:<8} {r['estrategia']:<10} {r['coste_eur']:.5f}"
        )
        total_coste += r["coste_eur"]
        total_in += r["tokens_in"]
        total_out += r["tokens_out"]
    lines += [
        "─" * 90,
        f"TOTAL: {len(results)} archivos",
        f"Tokens: {total_in} in + {total_out} out",
        f"Coste estimado: {total_coste:.4f} €",
        "",
        "LAGUNAS DETECTADAS POR ARCHIVO:",
    ]
    
    hay_lagunas = False
    for r in results:
        if r.get("lagunas"):
            hay_lagunas = True
            lines.append(f"\n> {r['file_final']}:")
            # Identar las lagunas
            lag_text = "  " + r["lagunas"].replace("\n", "\n  ")
            lines.append(lag_text)
    
    if not hay_lagunas:
        lines.append("  (No se detectaron lagunas significativas)")

    lines += [
        "",
        "PRÓXIMOS PASOS:",
        "  1. Revisar archivos en estado REVISAR/ERROR.",
        "  2. Subir los .md finales al dashboard de Agentia (están limpios de notas).",
        "  3. Revisar el apartado de LAGUNAS arriba para completar información faltante.",
        "  4. Aplicar parámetros de chunking individuales (override por archivo si aplica).",
        "",
    ]
    txt = "\n".join(lines)
    log_path.write_text(txt, encoding="utf-8")
    print("\n" + txt)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--proyecto", required=True, help="Nombre del proyecto (ej: MuseIA, LegacIA-Ruta3)")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--input", default="./md_brutos")
    p.add_argument("--output", default="./md_rag")
    p.add_argument("--prompt", default="./prompts/escritura_compact.md")
    p.add_argument("--only", nargs="*")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--fecha", default=datetime.now().strftime("%Y-%m-%d"))
    args = p.parse_args()

    if not API_KEY and not args.dry_run:
        sys.exit("Falta OPENROUTER_API_KEY en .env")

    in_dir = Path(args.input)
    out_dir = Path(args.output)
    prompt_path = Path(args.prompt)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not prompt_path.exists():
        sys.exit(f"No existe el prompt en {prompt_path}")

    files = sorted(in_dir.glob("*.md"))
    if args.only:
        files = [f for f in files if f.name in args.only]
    if not files:
        sys.exit(f"No hay .md en {in_dir}")

    # === Estimación previa ===
    skill_tk = len(prompt_path.read_text(encoding="utf-8")) // 4
    docs_tk = sum(len(f.read_text(encoding="utf-8")) for f in files) // 4
    rates = COST_PER_M.get(args.model, {"in": 1, "out": 5})
    est = ((skill_tk * len(files) + docs_tk) * rates["in"]
           + docs_tk * 1.1 * rates["out"]) / 1_000_000

    print(f"\n{'═' * 60}")
    print(f"Proyecto: {args.proyecto}")
    print(f"Modelo:   {args.model}")
    print(f"Archivos: {len(files)}")
    print(f"Estimación previa: ~{est:.4f} €")
    print(f"{'═' * 60}")

    if args.dry_run:
        print("\nDry-run terminado. Quita --dry-run para ejecutar.")
        for f in files:
            d = diagnosticar(f)
            dec = decidir_estrategia(d)
            print(f"  {f.name:<25} → {dec.estrategia:<10} ({d.tipo_proyecto}, {d.longitud_entidad})")
        return

    results = []
    # --- Procesamiento Incremental ---
    to_process = []
    processed_files = {f.name: f for f in out_dir.glob("*.md")}
    
    for f in files:
        # Generar nombre base esperado para verificar si existe
        d_temp = diagnosticar(f)
        nombre_esperado = generar_nombre(d_temp, indice=0, version=1)
        # Buscar si hay algún archivo en out_dir que contenga el nombre base
        match = next((path for name, path in processed_files.items() if f.stem in name), None)
        
        if match:
            # Si el archivo bruto es mas reciente que el procesado, lo re-procesamos
            if f.stat().st_mtime > match.stat().st_mtime:
                print(f"  [UPDATE] {f.name} (cambios detectados)")
                to_process.append(f)
            else:
                print(f"  [SKIP] {f.name} (ya procesado)")
        else:
            to_process.append(f)

    if not to_process:
        print("\nTodos los archivos ya están procesados en md_rag. Nada que hacer.")
        return

    for i, f in enumerate(to_process, start=1):
        try:
            r = procesar_archivo(f, out_dir, prompt_path, args.model, args.proyecto, i, args.fecha)
            results.append(r)
        except Exception as e:
            print(f"ERROR procesando {f.name}: {e}")
            results.append({"file_origen": f.name, "estado": "ERROR", "error": str(e)[:120]})

    informe_global(results, args.model, Path("./informe_global.txt"))


if __name__ == "__main__":
    main()
