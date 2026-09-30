"""Sessions, roles, rate limits and HTTP hardening.

IDENTITY: this prototype ships SYNTHETIC DEMO IDENTITIES only. A session token
is HMAC-signed by the server and carries the role server-side, so a client
cannot claim a role in a request body. It is authorization for a demo, not
authentication of a real officer: there is no password and no identity
provider. Production must replace `issue_demo_session` with OIDC (see
docs/SECURITY.md). Set TATHYON_DEMO_LOGIN=0 to disable demo sign-in.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Optional

from fastapi import Header, HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

SESSION_TTL_S = 8 * 3600
MAX_BODY_BYTES = 6_000_000
_SECRET = (os.environ.get("TATHYON_SESSION_SECRET") or secrets.token_hex(32)).encode()

DEMO_USERS: dict[str, dict] = {
    "dmo": {"user_id": "dr-meera-dmo", "role": "district_medical_officer", "facility_id": None,
            "display": "District Medical Officer"},
    "logistics": {"user_id": "log-ravi", "role": "logistics_officer", "facility_id": None,
                  "display": "District Logistics Officer"},
    "verifier": {"user_id": "fv-anil", "role": "field_verifier", "facility_id": None,
                 "display": "Field Verifier"},
    "incharge_y": {"user_id": "mo-phc-y", "role": "facility_incharge", "facility_id": "PHC-Y",
                   "display": "Medical Officer, PHC Y"},
    "incharge_a": {"user_id": "mo-phc-a", "role": "facility_incharge", "facility_id": "PHC-A",
                   "display": "Medical Officer, PHC A"},
    "custodian_x": {"user_id": "cust-x", "role": "field_verifier", "facility_id": "PHC-X",
                    "display": "Store custodian, PHC X (for separation-of-duty tests)"},
    "data_officer": {"user_id": "do-sunita", "role": "data_officer", "facility_id": None,
                     "display": "District Data Officer"},
    "auditor": {"user_id": "aud-kiran", "role": "auditor", "facility_id": None, "display": "Auditor (read-only)"},
    "incharge": {"user_id": "mo-incharge", "role": "facility_incharge", "facility_id": None, "scoped": True,
                 "display": "Facility in-charge (signed for one facility)"},
}


@dataclass(frozen=True)
class Principal:
    user_id: str
    role: str
    facility_id: Optional[str]
    display: str
    identity_kind: str = "SYNTHETIC_DEMO_IDENTITY"


def demo_login_enabled() -> bool:
    return os.environ.get("TATHYON_DEMO_LOGIN", "1").strip() == "1"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue_demo_session(user_key: str, facility_id: Optional[str] = None) -> tuple[str, Principal]:
    if not demo_login_enabled():
        raise HTTPException(403, {"error": "DEMO_LOGIN_DISABLED",
                                  "message": "Demo identities are disabled; configure an identity provider."})
    user = DEMO_USERS.get(user_key)
    if user is None:
        raise HTTPException(404, {"error": "UNKNOWN_DEMO_USER", "message": f"Choose one of {sorted(DEMO_USERS)}"})
    if user.get("scoped"):
        if not facility_id:
            raise HTTPException(422, {"error": "FACILITY_REQUIRED", "message": "A facility in-charge signs in for one facility."})
        user = {**user, "facility_id": facility_id[:60], "user_id": f"mo-{facility_id[:60].lower()}",
                "display": f"Medical officer in-charge, {facility_id[:60]}"}
    user = {k: v for k, v in user.items() if k != "scoped"}
    body = {**user, "exp": int(time.time()) + SESSION_TTL_S, "nonce": secrets.token_hex(6)}
    raw = json.dumps(body, sort_keys=True).encode()
    sig = hmac.new(_SECRET, raw, hashlib.sha256).digest()
    return f"{_b64(raw)}.{_b64(sig)}", Principal(user["user_id"], user["role"], user["facility_id"], user["display"])


def principal_from_token(token: str) -> Principal:
    try:
        raw_b64, sig_b64 = token.split(".", 1)
        raw = _unb64(raw_b64)
        if not hmac.compare_digest(hmac.new(_SECRET, raw, hashlib.sha256).digest(), _unb64(sig_b64)):
            raise ValueError("bad signature")
        body = json.loads(raw)
    except Exception as exc:
        raise HTTPException(401, {"error": "INVALID_SESSION", "message": "Sign in again."}) from exc
    if body.get("exp", 0) < time.time():
        raise HTTPException(401, {"error": "SESSION_EXPIRED", "message": "Sign in again."})
    return Principal(body["user_id"], body["role"], body.get("facility_id"), body.get("display", ""))


def current_principal(authorization: Optional[str] = Header(default=None)) -> Principal:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, {"error": "SESSION_REQUIRED", "message": "Sign in with a demo identity."})
    return principal_from_token(authorization[7:].strip())


def require_roles(principal: Principal, *roles: str) -> None:
    if principal.role not in roles:
        raise HTTPException(403, {"error": "ROLE_NOT_AUTHORISED",
                                  "message": f"Requires one of {list(roles)}; you are {principal.role}."})


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, bucket: str, limit: int, window_s: float = 60.0) -> None:
        now_ = time.monotonic()
        with self._lock:
            q = self._hits[(key, bucket)]
            while q and now_ - q[0] > window_s:
                q.popleft()
            if len(q) >= limit:
                raise HTTPException(429, {"error": "RATE_LIMITED", "message": f"Too many {bucket} requests."})
            q.append(now_)


limiter = RateLimiter()


def client_key(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown"))[:64]


CSP = ("default-src 'self'; "
       # Google Maps JS requires 'unsafe-eval' (documented by Google); Cesium runs workers from blob: URLs.
       "script-src 'self' 'unsafe-eval' https://cdnjs.cloudflare.com https://maps.googleapis.com "
       "https://*.googleapis.com https://*.gstatic.com; "
       "worker-src 'self' blob:; "
       "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://fonts.googleapis.com; "
       "font-src 'self' data: https://fonts.gstatic.com; "
       "img-src 'self' data: blob: https://*.tile.openstreetmap.org https://*.googleapis.com https://*.gstatic.com "
       "https://*.google.com https://cdnjs.cloudflare.com; "
       "connect-src 'self' https://*.googleapis.com https://*.gstatic.com https://cdnjs.cloudflare.com "
       "https://tile.googleapis.com; "
       "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")


class HardeningMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
            return JSONResponse({"error": "PAYLOAD_TOO_LARGE", "message": "Request body exceeds 6 MB."}, 413)
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            try:
                limiter.check(client_key(request), "write", 120)
            except HTTPException as exc:
                return JSONResponse(exc.detail, exc.status_code)
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Permissions-Policy", "geolocation=(), camera=(), microphone=()")
        if request.url.path.startswith(("/api/", "/health")):
            response.headers.setdefault("Cache-Control", "no-store")
        elif request.url.path == "/":
            response.headers.setdefault("Cache-Control", "no-cache")  # always pick up new asset versions
        return response
