"""
Tests for worker.py - the RabbitMQ consumer that persists payloads to
MongoDB, with retry / dead-letter handling.
"""
import json
from unittest.mock import MagicMock

import pika
import pytest


class FakeProperties:
    def __init__(self, headers=None):
        self.headers = headers


class TestGetRetryCount:
    def test_returns_zero_when_headers_is_none(self, worker_module):
        assert worker_module.get_retry_count(FakeProperties(headers=None)) == 0

    def test_returns_zero_when_x_death_key_absent(self, worker_module):
        props = FakeProperties(headers={"other": "value"})
        assert worker_module.get_retry_count(props) == 0

    def test_returns_zero_when_x_death_is_empty_list(self, worker_module):
        props = FakeProperties(headers={"x-death": []})
        assert worker_module.get_retry_count(props) == 0

    def test_returns_zero_when_x_death_is_not_a_list(self, worker_module):
        props = FakeProperties(headers={"x-death": "not-a-list"})
        assert worker_module.get_retry_count(props) == 0

    def test_sums_counts_across_all_death_hops(self, worker_module):
        props = FakeProperties(
            headers={"x-death": [{"count": 2}, {"count": 1}]}
        )
        assert worker_module.get_retry_count(props) == 3

    def test_treats_missing_count_key_as_zero(self, worker_module):
        props = FakeProperties(headers={"x-death": [{}, {"count": 4}]})
        assert worker_module.get_retry_count(props) == 4


class TestProcessPayload:
    """
    process_payload(ch, method, properties, body) has no return value; its
    effects are observed via the fake channel's ack/nack/publish calls and
    via the mocked Mongo collection's insert_one calls.
    """

    @pytest.fixture(autouse=True)
    def _wire_up(self, worker_module, fake_channel):
        self.worker = worker_module
        self.channel = fake_channel
        self.collection = MagicMock()
        worker_module.payload_collection = self.collection
        self.method = MagicMock(delivery_tag=42)

    def _properties(self, headers=None):
        return FakeProperties(headers=headers)

    def test_valid_payload_is_stored_and_acked(self):
        body = json.dumps({"event": "order.completed"}).encode("utf-8")
        self.worker.process_payload(self.channel, self.method, self._properties(), body)

        self.collection.insert_one.assert_called_once()
        stored = self.collection.insert_one.call_args[0][0]
        assert stored["event"] == "order.completed"
        assert "_processed_at" in stored
        assert stored["_retry_count"] == 0

        assert self.channel.acked == [42]
        assert self.channel.nacked == []
        assert self.channel.published == []

    def test_retry_count_from_headers_is_recorded_on_success(self):
        body = json.dumps({"event": "x"}).encode("utf-8")
        props = self._properties(headers={"x-death": [{"count": 2}]})
        self.worker.process_payload(self.channel, self.method, props, body)

        stored = self.collection.insert_one.call_args[0][0]
        assert stored["_retry_count"] == 2

    def test_malformed_json_body_is_nacked_without_requeue(self):
        body = b"not valid json"
        self.worker.process_payload(self.channel, self.method, self._properties(), body)

        self.collection.insert_one.assert_not_called()
        assert self.channel.acked == []
        assert self.channel.nacked == [(42, False)]

    def test_simulated_trigger_error_below_max_retries_is_nacked(self):
        body = json.dumps({"data": {"trigger_error": True}}).encode("utf-8")
        props = self._properties(headers={"x-death": [{"count": 1}]})
        self.worker.process_payload(self.channel, self.method, props, body)

        self.collection.insert_one.assert_not_called()
        assert self.channel.nacked == [(42, False)]
        assert self.channel.published == []

    def test_error_at_max_retries_is_sent_to_dead_letter_and_acked(self):
        body = json.dumps({"data": {"trigger_error": True}}).encode("utf-8")
        props = self._properties(headers={"x-death": [{"count": 3}]})
        self.worker.process_payload(self.channel, self.method, props, body)

        self.collection.insert_one.assert_not_called()
        assert self.channel.nacked == []
        assert self.channel.acked == [42]
        assert len(self.channel.published) == 1
        publish_kwargs = self.channel.published[0]
        assert publish_kwargs["exchange"] == "dlx_exchange"
        assert publish_kwargs["routing_key"] == "dead"
        assert publish_kwargs["body"] == body

    def test_error_above_max_retries_also_goes_to_dead_letter(self):
        body = json.dumps({"data": {"trigger_error": True}}).encode("utf-8")
        props = self._properties(headers={"x-death": [{"count": 10}]})
        self.worker.process_payload(self.channel, self.method, props, body)

        assert self.channel.acked == [42]
        assert len(self.channel.published) == 1

    def test_database_error_during_insert_follows_retry_path(self):
        """A downstream DB failure should be handled by the same
        retry/dead-letter logic as a bad payload, not propagate uncaught."""
        self.collection.insert_one.side_effect = RuntimeError("mongo is down")
        body = json.dumps({"event": "x"}).encode("utf-8")
        self.worker.process_payload(self.channel, self.method, self._properties(), body)

        assert self.channel.nacked == [(42, False)]


