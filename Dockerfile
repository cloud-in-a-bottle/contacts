FROM ubuntu:26.04

# git is the address book's storage engine, so it is a runtime dependency, not a build-time one.
# ca-certificates lets uv fetch the pinned CPython over TLS.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Keep the interpreter uv downloads inside the image rather than in a home directory that may not persist.
ENV UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

WORKDIR /app

# Source is copied before `uv sync` so the project itself builds during it.
COPY pyproject.toml uv.lock ./
COPY src/ src/
RUN uv sync --frozen --no-dev

EXPOSE 8080

# Exec the venv's hypercorn directly rather than through `uv run`, which would stay resident as a parent process,
# and serve from this one process with `--workers 0` rather than hypercorn's default of a supervisor plus a spawned
# worker and multiprocessing resource tracker.  Together those extra processes cost more memory than the app itself.
CMD ["/app/.venv/bin/hypercorn", "server.asgi:app", "--workers", "0", "--bind", "0.0.0.0:8080"]
