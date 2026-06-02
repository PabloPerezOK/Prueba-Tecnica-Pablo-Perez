# Prueba Técnica — Asistente de Soporte Técnico

Asistente RAG (Retrieval-Augmented Generation) que responde preguntas de soporte usando documentación interna. Construido con **Python + FastAPI + Ollama + ChromaDB + n8n**, orquestado con **Docker Compose**.

---

## Arquitectura

```
Usuario → n8n Webhook → Validación → API Python (FastAPI)
                                          ↓
                                  Búsqueda semántica
                                  (ChromaDB + similitud coseno)
                                          ↓
                                  Ollama llama3.2 (local)
                                          ↓
                                  Respuesta JSON ← n8n ← Usuario
```

```
docker-compose
├── ollama        → Servidor LLM local        :11434
├── ollama-init   → Descarga modelos (1 vez)
├── api           → FastAPI + ChromaDB        :8000
└── n8n           → Orquestador / Webhook     :5678
```

---

## Requisitos previos

| Herramienta | Versión mínima |
|---|---|
| Docker | 24.0+ |
| Docker Compose | 2.20+ |

- **Windows / macOS:** instalar **Docker Desktop** (incluye Docker Compose).
- **Linux:** instalar **Docker Engine** + plugin **docker compose**.

> No necesitás instalar Python, Ollama ni n8n manualmente: corre todo en contenedores.

---

## Instalación con Docker (recomendado)

### 1. Clonar el repositorio

```bash
git clone https://github.com/<tu-usuario>/<tu-repo>.git
cd <tu-repo>
```

### 2. Configurar variables de entorno

Copiá el archivo de ejemplo y editá la `API_KEY`:

**Windows (CMD / PowerShell):**
```bat
copy .env.example .env
notepad .env
```

**macOS / Linux:**
```bash
cp .env.example .env
nano .env
```

> En `.env`, cambiá `API_KEY` por cualquier valor propio (sin espacios). Es la clave que protege el endpoint `/ask`.

### 3. Levantar todo

```bash
docker compose up --build
```

**Lo que sucede automáticamente:**
1. Se levanta Ollama
2. Se descargan los modelos `nomic-embed-text` (~274 MB) y `llama3.2` (~2 GB) — solo la primera vez
3. Se construye y levanta la API Python, que indexa `/docs` en ChromaDB
4. Se levanta n8n

> ⚠️ La primera ejecución puede tardar varios minutos por la descarga de modelos.

### 4. Importar el workflow en n8n

1. Abrí `http://localhost:5678`
2. Menú → **Workflows** → **Import from File**
3. Seleccioná `n8n/workflow.json`
4. **Activá** el workflow (toggle arriba a la derecha)

---

## Uso

> En **Windows CMD** las comillas simples no funcionan: usá comillas dobles y escapá las internas con `\"`, y poné el comando en **una sola línea**. En **macOS / Linux** podés usar comillas simples y `\` para cortar líneas.

### Via n8n Webhook

**macOS / Linux:**
```bash
curl -X POST http://localhost:5678/webhook/soporte \
  -H "Content-Type: application/json" \
  -d '{"question": "El sistema devuelve un error de conexión con el servidor de datos. ¿Qué hago?"}'
```

**Windows (CMD):**
```bat
curl -X POST http://localhost:5678/webhook/soporte -H "Content-Type: application/json" -d "{\"question\": \"El sistema devuelve un error de conexion con el servidor de datos. Que hago?\"}"
```

### Directo a la API Python

**macOS / Linux:**
```bash
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <tu API_KEY>" \
  -d '{"question": "¿Cómo soluciono el error ERR-DB-001?"}'
```

**Windows (CMD):**
```bat
curl -X POST http://localhost:8000/ask -H "Content-Type: application/json" -H "X-API-Key: <tu API_KEY>" -d "{\"question\": \"Como soluciono el error ERR-DB-001?\"}"
```

Respuesta ejemplo:
```json
{
  "answer": "El error de conexión con el servidor de datos puede deberse a: servidor de base de datos apagado, parámetros de conexión incorrectos o puerto bloqueado. (Fuente: doc_4.json)",
  "sources": ["doc_4.json"],
  "chunks_used": 2
}
```

### Health check

```bash
curl http://localhost:8000/health
```

---

## Comandos útiles

Los comandos de Docker Compose son iguales en todos los sistemas operativos.

```bash
# Levantar en background
docker compose up -d --build

