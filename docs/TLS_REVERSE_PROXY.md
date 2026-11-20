# TLS reverse proxy guide

This guide describes placing a TLS-terminating reverse proxy in front of the API. The examples are starting points and are **NOT TESTED in this repository**.

## Why terminate TLS at the proxy

TLS protects bearer credentials and API data while they cross the network. The browser session cookie is HttpOnly; set `ANZMA_COOKIE_SECURE=true` when the browser reaches the API through HTTPS so browsers send that cookie only over secure connections. Keep the proxy-to-API network private and restricted as well.

## Proxy and network requirements

- Forward Socket.IO's WebSocket upgrade (`Upgrade` and `Connection`) and keep the connection open for the configured idle/read timeout. Socket.IO can also use its polling transport; allow the normal HTTP requests it makes.
- Forward the `Authorization` request header unchanged for bearer-authenticated API clients.
- Pass the upstream `Retry-After` response header through unchanged. Clients use it when a source receives `429 RATE_LIMITED`.
- Enforce a request-body limit of **5 MiB** (5 × 1024 × 1024 bytes), matching `BATCH_TOO_LARGE` in [`INGESTION_CONTRACT.md`, §10](INGESTION_CONTRACT.md#10-errors-and-retry-behavior). The batch record-count limit is separate.
- Set upstream connect/read/send timeouts to accommodate normal dashboard calls and long-lived Socket.IO connections. Ensure WebSocket idle timeout/keepalive settings do not close healthy connections; choose values for the deployment and verify them under its network conditions.
- Do not publish PostgreSQL or Ollama ports to untrusted networks. The repository Compose defaults bind those host ports to loopback; keep them private if placing a proxy on another host. Publish only the proxy's HTTPS listener as required by the site.
- Serve `/health` as liveness and `/ready` as database readiness. Both endpoints are public in the current application; restrict them at the edge if the deployment requires that.
- Consider HSTS only after HTTPS is correctly available for every covered hostname. Start with a deliberate `max-age`; add `includeSubDomains` or preload only when the operator has verified that all affected subdomains are HTTPS-ready.

### Client IP and trusted proxy headers

The ingestion rate limiter runs after successful source authentication and is keyed by the parsed source key prefix, not by client IP; see [`app/main.py`](../app/main.py) and [`app/services/rate_limit.py`](../app/services/rate_limit.py). Forwarding `X-Forwarded-For` therefore does not change ingestion rate-limit buckets.

The repository starts Uvicorn with `uvicorn app.main:asgi_app --host 0.0.0.0 --port 8000` in [`deploy/compose/Dockerfile`](../deploy/compose/Dockerfile), without explicit proxy-header flags. The pinned Uvicorn version is 0.34.0 (`requirements.txt`); its inspected defaults enable proxy headers but trust only `127.0.0.1` (`forwarded_allow_ips`). A reverse proxy in a separate container/host will generally not have that address, so forwarded client-IP/proto headers will not be trusted unless the deployment explicitly configures an appropriate trusted proxy IP/range. Configure that trust narrowly and ensure untrusted clients cannot reach the API directly or spoof forwarded headers. This guide does not change the repository's Uvicorn startup configuration.

## Caddy example

**NOT TESTED in this repository.** Replace `api.example.test` and the upstream address for the deployment. Caddy's `reverse_proxy` supports WebSocket upgrades and forwards request/response headers, including `Authorization` and `Retry-After`; this example explicitly sets the body cap and HSTS response header.

```caddyfile
api.example.test {
	request_body {
		max_size 5MB
	}
	header Strict-Transport-Security "max-age=31536000"
	reverse_proxy 127.0.0.1:8000 {
		transport http {
			read_timeout 75s
			write_timeout 75s
		}
	}
}
```

Use a timeout suitable for the deployment and verify Caddy's version and directive support before rollout. If the proxy and API are on separate hosts, replace the loopback upstream and restrict its network path.

## nginx example

**NOT TESTED in this repository.** Place the `map` in the `http` context and the `server` in the appropriate `http` configuration. Replace the hostname and certificate paths. The 5m nginx body-size suffix is 5 MiB.

```nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

server {
    listen 443 ssl;
    server_name api.example.test;

    ssl_certificate     /etc/ssl/certs/api.example.test.pem;
    ssl_certificate_key /etc/ssl/private/api.example.test.key;
    client_max_body_size 5m;
    add_header Strict-Transport-Security "max-age=31536000" always;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header Authorization $http_authorization;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_connect_timeout 5s;
        proxy_read_timeout 75s;
        proxy_send_timeout 75s;
    }
}
```

nginx forwards upstream response headers by default; do not configure `proxy_hide_header Retry-After`. Confirm TLS policy, timeouts, forwarded-header trust, direct API reachability, and health-check exposure for the actual deployment before use.

## Verification checklist for the operator

1. Set `ANZMA_COOKIE_SECURE=true` and serve the dashboard/API through HTTPS.
2. Confirm a batch slightly over 5 MiB is rejected by the application and the proxy accepts a valid batch up to the intended limit.
3. Confirm Socket.IO connects and reconnects through the proxy, including WebSocket upgrade.
4. Confirm bearer requests retain `Authorization` and a rate-limited response retains `Retry-After`.
5. Check `/health` and `/ready`; confirm PostgreSQL and Ollama are not reachable from untrusted networks.
6. Confirm the proxy's forwarded headers are trusted only from the intended proxy addresses. Ingestion rate limits remain keyed by source credential, independently of client IP.
