import random
from types import SimpleNamespace
from typing import Tuple

import pytest
from aiohttp import ClientResponseError, hdrs
from aiohttp import ClientSession
from aiohttp import TraceConfig
from aiohttp import TraceRequestStartParams

from aiohttp_retry import RetryClient, ExponentialRetry, RandomRetry, ListRetry
from tests.app import App


async def get_retry_client_and_test_app_for_test(
    aiohttp_client,
    raise_for_status: bool = False,
    *args, **kwargs
) -> Tuple[RetryClient, App]:
    test_app = App()
    app = test_app.web_app()

    retry_client = RetryClient(*args, **kwargs)
    retry_client._client = await aiohttp_client(app, raise_for_status=raise_for_status)

    return retry_client, test_app


async def test_hello(aiohttp_client, loop):
    retry_client, test_app = await get_retry_client_and_test_app_for_test(aiohttp_client)
    async with retry_client.get('/ping') as response:
        text = await response.text()
        assert response.status == 200
        assert text == 'Ok!'

        assert test_app.counter == 1

    await retry_client.close()


async def test_hello_by_request(aiohttp_client, loop):
    retry_client, test_app = await get_retry_client_and_test_app_for_test(aiohttp_client)
    async with retry_client.request(method=hdrs.METH_GET, url='/ping') as response:
        text = await response.text()
        assert response.status == 200
        assert text == 'Ok!'

        assert test_app.counter == 1

    await retry_client.close()


async def test_hello_with_context(aiohttp_client, loop):
    test_app = App()
    app = test_app.web_app()
    client = await aiohttp_client(app)
    async with RetryClient() as retry_client:
        retry_client._client = client
        async with retry_client.get('/ping') as response:
            text = await response.text()
            assert response.status == 200
            assert text == 'Ok!'

            assert test_app.counter == 1


async def test_internal_error(aiohttp_client, loop):
    retry_client, test_app = await get_retry_client_and_test_app_for_test(aiohttp_client)
    retry_options = ExponentialRetry(attempts=5)
    async with retry_client.get('/internal_error', retry_options) as response:
        assert response.status == 500
        assert test_app.counter == 5

    await retry_client.close()


async def test_not_found_error(aiohttp_client, loop):
    retry_client, test_app = await get_retry_client_and_test_app_for_test(aiohttp_client)
    retry_options = ExponentialRetry(attempts=5, statuses={404})
    async with retry_client.get('/not_found_error', retry_options) as response:
        assert response.status == 404
        assert test_app.counter == 5

    await retry_client.close()


async def test_sometimes_error(aiohttp_client, loop):
    retry_client, test_app = await get_retry_client_and_test_app_for_test(aiohttp_client)
    retry_options = ExponentialRetry(attempts=5)
    async with retry_client.get('/sometimes_error', retry_options) as response:
        text = await response.text()
        assert response.status == 200
        assert text == 'Ok!'

        assert test_app.counter == 3

    await retry_client.close()


async def test_sometimes_error_with_raise_for_status(aiohttp_client, loop):
    retry_client, test_app = await get_retry_client_and_test_app_for_test(aiohttp_client, raise_for_status=True)
    retry_options = ExponentialRetry(attempts=5, exceptions={ClientResponseError})
    async with retry_client.get('/sometimes_error', retry_options) \
            as response:
        text = await response.text()
        assert response.status == 200
        assert text == 'Ok!'

        assert test_app.counter == 3

    await retry_client.close()


async def test_override_options(aiohttp_client, loop):
    retry_client, test_app = await get_retry_client_and_test_app_for_test(
        aiohttp_client,
        retry_options=ExponentialRetry(attempts=1)
    )
    retry_options = ExponentialRetry(attempts=5)
    async with retry_client.get('/sometimes_error', retry_options) as response:
        text = await response.text()
        assert response.status == 200
        assert text == 'Ok!'

        assert test_app.counter == 3

    await retry_client.close()


async def test_hello_awaitable(aiohttp_client, loop):
    retry_client, test_app = await get_retry_client_and_test_app_for_test(aiohttp_client)
    response = await retry_client.get('/ping')
    text = await response.text()
    assert response.status == 200
    assert text == 'Ok!'

    assert test_app.counter == 1

    await retry_client.close()


