"""
Tests for app.py - the FastAPI webhook ingester.

app.py performs strict environment-variable validation at *import time* and
opens a real RabbitMQ connection on every request, so these tests:
  - Exercise the import-time validation directly (via fresh imports with a
    deliberately incomplete environment).
  - Use the `fake_pika` fixture to intercept every RabbitMQ call so no
    network access ever happens.
"""
import hashlib
import hmac
import sys

import pika
import pytest
from fastapi.testclient import TestClient

from tests.conftest import VALID_APP_ENV


def _import_app_expecting_error(monkeypatch, env_overrides):
    """
    Sets exactly the given env vars (on top of the already-cleared
    baseline from the autouse no_env_leak fixture) and attempts a fresh
    import of app.py, returning the raised exception's message.
    """
    for key, value in env_overrides.items():
        monkeypatch.setenv(key, value)
    sys.modules.pop("app", None)
    with pytest.raises(RuntimeError) as exc_info:
        import app  # noqa: F401
    sys.modules.pop("app", None)
    return str(exc_info.value)


class TestStartupValidation:
    """
    app.py raises RuntimeError at import time if any required secret is
    missing. Each check happens in its own `if`, so to pin down *which*
    check fires we supply every other required variable and omit only the
    one under test.
    """

    def test_raises_when_secret_missing(self, monkeypatch):
        env = {k: v for k, v in VALID_APP_ENV.items() if k != "SECRET"}
        message = _import_app_expecting_error(monkeypatch, env)
        assert "SECRET" in message

    def test_raises_when_api_key_missing(self, monkeypatch):
        env = {k: v for k, v in VALID_APP_ENV.items() if k != "API_KEY"}
        message = _import_app_expecting_error(monkeypatch, env)
        assert "API_KEY" in message

    def test_raises_when_rabbitmq_user_missing(self, monkeypatch):
        env = {k: v for k, v in VALID_APP_ENV.items() if k != "RABBITMQ_USER"}
        message = _import_app_expecting_error(monkeypatch, env)
        assert "RABBITMQ_USER" in message

    def test_raises_when_rabbitmq_pass_missing(self, monkeypatch):
        env = {k: v for k, v in VALID_APP_ENV.items() if k != "RABBITMQ_PASS"}
        message = _import_app_expecting_error(monkeypatch, env)
        assert "RABBITMQ_PASS" in message

    def test_imports_successfully_with_full_valid_environment(self, app_module):
        assert app_module.SECRET == VALID_APP_ENV["SECRET"].encode("utf-8")
        assert app_module.API_KEY == VALID_APP_ENV["API_KEY"]

    def test_rabbitmq_host_defaults_to_localhost(self, app_module):
        assert app_module.RABBITMQ_HOST == "localhost"

    def test_rabbitmq_host_honors_env_override(self, monkeypatch):
        for key, value in VALID_APP_ENV.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setenv("RABBITMQ_HOST", "custom-broker-host")
        sys.modules.pop("app", None)
        import app as app_mod

        assert app_mod.RABBITMQ_HOST == "custom-broker-host"
        sys.modules.pop("app", None)


@pytest.fixture
def client(app_module, fake_pika):
    """A TestClient wired up against the real FastAPI app, with pika faked."""
    return TestClient(app_module.app)


def _sign(secret: bytes, body: bytes) -> str:
    return "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()


class TestBearerTokenAuth:
    def test_valid_api_key_bypasses_signature_check(self, client, app_module):
        response = client.post(
            "/",
            content=b'{"event": "ping"}',
            headers={"Authorization": f"Bearer {app_module.API_KEY}"},
        )
        assert response.status_code == 200
        assert response.json() == {
            "status": "queued",
            "message": "Received and safely buffered.",
        }

    def test_wrong_bearer_token_falls_through_to_signature_check(self, client):
        response = client.post(
            "/",
            content=b'{"event": "ping"}',
            headers={"Authorization": "Bearer not-the-real-key"},
        )
        # No X-Hub-Signature-256 header either -> falls through to 401.
        assert response.status_code == 401


