# La imagen del sistema para la nube (hito 8): lo mismo que corre en tu computador, sin
# las herramientas de desarrollo. Render la arma desde este archivo en cada publicación.
#
# Para probarla en tu computador (con el PostgreSQL de Docker encendido):
#   docker build -t auto-ibaguer .
#   docker run --rm --network host -e ENTORNO=desarrollo auto-ibaguer

FROM python:3.13-slim

# uv, la misma versión que arma uv.lock, para instalar exactamente esas versiones.
RUN pip install --no-cache-dir uv==0.11.32

# Sin la caché de uv dentro de la imagen (solo la haría más pesada), sin descargar otro
# Python (usa el de la imagen) y con los mensajes del servidor saliendo de una vez.
ENV UV_COMPILE_BYTECODE=1 \
    UV_NO_CACHE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Primero solo las librerías: si no cambian, Docker reutiliza este paso y la imagen se
# arma en segundos.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-install-project

# Después el código del sistema.
COPY README.md alembic.ini ./
COPY migraciones ./migraciones
COPY app ./app
# Y el código ya traducido a bytecode, para que el servidor arranque más rápido.
RUN uv sync --locked --no-dev && python -m compileall -q app

# Corre con un usuario sin permisos de administrador: si alguien encontrara una falla,
# no podría tocar el resto del contenedor.
RUN useradd --create-home --uid 10001 auto
USER auto

ENV PATH="/app/.venv/bin:$PATH" \
    ENTORNO=produccion

# Render le dice al servidor en qué puerto escuchar (PORT). Detrás de Render, el HTTPS
# llega al proxy de Render: --proxy-headers hace que el servidor lo sepa.
EXPOSE 10000
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000} --proxy-headers --forwarded-allow-ips='*' --no-server-header"]
