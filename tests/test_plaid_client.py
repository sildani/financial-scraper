import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

import plaid

from src.plaid_client import FailedLink, create_link_token, fetch_links, get_public_token_from_link_session, get_transactions, links_needing_relink, load_tokens, remove_token, save_token


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


class FakeLinkSessionClient:
    """Plaid's /link/token/get, returning link_sessions, or raising error."""

    def __init__(self, link_sessions=None, error=None):
        self.link_sessions = link_sessions
        self.error = error

    def link_token_get(self, request):
        if self.error:
            raise self.error
        return {"link_sessions": self.link_sessions}


def finished_session(*public_tokens):
    item_add_results = [{"public_token": token} for token in public_tokens]
    return {"finished_at": "2026-10-18T12:00:00Z", "results": {"item_add_results": item_add_results}}


class GetPublicTokenFromLinkSessionTest(unittest.TestCase):
    def test_a_finished_session_gives_its_public_token(self):
        client = FakeLinkSessionClient([{}, finished_session("public-token-gingham")])
        self.assertEqual(get_public_token_from_link_session("link-token", client=client), "public-token-gingham")

    def test_a_session_without_a_public_token_gives_none(self):
        for link_sessions in [None, [], [{}], [{"results": None}], [finished_session()], [finished_session(None)]]:
            with self.subTest(link_sessions=link_sessions):
                client = FakeLinkSessionClient(link_sessions)
                self.assertIsNone(get_public_token_from_link_session("link-token", client=client))

    def test_a_plaid_error_gives_none_so_polling_carries_on(self):
        client = FakeLinkSessionClient(error=plaid_error("INVALID_LINK_TOKEN"))
        self.assertIsNone(get_public_token_from_link_session("link-token", client=client))


class TokenStoreTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.token_file = Path(directory.name) / ".plaid_tokens.sandbox.json"

    def test_no_token_file_means_no_links(self):
        self.assertEqual(load_tokens(self.token_file), {})

    def test_saved_tokens_are_kept_and_a_removed_one_leaves_the_others(self):
        save_token("item-gingham", "access-gingham", token_file=self.token_file)
        save_token("item-platypus", "access-platypus", token_file=self.token_file)
        remove_token("item-gingham", token_file=self.token_file)
        self.assertEqual(load_tokens(self.token_file), {"item-platypus": "access-platypus"})

    def test_removing_a_link_that_is_not_saved_changes_nothing(self):
        save_token("item-gingham", "access-gingham", token_file=self.token_file)
        remove_token("item-platypus", token_file=self.token_file)
        self.assertEqual(load_tokens(self.token_file), {"item-gingham": "access-gingham"})


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


class FailedLinkLabelTest(unittest.TestCase):
    def test_a_link_is_named_by_its_institution_then_its_link_id(self):
        link = FailedLink(item_id="eVBnVMp7zdTJLkRN", institution="First Gingham Credit Union", error_code="ITEM_LOGIN_REQUIRED")
        self.assertEqual(link.label, "First Gingham Credit Union [eVBnVM]")

    def test_a_link_whose_institution_is_unknown_is_named_by_its_link_id_alone(self):
        link = FailedLink(item_id="eVBnVMp7zdTJLkRN", institution="eVBnVMp7zdTJLkRN", error_code="INVALID_ACCESS_TOKEN")
        self.assertEqual(link.label, "eVBnVM")


class LinksNeedingRelinkTest(unittest.TestCase):
    def test_only_links_whose_login_is_required_need_re_linking(self):
        client = FakePlaidClient(
            [],
            institutions={"token-tartan": "Tartan Bank", "token-platypus": "First Platypus Bank", "token-gingham": "First Gingham Credit Union"},
            item_errors={"token-tartan": "ITEM_LOGIN_REQUIRED", "token-gingham": "INSTITUTION_DOWN"},
        )
        check = links_needing_relink(client=client, tokens={"item-tartan": "token-tartan", "item-platypus": "token-platypus", "item-gingham": "token-gingham"})
        self.assertEqual([(link.item_id, link.institution, link.needs_relink) for link in check.needing], [("item-tartan", "Tartan Bank", True)])

    def test_a_link_that_cannot_be_checked_is_reported_by_item_id_and_the_others_still_checked(self):
        client = FakePlaidClient([], institutions={"token-tartan": "Tartan Bank"}, item_errors={"token-tartan": "ITEM_LOGIN_REQUIRED"})
        check = links_needing_relink(client=client, tokens={"item-gone": "token-gone", "item-tartan": "token-tartan"})
        self.assertEqual([(link.item_id, link.institution, link.error_code) for link in check.unchecked], [("item-gone", "item-gone", "INVALID_ACCESS_TOKEN")])
        self.assertEqual([link.item_id for link in check.needing], ["item-tartan"])


if __name__ == "__main__":
    unittest.main()
