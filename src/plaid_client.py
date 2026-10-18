import plaid
import json
from typing import List, Optional
from pydantic import BaseModel
from plaid.api import plaid_api
from plaid.model.item_get_request import ItemGetRequest
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.link_token_transactions import LinkTokenTransactions
from plaid.model.products import Products
from plaid.model.link_token_get_request import LinkTokenGetRequest
from plaid.model.country_code import CountryCode
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.transactions_get_request import TransactionsGetRequest
from plaid.model.transactions_get_request_options import TransactionsGetRequestOptions

from src.config import PLAID_CLIENT_ID, PLAID_SECRET, current_plaid_environment
from src.schemas import link_institution

PLAID_PRODUCTS = [Products("transactions")]
PLAID_COUNTRY_CODES = [CountryCode("US")]
# Plaid's maximum; without it a Link only has about 90 days, which caps the Export window
PLAID_DAYS_REQUESTED = 730

def get_plaid_client():
    configuration = plaid.Configuration(
        host=current_plaid_environment().host,
        api_key={
            "clientId": PLAID_CLIENT_ID,
            "secret": PLAID_SECRET,
        },
    )
    api_client = plaid.ApiClient(configuration)
    return plaid_api.PlaidApi(api_client)

def create_link_token(access_token=None, client=None):
    """
    Creates a hosted Link token for a new Link, or with access_token, for re-linking that Link
    in update mode, which repairs it in place instead of adding a second Link.
    """
    client = client or get_plaid_client()
    if access_token:
        # Update mode rejects products, and the Link keeps the history it was created with
        link_token_kwargs = dict(access_token=access_token)
    else:
        link_token_kwargs = dict(products=PLAID_PRODUCTS, transactions=LinkTokenTransactions(days_requested=PLAID_DAYS_REQUESTED))
    request = LinkTokenCreateRequest(
        user=LinkTokenCreateRequestUser(client_user_id="user-id"), # This should be a unique ID for each user.
        client_name="Financial Scraper",
        country_codes=PLAID_COUNTRY_CODES,
        language="en",
        hosted_link={},
        **link_token_kwargs,
    )
    response = client.link_token_create(request)
    return response["link_token"], response["hosted_link_url"]

def exchange_public_token(public_token):
    client = get_plaid_client()
    request = ItemPublicTokenExchangeRequest(public_token=public_token)
    response = client.item_public_token_exchange(request)
    access_token = response["access_token"]
    item_id = response["item_id"]
    save_token(item_id, access_token)
    return access_token, item_id

def get_transactions(access_token, start_date, end_date, client=None):
    """
    Fetches one Link's Transactions for the Export window. Plaid returns at most 500 per call,
    so this pages until it has total_transactions.
    """
    client = client or get_plaid_client()
    response = None
    transactions = []
    while response is None or len(transactions) < response["total_transactions"]:
        request = TransactionsGetRequest(
            access_token=access_token,
            start_date=start_date,
            end_date=end_date,
            options=TransactionsGetRequestOptions(
                count=500,
                offset=len(transactions),
                include_personal_finance_category=True,
            ),
        )
        response = client.transactions_get(request).to_dict()
        if not response["transactions"]:
            break  # Plaid's total changed under us; don't loop forever
        transactions.extend(response["transactions"])
    response["transactions"] = transactions
    return response

def error_needs_relink(error_code: Optional[str]) -> bool:
    """Whether a Link with this Plaid error code is Needs re-link (GLOSSARY.md) rather than Active or failing for now."""
    return error_code == "ITEM_LOGIN_REQUIRED"


class FailedLink(BaseModel):
    item_id: str
    institution: str
    error_code: str

    @property
    def needs_relink(self) -> bool:
        return error_needs_relink(self.error_code)


class FetchedLinks(BaseModel):
    responses: List[dict]
    failed: List[FailedLink]  # Any failed Link makes the Export a Partial Export


def fetch_links(tokens: dict, start_date, end_date, client=None) -> FetchedLinks:
    """Fetches every Link's Transactions; a Link that fails is reported instead of failing the run."""
    client = client or get_plaid_client()
    responses, failed = [], []
    for item_id, access_token in tokens.items():
        try:
            responses.append(get_transactions(access_token, start_date, end_date, client=client))
        except plaid.ApiException as e:
            failed.append(FailedLink(item_id=item_id, institution=institution_name(client, item_id, access_token), error_code=error_code(e)))
    return FetchedLinks(responses=responses, failed=failed)


