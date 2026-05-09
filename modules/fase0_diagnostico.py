"""
FASE 0 — Diagnóstico cuantitativo y recomendación de parámetros.

Replica las heurísticas de la skill agentia-rag-builder en código:
- Detecta tipo de proyecto (operativo / cultural / mixto / definiciones)
- Calcula longitud media de secciones
- Recomienda max_chars / overlap / min_chunk_size
- Justifica cada valor

Cero tokens de LLM.
"""

import re
from pathlib import Path
from dataclasses import dataclass, asdict


# Keywords típicos por tipo de contenido (en español)
KEYWORDS_OPERATIVO = [
    "horario", "tarifa", "precio", "contacto", "teléfono", "email",
    "dirección", "acceso", "abierto", "cerrado", "entrada", "ticket",
    "reserva", "ubicación", "transporte", "aparcamiento",
]
KEYWORDS_CULTURAL = [
    "siglo", "historia", "época", "fundación", "colección", "exposición",
    "sala", "obra", "pieza", "yacimiento", "arqueológic", "patrimonio",
    "cultural", "tradición", "costumbre", "ritual", "ceremonia",
    # Vocabulario específico LegacIA / El Museo Canario
    "amazigh", "aborigen", "guanche", "grabado", "rupestre", "cueva",
    "prehispánico", "majos", "momia", "tamarco", "benahoarita", "majorero",
    "tinerfeno", "grancanario", "gomeró", "palmero", "canario", "neolítico",
    "sepultura", "túmulo", "datación", "radiocarbónic", "líbico", "tiburón",
    "alfarería", "cerámica aborigen", "molino naviforme",
]
KEYWORDS_DEFINICIONES = [
    "definición", "concepto", "término", "glosario", "significado",
    "se define como", "es decir", "se refiere a", "denomina",
]


@dataclass
class DiagnosticoFase0:
    archivo: str
    tipo_proyecto: str         # operativo | cultural | mixto | definiciones
    n_palabras: int
    n_caracteres: int
    n_headings: int
    n_separadores: int
    avg_section_len: int
    short_line_ratio: float
    longitud_entidad: str      # cortas | medias | largas | mixto
    max_chars: int
    overlap: int
    min_chunk_size: int
    estrategia_sugerida: str
    justificacion: dict


def _contar_estructura(text: str) -> dict:
    """Cuenta headings y separadores, calcula avg_section_len y short_line_ratio."""
    lines = text.split("\n")
    n_lines = len(lines)
    if n_lines == 0:
        return {"n_headings": 0, "n_separadores": 0, "avg_section_len": 0, "short_line_ratio": 0}

    n_headings = sum(1 for l in lines if re.match(r"^#{1,3}\s+", l))
    n_separadores = sum(1 for l in lines if l.strip() == "---")
    short_lines = sum(1 for l in lines if 0 < len(l.strip()) < 30)
    short_line_ratio = short_lines / n_lines

    # avg_section_len: longitud media entre headings/separadores
    breakpoints = [i for i, l in enumerate(lines)
                   if re.match(r"^#{1,3}\s+", l) or l.strip() == "---"]
    if len(breakpoints) >= 2:
        section_lens = []
        for i in range(len(breakpoints) - 1):
            section_text = "\n".join(lines[breakpoints[i]:breakpoints[i + 1]])
            section_lens.append(len(section_text))
        avg_section_len = sum(section_lens) // len(section_lens) if section_lens else 0
    else:
        avg_section_len = len(text)

    return {
        "n_headings": n_headings,
        "n_separadores": n_separadores,
        "avg_section_len": avg_section_len,
        "short_line_ratio": round(short_line_ratio, 3),
    }


def _detectar_tipo_proyecto(text: str) -> str:
    """Detecta el tipo dominante por keywords (case-insensitive)."""
    t = text.lower()
    score_op = sum(t.count(k) for k in KEYWORDS_OPERATIVO)
    score_cult = sum(t.count(k) for k in KEYWORDS_CULTURAL)
    score_def = sum(t.count(k) for k in KEYWORDS_DEFINICIONES)

    total = score_op + score_cult + score_def
    if total == 0:
        return "mixto"

    # Si uno domina con >60% del total, ese gana
    if score_def / total > 0.5:
        return "definiciones"
    if score_op / total > 0.6:
        return "operativo"
    if score_cult / total > 0.6:
        return "cultural"
    return "mixto"


def _detectar_longitud_entidad(text: str, avg_section_len: int) -> str:
    """Clasifica la longitud típica de las entidades del documento."""
    if avg_section_len < 400:
        return "cortas"
    if avg_section_len < 1000:
        return "medias"
    if avg_section_len < 2500:
        return "largas"
    # Si hay mezcla evidente: secciones muy dispares
    return "mixto"


