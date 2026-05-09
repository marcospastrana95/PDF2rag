"""
RAG Builder LegacIA — Pipeline híbrido para El Museo Canario / DesarrolloIA.

Basado en rag_builder.py (Agentia). Adaptaciones específicas para LegacIA:
  - Prompt de escritura con caveats críticos del museo (momias, Atlántico, etc.)
  - Checklist FASE 6 con reglas LegacIA (antipatrones 11 y 12)
  - Perfiles de visitante: turista, residente, estudiante, familia, investigador
  - tipo_proyecto "cultural-museo" con parámetros RAG específicos
  - Detección de caveats críticos en el output

Arquitectura (igual que Agentia):
  FASE 0 → CÓDIGO (diagnóstico + parámetros)
  FASE 1 → CÓDIGO (limpieza preclean)
  FASE 2 → CÓDIGO (decisión de estrategia)
  FASE 3+4 → LLM BARATO (única llamada, prompt escritura_legacia.md)
  FASE 5 → CÓDIGO (parámetros recuperación)
  FASE 6 → CÓDIGO (validación checklist + caveats LegacIA)
  FASE 7 → CÓDIGO (entrega + informe)

Uso básico:
    python rag_builder_legacia.py --proyecto LegacIA-Ruta1
    python rag_builder_legacia.py --proyecto LegacIA-LaGuancha --model deepseek/deepseek-chat-v3
    python rag_builder_legacia.py --proyecto LegacIA-Ruta2 --dry-run
    python rag_builder_legacia.py --proyecto LegacIA-Ruta3 --only doc1.md
"""

import argparse
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from modules.fase0_diagnostico import diagnosticar, imprimir_diagnostico
from modules.fase1_extraccion import preclean
from modules.fase2_estrategia import decidir_estrategia, imprimir_decision
from modules.fase34_llm import reescribir
from modules.fase6_checklist import validar, imprimir_checklist, fix_obvios
from modules.fase7_entrega import generar_nombre, recomendar_recuperacion, generar_informe


load_dotenv()
API_KEY = os.getenv("OPENROUTER_API_KEY")

DEFAULT_MODEL = "deepseek/deepseek-v4-flash"
DEFAULT_PROMPT = "./prompts/escritura_legacia_compact.md"

COST_PER_M = {
    "deepseek/deepseek-v4-flash":     {"in": 0.13, "out": 0.26},
    "deepseek/deepseek-chat-v3":      {"in": 0.30, "out": 0.83},
    "google/gemini-2.5-flash-lite":   {"in": 0.10, "out": 0.40},  # Actualizado OpenRouter
    "google/gemini-2.5-flash":        {"in": 0.10, "out": 0.40},  # Actualizado OpenRouter
    "anthropic/claude-sonnet-4.5":    {"in": 2.80, "out": 14.0},
}

# ─── Caveats críticos LegacIA ────────────────────────────────────────────────
# Patrones que NO deben aparecer en el output final.
# El LLM debería corregirlos via el prompt, pero el script los verifica también.

CAVEATS_LEGACIA = [
    {
        "id": "caveat_fluor",
        "patron": ["fluoruro", "flúor", "fluorosis"],
        "descripcion": "Error: fluoruro/flúor como causa de desgaste dental. "
                       "La causa documentada es el basalto del molino naviforme.",
    },
    {
        "id": "caveat_momias_intencional",
        "patron": ["momificación intencional", "proceso deliberado de momificación",
                   "momificaron intencionalmente"],
        "descripcion": "Error: conservación intencional de momias canarias. "
                       "La conservación es natural (condiciones ambientales de las cuevas).",
    },
    {
        "id": "caveat_atlantico",
        "patron": ["cruzaron el atlántico", "travesía atlántica", "travesía oceánica abierta"],
        "descripcion": "Error: 'cruzaron el Atlántico'. "
                       "Los canarios llegaron desde el norte de África por el Atlántico próximo.",
    },
    {
        "id": "caveat_metadatos_bloque",
        "patron": ["**Vigencia/Período:**", "**Vigencia/Periodo:**"],
        "descripcion": "Antipatrón 11: campo 'Vigencia/Período' como bloque de metadatos separado. "
                       "Los metadatos deben integrarse en el chunk, no como bloque aislado.",
    },
    {
        "id": "caveat_notas_proceso",
        "patron": ["Referencia interna: LegacIA", "Metodología: Extracción",
                   "reescritura de texto bruto"],
        "descripcion": "Antipatrón 12: nota de proceso o metodología en el cuerpo del RAG. "
                       "El RAG es solo datos del patrimonio.",
    },
]


def detectar_caveats(texto: str) -> list[dict]:
    """Detecta caveats críticos en el output. Devuelve lista de caveats encontrados."""
    encontrados = []
    texto_lower = texto.lower()
    for caveat in CAVEATS_LEGACIA:
        for patron in caveat["patron"]:
            if patron.lower() in texto_lower:
                encontrados.append(caveat)
                break  # un match por caveat es suficiente
    return encontrados