class TestMain:
    """
    main() reads env vars, connects to Mongo and RabbitMQ, then blocks
    forever in channel.start_consuming(). We fake both connections so the
    test completes, and simply assert the setup calls happened correctly.
    """

    @pytest.fixture(autouse=True)
    def _valid_env(self, monkeypatch):
        monkeypatch.setenv("RABBITMQ_USER", "rmq-user")
        monkeypatch.setenv("RABBITMQ_PASS", "rmq-pass")

    def test_exits_when_rabbitmq_credentials_missing(self, monkeypatch, worker_module):
        monkeypatch.delenv("RABBITMQ_USER", raising=False)
        with pytest.raises(SystemExit) as exc_info:
            worker_module.main()
        assert exc_info.value.code == 1

    def test_exits_when_mongo_connection_fails(self, monkeypatch, worker_module):
        class BoomClient:
            def __init__(self, *a, **kw):
                raise ConnectionError("no mongo")

        monkeypatch.setattr(worker_module, "MongoClient", BoomClient)
        with pytest.raises(SystemExit) as exc_info:
            worker_module.main()
        assert exc_info.value.code == 1

    def test_happy_path_wires_up_mongo_and_rabbitmq(
        self, monkeypatch, worker_module, fake_channel
    ):
        fake_mongo_collection = MagicMock()
        fake_db = MagicMock()
        fake_db.__getitem__.return_value = fake_mongo_collection

        class FakeMongoClient:
            def __init__(self, host=None, port=None):
                self.host = host
                self.port = port

            def __getitem__(self, name):
                assert name == "causality"
                return fake_db

        monkeypatch.setattr(worker_module, "MongoClient", FakeMongoClient)

        def _factory(*args, **kwargs):
            from tests.conftest import FakeConnection

            return FakeConnection(*args, channel=fake_channel, **kwargs)

        monkeypatch.setattr(pika, "BlockingConnection", _factory)

        worker_module.main()

        assert worker_module.payload_collection is fake_mongo_collection

        call_names = [name for name, _ in fake_channel.calls]
        assert "exchange_declare" in call_names
        # queue, retry_queue, dead_letter_queue
        assert call_names.count("queue_declare") == 3
        assert call_names.count("queue_bind") == 2
        assert "basic_qos" in call_names
        assert "basic_consume" in call_names
        assert "start_consuming" in call_names

    def test_mongo_port_env_var_is_cast_to_int(self, monkeypatch, worker_module, fake_channel):
        monkeypatch.setenv("MONGO_PORT", "27099")
        captured = {}

        class FakeMongoClient:
            def __init__(self, host=None, port=None):
                captured["host"] = host
                captured["port"] = port

            def __getitem__(self, name):
                return MagicMock()

        monkeypatch.setattr(worker_module, "MongoClient", FakeMongoClient)

        def _factory(*args, **kwargs):
            from tests.conftest import FakeConnection

            return FakeConnection(*args, channel=fake_channel, **kwargs)

        monkeypatch.setattr(pika, "BlockingConnection", _factory)

        worker_module.main()

        assert captured["port"] == 27099
        assert isinstance(captured["port"], int)
