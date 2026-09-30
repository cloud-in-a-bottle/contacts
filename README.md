# contacts

A contacts app for [Cloud in a Bottle](https://github.com/cloud-in-a-bottle). Your address book is **a git
repository of `.vcf` files**; there is a web UI to read and edit it, and a **CardDAV** endpoint so your phone
and laptop can sync against it.

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

## Import and export

**Import / export** in the nav takes a `.vcf` file — a Google Contacts, iCloud or Thunderbird export — and adds
every card in it, and hands the whole address book back as a single `.vcf`.

Import only ever adds; it never overwrites. A card is skipped when it is already stored, judged two ways: by UID
where the exporter wrote one, and otherwise by the card's exact bytes, which is what makes re-importing the same
file from Google Contacts (whose vCard export carries no UID at all) a no-op rather than a way to double your
address book. The same check runs within the file, so a file listing someone twice imports them once. The whole
import lands under one change sequence, so a syncing client sees a single delta rather than one change per card.

Export concatenates the stored cards untouched, which means an export is a faithful copy of what your clients
wrote rather than a re-rendering of it — it round-trips back into an empty address book byte-for-byte.

This reads vCards only. Google's "Google CSV" and "Outlook CSV" export options will not work; pick
*vCard (for iOS Contacts)*.

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

The address book is a git repository under the app's data directory — one `.vcf` file per contact, one commit
per change:

```
/data/app_data/contacts/addressbook/
├── .git/
├── 3f9c1e08-....vcf
└── ines-bergstrom.vcf
```

```
$ git log --format='%h %s'
847889f restore: Ines Bergström to f3810f6b
935e31c edit: Ines Bergström-Ek
f3810f6 carddav put: Ines Bergström
```

You can clone it, grep it, `git log` it, and restore any past version — **History** on a contact does that from
the UI. Restoring writes a new commit rather than rewinding, so an undo is itself undoable, and a deleted
contact stays in the history even though its file is gone.

### Why git suits CardDAV

The protocol's primitives turn out to already be git's, which is most of the reason this works:

| CardDAV needs | is |
|---|---|
| `sync-token` | the commit SHA |
| the delta since a token | `git diff --name-status <sha> HEAD` |
| deletions since a token | the `D` lines of that diff — no tombstone bookkeeping |
| `getetag` | the blob SHA, which is *already* the name of those bytes |
| `getctag` | the HEAD SHA |

An ETag is therefore not something this app computes and hopes stays in step with storage; it is the identifier
git itself uses. A sync token names a real snapshot, and one that git no longer has is answered honestly with
`409 valid-sync-token`.

Two consequences worth knowing. Writing a card that is byte-for-byte what is already stored does **not** create
a commit, because HEAD is the sync token and an empty commit would tell every client to resynchronise over
nothing. And every write takes an exclusive `flock` on the repository, so concurrent CardDAV and web writes
serialise instead of racing on git's index.

### What git cannot do

Sort and search. Those need every card parsed, so the derived fields — display name, sort key, search text —
are cached in memory against the current commit and rebuilt whenever HEAD moves. The cache cannot go stale,
because its key *is* the version of the data. The full vCard text is not cached: `list_contacts()` reads the
files, which the page cache makes cheap.

This is fine for a personal address book and would not be for a hundred thousand contacts; the ceiling is the
cost of parsing every card whenever HEAD moves.

### SQLite

Still present, holding exactly one thing: the CardDAV password. It is stored in the clear because the settings
page has to show it to you again, which is also why it stays out of the repository — a secret committed to git
is a secret in the log forever.

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
├── repo.py         # the git working tree: commits, diffs, history, the write lock
├── store.py        # contacts on top of the repository, with a commit-keyed index
├── naming.py       # which resource names are safe to use as filenames
├── db.py           # sqlite, for the CardDAV password and nothing else
├── credentials.py  # the CardDAV password
├── importing.py    # read an uploaded .vcf, skipping what is already stored
├── exporting.py    # hand the whole address book back as one .vcf
├── vcard/          # parse, build, split and summarise vCard text
├── dav/            # the CardDAV endpoint: paths, properties, PROPFIND, REPORT, method dispatch
└── web/            # the owner-facing UI: routes, forms, templates
```
