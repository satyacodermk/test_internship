"""Fine calculation and payments."""
from collections import defaultdict
from datetime import date

from config import NotFoundError, ValidationError, audited, check_permission, next_number
from models import Fine
from storage import PersistentCollection, UnitOfWork


class FineService(PersistentCollection):
    FILE = "fines"

    def __init__(self, storage, activity, settings, today=date.today):
        super().__init__(storage)
        self.activity, self.settings, self.today = activity, settings, today
        self._fines = []
        self._by_member = defaultdict(list)
        self.reload()

    def _serialize(self):
        return [f.to_dict() for f in self._fines]

    def _load(self, raw):
        self._fines = [Fine.from_dict(row) for row in raw]
        self._by_member = defaultdict(list)
        for fine in self._fines:
            self._by_member[fine.member_id].append(fine)

    # -- calculations
    @staticmethod
    def days_late(due_date, returned_on):
        """Only days after the due date count; never negative."""
        return max(0, (returned_on - due_date).days)

    def amount_for(self, days_late):
        return max(0, days_late) * self.settings.get("fine_rate")

    def create_fine(self, member_id, txn_id, days_late):
        """Add a fine (no save - call inside a UnitOfWork). Returns None when nothing is owed."""
        amount = self.amount_for(days_late)
        if days_late <= 0 or amount <= 0:
            return None
        fine = Fine(f"F-{next_number(f.fine_id for f in self._fines):04d}", member_id, txn_id,
                    days_late, amount, self.today())
        self._fines.append(fine)
        self._by_member[member_id].append(fine)
        return fine

    # -- queries
    def get(self, fine_id):
        for fine in self._fines:
            if fine.fine_id == fine_id.strip().upper():
                return fine
        raise NotFoundError(f"Fine '{fine_id}' was not found.")

    def fines_for_member(self, member_id):
        return list(self._by_member.get(member_id, []))

    def balance_for(self, member_id):
        return sum(f.balance for f in self._by_member.get(member_id, []))

    def outstanding(self):
        return [f for f in self._fines if f.balance > 0]

    def total_outstanding(self):
        return sum(f.balance for f in self._fines)

    def within_limit(self, amount_due):
        return amount_due <= self.settings.get("fine_limit")

    # -- payments
    @audited("FINE_PAYMENT", lambda r: f"{r['member_id']} paid {r['paid']} balance {r['balance']}")
    def record_payment(self, actor, member_id, amount):
        check_permission(actor, "manage_fines", self.activity)
        member_id = member_id.strip().upper()
        if isinstance(amount, bool) or not isinstance(amount, int):
            raise ValidationError("Payment must be a whole number of rupees.")
        if amount <= 0:
            raise ValidationError("Payment must be greater than zero.")
        balance = self.balance_for(member_id)
        if balance <= 0:
            raise ValidationError("This member has no outstanding fines.")
        if amount > balance:
            raise ValidationError(f"Payment exceeds the outstanding balance (Rs {balance}).")
        remaining = amount
        with UnitOfWork(self):
            for fine in self._by_member[member_id]:  # oldest fine first
                if remaining <= 0:
                    break
                part = min(remaining, fine.balance)
                if part > 0:
                    fine.apply_payment(part, self.today())
                    remaining -= part
        return {"member_id": member_id, "paid": amount, "balance": self.balance_for(member_id)}
