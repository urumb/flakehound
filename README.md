# flakehound

**Find flaky tests and prove *why* they're flaky.**

> 🚧 Early development. Not on PyPI yet.

flakehound runs your pytest suite under deliberately hostile conditions (shuffled order,
different seeds, timezones and dates, shrunken sleeps, thread stress), finds the tests that
flip between pass and fail, and explains the cause with evidence from real runs:

```
test_cart_starts_empty   ❌ failed 7/50 runs
  Cause: ORDER DEPENDENCY (confirmed)
  Polluter: test_add_item leaves cart.items = ['apple']
  Evidence: fails in 7/7 orders where test_add_item runs first; 0/43 otherwise
  Fix: reset the cart in a fixture/teardown
```

- 🆓 **Free forever.** No accounts, no servers, no paid services.
- 🔒 **Local and private.** Offline by default, no telemetry, secrets redacted from reports.
- 🧪 **Evidence, not guesses.** Every verdict comes with the runs that prove it.

## License

MIT
