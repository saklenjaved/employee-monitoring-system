"""The two links of LocalStorage (development only): the agent's upload and the person's view or download.

Like an S3 link, the signed token is the permission. It names one object, expires, and for an upload
also fixes the size and SHA-256. These views do nothing unless LocalStorage is the storage in use.
"""
import hashlib
import hmac
import time

from django.conf import settings
from django.core import signing
from django.http import FileResponse, Http404, HttpResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .storage import DOWNLOAD_SALT, UPLOAD_SALT, LocalStorage, get_storage


def _local_storage():
    storage = get_storage() if settings.SCREENSHOT_STORAGE.endswith("LocalStorage") else None
    if not isinstance(storage, LocalStorage):
        raise Http404
    return storage


def _payload(token, salt):
    """The token's contents, or None when it is forged, damaged or expired."""
    try:
        data = signing.loads(token, salt=salt)
    except signing.BadSignature:
        return None
    return data if data.get("exp", 0) > time.time() else None


@csrf_exempt
@never_cache
@require_http_methods(["PUT"])
def upload(request, token):
    """The agent's upload. Stored only if the size and SHA-256 are exactly what was authorized."""
    storage = _local_storage()
    data = _payload(token, UPLOAD_SALT)
    if data is None:
        return HttpResponse(status=403)
    size = data["size"]
    if request.META.get("CONTENT_LENGTH") != str(size):
        return HttpResponse(status=400)
    body = request.read(size + 1)  # never read more than was authorized
    if len(body) != size or not hmac.compare_digest(hashlib.sha256(body).hexdigest(), data["sha"]):
        return HttpResponse(status=400)
    path = storage.path_for(data["key"])
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".part")
    partial.write_bytes(body)
    partial.replace(path)  # the image appears whole or not at all
    return HttpResponse(status=200)


@never_cache
@require_http_methods(["GET"])
def download(request, token):
    """A person's view or download, through a link that lasts a minute."""
    storage = _local_storage()
    data = _payload(token, DOWNLOAD_SALT)
    if data is None:
        return HttpResponse(status=403)
    path = storage.path_for(data["key"])
    if not path.is_file():
        raise Http404
    response = FileResponse(path.open("rb"), content_type="image/png", as_attachment=bool(data["name"]), filename=data["name"] or None)
    response["Cache-Control"] = "no-store, private"
    response["Referrer-Policy"] = "no-referrer"
    return response
