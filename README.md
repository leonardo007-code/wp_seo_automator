# WP SEO Automator

Sistema de automatización de contenido en WordPress con IA desacoplada.

## Requisitos

- Python 3.11+
- WordPress con REST API habilitada y Application Passwords activadas

## Setup

```bash
# 1. Crear entorno virtual
python -m venv .venv
.venv\Scripts\activate  # Windows
# source .venv/bin/activate  # Linux/macOS

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Configurar entorno
cp .env.example .env
# Editar .env con tus credenciales reales

# 4. Arrancar el servidor
uvicorn src.main:app --reload
```

## Verificar que arranca

```
GET http://localhost:8000/health
```

Respuesta esperada:
```json
{
  "status": "ok",
  "llm_backend": "gemini",
  "dry_run_default": true
}
```

## Arquitectura

```
src/
├── config/       → Configuración type-safe (pydantic-settings)
├── domain/       → Entidades y contratos del negocio (sin dependencias externas)
├── application/  → Casos de uso y servicios de orquestación
├── infrastructure/ → Implementaciones: Gemini, WP REST, backups
└── api/          → Endpoints FastAPI, schemas, inyección de dependencias
```

## Proveedores LLM disponibles

| Backend | Variable `LLM_BACKEND` |
|---|---|
| Gemini API | `gemini` |
| Ollama local | `ollama` (Fase 3+) |
| OpenAI-compatible | `openai_compatible` (Fase 3+) |

## Roadmap

- [x] Fase 1: Config, domain entities, contratos
- [ ] Fase 2: Servicio de protección estructural (placeholder tokenizer)
- [ ] Fase 3: Proveedor Gemini
- [ ] Fase 4: Cliente WordPress REST
- [ ] Fase 5: Use Cases, backups, logs, diff, dry-run
- [ ] Fase 6: API completa y pruebas end-to-end
