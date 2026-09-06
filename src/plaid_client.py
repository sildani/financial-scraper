import plaid
import json
from plaid.api import plaid_api
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.products import Products
from plaid.model.link_token_get_request import LinkTokenGetRequest
from plaid.model.country_code import CountryCode
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.transactions_get_request import TransactionsGetRequest
from plaid.model.transactions_get_request_options import TransactionsGetRequestOptions

from src.config import PLAID_CLIENT_ID, PLAID_SECRET, PLAID_ENV, BASE_DIR

PLAID_PRODUCTS = [Products("transactions")]
PLAID_COUNTRY_CODES = [CountryCode("US")]
TOKEN_FILE = BASE_DIR / ".plaid_tokens.json"

PLAID_ENV_MAP = {
    'sandbox': plaid.Environment.Sandbox,
    'development': plaid.Environment.Production, # Development uses the Production infrastructure
    'production': plaid.Environment.Production,
}

def get_plaid_client():
    host = PLAID_ENV_MAP.get(PLAID_ENV, plaid.Environment.Sandbox)
    configuration = plaid.Configuration(
        host=host,
        api_key={
            "clientId": PLAID_CLIENT_ID,
            "secret": PLAID_SECRET,
        },
    )
    api_client = plaid.ApiClient(configuration)
    return plaid_api.PlaidApi(api_client)

def create_link_token():
    client = get_plaid_client()
    request = LinkTokenCreateRequest(
        user=LinkTokenCreateRequestUser(client_user_id="user-id"), # This should be a unique ID for each user.
        client_name="Financial Scraper",
        products=PLAID_PRODUCTS,
        country_codes=PLAID_COUNTRY_CODES,
        language="en",
        hosted_link={}
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

def get_transactions(access_token, start_date, end_date):
    client = get_plaid_client()
    request = TransactionsGetRequest(
        access_token=access_token,
        start_date=start_date,
        end_date=end_date,
        options=TransactionsGetRequestOptions(
            count=500,
            include_personal_finance_category=True,
        ),
    )
    response = client.transactions_get(request)
    return response.to_dict()

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
    with open(TOKEN_FILE, "w") as f:
        json.dump(tokens, f, indent=2)

def load_tokens():
    if not TOKEN_FILE.exists():
        return {}
    with open(TOKEN_FILE, "r") as f:
        return json.load(f)

if __name__ == "__main__":
    print("This script is not meant to be run directly anymore.")
    print("Please use the main.py script with the --plaid-link argument.")