async def test_add_trace_request_ctx(aiohttp_client, loop):
    actual_request_contexts = []

    async def on_request_start(
        _: ClientSession,
        trace_config_ctx: SimpleNamespace,
        __: TraceRequestStartParams,
    ) -> None:
        actual_request_contexts.append(trace_config_ctx)

    test_app = App()

    trace_config = TraceConfig()
    trace_config.on_request_start.append(on_request_start)  # type: ignore

    retry_client = RetryClient()
    retry_client._client = await aiohttp_client(
        test_app.web_app(),
        trace_configs=[trace_config]
    )

    async with retry_client.get('/sometimes_error', trace_request_ctx={'foo': 'bar'}):
        assert test_app.counter == 3

    assert actual_request_contexts == [
        SimpleNamespace(
            trace_request_ctx={
                'foo': 'bar',
                'current_attempt': i + 1,
            },
        )
        for i in range(3)
    ]


def test_retry_timeout_exponential_backoff():
    retry = ExponentialRetry(attempts=10)
    timeouts = [retry.get_timeout(x) for x in range(10)]
    assert timeouts == [0.1, 0.2, 0.4, 0.8, 1.6, 3.2, 6.4, 12.8, 25.6, 30.0]


def test_retry_timeout_random():
    retry = RandomRetry(attempts=10, random_func=random.Random(0).random)
    timeouts = [round(retry.get_timeout(x), 2) for x in range(10)]
    assert timeouts == [2.55, 2.3, 1.32, 0.85, 1.58, 1.27, 2.37, 0.98, 1.48, 1.79]


def test_retry_timeout_list():
    expected = [1.2, 2.1, 3.4, 4.3, 4.5, 5.4, 5.6, 6.5, 6.7, 7.6]
    retry = ListRetry(expected)
    timeouts = [retry.get_timeout(x) for x in range(10)]
    assert timeouts == expected


@pytest.mark.parametrize("attempts", [2, 3])
async def test_change_urls_in_request(aiohttp_client, loop, attempts):
    retry_client, test_app = await get_retry_client_and_test_app_for_test(
        aiohttp_client,
        retry_options=ExponentialRetry(attempts=attempts)
    )
    async with retry_client.get(url=['/internal_error', '/ping']) as response:
        text = await response.text()
        assert response.status == 200
        assert text == 'Ok!'

        assert test_app.counter == 2

    await retry_client.close()


@pytest.mark.parametrize("attempts", [2, 3])
async def test_change_urls_as_tuple_in_request(aiohttp_client, loop, attempts):
    retry_client, test_app = await get_retry_client_and_test_app_for_test(
        aiohttp_client,
        retry_options=ExponentialRetry(attempts=attempts)
    )
    async with retry_client.get(url=('/internal_error', '/ping')) as response:
        text = await response.text()
        assert response.status == 200
        assert text == 'Ok!'

        assert test_app.counter == 2

    await retry_client.close()


@pytest.mark.parametrize("url", [{"/ping", "/internal_error"}, []])
async def test_pass_bad_urls(aiohttp_client, loop, url):
    retry_client, test_app = await get_retry_client_and_test_app_for_test(aiohttp_client)
    with pytest.raises(ValueError):
        async with retry_client.get(url=url):
            pass

    await retry_client.close()


@pytest.mark.parametrize("url, method", [
    ("/options_handler", 'options'),
    ("/head_handler", 'head'),
    ("/post_handler", 'post'),
    ("/put_handler", 'put'),
    ("/patch_handler", 'patch'),
    ("/delete_handler", 'delete'),
])
async def test_hello(aiohttp_client, loop, url, method):
    retry_client, _ = await get_retry_client_and_test_app_for_test(aiohttp_client)
    method_func = getattr(retry_client, method)
    async with method_func(url) as response:
        assert response.method.lower() == method

    await retry_client.close()


async def test_not_found_error_with_retry_client_raise_for_status(aiohttp_client, loop):
    test_app = App()
    app = test_app.web_app

    client = await aiohttp_client(app)
    retry_client = RetryClient(raise_for_status=True)
    retry_client._client = client

    retry_options = ExponentialRetry(attempts=5, statuses={404})
    override_response = retry_client.get('/not_found_error', retry_options, raise_for_status=False)
    assert not override_response._raise_for_status
    response = retry_client.get('/not_found_error', retry_options)
    assert response._raise_for_status

    try:
        async with response:
            pass
    except ClientResponseError as exc:
        assert exc.status == 404
        assert test_app.counter == 5
    else:
        raise AssertionError('Expected ClientResponseError not raised')

    await retry_client.close()
    await client.close()


async def test_request(aiohttp_client, loop):
    retry_client, test_app = await get_retry_client_and_test_app_for_test(aiohttp_client)
    async with retry_client.request(hdrs.METH_GET, '/ping') as response:
        text = await response.text()
        assert response.status == 200
        assert text == 'Ok!'

        assert test_app.counter == 1

    await retry_client.close()
