- read README.md and style_guide.md at the beginning of every session.
- on first init, run `just setup` — this installs dependencies, the pre-commit hooks, and the playwright chromium browser. pre-commit runs ruff and mypy on commit.
- use uv for all python work (`uv run ...`, `uv add ...`, `uv sync`).
- this is an OpenHost app. `openhost.toml` is the app manifest.
- the app is a litestar/hypercorn backend that serves on port 8080 and exposes a `/health` endpoint. see "deploying & debugging on openhost" below.
- `just test` runs the in-process suite (no podman needed). `just test-all` adds `tests/test_harness.py`, which uses the OpenHost test harness (the `openhost[test-harness]` package, imported as `openhost_test_harness`): it builds the Dockerfile, runs the app under podman per `openhost.toml`, and fronts it with the real OpenHost router. those tests skip when podman is absent. `stack.url` goes through the router and requires owner auth (use `stack.owner_session` for requests, or `stack.playwright_login(page)` for browser tests); `stack.app_url` hits the container directly.

## what this app is

a single-owner contacts app: sqlite storage, a server-rendered admin UI, and a CardDAV endpoint for external clients. no user accounts.

## things to be careful about

- **two different auth models, deliberately.** the admin UI is gated by the OpenHost router (the app only trusts the `X-OpenHost-Is-Owner` header, which the router strips from inbound requests so it cannot be forged). the CardDAV paths are in `public_paths` and are gated by the app's own HTTP Basic check. do not add anything to `public_paths` without thinking hard: those paths are reachable from the public internet with nothing but the generated password in front of them.
- **stored vCards are byte-for-byte what the client sent.** that is the whole round-tripping story — properties the app does not model survive because they are never rewritten. if you find yourself normalising the stored text, stop and reconsider: the etag *is* the git blob name of those exact bytes. note `newline=""` on every read and write of a card file — without it python translates CRLF, which silently changes the blob name.
- **the CardDAV layer must stay off Litestar's HTTP routing.** litestar's `HTTPRouteHandler` only knows the standard methods; `PROPFIND`, `REPORT` and `PROPPATCH` reach us through an `asgi(..., is_mount=True)` mount, which is method-agnostic. inside the mount, read `scope["raw_path"]` and not `scope["path"]` — litestar rewrites the latter to the post-mount remainder and appends a trailing slash.
- **the git commit is the backbone of sync.** HEAD is the sync token and the ctag; the delta is `git diff` between two commits, and deletions come out of that diff, so there is no tombstone bookkeeping. two rules follow: a write that changes no bytes must not commit (an empty commit would move HEAD and tell every client to resync over nothing), and a token naming a commit we do not have must be answered `409 valid-sync-token` rather than guessed at.
- **every write takes the repository flock** (`Repository.locked`). git's index is a single shared file; concurrent CardDAV and web writes would corrupt it otherwise. reads do not take the lock.
- **a resource name becomes a filename**, so `server/naming.py` is a security boundary, not a formatting helper — it is what keeps `..`, `.git` and path separators out of the tree. do not loosen it without thinking about what a CardDAV client could then PUT.
- **the contact index is cached against HEAD.** it holds derived fields only, never the card text. the cache cannot go stale because its key is the version of the data; if you add a field to `IndexEntry`, it is rebuilt on the next commit automatically.
- **the list page must stay parse-free.** everything it renders comes from `IndexEntry`; `tests/test_listing_cost.py` makes `Repository.read_file` raise and asserts the page still works. if you need something new on that page, add it to the index rather than parsing in the handler — doing the latter is what made a 410-contact dashboard take 1.7 seconds.
- **when HEAD moves the index advances off the diff**, it is not rebuilt (`_advance_index`). a full rebuild per write would make a bulk carddav sync quadratic.
- **watch for accidental quadratic string building when touching the vcard parser.** an inline PHOTO is one value folded over thousands of lines; `unfold` collects chunks in a list and joins once for exactly that reason, and there is a test that unfolds 40k folded lines under a time bound to catch a regression.
- the protocol layer (`src/server/dav/`) is deliberately synchronous and pure: `DavRequest` in, `DavResponse` out. the ASGI adapter runs it on a worker thread. keep it that way — it is what makes the CardDAV tests readable.

## running the harness in the claude-workbench container

two environment traps, both found the hard way:

- **podman is installed but cannot build.** the workbench runs as root inside rootless podman with `cap-drop=ALL`, so a nested `podman build` fails at `lchown /etc/gshadow: invalid argument` ("potentially insufficient UIDs or GIDs available in user namespace"). `just test-all` therefore cannot pass here; run it on a normal host. `just test` excludes those tests via the `containers` marker.
- **the harness inherits `OPENHOST_ZONE_DOMAIN` from this container** (claude-workbench is itself an openhost app, so the router injects it). the router the harness spawns then scopes its `session_token` cookie to `host.zackpolizzi.com` while the harness talks to it at `harness.localhost`, the cookie is dropped, and setup fails with `setup did not set session_token cookie, got []`. clearing `OPENHOST_ZONE_DOMAIN` gets past it. this looks like a harness bug — it should override the openhost env vars for the router it spawns rather than inheriting them — so mention it rather than committing a workaround.

playwright's chromium does not install here by default (`Playwright does not support chromium on ubuntu26.04-x64`); `PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=ubuntu24.04-x64` downloads the 24.04 fallback build and it runs fine, which is enough to screenshot the admin UI against a locally-run `just run`.

## deploying & debugging on openhost

- openhost is a cloud platform for self-hosting apps. there's context on openhost at `~/openhost`; read `docs/src/creating_an_app.md` there for how apps are built and run.
- instances are managed via the `oh` cli. `oh instance list` shows the configured instances and the URL each is available at. the user will tell you which instance to use; do not touch the others. most commands take `--instance <name>`.
- these instances have web servers facing the public internet. be careful with anything that could open unsecured public access — eg adding `public_paths` in `openhost.toml`.
- prefer `oh` commands for debugging since they handle auth: `oh instance ssh` and `oh curl`. `oh instance token --instance <name>` gives a raw API token (Bearer auth) only if absolutely necessary — better not to see it, and never put it anywhere that might get committed.
- typical deploy loop: commit + push, then `oh app reload <app> --update --wait --instance <name>` to pull the changes and reload, then `oh app logs <app> --instance <name>` to check the logs.
- to test pages in a browser as the user would see them, use playwright and inject the API token as a Bearer header — this matches a request made with the owner's login cookies.
- if you run into any cases where the app test harness doesn't match the expected/real behavior of openhost, stop and mention this so that we can fix the test harness - don't just make some workaround to the issue.
- if you run into cases where openhost itself doesn't behave as expected, also stop and mention this so we can open a PR there to fix upstream.
