# WP SEO Automator

Sistema de automatización de contenido en WordPress con IA desacoplada.  
Modifica el texto de páginas WordPress usando Gemini (u otros LLMs) sin
tocar shortcodes, bloques Gutenberg, scripts ni iframes.

> **Estado actual (real):** Backend funcional para flujo `dry_run` con Gemini + WordPress REST API, con soporte operacional por modo para Classic/Gutenberg/Divi y análisis seguro para Elementor/Oxygen/Breakdance/Bricks.

---

## Arquitectura

```
src/
├── config/         → Configuración type-safe (pydantic-settings + .env)
├── domain/         → Entidades, excepciones y contratos (sin dependencias externas)
├── application/    → Casos de uso y servicios de orquestación
│   ├── services/   → ContentProtectionService, DiffService
│   └── use_cases/  → ModifyPageUseCase
├── infrastructure/ → Implementaciones concretas
│   ├── providers/  → GeminiProvider (Gemini API)
│   ├── repositories/ → LocalBackupRepository
│   └── wordpress/  → WpRestClient (REST API con Application Passwords)
└── api/            → FastAPI: endpoints, schemas, inyección de dependencias
    └── routes/     → POST /api/v1/modifications
```

**Flujo de una request:**
```
POST /api/v1/modifications
  → Resolve identifier (URL/slug/ID) → page_id
  → GET page content (context=edit, RAW HTML)
  → Save backup (siempre, incluso en dry_run)
  → Tokenize protected elements (shortcodes, wp:blocks, scripts, iframes, forms)
  → Extract editable text segments
  → Transform segments via Gemini
  → Validate structural integrity
  → Evaluate operation_mode (safe_apply | analysis_only | blocked_no_content)
  → [dry_run=true]  → Return result (NO publica nada)
  → [dry_run=false] → Publish only if policy allows it
  → Save audit log
```

### Modos operativos

- `safe_apply`: hay segmentos editables y publicación permitida (Classic/Gutenberg y Divi con cautela).
- `analysis_only`: hay segmentos útiles para propuesta/diff, pero publicación bloqueada por seguridad (Elementor/Oxygen/Breakdance/Bricks o detección ambigua).
- `blocked_no_content`: no se detectó contenido útil para editar con seguridad.

---

## Requirements

- Python 3.11+
- WordPress 5.6+ con REST API habilitada
- Application Passwords activadas en WordPress
- Cuenta de Google AI Studio (para Gemini API key)

---

## Setup

### 1. Clonar y crear entorno virtual

```bash
git clone <repo_url>
cd wp_seo_automator

python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate   # Linux/macOS

pip install -r requirements.txt
```

### 2. Crear el archivo `.env`

```bash
copy .env.example .env    # Windows
# cp .env.example .env   # Linux/macOS
```

Editar `.env` con tus credenciales reales:

```env
# === WordPress ===
WP_BASE_URL=https://tusitio.com          # Sin trailing slash
WP_API_USER=tu_usuario_wordpress
# Genera la Application Password desde:
# WP Admin → Usuarios → Tu perfil → Contraseñas de Aplicación → Nueva
WP_API_APP_PASSWORD=xxxx xxxx xxxx xxxx  # Los espacios son normales y deben mantenerse

# === LLM ===
LLM_BACKEND=gemini
# Obtén tu API key en: https://aistudio.google.com/app/apikey
GEMINI_API_KEY=AIzaSy...
GEMINI_MODEL=gemini-2.0-flash

# === Comportamiento ===
DRY_RUN_DEFAULT=true    # Valor por defecto cuando el request omite dry_run

# === Storage ===
BACKUP_DIR=./backups
LOG_DIR=./logs
LOG_LEVEL=INFO

# === HTTP ===
REQUEST_TIMEOUT_SECONDS=30
MAX_RETRIES=3
```

> ⚠️ **Regla de seguridad:** Deja `DRY_RUN_DEFAULT=true`. Si `dry_run` se omite en la request, el backend usará ese valor configurado.

