# Automated Download fetching

Downloads stay assisted: the app opens the Institution's page and waits for the file in your downloads folder, and you do the login and the clicking. The app does not fetch Downloads for you through vendor APIs, by driving your browser, or by reacting to statement emails.

## Why this is out of scope

Research into each vendor we have a Download Account for found no route that is both possible and allowed:

- **Official APIs.** None of them offers an API an individual can use for their own data. Their APIs are for partners, merchants or regulated third parties, or need a business account or an organization-level developer entitlement. Data sharing with consumers goes through onboarded aggregators such as Plaid, which is the Plaid Source's job already.
- **Driving your own, logged-in browser for the clicks.** Most of their website terms forbid any "robot, spider or other automatic device", with no exception for your own account. The rest fingerprint devices and score risk at login. The likely cost is a locked or limited account, which is far worse than the few clicks saved.
- **Statement-ready emails.** They are notices that link to the website and never attach the file, so they could only tell you to go and fetch, which a run already does.

Storing credentials, headless scraping, and unofficial or reverse-engineered APIs were out of scope from the start.

## What was kept

Opening the page with the date range and format already filled in, through URL parameters, is allowed and cheap if a vendor supports it. That can't be learned from documentation, so each vendor's Download adapter issue checks it on the real page while settling the export's layout, and `fetch` uses it where it works.

Third-party aggregators are a separate decision and not covered here.

## Prior requests

- #70: "Explore more automated Download fetching"
