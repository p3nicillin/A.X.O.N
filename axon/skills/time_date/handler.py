"""TimeDateSkill — current local time and date."""
from __future__ import annotations

from datetime import datetime

from ...ai.schema import Intent, SkillResult
from ..base import Skill


class TimeDateSkill(Skill):
    def execute(self, intent: Intent) -> SkillResult:
        now = datetime.now()
        if intent.type == "get_date":
            text = now.strftime("%A, %B %d, %Y")
            return self.ok(text, speak=f"Today is {text}.", date=text)
        text = now.strftime("%I:%M %p").lstrip("0")
        return self.ok(text, speak=f"It is {text}.", time=text)


SKILL = TimeDateSkill()