---

## Correr el servidor

```bash
# Con recarga automática (desarrollo)
.venv\Scripts\uvicorn src.main:app --reload

# Acceder a la documentación interactiva
# Swagger UI:  http://localhost:8000/docs
# ReDoc:       http://localhost:8000/redoc
```

---

## Verificar que el servidor arranca

```bash
curl http://localhost:8000/health
```

Respuesta esperada:
```json
{
  "status": "ok",
  "llm_backend": "gemini",
  "dry_run_default": true,
  "wp_base_url": "https://tusitio.com"
}
```

---

## Ejecutar los tests

```bash
# Todos los tests (no requiere .env ni conexión real)
.venv\Scripts\python -m pytest tests/ -v

# Solo un módulo
.venv\Scripts\python -m pytest tests/unit/test_gemini_provider.py -v

# Con reporte de cobertura (requiere pytest-cov)
.venv\Scripts\python -m pytest tests/ --cov=src --cov-report=term-missing
```

Los tests usan mocks — no necesitas credenciales reales para correrlos.

---

## Validar conectividad real (antes de usar el servidor)

Usa el script `scripts/validate.py` para verificar que todo funciona
**antes** de hacer requests al servidor:

```bash
# Validar todo en secuencia
.venv\Scripts\python scripts/validate.py

# Solo verificar la configuración (.env)
.venv\Scripts\python scripts/validate.py --only=env

# Solo verificar WordPress
.venv\Scripts\python scripts/validate.py --only=wp

# Solo verificar Gemini
.venv\Scripts\python scripts/validate.py --only=gemini

# Ejecutar el flujo dry_run completo contra una página específica
.venv\Scripts\python scripts/validate.py --only=dryrun --page="servicios"
.venv\Scripts\python scripts/validate.py --only=dryrun --page="42"
.venv\Scripts\python scripts/validate.py --only=dryrun --page="https://tusitio.com/servicios/"
```

---

## Probar el endpoint principal

Con el servidor corriendo:

```bash
# dry_run (SEGURO — no publica nada)
curl -X POST http://localhost:8000/api/v1/modifications \
  -H "Content-Type: application/json" \
  -d '{
    "identifier": "servicios",
    "instructions": "Mejora el SEO de este contenido sin keyword stuffing.",
    "dry_run": true
  }'
```

