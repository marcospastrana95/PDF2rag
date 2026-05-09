"""
FASE 2 — Decisión de estrategia de chunking (auto/semantic/fixed).

Replica EXACTAMENTE el árbol de decisión documentado en la skill:

¿ short_line_ratio > 0.60 ?
│
├── SÍ → FIXED (texto ruidoso/OCR)
│
└── NO → ¿ heading_count + separator_count >= 3 ?
         │
         ├── NO → FIXED (sin estructura detectable)
         │
         └── SÍ → ¿ avg_section_len < (max_chars × 3) ?
                  │
                  ├── SÍ → SEMANTIC
                  │
                  └── NO → FIXED (secciones enormes)

También verifica coherencia con los parámetros recomendados en FASE 0.
Cero tokens de LLM.
"""

from dataclasses import dataclass


@dataclass
class DecisionFase2:
    estrategia: str            # semantic | fixed | auto
    razon: str
    coherencia_ok: bool
    advertencias: list


def decidir_estrategia(diagnostico) -> DecisionFase2:
    """
    Toma un objeto DiagnosticoFase0 y decide la estrategia definitiva.
    """
    advertencias = []

    # Árbol de decisión
    if diagnostico.short_line_ratio > 0.60:
        estrategia = "fixed"
        razon = f"short_line_ratio={diagnostico.short_line_ratio} > 0.60 → texto ruidoso/OCR"
    elif (diagnostico.n_headings + diagnostico.n_separadores) < 3:
        estrategia = "fixed"
        razon = f"Solo {diagnostico.n_headings} headings + {diagnostico.n_separadores} '---' → sin estructura"
    elif diagnostico.avg_section_len >= diagnostico.max_chars * 3:
        estrategia = "fixed"
        razon = f"avg_section_len={diagnostico.avg_section_len} ≥ 3×max_chars={diagnostico.max_chars * 3} → secciones enormes"
    else:
        estrategia = "semantic"
        razon = f"Estructura suficiente ({diagnostico.n_headings + diagnostico.n_separadores} marcadores) y secciones razonables"

    # Verificación de coherencia con parámetros de FASE 0
    coherencia_ok = True

    if estrategia == "semantic":
        if diagnostico.avg_section_len > diagnostico.max_chars:
            advertencias.append(
                f"avg_section_len ({diagnostico.avg_section_len}) > max_chars ({diagnostico.max_chars}). "
                f"El fallback de párrafo se activará."
            )
        if diagnostico.n_separadores == 0:
            advertencias.append(
                "0 separadores '---'. Recomendado tras FASE 3 que el LLM añada al menos 1 cada concepto."
            )

    elif estrategia == "fixed":
        if diagnostico.overlap < diagnostico.max_chars * 0.10:
            advertencias.append(
                f"overlap ({diagnostico.overlap}) < 10% de max_chars. "
                f"Riesgo de pérdida de contexto entre chunks."
            )
            coherencia_ok = False

    return DecisionFase2(
        estrategia=estrategia,
        razon=razon,
        coherencia_ok=coherencia_ok,
        advertencias=advertencias,
    )


def imprimir_decision(d: DecisionFase2) -> str:
    out = f"""
[FASE 2] Decisión de estrategia
─────────────────────────────────────────────────────
Estrategia:    {d.estrategia}
Razón:         {d.razon}
Coherencia:    {'OK' if d.coherencia_ok else 'REVISAR'}
"""
    if d.advertencias:
        out += "Advertencias:\n"
        for a in d.advertencias:
            out += f"  ⚠  {a}\n"
    return out
