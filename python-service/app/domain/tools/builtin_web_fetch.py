import asyncio
import html
import re
import socket
import ssl
import urllib.parse
import urllib.request
import urllib.error
from ipaddress import ip_address, ip_network
from typing import Any

PRIVATE_NETWORKS = (
    ip_network("127.0.0.0/8"),
    ip_network("10.0.0.0/8"),
    ip_network("172.16.0.0/12"),
    ip_network("192.168.0.0/16"),
    ip_network("169.254.0.0/16"),
    ip_network("::1/128"),
    ip_network("fc00::/7"),
    ip_network("fe80::/10"),
)


def _is_private_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return True

    for info in infos:
        addr = info[4][0]
        try:
            ip_obj = ip_address(addr)
        except ValueError:
            return True
        if any(ip_obj in cidr for cidr in PRIVATE_NETWORKS):
            return True
    return False


def _clean_html_text(raw_html: str) -> str:
    content = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", raw_html)
    content = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", content)
    content = re.sub(r"(?is)<noscript[^>]*>.*?</noscript>", " ", content)
    content = re.sub(r"(?is)<[^>]+>", " ", content)
    content = html.unescape(content)
    content = re.sub(r"\s+", " ", content).strip()
    return content


def _extract_title(raw_html: str) -> str:
    match = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw_html)
    if not match:
        return ""
    return html.unescape(re.sub(r"\s+", " ", match.group(1))).strip()


def _fetch_sync(url: str, timeout_sec: int, max_chars: int) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise ValueError("仅支持 http/https 链接")
    if not parsed.netloc:
        raise ValueError("URL 缺少主机名")
    if _is_private_host(parsed.hostname or ""):
        raise ValueError("禁止访问内网/本地地址")

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            )
        },
        method="GET",
    )
    try:
        response = urllib.request.urlopen(request, timeout=timeout_sec)
    except urllib.error.URLError as exc:
        # 开发环境下兼容自签证书站点，失败后降级关闭证书校验重试一次。
        if parsed.scheme != "https":
            raise
        if "CERTIFICATE_VERIFY_FAILED" not in str(exc):
            raise
        insecure_context = ssl.create_default_context()
        insecure_context.check_hostname = False
        insecure_context.verify_mode = ssl.CERT_NONE
        response = urllib.request.urlopen(request, timeout=timeout_sec, context=insecure_context)

    with response:
        raw = response.read(2 * 1024 * 1024)
        charset = "utf-8"
        content_type = response.headers.get("content-type", "")
        match = re.search(r"charset=([a-zA-Z0-9._-]+)", content_type)
        if match:
            charset = match.group(1).lower()
        html_text = raw.decode(charset, errors="ignore")

    title = _extract_title(html_text)
    body_text = _clean_html_text(html_text)
    excerpt = body_text[:max_chars]
    return {
        "url": url,
        "title": title,
        "excerpt": excerpt,
        "contentLength": len(body_text),
        "capturedChars": len(excerpt),
    }


async def fetch_and_extract_webpage(
    url: str,
    *,
    timeout_sec: int = 12,
    max_chars: int = 12000,
) -> dict[str, Any]:
    safe_max_chars = max(400, min(int(max_chars), 50000))
    safe_timeout_sec = max(3, min(int(timeout_sec), 30))
    return await asyncio.to_thread(_fetch_sync, url, safe_timeout_sec, safe_max_chars)