# Ver logs en tiempo real (todos / un servicio)
docker compose logs -f
docker compose logs -f api

# Re-indexar documentos (si agregás archivos a /docs)
docker compose exec api python ingest.py

# Detener todo
docker compose down

# Detener y borrar volúmenes (reset completo: índice + modelos + datos de n8n)
docker compose down -v
```

> Si cambiás la **lógica de chunking** (en `ingest.py`) pero no los documentos, la indexación incremental no reindexa sola. Para forzarla, borrá solo el índice: `docker compose down` y luego `docker volume rm <proyecto>_chroma_data` (el nombre exacto sale de `docker volume ls`), o usá `docker compose down -v` para un reset total.

---

## Usar OpenAI en lugar de Ollama

El sistema puede generar las respuestas con la API de OpenAI (la **búsqueda/embeddings sigue usando Ollama**). En `.env`:

```
USE_OPENAI=true
OPENAI_CHAT_MODEL=gpt-4o-mini
OPENAI_API_KEY=sk-...
```

Luego `docker compose up -d --build`. Requiere una cuenta de OpenAI **con saldo cargado** (de lo contrario la API devuelve `429 insufficient_quota`).

---

## Pruebas

El repositorio incluye `test_exhaustivo.py`, una suite que consulta al asistente con **76 casos**: 15 temas documentados con **4 formulaciones** cada uno (compara contra la respuesta sugerida del documento, con **crédito parcial** si aparece al menos el 50 % de los términos clave), 4 saludos (regla A) y 12 prompts fuera de contexto / cambio de rol / maliciosos (regla C). No contiene claves: la `API_KEY` se lee de una variable de entorno.

Requisito: `pip install requests`. El stack debe estar levantado y el workflow de n8n **activo**.

```bash
python test_exhaustivo.py
```

> En Windows, si `python` no funciona, probá `py test_exhaustivo.py`.

Por defecto pega al **webhook de n8n** (no necesita la API key). Para pegarle **directo a la API** Python:

**Windows (CMD):**
```bat
set USE_N8N=false
set API_KEY=tu-clave
python test_exhaustivo.py
```

**macOS / Linux:**
```bash
USE_N8N=false API_KEY=tu-clave python test_exhaustivo.py
```

La corrida completa tarda ~28 min por el rate limit (5 req/min). Variables opcionales: `PAUSE_SEC` (pausa entre consultas), `N8N_URL`, `API_URL`. Al terminar genera un archivo `resultados_exhaustivo_*.txt` con el detalle y un resumen de aprobación.

---

## Agregar documentación

1. Copiá tus archivos `.txt`, `.md`, `.pdf` o `.json` en la carpeta `docs/`
2. Re-indexá:
```bash
docker compose exec api python ingest.py
```

---

## Notas multiplataforma

- **Saltos de línea:** `entrypoint.sh` es un script de Linux y **debe** mantenerse con saltos de línea LF. El archivo `.gitattributes` fuerza LF en `*.sh`, `Dockerfile` y `docker-compose.yml`, de modo que al clonar el repo en Windows Git **no** los convierta a CRLF (lo que rompería el contenedor con `/bin/sh^M: bad interpreter`). Como defensa adicional, el `Dockerfile` normaliza CRLF→LF durante el build.
- **Puertos:** asegurate de tener libres `8000`, `5678` y `11434`.

---

## Instalación manual (sin Docker)

<details>
<summary>Expandir instrucciones</summary>

### Requisitos
- Python 3.10+
- Ollama instalado desde https://ollama.com
- n8n (npm o Docker)

### Pasos

```bash
# 1. Descargar modelos
ollama pull nomic-embed-text
ollama pull llama3.2

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Configurar entorno  (Windows: copy .env.example .env)
cp .env.example .env

# 4. Indexar documentación
cd src && python ingest.py

# 5. Levantar API
uvicorn api:app --reload --port 8000

