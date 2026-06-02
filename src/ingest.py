import os
import json
import re
import hashlib
import asyncio
from pathlib import Path
from typing import List, Dict
from urllib.parse import urlparse

import pdfplumber
import httpx
import chromadb
from dotenv import load_dotenv

# Carga variables de entorno desde .env (en Docker las inyecta Compose directamente)
load_dotenv()

# ──────────────────────────────────────────
# Configuración y Seguridad
# ──────────────────────────────────────────

BASE_DIR    = Path(__file__).parent
DOCS_DIR    = BASE_DIR.parent / "docs"
DB_DIR      = BASE_DIR / "chroma_db"
HASH_FILE   = BASE_DIR / "file_hashes.json"

CHUNK_SIZE    = 800
CHUNK_OVERLAP = 80
MAX_FILE_SIZE_MB = 50  # Límite para evitar Out Of Memory (DoS)

OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")

# Validación SSRF básica para la URL de Ollama
def validate_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ["http", "https"] or not parsed.hostname:
        raise ValueError(f"[ALERTA DE SEGURIDAD] OLLAMA_BASE_URL inválida: {url}")
    return url

OLLAMA_BASE = validate_url(OLLAMA_BASE)

# Inicializar ChromaDB con métrica de Similitud de Coseno
chroma_client = chromadb.PersistentClient(path=str(DB_DIR))
collection = chroma_client.get_or_create_collection(
    name="documentos_rag",
    metadata={"hnsw:space": "cosine"}
)


# ──────────────────────────────────────────
# Indexación Incremental Segura (SHA-256)
# ──────────────────────────────────────────

def get_file_hash(path: Path) -> str:
    """Calcula el SHA-256 de un archivo (Criptográficamente seguro contra colisiones)."""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        # Leemos en bloques pequeños para no saturar la RAM con archivos medianos
        for chunk in iter(lambda: f.read(4096), b""):
            hasher.update(chunk)
    return hasher.hexdigest()

def load_hashes() -> Dict[str, str]:
    if HASH_FILE.exists():
        return json.loads(HASH_FILE.read_text(encoding="utf-8"))
    return {}

def save_hashes(hashes: Dict[str, str]) -> None:
    HASH_FILE.write_text(json.dumps(hashes, indent=2), encoding="utf-8")


# ──────────────────────────────────────────
# Lectura y Limpieza
# ──────────────────────────────────────────

def read_txt(path: Path) -> str: return path.read_text(encoding="utf-8", errors="ignore")
def read_md(path: Path) -> str: return path.read_text(encoding="utf-8", errors="ignore")

# Líneas que inician una sección nueva en los PDF de soporte. Se les antepone
# un salto de párrafo para que el chunker no mezcle secciones distintas en un
# mismo fragmento (p. ej. laboratorio + contacto quedaban juntos).
PDF_SECTION_RE = re.compile(
    r"^(?:\d+\.\d+(?:\.\d+)?\s+|Contacto de soporte|El cat\u00e1logo)",
    re.IGNORECASE,
)

def read_pdf(path: Path) -> str:
    out = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if not t:
                continue
            for line in t.split("\n"):
                line = line.strip()
                if not line:
                    continue
                # Header de sección -> nuevo párrafo; resto -> misma línea lógica
                out.append(("\n\n" + line) if PDF_SECTION_RE.match(line) else line)
    return "\n".join(out)

