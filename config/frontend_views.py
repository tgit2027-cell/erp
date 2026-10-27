from pathlib import Path

from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.http import require_GET


@require_GET
def app_index(_request):
    index_path = settings.BASE_DIR / "dist" / "index.html"
    if not index_path.is_file():
        return HttpResponse("Frontend build is unavailable.", status=503, content_type="text/plain")
    return HttpResponse(index_path.read_text(encoding="utf-8"), content_type="text/html; charset=utf-8")
