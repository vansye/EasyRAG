"""Hold process ownership until construction and all admitted work have finished."""

import asyncio
from contextlib import asynccontextmanager

from .application.process_lock import ProcessLock
from .application.runtime import RuntimeSettings, Services


async def _finish(task):
    """Native asyncio cancellation must not orphan a resource-owning worker."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    return task.result(), cancelled


def lifespan_for(services: Services | None, settings: RuntimeSettings):
    @asynccontextmanager
    async def lifespan(app):
        with ProcessLock(settings.runtime_lock_file):
            if services is None:
                active, cancelled = await _finish(asyncio.create_task(asyncio.to_thread(Services.create)))
            else:
                active, cancelled = services, False
            try:
                if cancelled:
                    raise asyncio.CancelledError
                app.state.services = active
                yield
            finally:
                _, cancelled = await _finish(asyncio.create_task(asyncio.to_thread(active.close)))
                if cancelled:
                    raise asyncio.CancelledError

    return lifespan
