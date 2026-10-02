# Browser implementation provenance

`internal/lightpanda/client.go` and its tests are reused from the user's
WebLens repository, revision `75f165cf97edd056e0b89a24714636143d77c1ce`.
Original module: github.com/tajleonbennis-maker/weblens.
The user explicitly requested reuse of this implementation in AgentPair.
No standalone LICENSE was found in the inspected checkout; retain attribution
and verify licensing before redistributing this extracted package publicly.

This package is not the WebLens product, API, plugin, or UI. It reuses the
existing CDP session lifecycle and browser operations beneath AgentPair tools.
The extracted client is deployed through AgentPair's private stdio BrowserKit.
The old patched PinchTab route is no longer the active browser execution path.

Known upstream caveats to address before deployment:
- Upstream waitStable returned nil after exhausting its polling window;
  extracted implementation now returns an error.
- Upstream evaluateString suppresses protocol errors; exposed tools instead
  use EvaluateVerified, retaining protocol and JavaScript errors.
- Upstream Render applies a fixed user-agent/header profile; exposed tools use
  NavigateVerified without those legacy site-specific overrides.
- One client is serial, not concurrency-safe.
- Storage/network inspection functions are not exposed to the agent toolkit.
