# Coding standards

These are the review conventions that no tool checks. Anything ruff or pyright checks runs in `.venv/bin/pre-commit run --all-files` (see `.pre-commit-config.yaml`), so it isn't listed here.

1. **Collaborators are parameters with defaults, typed as narrow Protocols.** A collaborator that a test fakes is a parameter with a default, either `None` and resolved inside the function, or the real collaborator. Its type is a `Protocol` that holds only the calls that function makes.
   _Why:_ tests pass fakes without patching. Real objects and fakes both match structurally, so a fake needs no subclassing and no `cast`.
   _Why one Protocol per call:_ pyright treats a Protocol method whose body is `...` or only `raise NotImplementedError` as abstract, so a fake that subclasses a wide Protocol can't be instantiated. Split it per call and combine the parts.
   _Examples:_ `run_export(..., spreadsheet: Spreadsheet | None = None, client: LinksClient | None = None)` (`main.py`), where `Spreadsheet` holds only `add_worksheet` (`src/sheets.py`) and `LinksClient` combines `ItemClient` and `TransactionsClient` (`src/plaid_client.py`); `run_relink(check, tokens, relink_session=hosted_relink_session, error_code=link_error_code)` (`main.py`).

2. **A `run_*` function that `main()` exits on returns a pydantic result model**, and `main()` keeps only a one-line, untested exit around it.
   _Why:_ the tests check the result model, so the only untested code left in `main()` is the exit.
   _Examples:_ `if not run_export(...).summaries: sys.exit(...)` and `if not run_relink(...).complete: sys.exit(...)` in `main.py`. Other `run_*` helpers, such as `run_hosted_link`, aren't bound by this rule.

3. **Names, comments and output use the `GLOSSARY.md` vocabulary**, and output names a Link or an Account the way the glossary says.
   _Why:_ one word per concept keeps code, output and discussion in step. `_Retired_:` terms are checked by `checks/check_glossary_terms.py`. Everything else, including `_Avoid_:` terms, is the reviewer's call.
   _Example:_ `link_label` in `src/schemas.py` names a Link by its Institution and then its Link ID, e.g. "First Gingham Credit Union [a1b2c3]".

4. **Comments are short and say why, not what**, and they cite the ADR where one applies.
   _Why:_ the code already says what it does. The reason is what a reader can't recover.
   _Example:_ `# The config's Account kind decides the Displayed amount's sign, not Gemini's guess (ADR-0001)` in `src/adapters/statement.py`.

5. **No real financial data in code, tests or fixtures:** no real Institution names, merchants, amounts, masks or Statement file names.
   _Why:_ the repo is public. Use made-up names. A vendor-named adapter module is fine, because the vendor is the subject of the code.
   _Example:_ `DownloadAccount(adapter="first_gingham", institution="First Gingham Credit Union", last4="4321", kind="liability")` in `tests/test_download_accounts.py`.

6. **Suppressions are narrow and give a reason.** A `# pyright: ignore[rule]` covers one line, and a ruff per-file ignore covers one file or one directory glob, never the whole repo. Its comment says why the finding is wrong there.
   _Why:_ a broad suppression hides the next real finding. The reviewer judges whether the reason holds.
   _Examples:_ the `reportArgumentType` ignore in `src/adapters/statement.py`, plus `"src/llm.py" = ["E402"]` and `"tests/**" = ["ANN001"]` in `pyproject.toml`.