O desde Swagger UI (http://localhost:8000/docs): usa el endpoint `POST /api/v1/modifications`.

El campo `identifier` acepta:
- **ID numérico**: `"42"`
- **Slug**: `"servicios"` o `"about-us"`
- **URL pública**: `"https://tusitio.com/servicios/"`

---

## Revisar backups y logs

Los backups se guardan automáticamente antes de cualquier procesamiento:

```
backups/
  {page_id}/
    {timestamp}.json    ← HTML original de la página antes de cada análisis
logs/
  modifications.jsonl   ← Registro de auditoría (JSON por línea, append-only)
```

```bash
# Ver el último backup
dir backups\               # Windows
ls -lt backups/            # Linux/macOS

# Ver el log de operaciones
type logs\modifications.jsonl           # Windows
cat logs/modifications.jsonl | python -m json.tool  # pretty print última línea
```

---

## Probar apply (publicar cambios reales)

> ⚠️ **Solo cuando estés 100% seguro.** Haz backup manual del contenido antes.

```bash
curl -X POST http://localhost:8000/api/v1/modifications \
  -H "Content-Type: application/json" \
  -d '{
    "identifier": "42",
    "instructions": "Mejora el SEO sin cambiar el significado.",
    "dry_run": false
  }'
```

El sistema valida integridad estructural y política de builder antes de publicar.
Si la validación falla o el builder es `analysis_only`, **rechaza la publicación** y devuelve error explícito.

---

## Proveedores LLM disponibles

| Backend | Variable `LLM_BACKEND` | Estado |
|---|---|---|
| Gemini API | `gemini` | ✅ Implementado |
| Ollama local | `ollama` | 🔜 Roadmap |
| OpenAI-compatible | `openai_compatible` | 🔜 Roadmap |

---

## Builders soportados y límites reales

| Builder | Detección | Extracción | Publicación |
|---|---|---|---|
| Classic Editor | ✅ | `standard` | ✅ |
| Gutenberg | ✅ | `standard` | ✅ |
| Divi | ✅ | `divi_shortcode` | ✅ (con cautela) |
| Elementor | ✅ | `rendered_html` | ❌ (`analysis_only`) |
| Oxygen | ✅ | `rendered_html` | ❌ (`analysis_only`) |
| Breakdance | ✅ | `rendered_html` | ❌ (`analysis_only`) |
| Bricks | ✅ | `rendered_html` | ❌ (`analysis_only`) |

### Reglas de extraccion builder-aware

Para `elementor`, `oxygen`, `breakdance` y `bricks`, el extractor usa HTML renderizado
con filtros por builder (no scraping ciego):

- **Detecta y prioriza texto SEO util**: `h1-h6`, `p`, `li`, `blockquote`, `button`, anchors con texto relevante, y `alt` util.
- **Incluye tabs/acordeones**: extrae texto en wrappers con firmas `tab|accordion|toggle|faq|panel|content`.
- **Evita ruido tecnico**: elimina `script`, `style`, `iframe`, `form`, `nav`, `header`, `footer`, `aside`.
- **Evita wrappers no editoriales** con reglas por builder:
  - Elementor: filtra nav/search/logo widgets, mantiene `elementor-widget-container`.
  - Oxygen: filtra wrappers de menu/header (`oxy-header`, `ct-menu`, etc.).
  - Breakdance: filtra clases de menu (`bde-menu`, `bde-mobile-menu`).
  - Bricks: filtra clases de menu (`bricks-nav-menu`, `bricks-mobile-menu`).
- **No publica en builders meta-driven**: resultado en `analysis_only` para proteger layout.

## Limitaciones actuales del MVP

1. **No hay escritura segura en post meta de builders**: Elementor/Oxygen/Breakdance/Bricks siguen en `analysis_only`. Se extrae bien para analisis SEO, pero no se publica via `content.raw`.
2. **No hay frontend**: La interacción es via API. Se puede usar Swagger UI, curl, Postman o cualquier cliente HTTP.
3. **Solo un proveedor LLM activo**: Gemini. El sistema está desacoplado para agregar otros.
4. **Los backups son locales**: En `./backups/`. No hay sincronización a S3 ni base de datos.
5. **Sin rate limiting**: El endpoint no tiene protección contra llamadas masivas.
6. **Sin autenticación de la API**: El servidor FastAPI no requiere auth. No exponer a internet sin un proxy con autenticación.

### Como probar la extraccion por builder

```bash
# Deteccion de builder y politica
.venv\Scripts\python -m pytest tests/unit/test_builder_detector.py -q

# Extraccion builder-aware desde HTML renderizado
.venv\Scripts\python -m pytest tests/unit/test_rendered_html_extractor.py -q

# Flujo completo del caso de uso (orquestacion + guardas)
.venv\Scripts\python -m pytest tests/unit/test_modify_page_usecase.py -q
```

---

## Qué falta antes de un frontend (opcional)

El backend está completo y funcional. Si decides agregar un frontend:

1. El endpoint `GET /health` ya existe para status checks.
2. El endpoint `POST /api/v1/modifications` devuelve el diff completo, el contenido original y el propuesto — suficiente para mostrar una comparación visual.
3. Necesitarías: autenticación (JWT o API keys), rate limiting, y posiblemente un endpoint `GET /modifications/{id}` para ver histórico.
4. La documentación Swagger en `/docs` ya es suficiente para integrar cualquier cliente.

**Conclusión**: No necesitas frontend para validar si el sistema funciona. El backend es tu validador real.