def _recomendar_parametros(tipo: str, longitud: str) -> tuple[int, int, int, dict]:
    """
    Aplica la tabla de la skill (FASE 0) ajustada por longitud de entidad.

    Tabla base de la skill:
    | Solo definiciones        | 800  | 80  | 80  |
    | Operativo                | 1200 | 120 | 80  |
    | Mixto o cultural medio   | 1500 | 150 | 100 |
    | Cultural complejo        | 2000 | 200 | 100 |
    """
    base = {
        "definiciones":  (800, 80, 80),
        "operativo":     (1200, 120, 80),
        "mixto":         (1500, 150, 100),
        "cultural":      (2000, 200, 100),  # asumido complejo por defecto
    }
    max_chars, overlap, min_chunk = base[tipo]

    justif = {
        "max_chars": f"Tipo '{tipo}' base de tabla skill",
        "overlap": "10% de max_chars (regla de proporcionalidad)",
        "min_chunk_size": "6-7% de max_chars (regla de proporcionalidad)",
    }

    # Ajustes por longitud real de entidades detectada
    if tipo == "cultural" and longitud == "medias":
        max_chars, overlap = 1500, 150
        justif["max_chars"] = "Cultural pero entidades medias detectadas → bajamos a 1500"
    if longitud == "largas" and tipo != "definiciones":
        max_chars = max(max_chars, 2000)
        overlap = max_chars // 10
        justif["max_chars"] = f"Entidades largas detectadas (avg ≥1000 chars) → 2000"

    return max_chars, overlap, min_chunk, justif


def diagnosticar(md_path: Path) -> DiagnosticoFase0:
    """Punto de entrada: analiza un .md bruto y devuelve el diagnóstico completo."""
    text = md_path.read_text(encoding="utf-8")

    estructura = _contar_estructura(text)
    tipo = _detectar_tipo_proyecto(text)
    longitud = _detectar_longitud_entidad(text, estructura["avg_section_len"])
    max_chars, overlap, min_chunk, justif = _recomendar_parametros(tipo, longitud)

    # La estrategia se decide en FASE 2; aquí solo damos sugerencia provisional
    estrategia_sugerida = "auto"
    if estructura["short_line_ratio"] > 0.60:
        estrategia_sugerida = "fixed"
        justif["estrategia"] = "short_line_ratio > 0.60 → texto ruidoso, fixed"
    elif estructura["n_headings"] + estructura["n_separadores"] >= 3:
        if estructura["avg_section_len"] < max_chars * 3:
            estrategia_sugerida = "semantic"
            justif["estrategia"] = "Estructura suficiente y secciones razonables → semantic"
        else:
            estrategia_sugerida = "fixed"
            justif["estrategia"] = "Secciones enormes (>3×max_chars) → fixed"
    else:
        estrategia_sugerida = "fixed"
        justif["estrategia"] = "Sin estructura detectable → fixed"

    return DiagnosticoFase0(
        archivo=md_path.name,
        tipo_proyecto=tipo,
        n_palabras=len(text.split()),
        n_caracteres=len(text),
        n_headings=estructura["n_headings"],
        n_separadores=estructura["n_separadores"],
        avg_section_len=estructura["avg_section_len"],
        short_line_ratio=estructura["short_line_ratio"],
        longitud_entidad=longitud,
        max_chars=max_chars,
        overlap=overlap,
        min_chunk_size=min_chunk,
        estrategia_sugerida=estrategia_sugerida,
        justificacion=justif,
    )


def imprimir_diagnostico(d: DiagnosticoFase0) -> str:
    """Formato legible para consola/log."""
    return f"""
[FASE 0] Diagnóstico de {d.archivo}
─────────────────────────────────────────────────────
Tipo proyecto:        {d.tipo_proyecto}
Longitud entidades:   {d.longitud_entidad}
Palabras / chars:     {d.n_palabras} / {d.n_caracteres}
Headings / sep '---': {d.n_headings} / {d.n_separadores}
avg_section_len:      {d.avg_section_len} chars
short_line_ratio:     {d.short_line_ratio}

Recomendación:
  max_chars:       {d.max_chars}  → {d.justificacion.get('max_chars', '')}
  overlap:         {d.overlap}  → {d.justificacion.get('overlap', '')}
  min_chunk_size:  {d.min_chunk_size}  → {d.justificacion.get('min_chunk_size', '')}
  estrategia:      {d.estrategia_sugerida}  → {d.justificacion.get('estrategia', '')}
"""
