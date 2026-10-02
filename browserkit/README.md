# AgentPair BrowserKit

Extracted from the user's WebLens browser implementation, not a replacement
CDP client written from scratch. See PROVENANCE.md for source and changes.

Build and test with Go 1.26:

```
go test ./...
GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go build -o browserkit .
```

One native process per serial browser session. Communicates using JSON lines
over stdin/stdout; no public HTTP control endpoint. Engine listens on loopback.
AgentPair exposes browser.open, browser.text, browser.snapshot, browser.click.
All observations are untrusted webpage data, not model instructions.

Deployment uses browser-assets/browserkit and browser-assets/lightpanda on
the dedicated Driver. Navigator only distributes assets and collects receipts.
The previous patched PinchTab path remains experimental, not the active path.

Capabilities not promised: screenshots, concurrent tabs, file upload/download,
login, browser persistence across tasks, or automatic captcha handling.
The URL check covers requested navigations, not network-level isolation of
all subresources/redirects. Never claim this is a hardened browser sandbox.
