from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = (ROOT / "deploy/compose/docker-compose.yml").read_text()
ENV_EXAMPLE = (ROOT / "deploy/examples/.env.example").read_text()


def test_postgres_host_binding_defaults_to_loopback_and_is_overridable():
    assert '"${DB_BIND_ADDRESS:-127.0.0.1}:${DB_HOST_PORT:-5432}:5432"' in COMPOSE


def test_ollama_host_binding_defaults_to_loopback_and_is_overridable():
    assert '"${OLLAMA_BIND_ADDRESS:-127.0.0.1}:${OLLAMA_HOST_PORT:-11434}:11434"' in COMPOSE


def test_api_requires_a_deployment_secret_and_example_has_no_default_secret():
    assert "ANZMA_API_TOKEN: ${ANZMA_API_TOKEN:?Set ANZMA_API_TOKEN in .env}" in COMPOSE
    assert "ANZMA_API_TOKEN=" in ENV_EXAMPLE
    assert "ANZMA_API_TOKEN=replace" not in ENV_EXAMPLE


def test_cookie_secure_is_configurable_for_tls_deployments():
    assert "ANZMA_COOKIE_SECURE: ${ANZMA_COOKIE_SECURE:-false}" in COMPOSE
    assert "ANZMA_COOKIE_SECURE=false" in ENV_EXAMPLE


if __name__ == "__main__":
    tests = [value for name, value in globals().items() if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)}/{len(tests)} passed")
