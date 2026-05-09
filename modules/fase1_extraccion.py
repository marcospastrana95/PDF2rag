"""
FASE 1 — Extracción y limpieza local del .md bruto.

Elimina basura típica de extracción PDF antes de pasar al modelo:
- Ligaduras tipográficas (ﬁ, ﬂ, ﬀ)
- Palabras cortadas con guion al final de línea
- Numeración de páginas (números sueltos, "Página X de Y")
- Headers/footers repetidos
- Espacios y saltos de línea sobrantes

Cero tokens de LLM. Ahorra ~15-20% de tokens input.
"""

import re


def preclean(text: str) -> tuple[str, dict]:
    """
    Limpia texto bruto extraído de PDF.
    Devuelve (texto_limpio, stats_de_limpieza).
    """
    original_len = len(text)
    stats = {"chars_eliminados": 0, "lineas_repetidas_eliminadas": 0}

    # 1. Ligaduras tipográficas
    ligaduras = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl"}
    for k, v in ligaduras.items():
        text = text.replace(k, v)

    # 2. Palabras cortadas con guion + salto de línea
    text = re.sub(r"(\w+)-\n(\w+)", r"\1\2", text)

    # 3. "Página X de Y" / "X / Y" en líneas propias
    text = re.sub(r"^.*P[áa]gina\s+\d+\s+de\s+\d+.*$", "", text, flags=re.M | re.I)
    text = re.sub(r"^\s*\d+\s*/\s*\d+\s*$", "", text, flags=re.M)

    # 4. Líneas que son solo un número (numeración de página suelta)
    text = re.sub(r"^\s*\d+\s*$", "", text, flags=re.M)

    # 5. Headers/footers repetidos: líneas idénticas que aparecen >3 veces
    lines = text.split("\n")
    counts = {}
    for ln in lines:
        s = ln.strip()
        if 5 < len(s) < 100:
            counts[s] = counts.get(s, 0) + 1
    repeated = {l for l, c in counts.items() if c > 3}
    if repeated:
        stats["lineas_repetidas_eliminadas"] = sum(counts[l] for l in repeated)
        lines = [l for l in lines if l.strip() not in repeated]
    text = "\n".join(lines)

    # 6. Espacios múltiples → uno
    text = re.sub(r"[ \t]+", " ", text)

    # 7. Saltos de línea múltiples → máximo 2
    text = re.sub(r"\n{3,}", "\n\n", text)

    text = text.strip()
    stats["chars_eliminados"] = original_len - len(text)
    stats["chars_originales"] = original_len
    stats["chars_limpios"] = len(text)
    return text, stats
