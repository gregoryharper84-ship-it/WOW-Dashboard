"""
skills/registry.py
Loads skill-registry.json and provides skill-id lookup.
"""
from __future__ import annotations

import json
import os
from typing import Any

_REGISTRY_PATH = os.path.join(os.path.dirname(__file__), "skill-registry.json")


class SkillRegistry:
    """Singleton registry loaded from skill-registry.json."""

    _instance: "SkillRegistry | None" = None

    def __init__(self, path: str = _REGISTRY_PATH) -> None:
        with open(path) as f:
            raw = json.load(f)
        self._pack:    str             = raw.get("pack", "")
        self._version: str             = raw.get("version", "")
        self._skills:  list[dict]      = raw.get("skills", [])
        self._by_id:   dict[str, dict] = {s["id"]: s for s in self._skills}

    @classmethod
    def get(cls) -> "SkillRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def pack(self) -> str:
        return self._pack

    @property
    def version(self) -> str:
        return self._version

    def all_skills(self) -> list[dict]:
        return list(self._skills)

    def get_skill(self, skill_id: str) -> dict | None:
        return self._by_id.get(skill_id)

    def skill_ids(self) -> list[str]:
        return list(self._by_id.keys())

    def is_known(self, skill_id: str) -> bool:
        return skill_id in self._by_id

    def ordered_skills(self) -> list[dict]:
        return sorted(self._skills, key=lambda s: s.get("priority", 999))

    def validate_registry(self) -> list[str]:
        """
        Validate registry integrity. Returns list of error strings (empty = valid).
        Checks: all skills have id, name, priority; no duplicate IDs; 21 skills total.
        """
        errors: list[str] = []
        seen_ids: set[str] = set()
        for s in self._skills:
            sid = s.get("id")
            if not sid:
                errors.append(f"Skill missing 'id': {s}")
                continue
            if not s.get("name"):
                errors.append(f"Skill {sid!r} missing 'name'")
            if s.get("priority") is None:
                errors.append(f"Skill {sid!r} missing 'priority'")
            if sid in seen_ids:
                errors.append(f"Duplicate skill id: {sid!r}")
            seen_ids.add(sid)
        if len(self._skills) != 22:
            errors.append(
                f"Registry must contain 22 skills; found {len(self._skills)}"
            )
        return errors
