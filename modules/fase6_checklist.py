"""
FASE 6 — Validación post-proceso replicando el checklist de la skill.

Cada item del checklist se traduce a un check programático.
Cero tokens de LLM.
"""

import re
from dataclasses import dataclass, field


@dataclass
class ChecklistFase6:
    archivo: str
    estado_general: str       # OK | REVISAR | ERROR
    checks_chunking: dict = field(default_factory=dict)
    checks_perfiles: dict = field(default_factory=dict)
    checks_arquitectura: dict = field(default_factory=dict)
    incidencias: list = field(default_factory=list)


def _check(condicion: bool, descripcion: str, incidencias: list, severidad="warn") -> bool:
    if not condicion:
        prefix = "✗" if severidad == "fail" else "⚠"
        incidencias.append(f"{prefix} {descripcion}")
    return condicion


def validar(md_text: str, diagnostico, decision, modo: str = "agentia") -> ChecklistFase6:
    archivo = diagnostico.archivo
    incidencias = []

    # === Limpieza previa: quitar wrappers obvios sin reintentar ===
    md_clean = md_text.strip()
    if md_clean.startswith("```"):
        md_clean = re.sub(r"^```\w*\n", "", md_clean)
        md_clean = re.sub(r"\n```$", "", md_clean)
        incidencias.append("⚠ El modelo envolvió la respuesta en ```...``` (corregido)")

    # === Checks de estructura/chunking ===
    checks_chunking = {}

    n_h2 = len(re.findall(r"^## ", md_clean, re.M))
    n_h3 = len(re.findall(r"^### ", md_clean, re.M))
    n_separadores = md_clean.count("\n---\n")
    n_h4 = len(re.findall(r"^#### ", md_clean, re.M))

    checks_chunking["tiene_h1"] = _check(
        bool(re.search(r"^# ", md_clean, re.M)),
        "Falta H1 inicial",
        incidencias, "fail",
    )
    checks_chunking["secciones_independientes"] = _check(
        n_h2 + n_separadores >= 2,
        f"Pocas secciones independientes ({n_h2} H2 + {n_separadores} separadores)",
        incidencias, "warn",
    )
    checks_chunking["sin_h4"] = _check(
        n_h4 == 0,
        f"Usa H4 ({n_h4} encontrados, no permitidos)",
        incidencias, "warn",
    )

    # Comprobar que ninguna sección excede max_chars
    secciones = re.split(r"\n## ", md_clean)
    secciones_largas = [
        i for i, s in enumerate(secciones)
        if len(s) > diagnostico.max_chars
    ]
    checks_chunking["max_chars_respetado"] = _check(
        not secciones_largas,
        f"{len(secciones_largas)} secciones superan max_chars={diagnostico.max_chars}",
        incidencias, "warn",
    )

    # Tablas con más de 8 filas (warn) o más de 20 filas (fail en LegacIA)
    tablas_largas = []
    tablas_muy_largas = []
    for tabla in re.finditer(r"((?:^\|.*\|\n)+)", md_clean, re.M):
        filas = tabla.group(1).strip().split("\n")
        n_datos = len(filas) - 2  # descontar cabecera y separador
        if n_datos > 20:
            tablas_muy_largas.append(n_datos)
        elif n_datos > 8:
            tablas_largas.append(n_datos)
    checks_chunking["sin_tablas_largas"] = _check(
        not tablas_largas and not tablas_muy_largas,
        f"Tablas con muchas filas: {tablas_largas + tablas_muy_largas} (límite recomendado: 8)",
        incidencias, "warn",
    )
    if tablas_muy_largas and modo == "legacia":
        _check(
            False,
            f"LegacIA: tabla(s) con más de 20 filas ({tablas_muy_largas}) en el cuerpo general — "
            "mover tabla completa a '## Datos técnicos para investigadores y docentes' "
            "y sustituir por resumen en bullets (Regla 5)",
            incidencias, "fail",
        )

    # === Checks de perfiles ===
    checks_perfiles = {}
    tiene_seccion_especializada = bool(
        re.search(r"##\s+Datos t[ée]cnicos", md_clean, re.I)
    )
    checks_perfiles["seccion_especializada_existe"] = tiene_seccion_especializada

    # === Checks de arquitectura ===
    checks_arquitectura = {}
    tamaño_kb = len(md_clean.encode("utf-8")) / 1024
    checks_arquitectura["bajo_100kb"] = _check(
        tamaño_kb < 100,
        f"Archivo muy grande: {tamaño_kb:.1f} KB (límite: 100 KB)",
        incidencias, "warn",
    )
    checks_arquitectura["estrategia_coherente"] = _check(
        decision.coherencia_ok,
        f"Decisión FASE 2 marcó coherencia=False",
        incidencias, "warn",
    )

    # === Detección de elementos prohibidos por la skill ===
    prohibidos = []
    if "⚠️" in md_clean or "🚫" in md_clean:
        prohibidos.append("emojis de aviso (⚠️ / 🚫)")
    if re.search(r"\[(WEB|DOSSIER|FUENTE|REF)\]", md_clean):
        prohibidos.append("etiquetas de fuente en el cuerpo")
    if re.search(r"^(Aquí tienes|A continuación|Espero que)", md_clean, re.I | re.M):
        prohibidos.append("preámbulos del modelo")

    if prohibidos:
        for p in prohibidos:
            incidencias.append(f"⚠ Contiene elementos prohibidos: {p}")

    # === Check Regla 4 LegacIA: sin párrafos narrativos (prosa corrida) ===
    if modo == "legacia":
        lines_body = [l for l in md_clean.split("\n") if l.strip()]
        prose_run = 0
        n_prose_blocks = 0
        for l in lines_body:
            s = l.strip()
            is_structure = (
                s.startswith("#") or s.startswith("-") or s.startswith("*") or
                s.startswith("|") or s == "---" or s.startswith(">") or
                s.startswith("**") or bool(re.match(r"^\d+\.", s))
            )
            if is_structure:
                if prose_run >= 3:
                    n_prose_blocks += 1
                prose_run = 0
            else:
                prose_run += 1
        if prose_run >= 3:
            n_prose_blocks += 1

        checks_chunking["sin_prosa_narrativa"] = _check(
            n_prose_blocks == 0,
            f"Párrafos narrativos detectados ({n_prose_blocks}) — convertir a bullets (Regla 4)",
            incidencias, "warn",
        )

    # === Checks específicos LegacIA ===
    if modo == "legacia":
        # Antipatrón 11 — campo Vigencia/Período como bloque aislado de metadatos
        _check(
            "**Vigencia/Período:**" not in md_clean
            and "**Vigencia/Periodo:**" not in md_clean,
            "LegacIA: campo 'Vigencia/Período' como bloque de metadatos aislado "
            "(antipatrón 11 — integrarlo dentro del chunk, Regla 3)",
            incidencias, "fail",
        )

        # Antipatrón 12 — notas de proceso o metodología en el cuerpo
        _check(
            "Referencia interna: LegacIA" not in md_clean
            and "Metodología: Extracción" not in md_clean
            and "reescritura de texto bruto" not in md_clean.lower(),
            "LegacIA: nota de proceso o metodología en el cuerpo del RAG "
            "(antipatrón 12 — eliminar, el RAG es solo datos del patrimonio)",
            incidencias, "fail",
        )

        # Cabecera debe referenciar LegacIA, no Agentia
        _check(
            "· LegacIA ·" in md_clean,
            "LegacIA: cabecera no referencia '· LegacIA ·' — "
            "revisar segunda línea del encabezado",
            incidencias, "warn",
        )

        # Sección de datos técnicos para investigadores debe estar presente
        _check(
            bool(re.search(r"##\s+datos\s+t[eé]cnicos\s+para\s+investigadores", md_clean, re.I)),
            "LegacIA: falta sección '## Datos técnicos para investigadores y docentes' — "
            "añadirla al final antes del glosario con bibliografía y cronologías técnicas",
            incidencias, "warn",
        )

    # === Estado general ===
    fails = sum(1 for i in incidencias if i.startswith("✗"))
    warns = sum(1 for i in incidencias if i.startswith("⚠"))
    if fails > 0:
        estado = "ERROR"
    elif warns > 2:
        estado = "REVISAR"
    else:
        estado = "OK"

    return ChecklistFase6(
        archivo=archivo,
        estado_general=estado,
        checks_chunking=checks_chunking,
        checks_perfiles=checks_perfiles,
        checks_arquitectura=checks_arquitectura,
        incidencias=incidencias,
    )


def imprimir_checklist(c: ChecklistFase6) -> str:
    out = f"\n[FASE 6] Checklist de {c.archivo} → {c.estado_general}\n"
    out += "─" * 53 + "\n"
    if c.incidencias:
        for i in c.incidencias:
            out += f"  {i}\n"
    else:
        out += "  ✓ Sin incidencias\n"
    return out


def fix_obvios(md_text: str) -> str:
    """Correcciones automáticas que no requieren re-llamar al LLM."""
    s = md_text.strip()
    # Quitar wrappers ```markdown
    if s.startswith("```"):
        s = re.sub(r"^```\w*\n", "", s)
        s = re.sub(r"\n```$", "", s)
    # Quitar preámbulos típicos
    s = re.sub(r"^(Aquí tienes.*?\n+|A continuación.*?\n+)", "", s, flags=re.I)
    return s.strip()