class TestHmacSignatureAuth:
    def test_missing_all_auth_headers_returns_401(self, client):
        response = client.post("/", content=b'{"event": "ping"}')
        assert response.status_code == 401
        assert "Missing authorization headers" in response.json()["detail"]

    def test_invalid_signature_returns_403(self, client):
        response = client.post(
            "/",
            content=b'{"event": "ping"}',
            headers={"X-Hub-Signature-256": "sha256=deadbeef"},
        )
        assert response.status_code == 403
        assert "Invalid authorization signature" in response.json()["detail"]

    def test_valid_signature_is_accepted(self, client, app_module):
        body = b'{"event": "order.completed"}'
        signature = _sign(app_module.SECRET, body)
        response = client.post(
            "/",
            content=body,
            headers={"X-Hub-Signature-256": signature},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "queued"

    def test_signature_is_bound_to_the_exact_body_bytes(self, client, app_module):
        """A signature computed over one payload must not validate a
        different payload, guarding against a naive/loose comparison."""
        signature = _sign(app_module.SECRET, b'{"event": "a"}')
        response = client.post(
            "/",
            content=b'{"event": "b"}',
            headers={"X-Hub-Signature-256": signature},
        )
        assert response.status_code == 403


class TestBrokerPublishing:
    def test_declares_topology_and_publishes_body_on_success(
        self, client, app_module, fake_pika
    ):
        body = b'{"event": "order.completed"}'
        signature = _sign(app_module.SECRET, body)
        response = client.post(
            "/", content=body, headers={"X-Hub-Signature-256": signature}
        )
        assert response.status_code == 200

        call_names = [name for name, _ in fake_pika.calls]
        assert call_names.count("exchange_declare") == 1
        # queue, retry_queue, dead_letter_queue
        assert call_names.count("queue_declare") == 3
        assert call_names.count("queue_bind") == 2
        assert call_names.count("basic_publish") == 1

        publish_kwargs = fake_pika.published[0]
        assert publish_kwargs["routing_key"] == "queue"
        assert publish_kwargs["body"] == body

    def test_main_queue_declared_with_dead_letter_arguments(
        self, client, app_module, fake_pika
    ):
        body = b"{}"
        signature = _sign(app_module.SECRET, body)
        client.post("/", content=body, headers={"X-Hub-Signature-256": signature})

        queue_declares = [kw for name, kw in fake_pika.calls if name == "queue_declare"]
        main_queue = next(q for q in queue_declares if q["queue"] == "queue")
        assert main_queue["arguments"]["x-dead-letter-exchange"] == "dlx_exchange"
        assert main_queue["arguments"]["x-dead-letter-routing-key"] == "retry"

        retry_queue = next(q for q in queue_declares if q["queue"] == "retry_queue")
        assert retry_queue["arguments"]["x-message-ttl"] == 5000

    def test_broker_failure_returns_500(self, monkeypatch, client, app_module):
        def boom(*args, **kwargs):
            raise ConnectionError("broker unreachable")

        monkeypatch.setattr(pika, "BlockingConnection", boom)
        body = b'{"event": "ping"}'
        response = client.post(
            "/",
            content=body,
            headers={"Authorization": f"Bearer {app_module.API_KEY}"},
        )
        assert response.status_code == 500
        assert "Message Broker Transaction Failure" in response.json()["detail"]

    def test_connection_is_closed_after_publishing(
        self, monkeypatch, app_module, fake_channel
    ):
        from tests.conftest import FakeConnection

        connections = []

        def _factory(*args, **kwargs):
            conn = FakeConnection(*args, channel=fake_channel, **kwargs)
            connections.append(conn)
            return conn

        monkeypatch.setattr(pika, "BlockingConnection", _factory)

        client = TestClient(app_module.app)
        body = b'{"event": "ping"}'
        signature = _sign(app_module.SECRET, body)
        client.post("/", content=body, headers={"X-Hub-Signature-256": signature})

        assert len(connections) == 1
        assert connections[0].closed is True