def read_json(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))

    def flatten(obj, prefix="") -> List[str]:
        lines = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                lines.extend(flatten(v, f"{prefix}{k}: "))
        elif isinstance(obj, list):
            for item in obj:
                lines.extend(flatten(item, prefix))
        else:
            val = str(obj).strip()
            if val and val[-1] not in ".!?":
                val += "."
            lines.append(f"{prefix}{val}")
        return lines

    # FIX: agrupar por REGISTRO. Cada objeto de una lista (p. ej. cada error en
    # "contenido") se vuelve UN bloque coherente (líneas unidas por \n), y los
    # bloques se separan con \n\n. Antes cada hoja del JSON era un párrafo
    # suelto -> el JSON quedaba en decenas de fragmentos minúsculos y la
    # solución de un error se separaba de su id/causas, perjudicando la
    # recuperación por código de error (ERR-DB-001, ERR-CAT-001).
    blocks = []

    def emit_list(key, lst):
        for item in lst:
            if isinstance(item, (dict, list)):
                blocks.append("\n".join(flatten(item)))
            else:
                blocks.append("\n".join(flatten(item, f"{key}: " if key else "")))

    def emit(obj):
        if isinstance(obj, dict):
            scalars = {k: v for k, v in obj.items() if not isinstance(v, (dict, list))}
            if scalars:
                blocks.append("\n".join(flatten(scalars)))
            for k, v in obj.items():
                if isinstance(v, list):
                    emit_list(k, v)
                elif isinstance(v, dict):
                    emit(v)
        elif isinstance(obj, list):
            emit_list("", obj)
        else:
            blocks.append("\n".join(flatten(obj)))

    emit(data)
    return "\n\n".join(b for b in blocks if b.strip())

READERS = {".txt": read_txt, ".md": read_md, ".pdf": read_pdf, ".json": read_json}

def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[^\x09\x0A\x20-\x7E\x80-\xFFáéíóúÁÉÍÓÚñÑüÜ]", " ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


# ──────────────────────────────────────────
# Chunking Semántico con Overlap Activo
# ──────────────────────────────────────────

def split_into_chunks(text: str, source: str) -> List[Dict]:
    """Corta inteligentemente respetando párrafos y oraciones, aplicando CHUNK_OVERLAP."""
    chunks = []
    paragraphs = text.split("\n\n")

    for p in paragraphs:
        if not p.strip():
            continue

        sentences = re.split(r'(?:(?<=[.!?])\s+|\n+)', p)
        current_chunk_sentences = []
        current_length = 0

        for s in sentences:
            s = s.strip()
            if not s:
                continue

            s_len = len(s) + 1

            if current_length + s_len <= CHUNK_SIZE:
                current_chunk_sentences.append(s)
                current_length += s_len
            else:
                if current_chunk_sentences:
                    chunks.append({
                        "text": " ".join(current_chunk_sentences).strip(),
                        "source": source
                    })

                overlap_sentences = []
                overlap_len = 0
                for old_s in reversed(current_chunk_sentences):
                    if overlap_len + len(old_s) + 1 <= CHUNK_OVERLAP:
                        overlap_sentences.insert(0, old_s)
                        overlap_len += len(old_s) + 1
                    else:
                        break

                current_chunk_sentences = overlap_sentences + [s]
                current_length = sum(len(x) + 1 for x in current_chunk_sentences)

        if current_chunk_sentences:
            chunks.append({
                "text": " ".join(current_chunk_sentences).strip(),
                "source": source
            })

    return chunks


# ──────────────────────────────────────────
# Embeddings Concurrentes (Asyncio)
# ──────────────────────────────────────────

async def get_embedding(client: httpx.AsyncClient, text: str, sem: asyncio.Semaphore) -> List[float]:
    async with sem:
        resp = await client.post(
            f"{OLLAMA_BASE}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text}
        )
        resp.raise_for_status()
        return resp.json()["embedding"]

async def embed_texts_concurrent(texts: List[str]) -> List[List[float]]:
    sem = asyncio.Semaphore(5)
    async with httpx.AsyncClient(timeout=120.0) as client:
        tasks = [get_embedding(client, text, sem) for text in texts]
        return await asyncio.gather(*tasks)


# ──────────────────────────────────────────
# Pipeline Principal
# ──────────────────────────────────────────

