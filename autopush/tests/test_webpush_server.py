import time
import unittest
from uuid import uuid4

import attr
import factory
from mock import Mock
from nose.tools import ok_, eq_

from autopush.db import (
    DatabaseManager,
    make_rotating_tablename,
    generate_last_connect,
)
from autopush.metrics import SinkMetrics
from autopush.settings import AutopushSettings
from autopush.websocket import USER_RECORD_VERSION
from autopush.webpush_server import (
    AutopushCall,
    Hello,
    HelloResponse,
)


class UserItemFactory(factory.Factory):
    class Meta:
        model = dict

    uaid = factory.LazyFunction(lambda: uuid4().hex)
    connected_at = factory.LazyFunction(lambda: int(time.time() * 1000)-10000)
    node_id = "http://something:3242/"
    router_type = "webpush"
    last_connect = factory.LazyFunction(generate_last_connect)
    record_version = USER_RECORD_VERSION
    current_month = factory.LazyFunction(
        lambda: make_rotating_tablename("message")
    )


class HelloFactory(factory.Factory):
    class Meta:
        model = Hello

    uaid = factory.LazyFunction(lambda: uuid4().hex)
    connected_at = factory.LazyFunction(lambda: int(time.time() * 1000))


class TestWebPushServer(unittest.TestCase):
    def setUp(self):
        self.settings = settings = AutopushSettings(
            hostname="localhost",
            port=8080,
            statsd_host=None,
            env="test",
        )

    def _makeFUT(self):
        from autopush.webpush_server import WebPushServer
        return WebPushServer(self.settings)

    def test_start_stop(self):
        ws = self._makeFUT()
        ws.start(num_threads=2)
        eq_(len(ws.workers), 2)
        ws.stop()
        eq_(len(ws.workers), 0)

    def test_hello_process(self):
        ws = self._makeFUT()
        ws.start(num_threads=2)
        try:
            hello = HelloFactory()
            call = AutopushCall()
            payload = dict(
                command="hello",
                uaid=hello.uaid.hex,
                connected_at=hello.connected_at,
            )
            ws.incoming.put((call, payload))
            call.called.wait()
            result = call.val
            ok_("error" not in result)
            ok_(hello.uaid.hex != result["uaid"])
        finally:
            ws.stop()


class TestHelloProcessor(unittest.TestCase):
    def setUp(self):
        self.settings = settings = AutopushSettings(
            hostname="localhost",
            port=8080,
            statsd_host=None,
            env="test",
        )
        self.db = db = DatabaseManager.from_settings(settings)
        self.metrics = db.metrics = Mock(spec=SinkMetrics)
        db.setup_tables()

    def _makeFUT(self):
        from autopush.webpush_server import HelloCommand
        return HelloCommand(self.settings, self.db)

    def test_nonexisting_uaid(self):
        p = self._makeFUT()
        hello = HelloFactory()
        result = p.process(hello)  # type: HelloResponse
        ok_(isinstance(result, HelloResponse))
        ok_(hello.uaid != result.uaid)

    def test_existing_uaid(self):
        p = self._makeFUT()
        hello = HelloFactory()
        success, _ = self.db.router.register_user(UserItemFactory(
            uaid=hello.uaid.hex))
        eq_(success, True)
        result = p.process(hello)  # type: HelloResponse
        ok_(isinstance(result, HelloResponse))
        eq_(hello.uaid.hex, result.uaid)

    def test_existing_newer_uaid(self):
        p = self._makeFUT()
        hello = HelloFactory()
        self.db.router.register_user(
            UserItemFactory(uaid=hello.uaid.hex,
                            connected_at=hello.connected_at+10)
        )
        result = p.process(hello)  # type: HelloResponse
        ok_(isinstance(result, HelloResponse))
        eq_(result.uaid, None)