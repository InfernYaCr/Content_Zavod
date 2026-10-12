import pytest

from content_zavod.config import ConfigError, load_settings

_BASE_ENV = {
    "TELEGRAM_BOT_TOKEN": "123:abc",
    "POSTGRES_DSN": "postgresql://localhost/content_zavod",
    "YANDEX_FOLDER_ID": "folder-1",
    "YANDEX_API_KEY": "api-key",
}


def test_load_settings_with_api_key() -> None:
    settings = load_settings(_BASE_ENV)

    assert settings.telegram_bot_token == "123:abc"
    assert settings.global_admin_telegram_ids == frozenset()
    assert settings.postgres_dsn == "postgresql://localhost/content_zavod"
    assert settings.yandex.folder_id == "folder-1"
    assert settings.yandex.api_key == "api-key"
    assert settings.yandex.oauth_token is None
    assert str(settings.timezone) == "Europe/Moscow"


def test_load_settings_with_oauth_token() -> None:
    env = {**_BASE_ENV, "YANDEX_API_KEY": ""}
    env["YANDEX_OAUTH_TOKEN"] = "oauth-token"

    settings = load_settings(env)

    assert settings.yandex.api_key is None
    assert settings.yandex.oauth_token == "oauth-token"


def test_load_settings_uses_custom_timezone() -> None:
    env = {**_BASE_ENV, "APP_TIMEZONE": "UTC"}

    settings = load_settings(env)

    assert str(settings.timezone) == "UTC"


@pytest.mark.parametrize("missing", ["TELEGRAM_BOT_TOKEN", "POSTGRES_DSN", "YANDEX_FOLDER_ID"])
def test_load_settings_raises_for_missing_required_var(missing: str) -> None:
    env = dict(_BASE_ENV)
    del env[missing]

    with pytest.raises(ConfigError):
        load_settings(env)


def test_load_settings_raises_when_no_yandex_credential_is_set() -> None:
    env = {**_BASE_ENV, "YANDEX_API_KEY": ""}

    with pytest.raises(ConfigError):
        load_settings(env)


def test_load_settings_raises_when_both_yandex_credentials_are_set() -> None:
    env = {**_BASE_ENV, "YANDEX_OAUTH_TOKEN": "oauth-token"}

    with pytest.raises(ConfigError):
        load_settings(env)


def test_global_admins_are_a_comma_separated_list_of_ids() -> None:
    env = {**_BASE_ENV, "GLOBAL_ADMIN_TELEGRAM_IDS": " 42, 7 ,"}

    assert load_settings(env).global_admin_telegram_ids == frozenset({42, 7})


def test_load_settings_raises_for_a_non_integer_global_admin_id() -> None:
    env = {**_BASE_ENV, "GLOBAL_ADMIN_TELEGRAM_IDS": "42,@admin"}

    with pytest.raises(ConfigError):
        load_settings(env)


def test_load_settings_raises_for_invalid_timezone() -> None:
    env = {**_BASE_ENV, "APP_TIMEZONE": "Not/A_Zone"}

    with pytest.raises(ConfigError):
        load_settings(env)


def test_load_settings_defaults_pricing_to_unset() -> None:
    settings = load_settings(_BASE_ENV)

    assert settings.yandex_pricing.text_cost_per_1k_tokens is None
    assert settings.yandex_pricing.image_cost_per_generation is None


def test_load_settings_parses_configured_pricing() -> None:
    env = {
        **_BASE_ENV,
        "YANDEX_TEXT_COST_PER_1K_TOKENS": "0.2",
        "YANDEX_IMAGE_COST_PER_GENERATION": "5",
    }

    settings = load_settings(env)

    assert settings.yandex_pricing.text_cost_per_1k_tokens == 0.2
    assert settings.yandex_pricing.image_cost_per_generation == 5.0


def test_load_settings_raises_for_non_numeric_pricing() -> None:
    env = {**_BASE_ENV, "YANDEX_TEXT_COST_PER_1K_TOKENS": "not-a-number"}

    with pytest.raises(ConfigError):
        load_settings(env)


def test_load_settings_web_search_is_on_by_default_without_search_pricing() -> None:
    settings = load_settings(_BASE_ENV)

    assert settings.web_search_enabled is True
    assert settings.yandex_pricing.search_cost_per_request is None


def test_load_settings_parses_web_search_switch_and_pricing() -> None:
    env = {**_BASE_ENV, "YANDEX_WEB_SEARCH": "off", "YANDEX_SEARCH_COST_PER_REQUEST": "0.48"}

    settings = load_settings(env)

    assert settings.web_search_enabled is False
    assert settings.yandex_pricing.search_cost_per_request == 0.48


def test_load_settings_rejects_a_malformed_web_search_switch() -> None:
    with pytest.raises(ConfigError):
        load_settings({**_BASE_ENV, "YANDEX_WEB_SEARCH": "maybe"})


def test_telegraph_access_token_is_optional() -> None:
    assert load_settings(_BASE_ENV).telegraph_access_token is None
    env = {**_BASE_ENV, "TELEGRAPH_ACCESS_TOKEN": "tg-token"}
    assert load_settings(env).telegraph_access_token == "tg-token"


def test_covers_are_off_by_default_and_switch_on() -> None:
    assert load_settings(_BASE_ENV).covers_enabled is False
    assert load_settings({**_BASE_ENV, "YANDEX_COVERS": "on"}).covers_enabled is True
    with pytest.raises(ConfigError):
        load_settings({**_BASE_ENV, "YANDEX_COVERS": "maybe"})


def test_yandex_models_default_to_explicit_names_and_are_configurable() -> None:
    models = load_settings(_BASE_ENV).yandex_models
    assert (models.text, models.image) == ("yandexgpt-5.1", "aliceai-image-art-3.0")

    env = {**_BASE_ENV, "YANDEX_TEXT_MODEL": "yandexgpt-5-lite", "YANDEX_IMAGE_MODEL": "art-x"}
    models = load_settings(env).yandex_models
    assert (models.text, models.image) == ("yandexgpt-5-lite", "art-x")


def test_signup_daily_limit_defaults_to_50_and_is_configurable() -> None:
    assert load_settings(_BASE_ENV).signup_daily_limit == 50
    assert load_settings({**_BASE_ENV, "SIGNUP_DAILY_LIMIT": "0"}).signup_daily_limit == 0
    for bad in ("-1", "many", "1.5"):
        with pytest.raises(ConfigError):
            load_settings({**_BASE_ENV, "SIGNUP_DAILY_LIMIT": bad})


def test_the_trial_limit_defaults_to_one_plan_and_eight_statyi() -> None:
    """The owner's decision in #118; `LIMIT_CONTACT` is optional."""
    trial = load_settings(_BASE_ENV).trial_limits
    assert (trial.plans, trial.articles, trial.jobs_per_day, trial.contact) == (1, 8, 50, None)


def test_the_trial_limit_is_configurable() -> None:
    env = {
        **_BASE_ENV,
        "TRIAL_PLAN_LIMIT": "2",
        "TRIAL_ARTICLE_LIMIT": "12",
        "JOBS_PER_DAY_LIMIT": "20",
        "LIMIT_CONTACT": " @owner ",
    }
    trial = load_settings(env).trial_limits
    assert (trial.plans, trial.articles, trial.jobs_per_day, trial.contact) == (2, 12, 20, "@owner")
    for name in ("TRIAL_PLAN_LIMIT", "TRIAL_ARTICLE_LIMIT", "JOBS_PER_DAY_LIMIT"):
        with pytest.raises(ConfigError):
            load_settings({**_BASE_ENV, name: "-1"})
