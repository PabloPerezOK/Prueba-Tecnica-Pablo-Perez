FROM python:3.11-slim

# curl necesario para el healthcheck del contenedor
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# FIX: crear usuario no-root para ejecutar la aplicación.
# Correr como root dentro del contenedor amplía la superficie de ataque
# innecesariamente; si una dependencia tiene una vulnerabilidad el atacante
# obtiene privilegios de root sin costo adicional.
RUN addgroup --system app && adduser --system --ingroup app app

WORKDIR /app

# Instalar dependencias primero (mejor uso de la caché de Docker)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código fuente y documentos
COPY src/ ./src/
COPY docs/ ./docs/

# Copiar y preparar el script de inicio
COPY entrypoint.sh /entrypoint.sh
# Normaliza saltos de línea (CRLF->LF) por si el repo se clonó en Windows,
# y da permisos de ejecución.
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

# Transferir propiedad al usuario no-root antes de cambiar de contexto
RUN mkdir -p /app/src/chroma_db
RUN chown -R app:app /app /entrypoint.sh

USER app

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
