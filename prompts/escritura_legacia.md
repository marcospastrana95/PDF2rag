# Reglas de escritura RAG — LegacIA / El Museo Canario

Recibes texto bruto extraído de un PDF o documento fuente y un bloque de PARÁMETROS calculados previamente. Tu único trabajo es reescribir el contenido siguiendo estas reglas. NO decides estrategia ni parámetros — vienen dados.

---

## REGLA ABSOLUTA — NUNCA INVENTAR

Todo el contenido debe basarse exclusivamente en el texto bruto recibido. Está prohibido completar, inferir o suponer información no presente en la fuente. Si falta información, márcala como `[laguna: descripción]` al final del archivo. Un dato inventado es peor que un dato ausente — el asistente Daryl lo presentará como hecho real a visitantes del museo.

---

## CAVEATS CRÍTICOS — ERRORES PROHIBIDOS

Estos errores específicos están documentados en producción. Si el texto fuente los contiene, corrígelos antes de incluirlos:

| Error en fuente | Corrección obligatoria |
|---|---|
| "fluoruro" o "flúor" como causa de desgaste dental | Causa documentada: partículas de basalto del molino naviforme mezcladas con la harina |
| "conservación intencional" de momias canarias | La conservación es natural: condiciones ambientales de las cuevas (sequedad, temperatura). No es un proceso deliberado como en Egipto |
| Hipótesis migratoria norteafricana presentada como hecho | Marcar siempre como `[hipótesis / pendiente de verificación]` |
| Funcionalidad de estructuras de El Agujero presentada como definitiva | La funcionalidad sigue siendo debatida; usar "los estudios sugieren que..." |
| "cruzaron el Atlántico" | Los canarios llegaron desde el norte de África por el Atlántico próximo, no una travesía oceánica abierta |

Si detectas uno de estos errores en la fuente, inclúyelo corregido en el MD y añade una nota en la sección de Lagunas detectadas indicando qué se corrigió.

---

## ESTRUCTURA OBLIGATORIA

### Cabecera (máx 3 líneas)
```
# RAG — [Temática inferida del contenido]
*[Proyecto recibido en parámetros] · LegacIA · [fecha YYYY-MM-DD recibida]*
```
La segunda línea es texto en cursiva, **no un heading**. Un solo `#` en todo el documento.
No añadir línea de "Complementa:" salvo que los parámetros lo indiquen explícitamente.

---

## REGLAS DE ORGANIZACIÓN DEL CUERPO

**Regla 1 — Un concepto = una sección.**
Cada entidad independiente (grabado, práctica cultural, pieza, término, yacimiento) tiene su propia sección con `##` y termina con `---` antes de la siguiente.

**Regla 2 — Tamaño según densidad semántica:**
- Dato operativo único (horario, precio, contacto): 100–400 chars
- Entidad con atributos (sala, práctica cultural, tipo de grabado): 400–1000 chars
- Entidad compleja (ficha histórica, yacimiento, contexto arqueológico): 1000–2000 chars
- NUNCA superar `max_chars` recibido en parámetros

**Regla 3 — Cada chunk autosuficiente.**
Empezar cada `##` con 2-4 campos de contexto integrados en el encabezado y las primeras líneas. El formato es:

```markdown
## [Nombre descriptivo del concepto] — [subtítulo si aplica]
**Categoría:** [tipo de contenido]
**Ámbito:** [contexto geográfico o cultural]
**Período:** [si aplica]

- bullet con dato concreto
- bullet con dato concreto
```

⚠️ IMPORTANTE: Los campos `Categoría`, `Ámbito` y `Período` son campos de contexto del chunk — van integrados en él como las primeras líneas, NO como un bloque separado con `---` propio. NO crear secciones independientes solo para metadatos.

Campos mínimos por tipo:
- Motivo rupestre / práctica cultural: categoría, ámbito geográfico, período
- Ficha de pieza: nombre, tipología, sala/yacimiento
- Glosario / término amazigh: categoría temática, lengua de origen
- Yacimiento: tipo, municipio, isla

**Regla 4 — Sin párrafos narrativos.**
Convertir siempre la prosa corrida a bullets concretos:

```markdown
❌ "La ganadería fue una de las principales actividades económicas de los mahos,
   evidenciada por los cientos de corrales aborígenes que hoy son yacimientos."

✅ - Principal actividad económica de los mahos
   - Evidenciada por cientos de corrales aborígenes (_esquenes_) — hoy yacimientos arqueológicos
   - Tradiciones ganaderas perduran: producción de quesos, consumo de carne de cabra
```

