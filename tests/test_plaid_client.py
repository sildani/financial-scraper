import json
import unittest
from datetime import date

import plaid

from src.plaid_client import create_link_token, fetch_links, get_transactions, links_needing_relink


def plaid_error(error_code):
    error = plaid.ApiException(status=400, reason="Bad Request")
    error.body = json.dumps({"error_type": "ITEM_ERROR", "error_code": error_code})
    return error


class FakePlaidClient:
    """Plaid's /transactions/get, serving page_size Transactions per call from offset.
    Access tokens in failing raise that Plaid error code; /item/get names each Link's Institution
    and reports the Link's error in item_errors, if any."""

    def __init__(self, transactions, page_size=500, failing=None, institutions=None, item_errors=None):
        self.transactions = transactions
        self.page_size = page_size
        self.failing = failing or {}
        self.institutions = institutions or {}
        self.item_errors = item_errors or {}

    def item_get(self, request):
        if request.access_token not in self.institutions:
            raise plaid_error("INVALID_ACCESS_TOKEN")
        error_code = self.item_errors.get(request.access_token)
        return {"item": {
            "item_id": "item-1", "institution_id": "ins_1", "institution_name": self.institutions.get(request.access_token),
            "error": {"error_code": error_code} if error_code else None,
        }}

    def transactions_get(self, request):
        if request.access_token in self.failing:
            raise plaid_error(self.failing[request.access_token])
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


class FakeLinkTokenClient:
    """Plaid's /link/token/create, keeping the request it was sent."""

    def link_token_create(self, request):
        self.request = request.to_dict()
        return {"link_token": "link-token", "hosted_link_url": "https://hosted.plaid.com/link"}


class CreateLinkTokenTest(unittest.TestCase):
    def test_re_link_uses_update_mode_on_the_existing_link(self):
        client = FakeLinkTokenClient()
        create_link_token(access_token="token-tartan", client=client)
        self.assertEqual(client.request["access_token"], "token-tartan")
        self.assertNotIn("products", client.request)  # Plaid rejects products in update mode

    def test_new_link_requests_transactions(self):
        client = FakeLinkTokenClient()
        create_link_token(client=client)
        self.assertNotIn("access_token", client.request)
        self.assertEqual(client.request["products"], ["transactions"])


class FetchLinksTest(unittest.TestCase):
    def test_a_link_needing_re_link_is_reported_and_the_others_still_fetched(self):
        client = FakePlaidClient(
            [{"transaction_id": "tx-1"}],
            failing={"token-tartan": "ITEM_LOGIN_REQUIRED"},
            institutions={"token-tartan": "Tartan Bank"},
        )
        fetched = fetch_links(
            {"item-platypus": "token-platypus", "item-tartan": "token-tartan"},
            date(2026, 9, 17), date(2026, 10, 17), client=client,
        )
        self.assertEqual(len(fetched.responses), 1)
        self.assertEqual([(f.item_id, f.institution, f.needs_relink) for f in fetched.failed], [("item-tartan", "Tartan Bank", True)])

    def test_a_failed_link_whose_institution_cannot_be_looked_up_is_named_by_item_id(self):
        client = FakePlaidClient([], failing={"token-gone": "INSTITUTION_DOWN"})
        fetched = fetch_links({"item-gone": "token-gone"}, date(2026, 9, 17), date(2026, 10, 17), client=client)
        self.assertEqual([(f.institution, f.needs_relink) for f in fetched.failed], [("item-gone", False)])


class LinksNeedingRelinkTest(unittest.TestCase):
    def test_only_links_whose_login_is_required_need_re_linking(self):
        client = FakePlaidClient(
            [],
            institutions={"token-tartan": "Tartan Bank", "token-platypus": "First Platypus Bank", "token-gingham": "First Gingham Credit Union"},
            item_errors={"token-tartan": "ITEM_LOGIN_REQUIRED", "token-gingham": "INSTITUTION_DOWN"},
        )
        needing = links_needing_relink(client=client, tokens={"item-tartan": "token-tartan", "item-platypus": "token-platypus", "item-gingham": "token-gingham"})
        self.assertEqual([(link.item_id, link.institution, link.needs_relink) for link in needing], [("item-tartan", "Tartan Bank", True)])


if __name__ == "__main__":
    unittest.main()
