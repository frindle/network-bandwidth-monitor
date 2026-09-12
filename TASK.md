# TASK: netmon-telegraf-metrics

## Confirmed defect (observed, not suspected)

confirmed: netmon exposes metrics only as dashboard-shaped JSON across ~32 Flask routes (app/api.py) and stores them in SQLite; there is no clean machine-readable metrics endpoint for the shared TIG stack to scrape. Telegraf (inputs.http) needs a single endpoint emitting InfluxDB line protocol. app/metrics.py is a placeholder.

## Entry point

app/metrics.py:1

## Required change

Implement `app/metrics.py` with TWO parts:

1) A PURE function (stdlib only -- do NOT import app.database, app.collector,
   flask, or anything else at the point it runs):

   `def render_line_protocol(interface_rates, wan_rates, poll):`

   - `interface_rates`: dict mapping iface name -> {'rx': float, 'tx': float}
     (bytes/sec, instantaneous). Emit one line per iface:
       `netmon_interface,iface=<ESCAPED> rx_rate=<rx>,tx_rate=<tx>`
   - `wan_rates`: same dict shape, WAN interfaces. Emit:
       `netmon_wan,iface=<ESCAPED> rx_rate=<rx>,tx_rate=<tx>`
   - `poll`: dict or None. When a non-empty dict, emit ONE line:
       `netmon_fw_poll,window=24h success_rate=<>,avg_latency_ms=<>,failures=<>i`
     When None or empty, emit NO poll line.
   - Return all lines joined by newlines (a single string). No trailing
     timestamp -- Telegraf timestamps on ingest.
   - **InfluxDB line-protocol escaping of TAG VALUES is mandatory**: in every
     tag value, replace a space with `\ `, a comma with `\,`, and a `=` with
     `\=` (device/interface display names contain spaces -- an unescaped space
     would be parsed as the tag/field separator and corrupt the point). Field
     VALUES here are numeric and MUST be emitted unquoted (e.g. `rx_rate=1.5`,
     never `rx_rate="1.5"`). Integer fields (failures) take the `i` suffix.
   - Empty inputs (all three empty/None) return an empty string.
   - There must be exactly ONE unescaped space per line (the tag/field
     separator).

2) A Flask Blueprint named `bp` exposing `GET /api/metrics` returning the
   rendered text with mimetype `text/plain`:

   `from flask import Blueprint, Response`
   `bp = Blueprint('metrics', __name__)`
   `@bp.route('/api/metrics')` -> gather live data and return
   `Response(render_line_protocol(...), mimetype='text/plain')`

   Inside the route handler ONLY (never at module top), lazily import
   `app.collector` and `app.database` to gather:
   - interface rates from `app.collector.current_rates()` (already
     {iface: {'rx':.., 'tx':..}})
   - wan rates: reuse the same shape; if unavailable pass `{}`.
   - poll: `app.database.query_fw_poll_summary(int(time.time()) - 86400)`
     (returns keys success_rate, avg_latency_ms, failures) or `{}`.
   Keep these lazy imports inside the function so `import app.metrics` stays
   dependency-light and the pure renderer is unit-testable in isolation.

Behaviour that must NOT change (regressions to keep green):
- `render_line_protocol` is pure and importable WITHOUT flask/db side effects
  at call time (the fixture imports the module and calls it directly).
- Existing app/api.py routes are untouched (this dispatch only adds
  app/metrics.py; the Blueprint is registered in api.py as a separate
  land-time step, out of scope here).

## Must contain

- `render_line_protocol`
- `netmon_interface`
- `netmon_wan`
- `netmon_fw_poll`
- `Blueprint`
- `/api/metrics`

(The gate holds the reference impl against this list. If the verify goes green
while one of these is absent from the changed files, the verify does not
enforce the spec -- that is a benign verify, caught mechanically.)

## Scope

Only edit `app/metrics.py`; do not edit `verify.sh`, `test_fixture.py` or `TASK.md`.
test_fixture.py is the test fixture -- changing it invalidates the check.

## Keep every changed line exercised (relevance)

After the job runs, a mutation check flips/deletes each line you changed and
asks the verify to catch it. A changed line whose every mutant survives --
because no test asserts it -- FAILS the gate even when the fix is correct, and
the review never runs. So do NOT emit an isolated, untested line:
- Fold an unavoidable constant onto a line the test already exercises. Put a
  `timeout=` / a `daemon=True` flag / a small tuning number on the SAME line as
  a header dict, URL, or argument the fixture checks -- never on its own line.
- Prefer falling through to an implicit `return None` over a standalone
  `return None` in an `except:` the tests do not assert.
- If a line genuinely cannot be asserted and cannot be folded, it usually
  should not be a separate line at all -- restructure so it isn't.
This is not about adding bogus assertions for constants; it is about not
leaving a lone line that carries no tested behaviour.

## Loop instruction

Run `bash verify.sh` after every edit and keep editing until it prints
`VERIFY_OK`.
