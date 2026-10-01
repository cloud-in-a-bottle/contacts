default: test

# Install dependencies, pre-commit hooks, and the playwright chromium browser.
setup:
    uv sync
    uv run pre-commit install
    uv run playwright install chromium

# Run the app locally on http://localhost:8080 against a scratch database, with the owner check disabled.
run:
    CONTACTS_DB_PATH=.local/contacts.db CONTACTS_REPO_PATH=.local/addressbook \
        CONTACTS_DEV_UNSAFE_NO_OWNER_AUTH=1 \
        uv run hypercorn server.asgi:app --bind 0.0.0.0:8080 --reload

# Run the tests that do not need podman.
test:
    uv run pytest -x -m "not containers"

# Run everything, including the containerized OpenHost harness (needs podman on the host).
test-all:
    uv run pytest -x

# Lint, format, and typecheck (same checks as the pre-commit hooks).
check:
    uv run ruff check --fix .
    uv run ruff format .
    uv run mypy

# Build the container image.
build:
    docker build -t contacts .
