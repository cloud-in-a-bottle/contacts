# contacts

A contacts app for [Cloud in a Bottle](https://github.com/cloud-in-a-bottle). It stores your address book in
SQLite, gives you a web UI to read and edit it, and serves the same data over **CardDAV** so your phone and
laptop can sync against it.

It serves exactly one person — the owner of the compute space. There are no user accounts.

## How access works

There are two doors into this app, and they are locked differently.

| | Who gets in | How |
|---|---|---|
| Admin UI (`/`, `/contacts/…`, `/settings`) | the compute space owner | Cloud in a Bottle's own login. The app adds no password of its own; it only trusts the `X-OpenHost-Is-Owner` header the router sets. |
| CardDAV (`/dav/…`, `/.well-known/carddav`) | anyone with the password | HTTP Basic, checked by this app against a password generated on first boot. |

CardDAV clients can't log in through Cloud in a Bottle — they hold no session cookie and can't follow an
interactive login. So the CardDAV paths, and only those, are listed in `public_paths` in `openhost.toml`, which
means the router hands them to the app without checking anything. **The generated password is the only thing
standing between those paths and the public internet.** It is 20 characters drawn from a 31-character alphabet
(~99 bits), and you can replace it from the settings page at any time.

`/.well-known/carddav` is public too, but it answers every request with the same `301` to `/dav/` and nothing
else; clients hit it before they have credentials.

## Connecting a client

Open **Settings** in the app. It shows the server URL, the username, and the password.

- **iOS / macOS** — Settings → Contacts → Accounts → Add Account → Other → Add CardDAV Account.
- **DAVx⁵ (Android)** — Add account → Login with URL and password.
- **Thunderbird** — Address Book → New → CardDAV Address Book.

Any username is accepted; only the password is checked. There is a single address book, called *Contacts*.

## What's supported

`PROPFIND`, `REPORT` (`addressbook-multiget`, `addressbook-query`, `sync-collection`), `GET`, `PUT`, `DELETE`,
`OPTIONS`, `HEAD`, `PROPPATCH`. ETags with `If-Match` / `If-None-Match` so two clients can't silently clobber
each other, `getctag` for clients that poll, and RFC 6578 sync tokens for clients that do incremental sync.
Deletions are tombstoned permanently, so a sync token of any age is answered exactly rather than forcing a
client to resynchronise from scratch.

`PROPPATCH` is accepted and reports `403` per property: every property here is derived from the contact data,
so there is nothing a client can set.

### vCards

A vCard written by a client is **stored byte-for-byte** and handed back unchanged. Parsing happens only to fill
in the columns used for listing and searching, so properties this app knows nothing about — photos, `X-`
extensions, `IMPP`, whatever your phone invents — survive a round trip untouched.

Editing a contact in the web UI rewrites the properties the form covers (`FN`, `N`, `ORG`, `EMAIL`, `TEL`,
`ADR`, …) and carries everything else across from the stored card. Cards written by the UI are vCard 3.0, which
every client in common use reads. The parser handles line folding, quoted-printable soft breaks and vCard 2.1
bare parameters, so cards exported from older address books import cleanly.

## Storage

One SQLite database, provisioned by Cloud in a Bottle via `sqlite = ["main"]` in the manifest, so it lives in
the app's backed-up data directory. The vCard text is the source of truth; every other column is derived from
it and can be rebuilt by rewriting the rows.

The CardDAV password is stored in the clear, because the settings page has to be able to show it to you again.

## Development

```bash
just setup   # install deps, pre-commit hooks, and the playwright chromium browser
just run     # http://localhost:8080 against .local/contacts.db
just test    # the suite that needs nothing but python
just test-all  # also the containerized OpenHost harness (needs podman)
just check   # ruff + mypy, the same checks pre-commit runs
```

`just run` sets `CONTACTS_DEV_UNSAFE_NO_OWNER_AUTH=1`, because outside a compute space there is no router to
authenticate the owner and the admin UI would otherwise refuse every request. Never set it in a deployed app.

`just test` runs against the ASGI app in-process, which covers the protocol end to end but cannot prove the
manifest is right. The tests in `tests/test_harness.py` do that — they build the image, run it under podman and
put the real router in front — and they skip when podman is absent.

## Layout

```
src/server/
├── app.py          # Litestar app assembly; asgi.py is the entry point hypercorn runs
├── config.py       # environment -> Config, failing loudly if the database is missing
├── db.py           # sqlite schema and per-thread connections
├── store.py        # contact CRUD and the change sequence that backs sync
├── credentials.py  # the CardDAV password
├── vcard/          # parse, build and summarise vCard text
├── dav/            # the CardDAV endpoint: paths, properties, PROPFIND, REPORT, method dispatch
└── web/            # the owner-facing UI: routes, forms, templates
```
