"""Shared application policy for externally supplied ontology IRIs."""

from __future__ import annotations

import re
from ipaddress import IPv6Address
from urllib.parse import urlsplit


_ABSOLUTE_IRI = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:.+$", re.DOTALL)
_INVALID_RAW_IRI_CHARACTERS = frozenset('<>"{}|^`')
_UNRESERVED = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
_USERINFO_RAW = _UNRESERVED | frozenset("!$&'()*+,;=:")
_URN_PCHAR_RAW = _UNRESERVED | frozenset("!$&'()*+,;=:@")
_URN_NSS_RAW = _URN_PCHAR_RAW | frozenset("/")
_URN_COMPONENT_RAW = _URN_NSS_RAW | frozenset("?")
_URN_NID = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,30}[A-Za-z0-9])")
_RFC3987_UCSCHAR_RANGES = (
    (0x00A0, 0xD7FF),
    (0xF900, 0xFDCF),
    (0xFDF0, 0xFFEF),
    *((plane << 16, (plane << 16) + 0xFFFD) for plane in range(1, 14)),
    (0xE1000, 0xEFFFD),
)
_SUPPORTED_SCHEMES = frozenset({"http", "https", "urn"})


def _valid_pct_component(value: str, allowed_raw: frozenset[str]) -> bool:
    """Validate an ASCII component made from allowed raw or percent-encoded octets."""
    if not value:
        return False
    index = 0
    while index < len(value):
        if value[index] in allowed_raw:
            index += 1
        elif (value[index] == "%" and index + 2 < len(value)
              and all(character in "0123456789abcdefABCDEF"
                      for character in value[index + 1:index + 3])):
            index += 3
        else:
            return False
    return True


def _valid_ascii_iri_characters(value: str) -> bool:
    """Reject ASCII whitespace and controls independently from Unicode policy."""
    return all(codepoint >= 0x21 and codepoint != 0x7F
               for character in value
               if (codepoint := ord(character)) < 0x80)


