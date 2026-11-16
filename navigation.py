"""Navigation validity shared by the crawler and analysis (no schema change)."""

NAVIGATION_PHASES = {"landing", "home_reload", "subpage"}
REQUIRED_PHASES = {"landing", "after_consent", "home_reload"}


def navigation_error(status, error=None):
    """A completed document navigation needs a successful HTTP response."""
    if error:
        return error
    if status is None:
        return "No main-document HTTP response"
    if status in (204, 205):
        return f"HTTP {status}: no document content"
    if not 200 <= status < 300:
        return f"HTTP {status}"
    return None


def measurement_error(visits):
    """Return why a site/run is unusable, including legacy 'ok' records.

    Require the three fixed phases and every attempted navigation to finish.
    Consent is an interaction, not a navigation: its HTTP status is normally NULL.
    This checks navigation/completion only, not consent success or bot challenges.
    """
    seen = set()
    for visit in visits:
        phase = visit["phase"]
        seen.add(phase)
        if visit["ended"] is None:
            return f"{phase}: visit did not finish"
        if phase in NAVIGATION_PHASES:
            problem = navigation_error(visit["status"], visit["error"])
        else:
            problem = visit["error"]
        if problem:
            return f"{phase}: {problem}"
    missing = REQUIRED_PHASES - seen
    if missing:
        return "Missing phases: " + ", ".join(sorted(missing))
    return None
