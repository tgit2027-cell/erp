from .changes import Collector, current_changes
from .models import AuditEvent
from .recording import current_request, record, touch_session

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
# Sign-in and sign-out are recorded by the auth signals with better detail.
SKIP = {"/api/auth/login/", "/api/auth/logout/"}


class AuditMiddleware:
    """Record data changes, refused requests and session activity on the API."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = current_request.set(request)
        changes = Collector()
        changes_token = current_changes.set(changes)
        try:
            response = self.get_response(request)
        finally:
            current_changes.reset(changes_token)
            current_request.reset(token)
        if not request.path.startswith("/api/") or request.path in SKIP:
            return response
        user = getattr(request, "user", None)
        signed_in = bool(user and user.is_authenticated)
        if signed_in:
            touch_session(request)
        status = response.status_code
        if status in (401, 403) and (signed_in or request.method in UNSAFE):
            record(AuditEvent.Kind.ACCESS_DENIED, request=request, status_code=status)
        elif signed_in and request.method in UNSAFE:
            succeeded = 200 <= status < 300
            detail = {"succeeded": succeeded}
            # A refused request rolls its writes back, so only a success lists records.
            if succeeded and changes:
                detail.update(changes.summary())
            record(AuditEvent.Kind.ACTION, request=request, status_code=status, detail=detail)
        return response
