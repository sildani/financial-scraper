# seed_sandbox.py
# Builds the Plaid Sandbox fixture Links described in docs/plaid-testing.md.
#
#   python scripts/seed_sandbox.py seed              # (re)create the seeded fixture Links
#   python scripts/seed_sandbox.py password tartan   # print the user_custom password for a hand-made Link
#   python scripts/seed_sandbox.py reset-login tartan  # force a Link into Needs re-link
#   python scripts/seed_sandbox.py inspect           # print raw Plaid transactions for every Link
import sys
import json
import argparse
from pathlib import Path
from datetime import date, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

import plaid
from plaid.model.products import Products
from plaid.model.item_get_request import ItemGetRequest
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.item_remove_request import ItemRemoveRequest
from plaid.model.sandbox_item_reset_login_request import SandboxItemResetLoginRequest
from plaid.model.sandbox_public_token_create_request import SandboxPublicTokenCreateRequest
from plaid.model.sandbox_public_token_create_request_options import SandboxPublicTokenCreateRequestOptions

from src.config import current_plaid_environment
from src.plaid_client import get_plaid_client, get_transactions, load_tokens, remove_token, save_token

FIXTURES_FILE = Path(__file__).parent / "sandbox_fixtures.json"
PENDING_POSTS_IN_DAYS = 3


def load_links():
    with open(FIXTURES_FILE) as f:
        return json.load(f)["links"]


def custom_user_config(link: dict) -> dict:
    """Turns a fixture Link into a Plaid custom Sandbox user config, with dates relative to today."""
    today = date.today()
    accounts = []
    for acc in link["accounts"]:
        transactions = []
        for tx in acc["transactions"]:
            transacted = today - timedelta(days=tx["days_ago"])
            posted = today + timedelta(days=PENDING_POSTS_IN_DAYS) if tx.get("pending") else transacted
            transactions.append({
                "date_transacted": transacted.isoformat(),
                "date_posted": posted.isoformat(),
                "amount": tx["amount"],
                "description": tx["description"],
                "currency": "USD",
            })
        accounts.append({
            "type": acc["type"],
            "subtype": acc["subtype"],
            "starting_balance": acc["starting_balance"],
            "currency": "USD",
            "transactions": transactions,
        })
    return {"override_accounts": accounts}


def item_ids_by_institution(client) -> dict[str, list[str]]:
    by_institution = {}
    for item_id, access_token in load_tokens().items():
        item = client.item_get(ItemGetRequest(access_token=access_token))["item"]
        by_institution.setdefault(item["institution_id"], []).append(item_id)
    return by_institution


def find_access_token(client, link: dict) -> str:
    item_ids = item_ids_by_institution(client).get(link["institution_id"], [])
    if not item_ids:
        sys.exit(f"No Link found for {link['institution_name']}. Create it first.")
    return load_tokens()[item_ids[0]]


# These work on access tokens alone, never the token store, so the integration test can keep its Links to itself
def create_fixture_link(client, link: dict) -> tuple[str, str]:
    """Creates a Link from a fixture through the API; returns (item_id, access_token)."""
    request = SandboxPublicTokenCreateRequest(
        institution_id=link["institution_id"],
        initial_products=[Products("transactions")],
        options=SandboxPublicTokenCreateRequestOptions(
            override_username="user_custom",
            override_password=json.dumps(custom_user_config(link)),
        ),
    )
    public_token = client.sandbox_public_token_create(request)["public_token"]
    response = client.item_public_token_exchange(ItemPublicTokenExchangeRequest(public_token=public_token))
    return response["item_id"], response["access_token"]


def remove_link(client, access_token: str):
    client.item_remove(ItemRemoveRequest(access_token=access_token))


def force_relink(client, access_token: str):
    """Puts a Link into Needs re-link (ITEM_LOGIN_REQUIRED)."""
    client.sandbox_item_reset_login(SandboxItemResetLoginRequest(access_token=access_token))


def seed(client, links):
    existing = item_ids_by_institution(client)
    tokens = load_tokens()
    for name, link in links.items():
        if not link["seeded"]:
            continue
        for item_id in existing.get(link["institution_id"], []):
            remove_link(client, tokens[item_id])
            remove_token(item_id)
            print(f"   - Removed old {link['institution_name']} Link ({item_id})")

        item_id, access_token = create_fixture_link(client, link)
        save_token(item_id, access_token)
        print(f"   ✓ Seeded {link['institution_name']} Link ({item_id})")


def reset_login(client, link):
    force_relink(client, find_access_token(client, link))
    print(f"   ✓ {link['institution_name']} Link now needs re-link")


def inspect(client):
    end_date = date.today() + timedelta(days=PENDING_POSTS_IN_DAYS)
    start_date = date.today() - timedelta(days=40)
    for item_id, access_token in load_tokens().items():
        try:
            response = get_transactions(access_token, start_date, end_date)
        except plaid.ApiException as e:
            print(f"\nItem {item_id}: {json.loads(e.body)['error_code']}")
            continue
        print(f"\nItem {item_id} ({response['item']['institution_id']})")
        accounts = {a["account_id"]: f"{a['subtype']} ({a['mask']})" for a in response["accounts"]}
        for acc in accounts.values():
            print(f"   account: {acc}")
        for tx in sorted(response["transactions"], key=lambda t: t["date"]):
            category = (tx.get("personal_finance_category") or {}).get("detailed")
            pending = "pending" if tx["pending"] else ""
            print(f"   {tx['date']} | {accounts[tx['account_id']]:<20} | {tx['name']:<38} | {tx['amount']:>9.2f} | {category} {pending}")


def main():
    parser = argparse.ArgumentParser(description="Plaid Sandbox fixture Links")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("seed")
    sub.add_parser("inspect")
    for command in ("password", "reset-login"):
        sub.add_parser(command).add_argument("link")
    args = parser.parse_args()

    try:
        environment = current_plaid_environment()
    except ValueError as e:
        sys.exit(f"Error: {e}")
    if environment.name != "sandbox":
        sys.exit(f"Refusing to run: PLAID_ENV is '{environment.name}', fixtures only exist in Sandbox.")

    links = load_links()
    if getattr(args, "link", None) and args.link not in links:
        sys.exit(f"Unknown Link '{args.link}'. Choose from: {', '.join(links)}")

    if args.command == "password":
        print(json.dumps(custom_user_config(links[args.link]), separators=(",", ":")))
        return

    client = get_plaid_client()
    if args.command == "seed":
        seed(client, links)
    elif args.command == "reset-login":
        reset_login(client, links[args.link])
    elif args.command == "inspect":
        inspect(client)


if __name__ == "__main__":
    main()
