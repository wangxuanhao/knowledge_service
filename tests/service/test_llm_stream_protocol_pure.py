"""Consumer cancellation and generator control reach each bound stream step."""
import asyncio
import unittest
from types import SimpleNamespace

from knowledge_service.core.llm_config import LLMConfig, bound_llm, bound_llm_config, current_llm
from knowledge_service.services.llm_runtime import llm_stream


class LLMStreamProtocolTests(unittest.TestCase):
    def setUp(self):
        self.config = LLMConfig(profile_id='pinned', model='pinned')
        self.owner = SimpleNamespace(llm_models=SimpleNamespace(resolve=lambda: self.config))

    def test_cancelled_error_reaches_inner_stream_as_the_original_instance(self):
        for after_delta in (False, True):
            with self.subTest(after_delta=after_delta):
                self.owner.llm_models.resolve = lambda: self.config
                received, closed = [], []

                @llm_stream
                def body(owner):
                    try:
                        yield 'evidence'
                        yield 'delta'
                    except (GeneratorExit, asyncio.CancelledError) as error:
                        received.append((error, current_llm()))
                        raise
                    finally:
                        closed.append(current_llm())

                stream = body(self.owner)
                self.assertEqual(next(stream), 'evidence')
                if after_delta:
                    self.assertEqual(next(stream), 'delta')
                self.owner.llm_models.resolve = lambda: LLMConfig(model='changed')
                cancellation = asyncio.CancelledError('consumer disconnected')
                consumer = LLMConfig(model='consumer')
                with bound_llm(consumer):
                    with self.assertRaises(asyncio.CancelledError) as raised:
                        stream.throw(cancellation)
                    self.assertIs(bound_llm_config(), consumer)
                self.assertIs(raised.exception, cancellation)
                self.assertEqual(received, [(cancellation, self.config)])
                self.assertEqual(closed, [self.config])
                self.assertEqual(list(stream), [])
                self.assertIsNone(bound_llm_config())

    def test_handled_throw_yields_recovery_and_keeps_following_values(self):
        received = []

        @llm_stream
        def body(owner):
            try:
                yield 'ready'
            except ValueError as error:
                received.append((error, current_llm()))
                yield 'recovered'
            yield 'continued'

        stream = body(self.owner)
        self.assertEqual(next(stream), 'ready')
        error = ValueError('recoverable')
        self.assertEqual(stream.throw(error), 'recovered')
        self.assertEqual(received, [(error, self.config)])
        self.assertIsNone(bound_llm_config())
        self.assertEqual(list(stream), ['continued'])
        self.assertIsNone(bound_llm_config())

    def test_handled_throw_can_finish_the_inner_stream(self):
        received = []

        @llm_stream
        def body(owner):
            try:
                yield 'ready'
            except ValueError as error:
                received.append((error, current_llm()))
                return

        stream = body(self.owner)
        next(stream)
        error = ValueError('finish')
        with self.assertRaises(StopIteration):
            stream.throw(error)
        self.assertEqual(received, [(error, self.config)])
        self.assertIsNone(bound_llm_config())

    def test_close_delivers_generator_exit_and_cleans_up_under_pinned_model(self):
        received, closed = [], []

        @llm_stream
        def body(owner):
            try:
                yield 'ready'
            except GeneratorExit as error:
                received.append((type(error), current_llm()))
                raise
            finally:
                closed.append(current_llm())

        stream = body(self.owner)
        next(stream)
        with bound_llm(LLMConfig(model='consumer')):
            stream.close()
            self.assertEqual(current_llm().model, 'consumer')
        stream.close()
        self.assertEqual(received, [(GeneratorExit, self.config)])
        self.assertEqual(closed, [self.config])
        self.assertIsNone(bound_llm_config())

    def test_send_reaches_inner_stream_without_leaking_bound_context(self):
        received = []

        @llm_stream
        def body(owner):
            value = yield 'ready'
            received.append((value, current_llm()))
            yield value

        stream = body(self.owner)
        next(stream)
        self.assertEqual(stream.send('payload'), 'payload')
        self.assertEqual(received, [('payload', self.config)])
        self.assertIsNone(bound_llm_config())
        stream.close()


if __name__ == '__main__':
    unittest.main()
