"""Write-only handling for secret settings (the Cloudflare API token).

GET /api/settings never returns a secret's value, only "<key>_set": true/false.
POST /api/settings keeps the stored secret when the field comes back blank, so
saving the form without retyping the token doesn't erase it. Send "<key>_clear": true
to remove it.
"""

SECRET_KEYS = frozenset({'cf_api_token'})


def public_view(vals: dict) -> dict:
    """Settings as the browser may see them: secrets replaced by a set/unset flag."""
    out = {k: v for k, v in vals.items() if k not in SECRET_KEYS}
    for k in SECRET_KEYS:
        out[f'{k}_set'] = bool(vals.get(k))
    return out


def updates(body: dict, keys) -> dict:
    """Map a POST body to {key: value-or-None}. None deletes. Blank secrets are skipped."""
    out = {}
    for key in keys:
        if key in SECRET_KEYS:
            if body.get(f'{key}_clear') is True:
                out[key] = None
                continue
            val = (body.get(key) or '').strip()
            if val:
                out[key] = val
            continue
        if key in body:
            v = body[key]
            out[key] = v.strip() if isinstance(v, str) and v.strip() else None
    return out
