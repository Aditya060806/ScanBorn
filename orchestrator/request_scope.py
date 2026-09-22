"""Close request-owned connections and finish jobs when a stage raises."""
import inspect
from contextvars import ContextVar
from functools import wraps

connections: ContextVar[list | None] = ContextVar("connections", default=None)
job_ids: ContextVar[list | None] = ContextVar("job_ids", default=None)


def _close(token, job_token, error):
    from orchestrator import jobs
    try:
        if error is not None:
            for identifier in job_ids.get() or []:
                job = jobs.get_job(identifier)
                if job and job["status"] == "running":
                    jobs.finish_job(identifier, ok=False, detail=str(error))
    finally:
        try:
            for conn in connections.get() or []:
                conn.close()
        finally:
            connections.reset(token)
            job_ids.reset(job_token)


def scoped(function):
    if inspect.iscoroutinefunction(function):
        @wraps(function)
        async def asynchronous(*args, **kwargs):
            token, job_token = connections.set([]), job_ids.set([])
            error = None
            try:
                return await function(*args, **kwargs)
            except BaseException as exc:
                error = exc
                raise
            finally:
                _close(token, job_token, error)
        return asynchronous

    @wraps(function)
    def synchronous(*args, **kwargs):
        token, job_token = connections.set([]), job_ids.set([])
        error = None
        try:
            return function(*args, **kwargs)
        except BaseException as exc:
            error = exc
            raise
        finally:
            _close(token, job_token, error)
    return synchronous
