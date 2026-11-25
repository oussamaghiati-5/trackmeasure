"""Opt-in, bounded request-body evidence linked to existing request rows."""
import hashlib


class RequestPayloadCapture:
    def __init__(self, db, max_request=65536, max_site=2 * 1024**2):
        self.db = db
        self.max_request = max_request
        self.max_site = max_site
        self.saved = 0

    def capture(self, request_id, request):
        content_type = ""
        body = b""
        original_bytes = None
        digest = None
        detail = None
        status = "unavailable"
        try:
            content_type = request.headers.get("content-type", "")
            original = request.post_data_buffer
            if original is None:
                status = "no_body"
                original_bytes = 0
            else:
                original_bytes = len(original)
                digest = hashlib.sha256(original).hexdigest()
                remaining = max(0, self.max_site - self.saved)
                body = original[:min(self.max_request, remaining)]
                self.saved += len(body)
                if len(body) == len(original):
                    status = "saved"
                elif remaining == 0:
                    status = "site_limit"
                else:
                    status = "truncated"
        except Exception as error:
            detail = type(error).__name__ + ": " + str(error)[:200]
        self.db.execute("INSERT INTO request_payloads VALUES(?,?,?,?,?,?,?,?)",
                        (request_id, status, content_type, original_bytes, len(body), digest, body, detail))
