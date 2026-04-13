# WP SEO Automator

Sistema de automatización de contenido en WordPress con IA desacoplada.  
Modifica el texto de páginas WordPress usando Gemini (u otros LLMs) sin
tocar shortcodes, bloques Gutenberg, scripts ni iframes.

> **Estado actual:** Backend 100% funcional. 133+ tests pasando. Validado en dry_run.

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
  → [dry_run=true]  → Return result (NO publica nada)
  → [dry_run=false] → Publish to WordPress
  → Save audit log
```

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
DRY_RUN_DEFAULT=true    # SIEMPRE en true hasta que valides el sistema

# === Storage ===
BACKUP_DIR=./backups
LOG_DIR=./logs
LOG_LEVEL=INFO

# === HTTP ===
REQUEST_TIMEOUT_SECONDS=30
MAX_RETRIES=3
```

> ⚠️ **Regla de seguridad:** Deja `DRY_RUN_DEFAULT=true` y `"dry_run": true`
> en los requests hasta que hayas validado el sistema. El servidor nunca publicará
> cambios reales si `dry_run=true` está presente en el request body.

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

El sistema valida la integridad estructural del HTML reconstruido antes de publicar.
Si la validación falla, **rechaza la publicación** y devuelve `ContentIntegrityError`.

---

## Proveedores LLM disponibles

| Backend | Variable `LLM_BACKEND` | Estado |
|---|---|---|
| Gemini API | `gemini` | ✅ Implementado |
| Ollama local | `ollama` | 🔜 Roadmap |
| OpenAI-compatible | `openai_compatible` | 🔜 Roadmap |

---

## Limitaciones actuales del MVP

1. **Segmentos con contenido mixto se omiten**: `<p>Texto <strong>negrita</strong></p>` — el `<p>` no se extrae porque tiene elementos hijo. Es conservador por diseño.
2. **No hay frontend**: La interacción es via API. Se puede usar Swagger UI, curl, Postman o cualquier cliente HTTP.
3. **Solo un proveedor LLM activo**: Gemini. El sistema está desacoplado para agregar otros fácilmente.
4. **Los backups son locales**: En `./backups/`. No hay sincronización a S3 ni base de datos.
5. **Sin rate limiting**: El endpoint no tiene protección contra llamadas masivas.
6. **Sin autenticación de la API**: El servidor FastAPI no requiere auth. No exponer a internet sin un proxy con autenticación.

---

## Qué falta antes de un frontend (opcional)

El backend está completo y funcional. Si decides agregar un frontend:

1. El endpoint `GET /health` ya existe para status checks.
2. El endpoint `POST /api/v1/modifications` devuelve el diff completo, el contenido original y el propuesto — suficiente para mostrar una comparación visual.
3. Necesitarías: autenticación (JWT o API keys), rate limiting, y posiblemente un endpoint `GET /modifications/{id}` para ver histórico.
4. La documentación Swagger en `/docs` ya es suficiente para integrar cualquier cliente.

**Conclusión**: No necesitas frontend para validar si el sistema funciona. El backend es tu validador real.