def estimar_coste(t_in: int, t_out: int, model: str) -> float:
    rates = COST_PER_M.get(model, {"in": 1, "out": 5})
    return (t_in * rates["in"] + t_out * rates["out"]) / 1_000_000


# Patrones de cabeceras de bibliografía (en minúsculas para comparación)
BIBLIO_HEADERS = [
    "## bibliografía", "## bibliografia", "## referencias",
    "## references", "## bibliography", "## fuentes",
    "## fuentes consultadas", "## referencias bibliográficas",
    "## notas y referencias",
]


def strip_tokens_innecesarios(texto: str) -> tuple[str, dict]:
    """
    Elimina antes de enviar al LLM:
      1. Referencias de imagenes ![](...)  → el LLM no puede verlas.
      2. Secciones de bibliografía        → no aportan valor al RAG.
    El archivo original en md_brutos NO se toca.
    """
    import re
    lineas = texto.splitlines()
    resultado = []
    n_imgs = 0
    n_biblio_lines = 0
    en_biblio = False

    for linea in lineas:
        linea_lower = linea.strip().lower()

        # Detectar inicio de sección de bibliografía
        if any(linea_lower.startswith(h) for h in BIBLIO_HEADERS):
            en_biblio = True
            n_biblio_lines += 1
            continue

        # Detectar fin de sección de bibliografía (siguiente ## de nivel igual o superior)
        if en_biblio:
            if re.match(r'^#{1,2}\s', linea.strip()) and not any(linea_lower.startswith(h) for h in BIBLIO_HEADERS):
                en_biblio = False  # Salimos de la sección biblio
            else:
                n_biblio_lines += 1
                continue

        # Eliminar referencias de imagen
        if re.match(r'^!\[.*?\]\(.*?\)', linea.strip()):
            n_imgs += 1
            continue

        resultado.append(linea)

    texto_limpio = "\n".join(resultado)
    stats = {"imagenes_eliminadas": n_imgs, "lineas_biblio_eliminadas": n_biblio_lines}
    return texto_limpio, stats


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
          f"({stats_clean['lineas_repetidas_eliminadas']} lineas repetidas eliminadas)")

    # === FASE 1b: Strip pre-LLM (imágenes + bibliografía) ===
    cleaned, stats_strip = strip_tokens_innecesarios(cleaned)
    tokens_ahorrados = (stats_strip['imagenes_eliminadas'] + stats_strip['lineas_biblio_eliminadas']) * 5
    print(f"[FASE 1b] Strip pre-LLM: {stats_strip['imagenes_eliminadas']} imgs eliminadas, "
          f"{stats_strip['lineas_biblio_eliminadas']} lineas de bibliografia eliminadas "
          f"(~{tokens_ahorrados} tokens ahorrados)")

    # Re-diagnosticar tras limpieza
    md_path_temp = md_path.parent / f".tmp_{md_path.name}"
    md_path_temp.write_text(cleaned, encoding="utf-8")
    diag = diagnosticar(md_path_temp)
    md_path_temp.unlink()
    diag.archivo = md_path.name

    # === FASE 2: Estrategia (código) ===
    dec = decidir_estrategia(diag)
    print(imprimir_decision(dec))

    # === FASE 3+4: LLM ===
    print(f"[FASE 3+4] Llamando a {model} con prompt LegacIA...")
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

    # === FASE 6: Checklist + Caveats LegacIA (código) ===
    check = validar(output_text, diag, dec, modo="legacia")
    print(imprimir_checklist(check))

    # Detección de caveats críticos LegacIA
    caveats_encontrados = detectar_caveats(output_text)
    if caveats_encontrados:
        print(f"\n⚠️  CAVEATS CRÍTICOS DETECTADOS ({len(caveats_encontrados)}):")
        for c in caveats_encontrados:
            print(f"  ❌ [{c['id']}] {c['descripcion']}")
        print("  → Revisar el archivo manualmente antes de subir a LegacIA.")
    else:
        print("  ✅ Sin caveats críticos LegacIA detectados.")

    # === FASE 5+7: Recuperación + Entrega (código) ===
    lagunas = ""
    match_lagunas = re.search(r"## Lagunas detectadas", output_text, re.I)
    if match_lagunas:
        output_text_clean = output_text[:match_lagunas.start()].strip()
        if output_text_clean.endswith("---"):
            output_text_clean = output_text_clean[:-3].strip()
        lagunas = output_text[match_lagunas.end():].strip()
        output_text = output_text_clean

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

    if lagunas:
        informe += f"\n⚠️ LAGUNAS DETECTADAS (Extraídas del MD):\n{lagunas}\n"

    if caveats_encontrados:
        informe += f"\n🔴 CAVEATS CRÍTICOS LEGACIA ({len(caveats_encontrados)}):\n"
        for c in caveats_encontrados:
            informe += f"  - [{c['id']}] {c['descripcion']}\n"

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
        "caveats": [c["id"] for c in caveats_encontrados],
    }


