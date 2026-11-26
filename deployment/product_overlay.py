"""Validate data-only product declarations before environment binding."""
import re


def require(condition, message):
    if not condition:
        raise ValueError(message)


def fields(value, expected, label):
    require(isinstance(value, dict) and set(value) == set(expected), f"Invalid {label} fields")


def choices(value, allowed, label):
    require(
        isinstance(value, list) and all(isinstance(item, str) for item in value)
        and len(set(value)) == len(value) and set(value) <= allowed,
        f"Invalid or duplicate {label}",
    )


def validate_tenant_version(value):
    part = r"(0|[1-9][0-9]{0,8})"
    require(isinstance(value, str) and re.fullmatch(rf"{part}\.{part}\.{part}", value),
            "Expected tenant_version as MAJOR.MINOR.PATCH")


def validate(overlay):
    require(isinstance(overlay, dict), "Expected product overlay object")
    expected = {"schema_version", "product", "display_name", "plane_overlay", "services"}
    fields(overlay, expected | ({"login", "tenant_version"} & overlay.keys()), "product overlay")
    require(type(overlay["schema_version"]) is int and overlay["schema_version"] == 1,
            "Unsupported product overlay schema version")
    for field, pattern in (("product", r"[a-z0-9]+(?:[.-][a-z0-9]+)*"),
                           ("plane_overlay", r"[a-z0-9]+(?:-[a-z0-9]+)*")):
        require(isinstance(overlay[field], str) and re.fullmatch(pattern, overlay[field]), f"Invalid {field}")
    name = overlay["display_name"]
    require(isinstance(name, str) and 0 < len(name) <= 95 and name == name.strip()
            and all(ord(char) >= 32 and char not in "<>" for char in name), "Invalid product display name")

    if "tenant_version" in overlay:
        validate_tenant_version(overlay["tenant_version"])

    services = overlay["services"]
    choices(services, {"plane", "keycloak"}, "overlay service")
    require("plane" in services and ("keycloak" in services) == ("login" in overlay),
            "Overlay services differ from login declaration")
    if "login" not in overlay:
        return
    login = overlay["login"]
    require(isinstance(login, dict), "Expected product login object")
    kind = login.get("kind")
    require(kind in ("portal", "shared-issuer"), "Unsupported product login kind")
    specific = "show_tenant_picker" if kind == "portal" else "options"
    fields(login, {"kind", "providers", specific}, "product login")
    providers = login["providers"]
    choices(providers, {"microsoft", "google", "github"}, "login provider")
    require(bool(providers), "Expected at least one login provider")
    if kind == "portal":
        require(type(login[specific]) is bool, "Expected boolean show_tenant_picker")
    else:
        choices(login[specific], {"siwe", *providers}, "login option")
        require(set(login[specific]) == {"siwe", *providers}, "Missing wallet or broker login option")
