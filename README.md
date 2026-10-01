# contacts

A single-owner contacts app for [Cloud in a Bottle](https://github.com/cloud-in-a-bottle/cloud-in-a-bottle): a web UI for your
address book, plus a **CardDAV** endpoint so your phone and laptop can sync against it. Contacts are stored as a
git repository of `.vcf` files, one commit per change, so every past version can be restored.

## Access

- **Web UI** — behind Cloud in a Bottle's owner login; no separate password.
- **CardDAV** (`/dav/`, `/.well-known/carddav`) — public paths protected by HTTP Basic with a password generated
  on first boot. Any username works; only the password is checked. View or replace it on the **Settings** page.

## Syncing a device

Open **Settings** in the app for the server URL and password, then add a CardDAV account:

- **Android** — any CardDAV client works; we suggest [DAVx⁵](https://www.davx5.com/), which is open source.
  It's free from [GitHub](https://github.com/bitfireAT/davx5-ose/releases) / F-Droid, or a few dollars on the
  Play Store. Add account → *Login with URL and password*.
- **iOS / macOS** — Settings → Contacts → Accounts → Add Account → Other → Add CardDAV Account.

## Import / export

**Import / export** accepts a vCard (`.vcf`) file — e.g. from Google Contacts (choose *vCard*, not CSV), iCloud
or Thunderbird. Import only adds; contacts already present are skipped, so re-importing is safe. Export returns
the whole address book as one `.vcf`.

## Backup

The address book (git repository) and the SQLite database holding the CardDAV password live in the app's
persistent data directory, so they're backed up automatically if backup is configured on your Bottle instance.

## Development

```bash
just setup     # install deps, pre-commit hooks, playwright browser
just run       # http://localhost:8080 (owner auth disabled for local dev)
just test      # in-process test suite
just test-all  # also the containerized OpenHost harness (needs podman)
just check     # ruff + mypy
```
