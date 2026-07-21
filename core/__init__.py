"""Shared, framework-free foundation imported by BOTH app/ and ml/.

Holds only pure constants and helpers (config, value normalization, logging,
error mapping, auth) — never business logic, never SQL, never FastAPI. This is
why ml/ importing core/ does not violate spec §2.2's "pipeline never imports
app/": core/ is deliberately not part of app/.
"""