def informe_global(results: list, model: str, log_path: Path):
    lines = [
        "═" * 90,
        "INFORME GLOBAL — RAG Builder LegacIA / El Museo Canario",
        f"Modelo: {model}    Fecha: {datetime.now().isoformat(timespec='seconds')}",
        "═" * 90,
        "",
        f"{'Origen':<25} {'Final':<35} {'Estado':<8} {'Estr':<10} {'Caveats':<8} €",
        "─" * 90,
    ]
    total_coste = 0
    total_in = total_out = 0
    for r in results:
        if r.get("estado") == "ERROR":
            lines.append(f"{r.get('file_origen', '?'):<25} ERROR: {r.get('error', '')}")
            continue
        n_caveats = len(r.get("caveats", []))
        caveat_badge = f"🔴 {n_caveats}" if n_caveats else "✅ 0"
        lines.append(
            f"{r['file_origen']:<25} {r['file_final']:<35} "
            f"{r['estado']:<8} {r['estrategia']:<10} {caveat_badge:<8} {r['coste_eur']:.5f}"
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
        "CAVEATS CRÍTICOS LEGACIA POR ARCHIVO:",
    ]

    hay_caveats = False
    for r in results:
        if r.get("caveats"):
            hay_caveats = True
            lines.append(f"\n> {r['file_final']}:")
            for cid in r["caveats"]:
                c = next((x for x in CAVEATS_LEGACIA if x["id"] == cid), None)
                if c:
                    lines.append(f"  ❌ [{cid}] {c['descripcion']}")
    if not hay_caveats:
        lines.append("  ✅ Sin caveats críticos detectados en ningún archivo.")

    lines += [
        "",
        "LAGUNAS DETECTADAS POR ARCHIVO:",
    ]
    hay_lagunas = False
    for r in results:
        if r.get("lagunas"):
            hay_lagunas = True
            lines.append(f"\n> {r['file_final']}:")
            lag_text = "  " + r["lagunas"].replace("\n", "\n  ")
            lines.append(lag_text)
    if not hay_lagunas:
        lines.append("  (No se detectaron lagunas significativas)")

    lines += [
        "",
        "PRÓXIMOS PASOS:",
        "  1. Revisar archivos con caveats críticos (🔴) — no subir sin revisión manual.",
        "  2. Revisar archivos en estado REVISAR/ERROR.",
        "  3. Subir los .md finales al dashboard de LegacIA (Dashboard → Knowledge Base).",
        "  4. Revisar el apartado de LAGUNAS para completar información faltante.",
        "  5. Aplicar parámetros de chunking (override por archivo si aplica).",
        "",
    ]
    txt = "\n".join(lines)
    log_path.write_text(txt, encoding="utf-8")
    print("\n" + txt)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--proyecto", required=True,
                   help="Nombre del proyecto (ej: LegacIA-Ruta1, LegacIA-LaGuancha)")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--input", default="./md_brutos")
    p.add_argument("--output", default="./md_rag")
    p.add_argument("--prompt", default=DEFAULT_PROMPT)
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
        sys.exit(f"No existe el prompt en {prompt_path}\n"
                 f"Copia escritura_legacia.md a {prompt_path}")

    files = sorted(in_dir.glob("*.md"))
    if args.only:
        files = [f for f in files if f.name in args.only]
    if not files:
        sys.exit(f"No hay .md en {in_dir}")

    # === Estimación previa (aplicando strip de imagenes y bibliografia) ===
    skill_tk = len(prompt_path.read_text(encoding="utf-8")) // 4
    docs_tk_bruto = sum(len(f.read_text(encoding="utf-8")) for f in files) // 4
    # Aplicar strip para estimacion real
    docs_tk = sum(
        len(strip_tokens_innecesarios(f.read_text(encoding="utf-8"))[0])
        for f in files
    ) // 4
    rates = COST_PER_M.get(args.model, {"in": 1, "out": 5})
    est = ((skill_tk * len(files) + docs_tk) * rates["in"]
           + docs_tk * 1.1 * rates["out"]) / 1_000_000
    ahorro_tk = docs_tk_bruto - docs_tk

    print(f"\n{'=' * 60}")
    print(f"Proyecto: {args.proyecto}")
    print(f"Modelo:   {args.model}")
    print(f"Prompt:   {prompt_path}")
    print(f"Archivos: {len(files)}")
    print(f"Tokens brutos:   ~{docs_tk_bruto:,}")
    print(f"Tokens tras strip: ~{docs_tk:,} (ahorro: ~{ahorro_tk:,} tokens)")
    print(f"Estimacion previa: ~{est:.4f} EUR")
    print(f"{'=' * 60}")

    if args.dry_run:
        print("\nDry-run terminado. Quita --dry-run para ejecutar.")
        for f in files:
            d = diagnosticar(f)
            dec = decidir_estrategia(d)
            print(f"  {f.name:<25} → {dec.estrategia:<10} ({d.tipo_proyecto}, {d.longitud_entidad})")
        return

    results = []

    # --- Procesamiento incremental ---
    to_process = []
    processed_files = {f.name: f for f in out_dir.glob("*.md")}

    for f in files:
        d_temp = diagnosticar(f)
        match = next((path for name, path in processed_files.items() if f.stem in name), None)

        if match:
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

    informe_global(results, args.model, Path("./informe_global_legacia.txt"))


if __name__ == "__main__":
    main()
