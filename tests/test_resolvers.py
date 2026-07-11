import pytest

from hermes_live_clipper.resolvers import ResolveError, normalize_url


@pytest.mark.parametrize(
    ("url", "provider", "external_id"),
    [
        ("https://youtu.be/abcDEF_123", "youtube", "abcDEF_123"),
        ("https://www.youtube.com/live/abcDEF_123?feature=share", "youtube", "abcDEF_123"),
        ("https://www.youtube.com/watch?v=abcDEF_123", "youtube", "abcDEF_123"),
        ("https://twitch.tv/TechFren", "twitch", "techfren"),
    ],
)
def test_normalize_url(url, provider, external_id):
    identity = normalize_url(url)
    assert identity.provider == provider
    assert identity.external_id == external_id
    assert "feature=share" not in identity.canonical_url


def test_rejects_unsupported_url():
    with pytest.raises(ResolveError):
        normalize_url("https://example.com/live")
