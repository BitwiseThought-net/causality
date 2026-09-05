"""
Shared fixtures for the causality test suite.

Both app.py and worker.py talk to real network services (RabbitMQ via pika,
MongoDB via pymongo) and app.py additionally performs strict environment
variable validation at *import time* (it raises RuntimeError if required
secrets are missing). To keep the suite fast, deterministic, and free of any
real broker/database, this conftest:

  - Ensures the project root is importable.
  - Provides fake pika Connection/Channel objects that record every call
    made against them instead of touching a real RabbitMQ instance.
  - Provides fixtures that import app.py / worker.py fresh (popping any
    previously-cached module) after setting up a clean, known environment,
    since both modules read `os.environ` at import time or read module-level
    globals that tests need to control precisely.
"""
import os
import sys
import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# All environment variables either app.py or worker.py read directly.
ALL_RELEVANT_ENV_VARS = [
    "SECRET",
    "API_KEY",
    "RABBITMQ_HOST",
    "RABBITMQ_USER",
    "RABBITMQ_PASS",
    "MONGO_HOST",
    "MONGO_PORT",
]

# The minimal set of env vars app.py requires to be non-empty at import time.
VALID_APP_ENV = {
    "SECRET": "test-secret",
    "API_KEY": "test-api-key",
    "RABBITMQ_USER": "test-rmq-user",
    "RABBITMQ_PASS": "test-rmq-pass",
}


@pytest.fixture(autouse=True)
def no_env_leak(monkeypatch):
    """
    Strips every env var app.py/worker.py look at before each test, so tests
    that check default-value fallback or "missing credential" behavior
    aren't accidentally influenced by the shell environment the suite
    happens to run in, and so no test can leak its env into another.
    """
    for key in ALL_RELEVANT_ENV_VARS:
        monkeypatch.delenv(key, raising=False)


class FakeChannel:
    """Records every call made against it instead of talking to RabbitMQ."""

    def __init__(self, fail_on=None):
        self.calls = []
        # Optional method name -> Exception instance to raise when that
        # method is called, used to simulate broker failures.
        self._fail_on = fail_on or {}
        self.acked = []
        self.nacked = []
        self.published = []

    def _maybe_fail(self, name):
        if name in self._fail_on:
            raise self._fail_on[name]

    def exchange_declare(self, **kwargs):
        self._maybe_fail("exchange_declare")
        self.calls.append(("exchange_declare", kwargs))

    def queue_declare(self, **kwargs):
        self._maybe_fail("queue_declare")
        self.calls.append(("queue_declare", kwargs))

    def queue_bind(self, **kwargs):
        self._maybe_fail("queue_bind")
        self.calls.append(("queue_bind", kwargs))

    def basic_publish(self, **kwargs):
        self._maybe_fail("basic_publish")
        self.calls.append(("basic_publish", kwargs))
        self.published.append(kwargs)

    def basic_ack(self, delivery_tag=None):
        self.calls.append(("basic_ack", {"delivery_tag": delivery_tag}))
        self.acked.append(delivery_tag)

    def basic_nack(self, delivery_tag=None, requeue=None):
        self.calls.append(
            ("basic_nack", {"delivery_tag": delivery_tag, "requeue": requeue})
        )
        self.nacked.append((delivery_tag, requeue))

    def basic_qos(self, **kwargs):
        self.calls.append(("basic_qos", kwargs))

    def basic_consume(self, **kwargs):
        self.calls.append(("basic_consume", kwargs))

    def start_consuming(self):
        self.calls.append(("start_consuming", {}))


class FakeConnection:
    """Stand-in for pika.BlockingConnection."""

    def __init__(self, *args, channel=None, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self._channel = channel or FakeChannel()
        self.closed = False

    def channel(self):
        return self._channel

    def close(self):
        self.closed = True


@pytest.fixture
def fake_channel():
    """A fresh FakeChannel a test can inspect after exercising code."""
    return FakeChannel()


@pytest.fixture
def fake_pika(monkeypatch, fake_channel):
    """
    Patches the real `pika` module's BlockingConnection so any code that
    does `pika.BlockingConnection(...)` - whether in app.py or worker.py -
    gets a FakeConnection wrapping `fake_channel` instead of opening a real
    socket. Returns the fake_channel for assertions.
    """
    import pika

    def _factory(*args, **kwargs):
        return FakeConnection(*args, channel=fake_channel, **kwargs)

    monkeypatch.setattr(pika, "BlockingConnection", _factory)
    return fake_channel


@pytest.fixture
def app_module(monkeypatch):
    """
    Imports a fresh copy of app.py with a valid, minimal environment set up
    so its module-level startup validation passes. Yields the imported
    module and un-imports it afterwards so other tests can control the
    environment independently.
    """
    for key, value in VALID_APP_ENV.items():
        monkeypatch.setenv(key, value)
    sys.modules.pop("app", None)
    import app as app_mod

    yield app_mod
    sys.modules.pop("app", None)


@pytest.fixture
def worker_module():
    """
    Imports a fresh copy of worker.py. Unlike app.py, worker.py performs no
    validation at import time, so no environment setup is required here -
    individual tests configure `worker_module.payload_collection` etc.
    directly.
    """
    sys.modules.pop("worker", None)
    import worker as worker_mod

    yield worker_mod
    sys.modules.pop("worker", None)
