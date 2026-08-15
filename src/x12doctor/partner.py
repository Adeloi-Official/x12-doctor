# SPDX-License-Identifier: Apache-2.0
"""Trading partner profiles.

A partner profile carries the rules that are not in the X12 standard but are in
the partner's implementation guide: which REF qualifiers they insist on, which
unit codes they accept, which IDs must appear in the envelope. Without one,
x12-doctor checks the standard. With one, it checks the relationship.

This is deliberately generic and is **not** a substitute for a real
implementation guide — those are proprietary. It mirrors ``definePartner()``
from ``@adeloi/edi-fixtures`` field for field, so a profile used to generate
test documents can be used unchanged to diagnose them.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any

__all__ = ["DEFAULT_PARTNER", "PartnerProfile", "ProfileError"]


class ProfileError(ValueError):
    """A partner profile could not be read or contains an unusable value."""


def _load_toml(raw: bytes, path: Path) -> dict[str, Any]:
    """Parse a TOML profile."""
    try:
        parsed: Any = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ProfileError(f"{path}: {exc}") from None

    if not isinstance(parsed, dict):
        raise ProfileError(f"{path}: expected a table at the top level")
    return parsed


@dataclass(frozen=True)
class PartnerProfile:
    """What one trading partner requires beyond the standard."""

    #: Human-readable name, used in output headers only.
    name: str | None = None
    #: Expected ISA05/ISA07 qualifier when the specific ones are unset.
    isa_qualifier: str | None = None
    #: Expected ISA05. Falls back to ``isa_qualifier``.
    sender_qualifier: str | None = None
    #: Expected ISA07. Falls back to ``isa_qualifier``.
    receiver_qualifier: str | None = None
    #: Expected ISA06, compared after stripping padding.
    sender_id: str | None = None
    #: Expected ISA08, compared after stripping padding.
    receiver_id: str | None = None
    #: REF01 qualifiers that must appear, e.g. ``["DP", "IA"]``.
    required_refs: tuple[str, ...] = ()
    #: Unit of measure codes the partner accepts. Empty means "any valid code".
    uom_whitelist: tuple[str, ...] = ()
    #: Expected ISA15: ``"P"`` for production, ``"T"`` for test.
    usage_indicator: str | None = None
    #: Expected version, e.g. ``"004010"``.
    version: str | None = None
    #: Extra segments the partner requires, keyed by transaction set:
    #: ``{"850": ["FOB", "ITD"]}``.
    required_segments: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: REF qualifiers required only for certain transaction sets:
    #: ``{"856": ["BM"]}``. Merged with ``required_refs``.
    required_refs_by_doc: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def expected_sender_qualifier(self) -> str | None:
        return self.sender_qualifier or self.isa_qualifier

    @property
    def expected_receiver_qualifier(self) -> str | None:
        return self.receiver_qualifier or self.isa_qualifier

    def refs_for(self, doc_type: str) -> tuple[str, ...]:
        """Required REF qualifiers for one transaction set, deduplicated."""
        merged = list(self.required_refs)
        for qualifier in self.required_refs_by_doc.get(doc_type, ()):
            if qualifier not in merged:
                merged.append(qualifier)
        return tuple(merged)

    def segments_for(self, doc_type: str) -> tuple[str, ...]:
        """Extra required segments for one transaction set."""
        return self.required_segments.get(doc_type, ())

    @property
    def is_empty(self) -> bool:
        """True when the profile constrains nothing."""
        return self == PartnerProfile()

    def merge(self, **overrides: Any) -> PartnerProfile:
        """Return a copy with fields replaced."""
        return replace(self, **overrides)

    # -- loading ---------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path) -> PartnerProfile:
        """Read a profile from a ``.toml`` or ``.json`` file.

        Both formats are first-class. TOML is the readable one for a profile a
        human maintains; JSON is there because partner configuration is often
        generated — emitted by a provisioning script, pulled from a config
        service, or shared with a system that has no TOML reader. The format is
        chosen by the file extension and the resulting profile is identical.
        """
        path = Path(path)
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ProfileError(f"cannot read partner profile {path}: {exc}") from None

        if path.suffix.lower() == ".json":
            try:
                data = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                raise ProfileError(f"{path}: {exc}") from None
        else:
            data = _load_toml(raw, path)

        if not isinstance(data, dict):
            raise ProfileError(f"{path}: expected a table at the top level")

        # A [partner] table is accepted so a profile can live inside a larger
        # config file without colliding with other tools' keys.
        if "partner" in data and isinstance(data["partner"], dict):
            data = data["partner"]

        return cls.from_dict(data, origin=str(path))

    @classmethod
    def from_toml(cls, path: str | Path) -> PartnerProfile:
        """Alias for :meth:`load`, kept for symmetry with the CLI flag."""
        return cls.load(path)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, origin: str = "profile") -> PartnerProfile:
        """Build a profile from a plain mapping, validating as we go."""
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(data) - known)
        if unknown:
            raise ProfileError(
                f"{origin}: unknown key{'s' if len(unknown) > 1 else ''} "
                f"{', '.join(repr(k) for k in unknown)}. "
                f"Known keys: {', '.join(sorted(known))}."
            )

        def text(key: str) -> str | None:
            value = data.get(key)
            if value is None:
                return None
            if not isinstance(value, str):
                raise ProfileError(f"{origin}: {key!r} must be a string")
            return value

        def text_list(key: str) -> tuple[str, ...]:
            value = data.get(key, ())
            if isinstance(value, str):
                raise ProfileError(
                    f"{origin}: {key!r} must be a list of strings, not a single string"
                )
            if not isinstance(value, (list, tuple)):
                raise ProfileError(f"{origin}: {key!r} must be a list of strings")
            for item in value:
                if not isinstance(item, str):
                    raise ProfileError(f"{origin}: {key!r} must contain only strings")
            return tuple(value)

        def text_list_map(key: str) -> dict[str, tuple[str, ...]]:
            value = data.get(key, {})
            if not isinstance(value, dict):
                raise ProfileError(
                    f"{origin}: {key!r} must be a table of transaction set to list"
                )
            result: dict[str, tuple[str, ...]] = {}
            for doc_type, items in value.items():
                if isinstance(items, str) or not isinstance(items, (list, tuple)):
                    raise ProfileError(
                        f"{origin}: {key!r}.{doc_type} must be a list of strings"
                    )
                result[str(doc_type)] = tuple(str(i) for i in items)
            return result

        usage = text("usage_indicator")
        if usage is not None and usage.upper() not in ("P", "T"):
            raise ProfileError(
                f"{origin}: 'usage_indicator' must be 'P' (production) or 'T' (test)"
            )

        return cls(
            name=text("name"),
            isa_qualifier=text("isa_qualifier"),
            sender_qualifier=text("sender_qualifier"),
            receiver_qualifier=text("receiver_qualifier"),
            sender_id=text("sender_id"),
            receiver_id=text("receiver_id"),
            required_refs=text_list("required_refs"),
            uom_whitelist=text_list("uom_whitelist"),
            usage_indicator=usage.upper() if usage else None,
            version=text("version"),
            required_segments=text_list_map("required_segments"),
            required_refs_by_doc=text_list_map("required_refs_by_doc"),
        )


#: A profile that constrains nothing — the default when ``--partner`` is absent.
DEFAULT_PARTNER = PartnerProfile()