def institution_name(client, item_id: str, access_token: str) -> str:
    # /item/get still answers for a Link that needs re-linking, but not for every failure
    try:
        item = client.item_get(ItemGetRequest(access_token=access_token))["item"]
    except plaid.ApiException:
        return item_id
    return link_institution(item)


def error_code(e: plaid.ApiException) -> str:
    # Plaid errors carry a JSON body; a transport failure may not
    try:
        return json.loads(e.body)["error_code"]
    except (TypeError, ValueError, KeyError):
        return "UNKNOWN"


class RelinkCheck(BaseModel):
    needing: List[FailedLink]
    unchecked: List[FailedLink]  # /item/get raised, so whether they need re-linking is unknown


def links_needing_relink(client=None, tokens=None) -> RelinkCheck:
    """Every Link in tokens (default: the Environment's token store) whose /item/get says it needs re-linking;
    a Link whose /item/get raises is reported instead of failing the run."""
    client = client or get_plaid_client()
    tokens = load_tokens() if tokens is None else tokens
    needing, unchecked = [], []
    for item_id, access_token in tokens.items():
        try:
            item = client.item_get(ItemGetRequest(access_token=access_token))["item"]
        except plaid.ApiException as e:
            unchecked.append(FailedLink(item_id=item_id, institution=item_id, error_code=error_code(e)))
            continue
        error = item.get("error")
        if error and error_needs_relink(error["error_code"]):
            needing.append(FailedLink(item_id=item_id, institution=link_institution(item), error_code=error["error_code"]))
    return RelinkCheck(needing=needing, unchecked=unchecked)


def link_error_code(access_token: str) -> str | None:
    item = get_plaid_client().item_get(ItemGetRequest(access_token=access_token))["item"]
    error = item.get("error")
    return error["error_code"] if error else None


def link_session_finished(link_token: str) -> bool:
    """Whether the hosted Link session for link_token has ended, successfully or not. Update mode
    returns no public token, so this, not get_public_token_from_link_session, tells when a re-link is done."""
    try:
        response = get_plaid_client().link_token_get(LinkTokenGetRequest(link_token=link_token))
    except plaid.ApiException:
        return False  # Same as get_public_token_from_link_session: keep polling
    return any(session.get("finished_at") for session in response.get("link_sessions") or [])


def get_public_token_from_link_session(link_token: str) -> str | None:
    """
    Polls the /link/token/get endpoint to see if the Link flow is complete.
    Returns the public_token if available, otherwise None.
    """
    client = get_plaid_client()
    request = LinkTokenGetRequest(link_token=link_token)
    try:
        response = client.link_token_get(request)
        
        # The session data is in the top-level `link_sessions` list.
        if hasattr(response, 'link_sessions') and response.link_sessions:
            for session in response.link_sessions:
                # A completed session will have a `results` object.
                if hasattr(session, 'results') and session.results:
                    # The results may contain multiple added items.
                    if hasattr(session.results, 'item_add_results') and session.results.item_add_results:
                        for item_result in session.results.item_add_results:
                            if hasattr(item_result, 'public_token') and item_result.public_token:
                                return item_result.public_token
    except plaid.ApiException as e:
        # This can happen if the token is expired or invalid, or if the session is not yet complete.
        # For polling, it's safe to ignore API exceptions and return None.
        pass
    return None

def save_token(item_id, access_token):
    tokens = load_tokens()
    tokens[item_id] = access_token
    with open(current_plaid_environment().token_file, "w") as f:
        json.dump(tokens, f, indent=2)

def remove_token(item_id):
    tokens = load_tokens()
    tokens.pop(item_id, None)
    with open(current_plaid_environment().token_file, "w") as f:
        json.dump(tokens, f, indent=2)

def load_tokens():
    token_file = current_plaid_environment().token_file
    if not token_file.exists():
        return {}
    with open(token_file, "r") as f:
        return json.load(f)

if __name__ == "__main__":
    print("This script is not meant to be run directly anymore.")
    print("Please use the main.py script with the --plaid-link argument.")
