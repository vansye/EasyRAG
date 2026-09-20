"""Bounded SSE transport; the producer owns resources until its actual exit."""

import asyncio
import json
import logging
from contextlib import closing
from queue import Empty, Full, Queue
from threading import Event

import anyio
from fastapi.encoders import jsonable_encoder
from starlette.responses import StreamingResponse

from .application.errors import GateBusy, QuestionFailed, RecoveryFailed
from .application.questions import StreamControl
from .modules.qa.public import StreamCancelled


logger = logging.getLogger(__name__)


class QuestionStreamResponse(StreamingResponse):
    def __init__(self, services, question):
        self._services = services
        self._question = question
        self._control = StreamControl()
        self._queue = Queue(maxsize=8)
        self._finished = Event()
        self._wake = asyncio.Event()
        super().__init__(self._events(), media_type='text/event-stream', headers={
            'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no',
        })

    def _put(self, kind, data, loop):
        payload = f'event: {kind}\ndata: {json.dumps(jsonable_encoder(data), ensure_ascii=False)}\n\n'.encode('utf-8')
        while True:
            self._control.check()
            try:
                self._queue.put(payload, timeout=.05)
                loop.call_soon_threadsafe(self._wake.set)
                return
            except Full:
                continue

    def _produce(self, loop):
        try:
            with self._services.request_work():
                with closing(self._services.questions.stream(self._question, self._control)) as events:
                    terminal = False
                    for event in events:
                        self._put(event.kind, event.data, loop)
                        if event.kind == 'done':
                            terminal = True
                            break
                    if not terminal:
                        raise QuestionFailed()
        except StreamCancelled:
            pass
        except Exception as failure:
            logger.warning('question_stream_failed cause=%s', type(failure).__name__)
            data = {'error': '这次回答没有完成，请重试。'}
            if isinstance(failure, GateBusy):
                data = {'error': str(failure), 'state': failure.state}
            elif isinstance(failure, RecoveryFailed):
                data = {'error': str(failure), 'state': 'RECOVERY_REQUIRED'}
            try:
                self._put('error', data, loop)
            except StreamCancelled:
                pass
        finally:
            self._finished.set()
            loop.call_soon_threadsafe(self._wake.set)

    async def _events(self):
        while True:
            self._wake.clear()
            try:
                yield self._queue.get_nowait()
            except Empty:
                if self._finished.is_set():
                    return
                await self._wake.wait()

    async def __call__(self, scope, receive, send):
        worker = asyncio.create_task(asyncio.to_thread(self._produce, asyncio.get_running_loop()))
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._control.cancel()
            # Neither ASGI disconnect nor native cancellation may orphan the sync worker.
            cancelled = False
            with anyio.CancelScope(shield=True):
                while not worker.done():
                    try:
                        await asyncio.shield(worker)
                    except asyncio.CancelledError:
                        cancelled = True
                worker.result()
            if cancelled:
                raise asyncio.CancelledError
