"""Member model (inherits from User)."""
from datetime import date

from config import Role
from models.user import User


class Member(User):
    ID_PREFIX = "M"
    DEFAULT_ROLE = Role.MEMBER

    def __init__(self, *args, phone="", joined_on=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.phone = phone
        self.joined_on = joined_on or date.today().isoformat()

    def to_dict(self):
        data = super().to_dict()
        data.update({"phone": self.phone, "joined_on": self.joined_on})
        return data

    @classmethod
    def from_dict(cls, data):
        member = super().from_dict(data)
        member.phone = data.get("phone", "")
        member.joined_on = data.get("joined_on", member.joined_on)
        return member


def user_from_dict(data):
    """Build a Member or a plain User depending on the stored role."""
    return Member.from_dict(data) if data.get("role") == Role.MEMBER.value else User.from_dict(data)