def _valid_raw_ucschar(value: str) -> bool:
    """Allow raw non-ASCII only from RFC3987 ``ucschar`` ranges."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    for character in value:
        codepoint = ord(character)
        if codepoint >= 0x80 and not any(
                start <= codepoint <= end
                for start, end in _RFC3987_UCSCHAR_RANGES):
            return False
    return True


def _sanitized_ip_literal(literal: str) -> str | None:
    """Validate an IPv6/IPvFuture literal and return a parser-safe equivalent."""
    if re.fullmatch(r"[vV][0-9A-Fa-f]+\.[A-Za-z0-9._~!$&'()*+,;=:-]+", literal):
        return f"v{literal[1:]}"
    address = literal
    if "%" in literal:
        address, separator, zone = literal.partition("%25")
        if (not separator or not zone or "%" in address or "%25" in zone
                or not _valid_pct_component(zone, _UNRESERVED)):
            return None
    try:
        IPv6Address(address)
    except ValueError:
        return None
    return address


def _sanitize_http_authority(value: str) -> str | None:
    """Enforce the application-safe HTTP authority and ZoneID policy."""
    scheme_end = value.find(":")
    if scheme_end < 0 or value[scheme_end + 1:scheme_end + 3] != "//":
        return None
    authority_start = scheme_end + 3
    suffix_start = min(
        (position for marker in "/?#"
         if (position := value.find(marker, authority_start)) >= 0),
        default=len(value),
    )
    authority = value[authority_start:suffix_start]
    suffix = value[suffix_start:]
    if (not authority or authority.count("@") > 1
            or "[" in suffix or "]" in suffix):
        return None
    userinfo, separator, host_port = authority.rpartition("@")
    if not separator:
        host_port = authority
    elif not _valid_pct_component(userinfo, _USERINFO_RAW):
        return None
    if host_port.endswith(":"):
        return None

    if "[" in authority or "]" in authority:
        match = re.fullmatch(r"\[([^\[\]]+)\](?::([0-9]+))?", host_port)
        if not match or "[" in userinfo or "]" in userinfo:
            return None
        sanitized_literal = _sanitized_ip_literal(match.group(1))
        if sanitized_literal is None:
            return None
        port = f":{match.group(2)}" if match.group(2) else ""
        safe_host_port = f"[{sanitized_literal}]{port}"
    else:
        safe_host_port = host_port
    safe_authority = f"{userinfo}@{safe_host_port}" if separator else safe_host_port
    return f"{value[:authority_start]}{safe_authority}{suffix}"


def _valid_urn_component(
        value: str, allowed_raw: frozenset[str], *, allow_empty: bool = False,
        first_allowed_raw: frozenset[str] | None = None) -> bool:
    """Validate one RFC 8141 component after global Unicode screening."""
    if not value:
        return allow_empty
    index = 0
    while index < len(value):
        character = value[index]
        raw_characters = (
            first_allowed_raw
            if index == 0 and first_allowed_raw is not None
            else allowed_raw
        )
        if ord(character) >= 0x80 or character in raw_characters:
            index += 1
        elif (character == "%" and index + 2 < len(value)
              and all(item in "0123456789abcdefABCDEF"
                      for item in value[index + 1:index + 3])):
            index += 3
        else:
            return False
    return True


def _valid_urn(value: str) -> bool:
    """Validate the RFC 8141 NID, NSS, optional r/q components, and fragment."""
    assigned_name = value.split(":", 1)[1]
    nid, separator, tail = assigned_name.partition(":")
    if not separator or not _URN_NID.fullmatch(nid):
        return False

    body, fragment_separator, fragment = tail.partition("#")
    if (fragment_separator
            and not _valid_urn_component(
                fragment, _URN_COMPONENT_RAW, allow_empty=True)):
        return False

    question = body.find("?")
    nss = body if question < 0 else body[:question]
    suffix = "" if question < 0 else body[question:]
    if not _valid_urn_component(
            nss, _URN_NSS_RAW, first_allowed_raw=_URN_PCHAR_RAW):
        return False
    if not suffix:
        return True

    r_component = q_component = None
    if suffix.startswith("?+"):
        remainder = suffix[2:]
        q_separator = remainder.find("?=")
        if q_separator >= 0:
            r_component = remainder[:q_separator]
            q_component = remainder[q_separator + 2:]
        else:
            r_component = remainder
    elif suffix.startswith("?="):
        q_component = suffix[2:]
    else:
        return False
    return all(
        _valid_urn_component(
            component, _URN_COMPONENT_RAW, first_allowed_raw=_URN_PCHAR_RAW)
        for component in (r_component, q_component)
        if component is not None
    )


def valid_application_iri(value) -> bool:
    """Return whether ``value`` is an application-safe HTTP(S) or URN IRI."""
    if not isinstance(value, str):
        return False
    if (not _ABSOLUTE_IRI.fullmatch(value)
            or "\\" in value
            or any(character in _INVALID_RAW_IRI_CHARACTERS for character in value)
            or value.count("#") > 1
            or not _valid_ascii_iri_characters(value)
            or not _valid_raw_ucschar(value)
            or re.search(r"%(?![0-9A-Fa-f]{2})", value)):
        return False
    scheme = value.split(":", 1)[0].lower()
    if scheme not in _SUPPORTED_SCHEMES:
        return False
    parsed_value = value
    if scheme in {"http", "https"}:
        parsed_value = _sanitize_http_authority(value)
        if parsed_value is None:
            return False
    elif scheme == "urn":
        return _valid_urn(value)
    elif "[" in value or "]" in value:
        return False
    try:
        parsed = urlsplit(parsed_value)
        if parsed.scheme.lower() in {"http", "https"}:
            parsed.port
            return bool(parsed.hostname)
    except ValueError:
        return False
    return bool(parsed.scheme and parsed.path)
