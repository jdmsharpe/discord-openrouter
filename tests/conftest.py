import sys
from types import ModuleType


try:
    import httpx  # noqa: F401
except ModuleNotFoundError:
    fake_httpx = ModuleType("httpx")

    class Timeout:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    class AsyncClient:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, *args, **kwargs):
            raise RuntimeError("httpx AsyncClient.get was called without a test stub.")

        def stream(self, *args, **kwargs):
            raise RuntimeError("httpx AsyncClient.stream was called without a test stub.")

    fake_httpx.AsyncClient = AsyncClient
    fake_httpx.Timeout = Timeout
    sys.modules["httpx"] = fake_httpx
