# RAG Builder Pipeline — Edición LegacIA

Pipeline para convertir documentos PDF en Markdown optimizado para RAG (Retrieval-Augmented Generation), listo para ingesta en la plataforma Agentia/LegacIA de El Museo Canario.

## Filosofía

- **6 de las 7 fases se ejecutan en código** (decisiones cuantitativas, replicables, sin coste de API).
- **Solo 1 fase (escritura RAG) usa LLM**, lo que permite usar modelos baratos sin perder calidad.
- **Ahorro inteligente de tokens**: los documentos que no necesitan refinado se copian directamente sin llamar a la API.
- **Sin pérdida de contenido en documentos grandes**: chunking automático por estructura (headings → párrafos → tamaño fijo).
- **Fiabilidad en documentos grandes**: `max_tokens` dinámico proporcional al chunk + reintento automático si la respuesta se trunca.
- **Visibilidad de pérdida de contenido**: ratio de retención por archivo en el informe — alerta automática si se descarta demasiado.

---

## Arquitectura

```
[ PDFs originales ]
        │
        ▼  Etapa 1 — convert_pdfs.py
        │
        │  · Extracción nativa de texto (pymupdf4llm)
        │  · Validación del paquete español de Tesseract
        │  · Si cobertura < 10% → OCR automático (Tesseract spa)
        │  · Reconstrucción de tablas (pdfplumber)
        │  · Fusión de tablas multi-página con mismos encabezados
        │  · Limpieza: índices, notas al pie, imágenes, citas largas
        │  · Informe de cobertura por archivo
        │
        ▼
[ md_brutos/ ]
        │
        ▼  Etapa 1.5 — refine_markdown.py
        │
        │  · Detección automática de necesidad (4 señales):
        │      - Mojibake (Ã¡, Ã³...) → encoding roto
        │      - OCR corrupto (~, dígitos mezclados con letras)
        │      - Encabezados de revista repetidos (> 3 veces)
        │      - Artículo enterrado en tomo completo
        │  · Si el archivo está limpio → copia directa (0 tokens)
        │  · Si necesita refinado → LLM (OpenRouter):
        │      - Extrae solo el artículo de interés
        │      - Repara encoding y OCR corrupto
        │      - Elimina ruido estructural de la revista
        │      - Unifica párrafos fragmentados por saltos de página
        │  · Chunking automático para documentos > 12.000 palabras
        │  · Cache: salta archivos ya existentes en md_refinados/
        │  · Informe de tokens consumidos por archivo
        │
        ▼
[ md_refinados/ ]
        │
        ▼  Etapa 2 — rag_builder_legacia.py
        │
        │  FASE 0 — Diagnóstico (código)
        │    · Detecta tipo de contenido: cultural | operativo | mixto | definiciones
        │    · Calcula parámetros de chunking: max_chars, overlap, min_chunk_size
        │    · Estima estrategia: semantic | fixed
        │
        │  FASE 1 — Preclean (código)
        │    · Elimina ligaduras, paginación, líneas repetidas
        │    · Strip pre-LLM: elimina imágenes y secciones de bibliografía
        │
        │  FASE 2 — Estrategia (código)
        │    · Árbol de decisión cuantitativo:
        │      short_line_ratio > 0.60 → fixed (OCR/ruido)
        │      headings + separadores < 3 → fixed (sin estructura)
        │      avg_section_len ≥ 3×max_chars → fixed (secciones enormes)
        │      resto → semantic
        │
        │  FASE 3+4 — Reescritura (LLM — única llamada por chunk)
        │    · Chunking automático para documentos grandes (> 60.000 chars):
        │        Nivel 1: divide por headings ##
        │        Nivel 2: si sección > límite → divide por párrafos \n\n
        │        Nivel 3: si párrafo > límite → corte fijo (último recurso)
        │    · Aplica prompt escritura_legacia_compact.md
        │    · Convierte en chunks RAG autosuficientes con contexto embebido
        │
        │  FASE 6 — Validación (código)
        │    · Checklist de calidad RAG
        │    · Detección de caveats críticos LegacIA:
        │        ❌ fluoruro como causa de desgaste dental
        │        ❌ momificación intencional de momias canarias
        │        ❌ "cruzaron el Atlántico"
        │        ❌ antipatrones de metadatos (11 y 12)
        │
        │  FASE 5+7 — Entrega (código)
        │    · Nombre normalizado del archivo
        │    · Parámetros de recuperación recomendados
        │    · Informe con coste, tokens y lagunas detectadas
        │
        ▼
[ md_rag/ ]  +  [ informes ]
```

---

## Setup

