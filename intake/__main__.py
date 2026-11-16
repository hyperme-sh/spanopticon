"""Private listener entry point; TLS is owned by HAProxy at the edge."""

import ipaddress
import json
import os
import re
import subprocess
import uvicorn


def bind_address(address, interface=None):
    ip = ipaddress.ip_address(address)
    if ip.is_loopback:
        return str(ip)
    if not interface or not re.fullmatch(r"[A-Za-z0-9_-]{1,15}", interface):
        raise ValueError("Loopback or a verified WireGuard interface is required")
    links = json.loads(
        subprocess.check_output(
            ["ip", "-details", "-json", "address", "show", "dev", interface]
        )
    )
    if len(links) != 1 or links[0].get("linkinfo", {}).get("info_kind") != "wireguard":
        raise ValueError("Configured interface is not WireGuard")
    if str(ip) not in [a["local"] for a in links[0]["addr_info"]]:
        raise ValueError("Address does not belong to WireGuard")
    return str(ip)


if __name__ == "__main__":
    address = bind_address(
        os.environ.get("INTAKE_BIND_ADDRESS", "127.0.0.1"),
        os.environ.get("INTAKE_WIREGUARD_INTERFACE"),
    )
    uvicorn.run(
        "intake.service:app",
        host=address,
        port=8086,
        access_log=False,
        proxy_headers=False,
    )