async def build_index_async():
    if not DOCS_DIR.exists():
        print(f"[ERROR] La carpeta {DOCS_DIR} no existe.")
        return

    file_hashes = load_hashes()
    new_hashes = {}
    chunks_to_process = []

    max_bytes = MAX_FILE_SIZE_MB * 1024 * 1024

    # 1. Escanear, Limpiar y Hashing
    for path in sorted(DOCS_DIR.iterdir()):
        ext = path.suffix.lower()
        if ext not in READERS: continue

        # Filtro de tamaño para evitar DoS por archivos masivos
        if path.stat().st_size > max_bytes:
            print(f"[ALERTA] {path.name} excede el límite de {MAX_FILE_SIZE_MB}MB. Omitiendo.")
            if path.name in file_hashes:
                new_hashes[path.name] = file_hashes[path.name]
            continue

        try:
            current_hash = get_file_hash(path)
            new_hashes[path.name] = current_hash

            if file_hashes.get(path.name) == current_hash:
                print(f"[SKIP] {path.name} (Sin cambios)")
                continue

            if path.name in file_hashes:
                print(f"[UPDATE] {path.name} modificado. Limpiando fragmentos obsoletos...")
                collection.delete(where={"source": path.name})
            else:
                print(f"[READ] {path.name} (Nuevo archivo)")

            raw = READERS[ext](path)
            cleaned = clean_text(raw)
            chunks = split_into_chunks(cleaned, source=path.name)

            for idx, chunk in enumerate(chunks):
                chunk["id"] = f"{path.name}_chunk_{idx}"

            chunks_to_process.extend(chunks)

        except Exception as exc:
            print(f"  [ERROR] Fallo crítico leyendo {path.name}: {exc}")

    # Manejo de archivos eliminados
    deleted_files = set(file_hashes.keys()) - set(new_hashes.keys())
    for df in deleted_files:
        collection.delete(where={"source": df})
        print(f"[DELETE] {df} removido por completo del índice.")

    if not chunks_to_process:
        print("\n[OK] No hay cambios pendientes. Índice al día.")
        save_hashes(new_hashes)
        return

    # 2. Generar Embeddings
    print(f"\n[EMBED] Generando embeddings para {len(chunks_to_process)} fragmentos...")
    texts = [c["text"] for c in chunks_to_process]
    vectors = await embed_texts_concurrent(texts)

    # 3. Guardar en ChromaDB
    print(f"[DB] Guardando en ChromaDB...")
    ids = [c["id"] for c in chunks_to_process]
    metadatas = [{"source": c["source"]} for c in chunks_to_process]

    collection.upsert(
        ids=ids,
        embeddings=vectors,
        documents=texts,
        metadatas=metadatas
    )

    save_hashes(new_hashes)
    print(f"[OK] Proceso terminado con éxito. {len(chunks_to_process)} fragmentos procesados.")


# ──────────────────────────────────────────
# Búsqueda usando ChromaDB
# ──────────────────────────────────────────

async def search(query: str, top_k: int = 5) -> List[Dict]:
    # FIX: raise_for_status() + manejo de errores.
    # El original no validaba la respuesta de Ollama; si estaba caído o devolvía
    # un error, resp.json()["embedding"] lanzaba KeyError sin logging ni 
    # mensaje claro al usuario.
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{OLLAMA_BASE}/api/embeddings",
                json={"model": EMBED_MODEL, "prompt": query}
            )
            resp.raise_for_status()
            query_vector = resp.json().get("embedding")

        if not query_vector:
            raise ValueError("Ollama devolvió una respuesta de embedding vacía.")

    except httpx.TimeoutException as exc:
        raise TimeoutError("Timeout al contactar Ollama para generar el embedding.") from exc
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"Error HTTP de Ollama al generar embedding: {exc.response.status_code}"
        ) from exc

    # FIX: ChromaDB lanza excepción si n_results > cantidad de documentos en
    # la colección. Se limita al mínimo entre top_k y lo que hay indexado.
    available = collection.count()
    n_results = min(top_k, available) if available > 0 else 1

    results = collection.query(
        query_embeddings=[query_vector],
        n_results=n_results
    )

    formatted_results = []
    if results["ids"] and len(results["ids"][0]) > 0:
        for i in range(len(results["ids"][0])):
            formatted_results.append({
                "id": results["ids"][0][i],
                "text": results["documents"][0][i],
                "source": results["metadatas"][0][i]["source"],
                "distance": results["distances"][0][i]
            })

    return formatted_results


if __name__ == "__main__":
    asyncio.run(build_index_async())
