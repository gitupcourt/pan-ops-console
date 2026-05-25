"""Placeholder for SAML/SSO authentication.

When ready to implement:

1. Add `python3-saml` (or `authlib`) to requirements.txt.
2. Add IdP config (entity ID, SSO URL, x509 cert) to settings via env vars
   or a SamlIdp DB table if you want multi-IdP support.
3. Implement two routes in app/routes/auth.py:
     GET  /api/auth/saml/login       -> redirects to IdP
     POST /api/auth/saml/acs         -> consumes SAMLResponse, creates/updates
                                        a User row with auth_provider='saml:<idp_id>',
                                        issues a JWT via create_access_token().
4. The rest of the API requires no changes — get_current_user already trusts JWTs
   regardless of how they were issued.

Keep local auth available as a break-glass admin login.
"""
