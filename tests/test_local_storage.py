import re

import pytest

from app import templating
from app.utils import local_storage


def test_derive_user_key_is_stable_and_pseudonymous(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_storage.config.security_settings, "secret_key", "test-secret")
    user = {
        "provider": "GitHub",
        "sub": "123456",
        "email": "person@example.com",
    }

    first_key = local_storage.derive_user_key(user)
    second_key = local_storage.derive_user_key(user)

    assert first_key == second_key
    assert re.fullmatch(r"[A-Za-z0-9_-]{22}", first_key)
    assert "person" not in first_key
    assert "123456" not in first_key


def test_derive_user_key_changes_for_different_provider_subject(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_storage.config.security_settings, "secret_key", "test-secret")

    first_key = local_storage.derive_user_key({"provider": "github", "sub": "123"})
    second_key = local_storage.derive_user_key({"provider": "github", "sub": "456"})

    assert first_key != second_key


def test_derive_user_key_prefers_provider_subject_over_email(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_storage.config.security_settings, "secret_key", "test-secret")

    first_key = local_storage.derive_user_key({"provider": "github", "sub": "123", "email": "first@example.com"})
    second_key = local_storage.derive_user_key({"provider": "github", "sub": "123", "email": "second@example.com"})

    assert first_key == second_key


def test_derive_user_key_uses_normalized_email_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_storage.config.security_settings, "secret_key", "test-secret")

    first_key = local_storage.derive_user_key({"email": " Person@Example.com "})
    second_key = local_storage.derive_user_key({"email": "person@example.com"})

    assert first_key == second_key


def test_derive_user_key_requires_identity_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_storage.config.security_settings, "secret_key", "test-secret")

    with pytest.raises(ValueError, match="stable identity"):
        local_storage.derive_user_key({})


def test_template_global_exposes_user_key_helper() -> None:
    assert templating.templates.env.globals["local_storage_user_key"] is local_storage.derive_user_key
