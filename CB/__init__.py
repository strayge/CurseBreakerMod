import httpx
from typing import Any, TypeVar, override
from collections.abc import Callable
from collections.abc import Generator

__version__ = '5.0.0'
__license__ = 'GPLv3'
__copyright__ = '2019-2025, Paweł Jastrzębski <pawelj@iosphe.re>'
__docformat__ = 'restructuredtext en'

T = TypeVar('T')


def retry(custom_error: str | bool = False) -> Callable[[Callable[..., T]], Callable[..., T]]:
    def wraps(func: Callable[..., T]) -> Callable[..., T]:
        def inner(*args: Any, **kwargs: Any) -> T:
            description: str | None = None
            for _ in range(2):
                try:
                    result = func(*args, **kwargs)
                except KeyboardInterrupt:
                    raise
                except Exception as e:
                    description = str(e).replace('Failed to parse addon data: ', '')
                    continue
                else:
                    return result
            if custom_error:
                raise RuntimeError(custom_error) from None
            elif description:
                raise RuntimeError(f'Failed to parse addon data: {description}') from None
            else:
                raise RuntimeError(
                    'Unknown error during parsing addon data. '
                    'There may be some issue with the website.'
                ) from None
        return inner
    return wraps


class APIAuth(httpx.Auth):
    def __init__(self, header: str, token: str) -> None:
        self.header: str = header
        self.token: str = token

    @override
    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        if self.token != '':
            request.headers['Authorization'] = f'{self.header} {self.token}'
        yield request
