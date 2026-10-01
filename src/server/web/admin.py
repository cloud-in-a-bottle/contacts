from typing import Annotated
from typing import Any

from anyio.to_thread import run_sync
from litestar import Request
from litestar import Response
from litestar import Router
from litestar import get
from litestar import post
from litestar.datastructures import UploadFile
from litestar.di import NamedDependency
from litestar.enums import RequestEncodingType
from litestar.exceptions import NotFoundException
from litestar.params import Body
from litestar.params import FromPath
from litestar.params import FromQuery
from litestar.response import Redirect
from litestar.response import Template
from litestar.types import Guard

from server.credentials import CredentialStore
from server.dav.paths import ADDRESSBOOK_PATH
from server.dav.paths import DAV_ROOT
from server.exporting import EXPORT_CONTENT_TYPE
from server.exporting import build_export
from server.exporting import export_filename
from server.importing import MAX_IMPORT_BYTES
from server.importing import VcfImportError
from server.importing import import_vcf
from server.models import Contact
from server.store import ContactStore
from server.store import describe
from server.vcard.build import new_uid
from server.vcard.build import render_vcard
from server.vcard.model import ContactFields
from server.vcard.model import PostalAddress
from server.vcard.parse import extract_fields
from server.vcard.parse import extract_photo
from server.vcard.parse import parse_vcard
from server.vcard.summary import summarize
from server.web.formatting import humanize_timestamp
from server.web.forms import contact_fields_from_form
from server.web.urls import external_origin


async def _require_contact(store: ContactStore, resource_name: str) -> Contact:
    contact = await run_sync(store.get, resource_name)
    if contact is None:
        raise NotFoundException(detail="no such contact")
    return contact


@get("/", name="contact_list")
async def contact_list(store: NamedDependency[ContactStore], q: FromQuery[str] = "") -> Template:
    entries = await run_sync(store.list_summaries, q)
    total = await run_sync(store.count)
    return Template(template_name="list.html", context={"contacts": entries, "total": total, "search": q})


@get("/contacts/new", name="contact_new")
async def contact_new() -> Template:
    return Template(
        template_name="form.html",
        context={
            "heading": "New contact",
            "action": "/contacts",
            "cancel_url": "/",
            "fields": ContactFields(),
            "empty_address": PostalAddress(),
            "error": None,
        },
    )


@post("/contacts", name="contact_create")
async def contact_create(request: Request[Any, Any, Any], store: NamedDependency[ContactStore]) -> Template | Redirect:
    fields = contact_fields_from_form(await request.form())
    if not fields.best_display_name:
        return Template(
            template_name="form.html",
            status_code=422,
            context={
                "heading": "New contact",
                "action": "/contacts",
                "cancel_url": "/",
                "fields": fields,
                "empty_address": PostalAddress(),
                "error": "Give the contact a name, an email, or a phone number so it can be listed.",
            },
        )

    uid = new_uid()
    await run_sync(store.put, uid, render_vcard(fields, uid), describe("create", fields.best_display_name))
    return Redirect(path=f"/contacts/{uid}", status_code=303)


@get("/contacts/{resource_name:str}", name="contact_detail")
async def contact_detail(store: NamedDependency[ContactStore], resource_name: FromPath[str]) -> Template:
    contact = await _require_contact(store, resource_name)
    summary = summarize(contact.vcard, fallback_uid=resource_name)
    return Template(
        template_name="detail.html",
        context={
            "contact": contact,
            "fields": summary.fields,
            "updated_at": humanize_timestamp(contact.updated_at),
            "initials": summary.initials,
            "has_photo": summary.has_photo,
        },
    )


@get("/contacts/{resource_name:str}/edit", name="contact_edit")
async def contact_edit(store: NamedDependency[ContactStore], resource_name: FromPath[str]) -> Template:
    contact = await _require_contact(store, resource_name)
    return Template(
        template_name="form.html",
        context={
            "heading": f"Edit {contact.display_name}",
            "action": f"/contacts/{resource_name}",
            "cancel_url": f"/contacts/{resource_name}",
            "fields": extract_fields(parse_vcard(contact.vcard)),
            "empty_address": PostalAddress(),
            "error": None,
        },
    )


@post("/contacts/{resource_name:str}", name="contact_update")
async def contact_update(
    request: Request[Any, Any, Any], store: NamedDependency[ContactStore], resource_name: FromPath[str]
) -> Redirect:
    contact = await _require_contact(store, resource_name)
    fields = contact_fields_from_form(await request.form())
    # The stored card is the source of truth for everything the form does not cover (photos, X- extensions), so
    # it is passed back in and those properties are carried across unchanged.
    await run_sync(
        store.put,
        resource_name,
        render_vcard(fields, contact.uid, contact.vcard),
        describe("edit", fields.best_display_name or contact.display_name),
    )
    return Redirect(path=f"/contacts/{resource_name}", status_code=303)


@post("/contacts/{resource_name:str}/delete", name="contact_delete")
async def contact_delete(store: NamedDependency[ContactStore], resource_name: FromPath[str]) -> Redirect:
    contact = await _require_contact(store, resource_name)
    await run_sync(store.delete, resource_name, describe("delete", contact.display_name))
    return Redirect(path="/", status_code=303)