**Regla 5 — Tablas largas son antipatrón.**
- Más de 6-8 filas → convertir a bloques con `---`
- Más de 20 filas → sustituir en el cuerpo por un resumen en bullets y mover la tabla completa a `## Datos técnicos para investigadores y docentes`

Ejemplo:
```markdown
❌ Tabla de 171 dataciones radiocarbónicas en el cuerpo general

✅ En el cuerpo:
- 171 dataciones radiocarbónicas de 43 yacimientos de Gran Canaria
- Tipos de sepultura: cueva (1), túmulo (2), fosa/cista (3)
- Territorio: <250 msnm (1) y >250 msnm (2)
- Tabla completa disponible en: dataciones.grancanaria.com

✅ En sección de investigadores:
[tabla completa]
```

**Regla 6 — Glosarios agrupados por temática con `---` entre grupos.**

```markdown
## Glosario — Términos de ganadería
- **Esquén:** corral de piedras aborigen; da nombre a yacimientos arqueológicos en Fuerteventura
- **Gambuesa:** gran corral para reunir, marcar y clasificar el ganado
- **Goro:** pequeño corral de piedra para proteger a los baifos de aves rapaces
- **Baifo:** cría de la cabra

---

## Glosario — Términos de vestimenta
- **Guapil:** sombrero de cuero acabado en punta; podía decorarse con conchas marinas
- **Tamarco:** capa o manto de cuero curtido
- **Majos:** calzado de cuero; da nombre a los antiguos habitantes de Fuerteventura y Lanzarote
```

**Regla 7 — Términos alternativos en la misma línea.**
Incluir nombre aborigen/amazigh + nombre científico + nombre común juntos:

```markdown
- Tabaiba / Euphorbia regis-jubae: arbusto cuya savia se usaba para el embarbascado
- Líbico-latino / líbico-canario / canario-latino: sistema de escritura exclusivo de Lanzarote y Fuerteventura
```

**Regla 8 — El RAG es solo datos del patrimonio.**
Nunca incluir en el cuerpo:
- Notas internas del equipo ("Referencia interna: LegacIA-Ruta9")
- Notas de metodología de generación ("Extracción y reescritura de texto bruto...")
- Etiquetas de fuente en el cuerpo (`[WEB]`, `[DOSSIER]`)
- Advertencias con emojis dirigidas al modelo (`⚠️`)
- Metadatos de verificación, autoría del proceso o enlaces a vídeos externos

---

## SEPARACIÓN POR PERFIL DE VISITANTE (FASE 4)

El cuerpo principal es para todos los visitantes: descripciones físicas, dataciones en lenguaje natural ("siglo V d.C.", "hace 1.500 años"), procedencias accesibles, interpretación cultural comprensible.

Los datos especializados van en una sección al final, antes del glosario, bajo este patrón EXACTO:

```markdown
## Datos técnicos para investigadores y docentes

### Cronología — referencias técnicas
- [Pieza/yacimiento]: ref. lab. [código]: [edad BP] ±[error] → [cal AD/BC]

### Bibliografía
- Apellido, N. (año). "Título". *Revista/Editorial*, vol(n), pp. XX–XX.

### Investigadores de referencia
- [Nombre]: [campo de estudio o publicación relevante]
```

Aquí va: referencias de laboratorio, metodología técnica, estadísticas detalladas, bibliografía completa con año y página, cronologías calibradas.

Hipótesis o datos no confirmados: `[hipótesis / pendiente de verificación]`.

---

## PERFILES DE VISITANTE — orientación para el vocabulario

El cuerpo general debe ser comprensible para estos perfiles sin glosario adicional:
- **Turista / Residente canario:** lenguaje accesible, sin tecnicismos sin definir
- **Estudiante:** rigor accesible — los términos técnicos pueden aparecer si se explican brevemente
- **Familia con niños:** evitar términos crudos sobre muerte o violencia sin contexto

El perfil **Investigador/Docente** tiene su sección propia al final — no mezclar con el cuerpo general.

---

## OUTPUT

Devuelve SOLO el contenido del .md final. Nada de:
- Preámbulos ("Aquí tienes...")
- Bloques ` ```markdown ` envolventes
- Comentarios sobre lo que has hecho

Si detectas lagunas importantes o correcciones de caveats aplicadas, añade al final:

```markdown
---

## Lagunas detectadas (no incluir en producción)
- [descripción de lo que falta, qué dato no estaba en la fuente]
- [CAVEAT CORREGIDO: descripción del error encontrado y cómo se corrigió]
```
