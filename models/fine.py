"""Fine model."""
from dataclasses import dataclass, field
from datetime import date

from config import ValidationError


@dataclass
class Fine:
    fine_id: str
    member_id: str
    txn_id: str
    days_late: int
    amount: int
    created_on: date
    paid: int = 0
    payments: list = field(default_factory=list)

    @property
    def balance(self):
        return self.amount - self.paid

    @property
    def is_settled(self):
        return self.balance <= 0

    def apply_payment(self, amount, on_date):
        if amount <= 0:
            raise ValidationError("Payment must be greater than zero.")
        if amount > self.balance:
            raise ValidationError(f"Payment exceeds the balance of this fine ({self.balance}).")
        self.paid += amount
        self.payments.append({"date": on_date.isoformat(), "amount": amount})

    def to_dict(self):
        return {"fine_id": self.fine_id, "member_id": self.member_id, "txn_id": self.txn_id,
                "days_late": self.days_late, "amount": self.amount, "created_on": self.created_on.isoformat(),
                "paid": self.paid, "payments": list(self.payments)}

    @classmethod
    def from_dict(cls, d):
        return cls(d["fine_id"], d["member_id"], d["txn_id"], d["days_late"], d["amount"],
                   date.fromisoformat(d["created_on"]), d.get("paid", 0), list(d.get("payments", [])))
