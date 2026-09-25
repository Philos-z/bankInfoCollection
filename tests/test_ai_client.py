"""Offline tests for proxy retry policy."""

import unittest

import httpx2 as httpx
from openai import APIConnectionError, APIStatusError

import ai_client


class RetryPolicyTests(unittest.TestCase):
    def test_connection_error_is_retryable(self):
        request = httpx.Request("POST", "http://proxy.test/v1/chat/completions")
        self.assertTrue(ai_client._retryable_http_error(APIConnectionError(request=request)))

    def test_server_error_is_retryable_but_client_error_is_not(self):
        request = httpx.Request("POST", "http://proxy.test/v1/chat/completions")
        server_response = httpx.Response(503, request=request)
        client_response = httpx.Response(400, request=request)
        self.assertTrue(ai_client._retryable_http_error(
            APIStatusError("server", response=server_response, body=None)
        ))
        self.assertFalse(ai_client._retryable_http_error(
            APIStatusError("client", response=client_response, body=None)
        ))


if __name__ == "__main__":
    unittest.main()
