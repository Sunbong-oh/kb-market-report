"""KB증권 OpenAPI(B2C) 비동기 클라이언트.

kbsecurities/kb-openapi 의 example/python (common.py, auth_example.py,
investment_info_example.py) 요청 방식을 그대로 따른다.

    POST {base_url}/oauth2/token          -> access_token (expires_in 초, 기본 86400)
    POST {base_url}/api/v1/{tr코드 소문자} -> Headers: appKey, Authorization: bearer <token>
    Body: {"dataHeader": {"ipAddr": "", "macAddr": ""}, "dataBody": {...}}

샘플 코드는 매 호출마다 토큰을 새로 발급하지만, 여기서는 README 권고대로
만료 시각까지 캐싱하고, 같은 TR·같은 조건 호출도 짧게(TTL) 캐싱해 운영 서버
과다 호출을 막는다.
"""

from __future__ import annotations

import asyncio
import json
import socket
import time
import uuid
from typing import Any

import httpx

from . import demo
from .config import Settings

def _local_ip_addr() -> str:
    # UDP connect는 패킷을 보내지 않고 외부로 나가는 인터페이스 IP만 알아낸다
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def _local_mac_addr() -> str:
    node = uuid.getnode()
    return ":".join(f"{(node >> shift) & 0xFF:02X}" for shift in range(40, -1, -8))


# 샘플 README는 비워도 된다고 하지만 운영 서버는 빈 ipAddr을 거절한다
# ("입력 전문 [dataHeader.ipAddr]을 확인해 주세요."). 공식 백엔드
# (backend/services/openapi_test_defaults.py)처럼 로컬 IP/MAC을 채운다.
DATA_HEADER = {"ipAddr": _local_ip_addr(), "macAddr": _local_mac_addr()}


class KBApiError(RuntimeError):
    pass


class KBClient:
    def __init__(self, settings: Settings):
        self.s = settings
        self._http = httpx.AsyncClient(base_url=settings.base_url, timeout=10)
        self._token: str | None = None
        self._token_exp = 0.0
        self._token_lock = asyncio.Lock()
        self._cache: dict[str, tuple[float, dict]] = {}

    async def close(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ auth
    async def _issue_token(self) -> None:
        body = {
            "dataHeader": DATA_HEADER,
            "dataBody": {
                "appKey": self.s.app_key,
                "appSecret": self.s.app_secret,
                "grantType": "client_credentials",
            },
        }
        res = await self._http.post("/oauth2/token", json=body, headers={"Content-Type": "application/json"})
        if res.status_code != 200:
            raise KBApiError(f"토큰 발급 실패 (HTTP {res.status_code}): {res.text[:200]}")
        payload = res.json()
        data = payload.get("dataBody", payload)
        token = data.get("access_token") or data.get("accessToken")
        if not token:
            raise KBApiError(f"토큰 발급 응답에 access_token이 없습니다: {str(payload)[:200]}")
        expires_in = float(data.get("expires_in") or 86400)
        self._token = token
        # 만료 5분 전에 미리 갱신
        self._token_exp = time.time() + max(expires_in - 300, 60)

    async def token(self, force: bool = False) -> str:
        async with self._token_lock:
            if force or not self._token or time.time() >= self._token_exp:
                await self._issue_token()
            return self._token  # type: ignore[return-value]

    # ------------------------------------------------------------------- TR
    async def call(self, tr_code: str, data_body: dict[str, Any] | None = None, ttl: float | None = None) -> dict:
        """TR을 호출하고 dataBody를 반환한다."""
        data_body = data_body or {}
        ttl = self.s.cache_ttl if ttl is None else ttl
        key = tr_code + json.dumps(data_body, sort_keys=True)
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]

        if self.s.demo_mode:
            result = demo.response(tr_code, data_body)
        else:
            result = await self._call_remote(tr_code, data_body)
        self._cache[key] = (time.time(), result)
        return result

    async def _call_remote(self, tr_code: str, data_body: dict[str, Any]) -> dict:
        endpoint = f"/api/v1/{tr_code.lower()}"
        body = {"dataHeader": DATA_HEADER, "dataBody": data_body}
        for attempt in range(2):
            headers = {
                "Content-Type": "application/json",
                "appKey": self.s.app_key,
                "Authorization": f"bearer {await self.token(force=attempt > 0)}",
            }
            res = await self._http.post(endpoint, json=body, headers=headers)
            if res.status_code == 401 and attempt == 0:
                continue  # 토큰 만료 → 재발급 후 1회 재시도
            if res.status_code != 200:
                raise KBApiError(f"{tr_code} 호출 실패 (HTTP {res.status_code}): {res.text[:200]}")
            try:
                payload = res.json()
            except json.JSONDecodeError as exc:
                raise KBApiError(f"{tr_code}: JSON이 아닌 응답 {res.text[:200]!r}") from exc
            # KB는 업무 오류도 200으로 내려주는 경우가 있어 dataBody 존재 여부를 확인한다
            data = payload.get("dataBody")
            if not isinstance(data, dict):
                header = payload.get("dataHeader") or {}
                msg = header.get("processMessage") or str(payload)[:200]
                raise KBApiError(f"{tr_code}: {msg} (processCode {header.get('processCode', '-')})")
            return data
        raise KBApiError(f"{tr_code}: 인증 실패")
