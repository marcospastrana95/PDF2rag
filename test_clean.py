"""
test_clean.py — Tests de regresión para clean_markdown_text().
Casos extraídos de los MD brutos reales del proyecto.
Ejecutar: python test_clean.py
"""

from convert_pdfs import clean_markdown_text

PASS = 0
FAIL = 0


def check(nombre, resultado, esperado_ausente=None, esperado_presente=None):
    global PASS, FAIL
    ok = True
    if esperado_ausente:
        for patron in esperado_ausente:
            if patron in resultado:
                print(f"  [FAIL] {nombre} — sigue presente: {repr(patron[:60])}")
                ok = False
    if esperado_presente:
        for patron in esperado_presente:
            if patron not in resultado:
                print(f"  [FAIL] {nombre} — desapareció: {repr(patron[:60])}")
                ok = False
    if ok:
        print(f"  [OK]   {nombre}")
        PASS += 1
    else:
        FAIL += 1


# ---------------------------------------------------------------------------
# 1. Bibliografía con heading normal
# ---------------------------------------------------------------------------
t1 = "Contenido real.\n\n## Referencias\n\n- Autor (2020). Libro.\n- Otro (2021)."
check("Bibliografía heading normal",
      clean_markdown_text(t1),
      esperado_ausente=["## Referencias", "Autor (2020)"],
      esperado_presente=["Contenido real."])

# ---------------------------------------------------------------------------
# 2. Bibliografía con heading en negrita (caso real: El papel de la mujer)
# ---------------------------------------------------------------------------
t2 = "Contenido real.\n\n## **Referencias**\n\n- de Bethencourt, J. (1874). Le Canarien."
check("Bibliografía heading con negritas",
      clean_markdown_text(t2),
      esperado_ausente=["## **Referencias**", "de Bethencourt"],
      esperado_presente=["Contenido real."])

# ---------------------------------------------------------------------------
# 3. Índice con heading normal
# ---------------------------------------------------------------------------
t3 = "Texto previo.\n\n## Índice\n\nCapítulo 1 ...... 10\nCapítulo 2 ...... 20\n\n## Introducción\n\nContenido."
check("Índice heading normal",
      clean_markdown_text(t3),
      esperado_ausente=["Capítulo 1 ...... 10"],
      esperado_presente=["Contenido."])

# ---------------------------------------------------------------------------
# 4. Índice con heading en negrita y sin tilde (caso real: Actividad física)
# ---------------------------------------------------------------------------
t4 = "Texto.\n\n## **INDICE**\n\n| Capítulo 1 .............. 19 |\n|---|\n\n## **Introducción**\n\nContenido real."
check("Índice heading con negritas y sin tilde",
      clean_markdown_text(t4),
      esperado_ausente=["## **INDICE**", "Capítulo 1 .............."],
      esperado_presente=["Contenido real."])

# ---------------------------------------------------------------------------
# 5. Tabla-índice Markdown con puntos de relleno (caso real: Actividad física)
# ---------------------------------------------------------------------------
t5 = "Texto.\n\n## **INDICE**\n\n| **CAPÍTULO 1 ..... 19** |\n|---|\n| 1.1. La propiedad ..... 31 |\n\n## Contenido\n\nTexto real."
check("Tabla-índice con puntos de relleno",
      clean_markdown_text(t5),
      esperado_ausente=["CAPÍTULO 1 .....", "1.1. La propiedad"],
      esperado_presente=["Texto real."])

# ---------------------------------------------------------------------------
# 6. Líneas de índice sueltas con puntos (.... y . . . .)
# ---------------------------------------------------------------------------
t6 = "Prólogo . . . . . . . . 19\nContenido real.\n2.1. El trabajo de la tierra . . . . . 49"
check("Líneas índice sueltas puntos espacio",
      clean_markdown_text(t6),
      esperado_ausente=["Prólogo . . . . . . . . 19", "2.1. El trabajo"],
      esperado_presente=["Contenido real."])

# ---------------------------------------------------------------------------
# 7. Notas al pie como blockquote ("> 1 texto")
# ---------------------------------------------------------------------------
t7 = "Párrafo normal.\n\n> 1 Véase también Bethencourt (1874) para más detalles.\n\nSiguiente párrafo."
check("Notas al pie como blockquote",
      clean_markdown_text(t7),
      esperado_ausente=["> 1 Véase también"],
      esperado_presente=["Párrafo normal.", "Siguiente párrafo."])

# ---------------------------------------------------------------------------
# 8. Imágenes pymupdf4llm no se eliminan sección de contenido
# ---------------------------------------------------------------------------
t8 = "Párrafo antes.\n\n![](images/doc-0001-02.png)\n\nPárrafo después."
check("Imágenes pymupdf4llm eliminadas",
      clean_markdown_text(t8),
      esperado_ausente=["![](images/doc-0001-02.png)"],
      esperado_presente=["Párrafo antes.", "Párrafo después."])

# ---------------------------------------------------------------------------
# 9. Citas largas inline eliminadas (≥2 punto y coma, >80 chars)
# ---------------------------------------------------------------------------
t9 = "El estudio demostro (Alvarez Martinez 2014; Bethencourt y Otros 1994; Rodriguez Perez 2001; Garcia Lopez 2010) que los canarios prehispanicos."
check("Citas largas inline eliminadas",
      clean_markdown_text(t9),
      esperado_ausente=["Alvarez Martinez 2014; Bethencourt"],
      esperado_presente=["El estudio demostro", "que los canarios prehispanicos."])

# ---------------------------------------------------------------------------
# 10. Cita corta normal NO eliminada
# ---------------------------------------------------------------------------
t10 = "Según Bethencourt (1874) los canarios vivían en cuevas."
check("Cita corta normal preservada",
      clean_markdown_text(t10),
      esperado_presente=["Bethencourt (1874)"])

# ---------------------------------------------------------------------------
# 11. Tablas de contenido real NO eliminadas
# ---------------------------------------------------------------------------
t11 = "## Resultados\n\n| Isla | Población | Periodo |\n|---|---|---|\n| Gran Canaria | 50.000 | Siglo XV |\n"
check("Tabla de datos real preservada",
      clean_markdown_text(t11),
      esperado_presente=["Gran Canaria", "50.000", "Siglo XV"])

# ---------------------------------------------------------------------------
# Resumen
# ---------------------------------------------------------------------------
print(f"\n{'='*40}")
print(f"Resultado: {PASS} OK  /  {FAIL} FAIL  /  {PASS+FAIL} total")
if FAIL:
    exit(1)
