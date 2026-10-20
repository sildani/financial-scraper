from src.adapters.first_gingham import FirstGinghamAdapter
from src.adapters.statement import StatementAdapter
from src.downloads import Adapter


def adapters(force: bool = False) -> dict[str, Adapter]:
    """Every adapter by the name download_accounts.toml gives it; force re-parses Statements, bypassing the cache."""
    return {
        "statement": StatementAdapter(force=force),
        "first_gingham": FirstGinghamAdapter(),
    }
