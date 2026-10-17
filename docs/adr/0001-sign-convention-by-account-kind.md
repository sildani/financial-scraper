# Sign convention follows Account kind; Analysis uses derived Spend

A Transaction's Displayed amount keeps the sign its Institution shows, so it depends on the Account's kind: positive is money out for a Liability, positive is money in for a Deposit account. Analysis never sums Displayed amounts; it sums a derived Spend value, positive for money out, counted only for Transactions that are Spend.

We rejected a single project-wide sign because Details should match what the user sees when they open their bank or card statement, which is how they verify the data. The cost is that Displayed amounts can't be summed across Account kinds, so all totals must go through Spend.
