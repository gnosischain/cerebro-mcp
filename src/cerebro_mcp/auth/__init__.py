"""OAuth 2.1 resource-server components (connector plan R10).

Cerebro is a PURE resource server: it consumes the IdP's JWKS, serves RFC
9728 protected-resource metadata, validates tokens, and authorizes — it
never issues tokens, never stores them, and never forwards an inbound token
downstream. SSRF-discovery, OAuth state and response-binding hardening
belong to clients and authorization servers, not here (R9 §6 determination).
"""
