import unittest
from datetime import date

from src.plaid_client import get_transactions


class FakePlaidClient:
    """Plaid's /transactions/get, serving page_size Transactions per call from offset."""

    def __init__(self, transactions, page_size):
        self.transactions = transactions
        self.page_size = page_size

    def transactions_get(self, request):
        offset = request.options.offset
        return FakeResponse({
            "accounts": [{"account_id": "acc-1"}],
            "transactions": self.transactions[offset:offset + self.page_size],
            "total_transactions": len(self.transactions),
            "item": {"item_id": "item-1"},
        })


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def to_dict(self):
        return self.body


class GetTransactionsTest(unittest.TestCase):
    def test_every_page_is_fetched(self):
        transactions = [{"transaction_id": f"tx-{i}"} for i in range(5)]
        response = get_transactions("access-token", date(2026, 1, 1), date(2026, 3, 31), client=FakePlaidClient(transactions, page_size=2))
        self.assertEqual([tx["transaction_id"] for tx in response["transactions"]], [f"tx-{i}" for i in range(5)])


if __name__ == "__main__":
    unittest.main()
