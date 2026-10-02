# Native browser Driver acceptance — 2026-09-30

## Scope and outcome

Real cloud Driver `uhost-1vwh7b6ktqhe` (Ubuntu 22.04, amd64).
No Docker. No browser processes on Navigator.
PinchTab v0.15.2 with the adjacent experimental compatibility patch drives
Lightpanda via a loopback CDP adapter. This is not upstream compatibility support.

Verified on the actual Driver:

- Browser.getVersion queried over real CDP; PinchTab health returns ok.
- Navigate to a controlled local HTML page and obtain title/URL.
- Snapshot returns seven accessibility nodes including the button.
- Text initially contains WAITING_FOR_CLICK.
- Clicking #expand returns clicked=true; subsequent text contains CLICK_VERIFIED.
- Public HTTPS navigation to https://example.com/ returns Example Domain;
  subsequent text returns actual multilingual page content.

Initial public navigation was correctly rejected by the domain allowlist.
Only example.com was added for the public acceptance check. No policy disabled.

## Components

- Lightpanda: /home/pair/agentpair-lightpanda-linux-20260930, loopback 9222.
  SHA256: 16ee4443e34d09c522d8c416c6096bcd5a3dffd56706b7a8e2d7d0b1172ecc62
- CDP adapter: /home/pair/cdp-proxy-v2, loopback 9223.
- Patched PinchTab: /home/pair/pinchtab-lp-v4, loopback 9868.
- PinchTab token stays in the Driver config; not included here.
- PINCHTAB_LIGHTPANDA=1 explicitly enables compatibility behavior.

## Reproduction

Use official PinchTab v0.15.2 source (archive revision prefix 9de129a).
Apply ops/pinchtab-lightpanda.patch with `patch -p1` in that source directory.
Build `go build ./cmd/pinchtab` with Go 1.26. The CDP adapter source is
ops/lightpanda_cdp_proxy.go; build it within that Go module to resolve gobwas/ws.
Start Lightpanda on 9222, then the adapter on 9223, then patched PinchTab:

    PINCHTAB_LIGHTPANDA=1 ./pinchtab bridge --bind 127.0.0.1 --port 9868 --cdp-attach ws://127.0.0.1:9223/devtools/browser/lightpanda

Run ops/test_native_browser.py on a clean browser instance. It uses a local
controlled fixture for interaction and a one-off public read. The public domain
must be explicitly allowlisted. No public control port is required.

## Known boundaries

- One managed page; reuse tabId. Multi-page/concurrent sessions are not supported.
- Missing Debugger domain is tolerated only for explicit Lightpanda mode and
  CDP method-not-found. Debugger pause protection is unavailable in this mode.
- Page readiness uses document.readyState rather than lifecycle events.
  This is preliminary: redirects, same-URL reloads and asynchronous SPAs need
  dedicated tests before general-purpose production use.
- PinchTab reports provider=chrome because this is its generic CDP path;
  actual browser is Lightpanda. Do not present that field as engine identity.
- Screenshots, login, uploads, downloads, typing, scrolling and complex sites
  were not accepted by this run.
- Native Driver toolkit registration and Navigator automatic research remain
  unfinished; this report does not imply production task routing is enabled.

## References consulted

- https://github.com/pinchtab/pinchtab/tree/v0.15.2
- https://github.com/lightpanda-io/cdpproxy (protocol logging, not a compatibility shim)
- Existing WebLens internal/lightpanda/client.go at 75f165cf97edd056e0b89a24714636143d77c1ce:
  dedicated root-WebSocket connection, browser-context setup, DOM stability checks.

The Driver retains its original lease, ending at 2026-09-30T04:43:50Z.
This test did not create a replacement machine or extend the lease.