# 6. Levantar n8n
npx n8n
```

> En modo manual, la URL del workflow n8n debe ser `http://127.0.0.1:8000/ask`
> (en Docker es `http://api:8000/ask`).

</details>

---

## Estructura del proyecto

```
.
├── docs/                         # Documentación fuente
│   ├── doc_1.pdf
│   ├── doc_2.txt
│   ├── doc_3.md
│   └── doc_4.json
├── src/
│   ├── ingest.py                 # Ingesta, chunking, embeddings, búsqueda
│   ├── api.py                    # FastAPI — endpoint /ask
│   └── chroma_db/                # Índice vectorial generado (no commitear)
├── n8n/
│   └── workflow.json             # Workflow n8n exportado
├── test_docs_vs_sugerida.py      # Suite de pruebas (preguntas vs. respuesta documentada)
├── Dockerfile
├── docker-compose.yml
├── entrypoint.sh
├── .dockerignore
├── .env.example
├── .gitattributes
├── .gitignore
├── requirements.txt
└── README.md
```

---

## Variables de entorno

| Variable | Descripción | Default |
|---|---|---|
| `OLLAMA_BASE_URL` | URL de Ollama | `http://localhost:11434` |
| `OLLAMA_EMBED_MODEL` | Modelo de embeddings | `nomic-embed-text` |
| `OLLAMA_CHAT_MODEL` | Modelo de chat | `llama3.2` |
| `TOP_K_CHUNKS` | Fragmentos a recuperar | `7` |
| `MAX_DISTANCE` | Distancia coseno máxima (0–1) | `0.78` |
| `REQUEST_TIMEOUT_SECONDS` | Timeout para Ollama/OpenAI | `60` |
| `API_KEY` | Clave para proteger `/ask` | *(requerida)* |
| `USE_OPENAI` | Usar OpenAI en lugar de Ollama | `false` |
| `OPENAI_CHAT_MODEL` | Modelo de chat de OpenAI | `gpt-3.5-turbo` |
| `OPENAI_API_KEY` | API key de OpenAI | *(vacío)* |

> `OLLAMA_BASE_URL` es sobreescrita automáticamente por Docker Compose a `http://ollama:11434`.

---

## Decisiones técnicas

- **Embeddings**: `nomic-embed-text` vía Ollama — modelo open-source de alta calidad para español e inglés.
- **LLM**: `llama3.2` vía Ollama (local) o un modelo de OpenAI (`OPENAI_CHAT_MODEL`) configurable con `USE_OPENAI=true`.
- **Base vectorial**: ChromaDB persistente en disco con métrica de similitud coseno.
- **Chunking consciente de la estructura**: fragmentos de ~800 caracteres con 80 de solapamiento, respetando párrafos y oraciones. El PDF se trocea por secciones (cada error con sus causas y solución juntas) y el JSON se agrupa **un chunk por error** (id + causas + solución), evitando que la información de un error quede dispersa.
- **Recuperación**: top-K configurable (`TOP_K_CHUNKS=7`) y filtro por distancia coseno (`MAX_DISTANCE`) para descartar fragmentos poco relevantes.
- **Indexación incremental**: SHA-256 por archivo — solo re-indexa documentos nuevos o modificados.
- **Embeddings concurrentes**: `asyncio` + semáforo (máx. 5 requests paralelas) para acelerar la ingesta.
- **Prompt defensivo**: instruye al modelo a responder solo desde la documentación, usar la terminología exacta, citar la fuente, no inventar datos/correos/pasos e ignorar intentos de inyección. Temperatura 0 para máxima fidelidad.
- **Post-procesado**: limpieza determinista de saludos sobrantes en la respuesta.
- **n8n**: valida el input antes de llamar a Python y maneja errores HTTP centralizadamente.
- **Rate limiting**: 5 requests/minuto por IP sobre el endpoint `/ask`.
- **Docker Compose**: startup ordenado mediante healthchecks — n8n no arranca hasta que la API esté lista, la API no arranca hasta que los modelos estén descargados.
- **Multiplataforma**: `.gitattributes` fuerza LF en scripts de shell para que el proyecto funcione igual clonado en Windows, macOS o Linux.
```