@get("/contacts/{resource_name:str}/history", name="contact_history")
async def contact_history(store: NamedDependency[ContactStore], resource_name: FromPath[str]) -> Template:
    contact = await _require_contact(store, resource_name)
    revisions = await run_sync(store.history, resource_name)
    return Template(
        template_name="history.html",
        context={
            "contact": contact,
            "revisions": [
                {
                    "commit": revision.commit,
                    "short": revision.commit[:8],
                    "when": humanize_timestamp(revision.timestamp),
                    "summary": revision.summary,
                    "is_current": index == 0,
                }
                for index, revision in enumerate(revisions)
            ],
        },
    )


@get("/contacts/{resource_name:str}/history/{commit:str}", name="contact_version")
async def contact_version(
    store: NamedDependency[ContactStore], resource_name: FromPath[str], commit: FromPath[str]
) -> Response[bytes]:
    text = await run_sync(store.version, resource_name, commit)
    if text is None:
        raise NotFoundException(detail="no such version of this contact")
    return Response(content=text.encode("utf-8"), media_type=EXPORT_CONTENT_TYPE)


@post("/contacts/{resource_name:str}/restore", name="contact_restore")
async def contact_restore(
    request: Request[Any, Any, Any], store: NamedDependency[ContactStore], resource_name: FromPath[str]
) -> Redirect:
    commit = (await request.form()).get("commit")
    if not isinstance(commit, str):
        raise NotFoundException(detail="no version was named")
    text = await run_sync(store.version, resource_name, commit)
    if text is None:
        raise NotFoundException(detail="no such version of this contact")
    # Restoring writes a new commit rather than rewinding history, so the undo is itself undoable.
    restored = summarize(text, fallback_uid=resource_name).display_name
    await run_sync(store.put, resource_name, text, describe("restore", f"{restored} to {commit[:8]}"))
    return Redirect(path=f"/contacts/{resource_name}", status_code=303)


@get("/contacts/{resource_name:str}/vcard", name="contact_vcard")
async def contact_vcard(store: NamedDependency[ContactStore], resource_name: FromPath[str]) -> Response[bytes]:
    contact = await _require_contact(store, resource_name)
    return Response(
        content=contact.vcard.encode("utf-8"),
        media_type=EXPORT_CONTENT_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{resource_name}.vcf"'},
    )


@get("/contacts/{resource_name:str}/photo", name="contact_photo")
async def contact_photo(store: NamedDependency[ContactStore], resource_name: FromPath[str]) -> Response[bytes]:
    contact = await _require_contact(store, resource_name)
    photo = extract_photo(parse_vcard(contact.vcard))
    if photo is None:
        raise NotFoundException(detail="this contact has no embedded photo")
    return Response(
        content=photo.data,
        media_type=photo.media_type,
        headers={"Cache-Control": "private, max-age=300", "ETag": contact.etag},
    )


@get("/import", name="contact_import_form")
async def contact_import_form(store: NamedDependency[ContactStore]) -> Template:
    return Template(template_name="import.html", context={"error": None, "contact_count": await run_sync(store.count)})


@post("/import", name="contact_import", request_max_body_size=MAX_IMPORT_BYTES)
async def contact_import(
    store: NamedDependency[ContactStore],
    data: Annotated[UploadFile, Body(media_type=RequestEncodingType.MULTI_PART)],
) -> Template:
    raw = await data.read()
    try:
        report = await run_sync(import_vcf, store, raw)
    except VcfImportError as error:
        return Template(
            template_name="import.html",
            status_code=422,
            context={"error": str(error), "contact_count": await run_sync(store.count)},
        )
    return Template(template_name="import_result.html", context={"report": report})


@get("/export", name="contact_export")
async def contact_export(store: NamedDependency[ContactStore]) -> Response[bytes]:
    contacts = await run_sync(store.list_contacts)
    return Response(
        content=build_export(contacts),
        media_type=EXPORT_CONTENT_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{export_filename()}"'},
    )


@get("/settings", name="settings")
async def settings(
    request: Request[Any, Any, Any],
    store: NamedDependency[ContactStore],
    credentials: NamedDependency[CredentialStore],
    owner_username: NamedDependency[str],
    regenerated: FromQuery[bool] = False,
) -> Template:
    secret = await run_sync(credentials.carddav_password)
    origin = external_origin(request)
    return Template(
        template_name="settings.html",
        context={
            "base_url": f"{origin}{DAV_ROOT}/",
            "collection_url": f"{origin}{ADDRESSBOOK_PATH}",
            "host": origin.split("://", 1)[-1],
            "owner_username": owner_username,
            "password": secret.value,
            "password_created_at": humanize_timestamp(secret.created_at),
            "contact_count": await run_sync(store.count),
            "regenerated": regenerated,
        },
    )


@post("/settings/regenerate-password", name="settings_regenerate")
async def regenerate_password(credentials: NamedDependency[CredentialStore]) -> Redirect:
    await run_sync(credentials.regenerate_carddav_password)
    return Redirect(path="/settings?regenerated=true", status_code=303)


def admin_router(owner_guard: Guard) -> Router:
    return Router(
        path="/",
        guards=[owner_guard],
        route_handlers=[
            contact_list,
            contact_import_form,
            contact_import,
            contact_export,
            contact_new,
            contact_create,
            contact_detail,
            contact_edit,
            contact_update,
            contact_delete,
            contact_history,
            contact_version,
            contact_restore,
            contact_vcard,
            contact_photo,
            settings,
            regenerate_password,
        ],
    )
