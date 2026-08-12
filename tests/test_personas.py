import pytest

from content_zavod.personas import platform_profile


def test_unknown_platform_fails_closed() -> None:
    with pytest.raises(ValueError, match="Unknown platform"):
        platform_profile("unknown")
