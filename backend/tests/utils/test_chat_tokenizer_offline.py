"""Chat history's token counter must never fetch its encoding.

tiktoken.get_encoding("cl100k_base") downloads the file from
openaipublic.blob.core.windows.net whenever its temp-dir cache is empty, as it
is after every reboot. The counter reads the copy LlamaIndex ships instead.
The test runs with non-loopback lookups and connects refused, and on a cold
tiktoken cache.
"""
import socket
import tempfile

import pytest


@pytest.fixture
def no_network(monkeypatch):
    attempts = []
    real_connect = socket.socket.connect
    real_getaddrinfo = socket.getaddrinfo

    def local(host):
        return str(host) in ("localhost", "::1") or str(host).startswith("127.")

    def getaddrinfo(host, *args, **kwargs):
        if not local(host):
            attempts.append(host)
            raise OSError(f"test blocked lookup of {host}")
        return real_getaddrinfo(host, *args, **kwargs)

    def connect(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and not local(address[0]):
            attempts.append(address)
            raise OSError(f"test blocked connect to {address}")
        return real_connect(self, address)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", connect)
    yield attempts
    assert attempts == [], f"reached for the network: {attempts}"


def test_chat_token_count_reads_the_bundled_encoding(no_network, monkeypatch, tmp_path):
    import llama_index.core
    import tiktoken

    # Cold cache: tiktoken's default cache lives under tempfile.gettempdir().
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.delenv("TIKTOKEN_CACHE_DIR", raising=False)
    monkeypatch.setattr(llama_index.core, "global_tokenizer", None)
    monkeypatch.setattr("tiktoken.registry.ENCODINGS", {})

    def no_get_encoding(name):
        raise AssertionError(f"tiktoken.get_encoding({name!r}) reads from the network when uncached")

    monkeypatch.setattr(tiktoken, "get_encoding", no_get_encoding)
    from backend.utils.chat_utils import _get_tokenizer_fn

    count = _get_tokenizer_fn()
    # cl100k_base: 9 tokens; the words*1.3 fallback would give 11.
    assert count("The quick brown fox jumps over the lazy dog") == 9
    assert list(tmp_path.iterdir()) == []
