"""
FASE 7 — Entrega final.

- Nomenclatura: [NN]_[tema]_sem_v[N].md
- Recomendación de parámetros de recuperación (Top-K, umbral, etc.)
- Generación de informe final
"""

import re
from pathlib import Path


# Tabla de parámetros de recuperación (FASE 5 de la skill)
PARAMS_RECUPERACION = {
    "operativo":       {"top_k": 8,  "tokens_total": 4000, "tokens_chunk": 300, "umbral": 0.40, "ctx_msg": 2},
    "cultural":        {"top_k": 12, "tokens_total": 7000, "tokens_chunk": 600, "umbral": 0.30, "ctx_msg": 3},
    "mixto":           {"top_k": 15, "tokens_total": 9000, "tokens_chunk": 600, "umbral": 0.30, "ctx_msg": 3},
    "definiciones":    {"top_k": 5,  "tokens_total": 2000, "tokens_chunk": 250, "umbral": 0.45, "ctx_msg": 1},
    # LegacIA / El Museo Canario: contenido patrimonial denso, vocabulario técnico/amazigh,
    # preguntas que cruzan varios yacimientos o conceptos → top_k alto, umbral bajo
    "cultural-museo":  {"top_k": 15, "tokens_total": 9000, "tokens_chunk": 700, "umbral": 0.28, "ctx_msg": 4},
}


def generar_nombre(diagnostico, indice: int = 1, version: int = 1) -> str:
    """
    Convierte el nombre del archivo bruto a la nomenclatura de la skill.
    [NN]_[tema]_sem_v[N].md
    """
    # Extraer "tema" del nombre original
    base = Path(diagnostico.archivo).stem
    base = re.sub(r"^\d+[_-]", "", base)         # quitar prefijos numéricos previos
    base = re.sub(r"[^a-zA-Z0-9]+", "_", base)   # normalizar
    base = base.strip("_").lower()[:30]          # limitar longitud
    return f"{indice:02d}_{base}_sem_v{version}.md"


def recomendar_recuperacion(tipo_proyecto: str) -> dict:
    """Devuelve los parámetros de recuperación según tipo."""
    params = PARAMS_RECUPERACION.get(tipo_proyecto, PARAMS_RECUPERACION["mixto"]).copy()
    return params


def generar_informe(
    nombre_final: str,
    diagnostico,
    decision,
    checklist,
    params_recuperacion: dict,
    tokens_in: int,
    tokens_out: int,
    coste_eur: float,
) -> str:
    """Informe final tipo entrega FASE 7."""
    return f"""
═══════════════════════════════════════════════════════════════
INFORME DE ENTREGA — {nombre_final}
═══════════════════════════════════════════════════════════════

ESTRATEGIA DE CHUNKING
  Estrategia activada: {decision.estrategia}
  Razón: {decision.razon}

PARÁMETROS DE CHUNKING (subir al dashboard de Agentia)
  max_chars:      {diagnostico.max_chars}
  overlap:        {diagnostico.overlap}
  min_chunk_size: {diagnostico.min_chunk_size}

PARÁMETROS DE RECUPERACIÓN (configurar en el asistente)
  Tipo proyecto inferido: {diagnostico.tipo_proyecto}
  Top-K:                  {params_recuperacion['top_k']}
  Tokens totales:         {params_recuperacion['tokens_total']}
  Tokens por fragmento:   {params_recuperacion['tokens_chunk']}
  Umbral similitud:       {params_recuperacion['umbral']}
  Mensajes contexto:      {params_recuperacion['ctx_msg']}

VALIDACIÓN
  Estado checklist:    {checklist.estado_general}
  Incidencias:         {len(checklist.incidencias)}

CONSUMO
  Tokens input:        {tokens_in}
  Tokens output:       {tokens_out}
  Coste estimado:      {coste_eur:.5f} €

═══════════════════════════════════════════════════════════════
"""
