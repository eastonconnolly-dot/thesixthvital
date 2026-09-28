"""Shared helpers for builder modules -- the candidate dict shape and a
generic within-batch dedup helper. Framework-free (no Flask import) so every
builder stays independently testable."""


def Candidate(
    name, track, source, email=None, phone=None, org=None, role=None,
    state=None, external_id=None, tags=None,
):
    """Constructs a candidate dict with a consistent key set. A plain
    function (not a class) so candidates stay trivially JSON-serializable
    and diffable in tests."""
    return {
        "name": name,
        "email": (email or None),
        "phone": (phone or None),
        "org": (org or None),
        "role": (role or None),
        "state": (state or None),
        "track": track,
        "source": source,
        "external_id": external_id,
        "tags": list(tags) if tags else [],
    }


def dedupe_by_key(candidates, key_fn):
    """Removes candidates that produce a duplicate key (case-insensitive if
    the key is a string), keeping the first occurrence. Skips candidates
    whose key_fn returns a falsy value (nothing to dedupe on) -- they pass
    through unchanged rather than being collapsed onto each other."""
    seen = set()
    deduped = []
    for candidate in candidates:
        key = key_fn(candidate)
        if not key:
            deduped.append(candidate)
            continue
        norm = key.strip().lower() if isinstance(key, str) else key
        if norm in seen:
            continue
        seen.add(norm)
        deduped.append(candidate)
    return deduped
