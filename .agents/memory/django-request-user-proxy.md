---
name: Django request-user proxies
description: A runtime distinction between API tests that pass a User object directly and authenticated HTTP requests.
---

For ORM access to the configured user model from an authenticated Django/DRF request, use `get_user_model()` rather than deriving the model class with `type(request.user)`. Middleware may expose `request.user` as a `SimpleLazyObject`, while direct-authentication tests can pass the underlying model instance and conceal the difference.

**Why:** The reporting panel-management API returned HTTP 500 in the running preview even though its `force_authenticate` test passed.

**How to apply:** When a view needs a user queryset, use the configured model and add at least one session-authenticated API test for that view.
