"""Dependency-free Python client for the minimum v1 tenant resource."""
import json
from urllib.request import Request, urlopen
from uuid import UUID


def get_tenant(origin, tenant_id, token):
    url = f"{origin.rstrip('/')}/v1/tenants/{UUID(str(tenant_id))}"
    request = Request(url, headers={"Authorization": "Bearer " + token})
    with urlopen(request, timeout=10) as response:
        return json.load(response)
