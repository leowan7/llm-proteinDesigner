"""Bootstrap-only test so CI exits 0 on the 13-02 merge commit.

Plans 13-04 and 13-05 add real tests under tests/; this placeholder is
superseded naturally as soon as those land but stays as a defensive
version pin.
"""

import bindwave


def test_version():
    assert bindwave.__version__ == "0.1.0"


def test_public_surface_importable():
    """All 12 names in __all__ resolve, even if they are placeholders."""
    from bindwave import (
        Client,
        AsyncClient,
        BindwaveError,
        BindwaveAuthError,
        BindwaveRateLimitError,
        BindwaveValidationError,
        BindwaveJobError,
        BindwaveAPIError,
        Job,
        JobStatus,
        Candidate,
        ApiKey,
    )

    assert issubclass(BindwaveAuthError, BindwaveError)