### Requisitos del sistema
- **Python 3.10+**
- **Tesseract OCR** (solo si tienes PDFs escaneados): instalar desde [UB Mannheim](https://github.com/UB-Mannheim/tesseract/wiki) incluyendo el paquete de idioma **Spanish (spa)**.

```bash
# 1. Crear estructura de carpetas
mkdir pdfs md_brutos md_refinados md_rag

# 2. Configurar clave API en .env
# OPENROUTER_API_KEY=sk-or-v1-...

# 3. Instalar dependencias
python -m venv .venv
.venv\Scripts\Activate.ps1          # Windows
source .venv/bin/activate           # Mac/Linux
pip install -r requirements.txt

# 4. Colocar PDFs en pdfs/
```

---

## Uso

```bash
# Pipeline completo: PDF → MD bruto → MD refinado → MD RAG
python scripts/run_all.py --proyecto LegacIA-Ruta9

# Solo Etapa 1 (PDF → MD bruto)
python scripts/run_all.py --proyecto LegacIA-Ruta9 --solo-etapa 1

# Solo Etapa 1.5 (MD bruto → MD refinado con IA)
python scripts/run_all.py --proyecto LegacIA-Ruta9 --solo-etapa 1.5

# Solo Etapa 2 (MD refinado → MD RAG)
python scripts/run_all.py --proyecto LegacIA-Ruta9 --solo-etapa 2

# Saltar la etapa de refinado (útil si los documentos ya están limpios)
python scripts/run_all.py --proyecto LegacIA-Ruta9 --sin-refinado

# Ver estimación de coste sin llamar a la API
python scripts/run_all.py --proyecto LegacIA-Ruta9 --dry-run

# Refinar solo un archivo concreto
python scripts/refine_markdown.py --archivo "Embalsamamiento de cadáveres.md"

# Re-forzar refinado aunque ya exista en md_refinados/
python scripts/refine_markdown.py --forzar

# Procesar solo un archivo en la Etapa 2
python scripts/rag_builder_legacia.py --proyecto LegacIA-Ruta9 --only doc1.md

# Re-procesar un archivo problemático con un modelo mejor
python scripts/rag_builder_legacia.py --proyecto LegacIA-Ruta9 --model google/gemini-2.5-flash --only doc3.md
```

---

## Coste estimado

La Etapa 1.5 solo consume tokens en archivos que realmente lo necesitan. Los documentos limpios no generan coste.

| Etapa | Modelo default | Coste por documento (~5.000 palabras) |
|---|---|---|
| Etapa 1.5 (refinado) | `deepseek/deepseek-v4-flash` | ~0.002 € (solo si necesita refinado) |
| Etapa 2 (RAG builder) | `deepseek/deepseek-v4-flash` | ~0.003 € |

Para documentos grandes (tomos completos de revista) el coste de la Etapa 1.5 puede ser mayor, pero después la Etapa 2 recibe un documento mucho más pequeño — el coste total es similar o inferior.

---

## Outputs

| Archivo | Descripción |
|---|---|
| `md_brutos/*.md` | Texto extraído del PDF, con limpieza básica |
| `md_refinados/*.md` | MD mejorado por IA (o copia directa si estaba limpio) |
| `md_rag/*.md` | MD final optimizado para RAG, listo para Agentia |
| `informe_cobertura.txt` | % de texto extraído por PDF en la Etapa 1 |
| `informe_refinado.txt` | Tokens consumidos y estado por archivo en la Etapa 1.5 |
| `informe_global_legacia.txt` | Resumen global de la Etapa 2: costes, caveats y lagunas |

---

## Estructura del repositorio

```
PDF2rag/
├── scripts/
│   ├── run_all.py               ← orquestador: Etapa 1 + 1.5 + 2
│   ├── convert_pdfs.py          ← Etapa 1: PDF → MD bruto
│   ├── refine_markdown.py       ← Etapa 1.5: MD bruto → MD refinado (IA)
│   └── rag_builder_legacia.py   ← Etapa 2: MD refinado → MD RAG
├── modules/
│   ├── fase0_diagnostico.py     ← diagnóstico cuantitativo y parámetros RAG
│   ├── fase1_extraccion.py      ← preclean y strip pre-LLM
│   ├── fase2_estrategia.py      ← árbol de decisión semantic/fixed
│   ├── fase34_llm.py            ← única llamada LLM con chunking automático
│   ├── fase6_checklist.py       ← validación + detección caveats LegacIA
│   └── fase7_entrega.py         ← naming, parámetros recuperación, informe
├── prompts/
│   ├── escritura_legacia_compact.md  ← prompt RAG para Etapa 2
│   └── refine_md.md                  ← prompt de refinado para Etapa 1.5
├── tests/                       ← scripts de diagnóstico y pruebas
├── legacy/                      ← versiones anteriores
├── output/                      ← informes generados
├── pdfs/                        ← input: PDFs originales (no versionado)
├── md_brutos/                   ← Etapa 1 output (no versionado)
├── md_refinados/                ← Etapa 1.5 output (no versionado)
├── md_rag/                      ← Etapa 2 output, listos para Agentia (no versionado)
├── requirements.txt
└── .gitignore
```

---

*Desarrollado para el ecosistema LegacIA — El Museo Canario.*
