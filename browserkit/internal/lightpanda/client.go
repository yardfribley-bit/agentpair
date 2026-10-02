// Package lightpanda is a minimal CDP client for the Lightpanda browser
// engine. It drives the engine through the same session flow the engine
// requires — createBrowserContext → createTarget → attachToTarget — which
// plain Chrome-oriented clients skip.
//
// Lightpanda specifics handled here:
//   - the WebSocket endpoint must keep its trailing slash ("ws://host:port/");
//   - the handshake must NOT send an Origin header (gobwas/ws defaults to none);
//   - a single connection serves a single browsing context, so concurrency
//     comes from running several engine processes on several ports.
package lightpanda

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
	"strings"
	"time"

	"github.com/gobwas/ws"
	"github.com/gobwas/ws/wsutil"
)

// Client is a live CDP session against one Lightpanda process.
type Client struct {
	conn    net.Conn
	nextID  int
	session string // set once attachToTarget returns a flattened session id
}

type cdpMsg struct {
	ID        int             `json:"id"`
	Method    string          `json:"method,omitempty"`
	Params    json.RawMessage `json:"params,omitempty"`
	SessionID string          `json:"sessionId,omitempty"`
}

type cdpResp struct {
	ID     int             `json:"id"`
	Result json.RawMessage `json:"result,omitempty"`
	Error  *cdpErr         `json:"error,omitempty"`
}

type cdpErr struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
}

// Connect dials the Lightpanda CDP endpoint (host:port) and establishes a
// browsing-context session. The trailing slash is appended automatically.
func Connect(ctx context.Context, addr string) (*Client, error) {
	dialCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()

	// Trailing slash is required by Lightpanda; no Origin header is sent
	// (gobwas/ws does not add one), which Lightpanda also requires.
	conn, _, _, err := ws.Dial(dialCtx, "ws://"+addr+"/")
	if err != nil {
		return nil, fmt.Errorf("dial lightpanda %s: %w", addr, err)
	}

	c := &Client{conn: conn}
	if err := c.initSession(ctx); err != nil {
		_ = conn.Close()
		return nil, err
	}
	return c, nil
}

func (c *Client) initSession(ctx context.Context) error {
	var br struct {
		BrowserContextID string `json:"browserContextId"`
	}
	if err := c.call(ctx, "Target.createBrowserContext", nil, &br); err != nil {
		return fmt.Errorf("createBrowserContext: %w", err)
	}

	createParams, _ := json.Marshal(map[string]any{
		"url":              "about:blank",
		"browserContextId": br.BrowserContextID,
	})
	var target struct {
		TargetID string `json:"targetId"`
	}
	if err := c.call(ctx, "Target.createTarget", createParams, &target); err != nil {
		return fmt.Errorf("createTarget: %w", err)
	}

	attachParams, _ := json.Marshal(map[string]any{
		"targetId": target.TargetID,
		"flatten":  true,
	})
	var attached struct {
		SessionID string `json:"sessionId"`
	}
	if err := c.call(ctx, "Target.attachToTarget", attachParams, &attached); err != nil {
		return fmt.Errorf("attachToTarget: %w", err)
	}
	c.session = attached.SessionID
	return nil
}

// StorageSnapshot holds the localStorage and sessionStorage contents of the
// rendered page. Values are returned verbatim; the caller is responsible for
// key-exposure detection on them.
type StorageSnapshot struct {
	LocalStorage  map[string]string `json:"localStorage,omitempty"`
	SessionStorage map[string]string `json:"sessionStorage,omitempty"`
	Cookies       map[string]string `json:"cookies,omitempty"`
}

// Storage returns localStorage, sessionStorage and (document.cookie) contents
// of the currently rendered page via Runtime.evaluate. Reads are best-effort:
// pages that forbid access (e.g. opaque origins) yield empty maps, not errors.
func (c *Client) Storage(ctx context.Context) (StorageSnapshot, error) {
	var snap StorageSnapshot
	expr := `(() => {
		const ls = {}; for (let i = 0; i < localStorage.length; i++) { const k = localStorage.key(i); ls[k] = localStorage.getItem(k); }
		const ss = {}; for (let i = 0; i < sessionStorage.length; i++) { const k = sessionStorage.key(i); ss[k] = sessionStorage.getItem(k); }
		const ck = {}; (document.cookie || '').split(';').forEach(p => { const i = p.indexOf('='); if (i > 0) ck[p.slice(0,i).trim()] = p.slice(i+1).trim(); });
		return JSON.stringify({localStorage: ls, sessionStorage: ss, cookies: ck});
	})()`
	params, _ := json.Marshal(map[string]any{
		"expression":    expr,
		"returnByValue": true,
	})
	var eval struct {
		Result struct {
			Value json.RawMessage `json:"value"`
		} `json:"result"`
	}
	if err := c.call(ctx, "Runtime.evaluate", params, &eval); err != nil {
		return snap, err
	}
	// The evaluated expression returns a JSON string; unwrap it.
	var raw string
	if err := json.Unmarshal(eval.Result.Value, &raw); err == nil {
		_ = json.Unmarshal([]byte(raw), &snap)
	} else {
		_ = json.Unmarshal(eval.Result.Value, &snap)
	}
	return snap, nil
}

// NetworkCapture enables Network domain, navigates to url, and collects the
// bodies of all HTTP responses observed while the page loads. This catches API
// responses (e.g. /api/v1/settings) that the static fetch never sees,
// including ones gated behind client-side auth. It is bounded: only responses
// whose URL looks interesting, that are JSON-ish and <= maxBytes are kept.
//
// All CDP traffic happens on the single connection, so this method runs its
// own read loop instead of using call(): the navigate command and any
// getResponseBody commands are written inline and their matching responses are
// consumed in the same loop, while Network.* events are collected as they
// arrive. This avoids two goroutines racing on conn.
func (c *Client) NetworkCapture(ctx context.Context, url string, maxBytes int64) ([]NamedBody, error) {
	if err := c.call(ctx, "Network.enable", nil, nil); err != nil {
		return nil, err
	}

	var out []NamedBody
	collected := map[string]string{} // requestId -> url

	// writeCommand sends a CDP command on the connection.
	writeCommand := func(id int, method string, params json.RawMessage) error {
		msg := cdpMsg{ID: id, Method: method, Params: params, SessionID: c.session}
		data, err := json.Marshal(msg)
		if err != nil {
			return err
		}
		return wsutil.WriteClientText(c.conn, data)
	}

	// Kick off navigation; the read loop below processes its response.
	navParams, _ := json.Marshal(map[string]any{"url": url})
	navID := c.nextID + 1
	c.nextID = navID
	if err := writeCommand(navID, "Page.navigate", navParams); err != nil {
		return nil, err
	}

	// readLoop drains the connection until we've seen the navigate response
	// plus a settle window, collecting Network events and pulling response
	// bodies as loadingFinished arrives.
	type pendingBody struct {
		id      int
		reqID   string
		url     string
	}
	var bodyReq *pendingBody
	deadline := time.Now().Add(10 * time.Second)
	for {
		if ctx.Err() != nil {
			return out, ctx.Err()
		}
		if err := c.conn.SetReadDeadline(deadline); err != nil {
			return out, err
		}
		payload, err := wsutil.ReadServerText(c.conn)
		if err != nil {
			if netErr, ok := err.(net.Error); ok && netErr.Timeout() {
				// 2s without any traffic after navigation: done.
				return out, nil
			}
			return out, err
		}
		var msg struct {
			ID     int             `json:"id"`
			Method string          `json:"method"`
			Result json.RawMessage `json:"result,omitempty"`
			Params struct {
				RequestID         string `json:"requestId"`
				EncodedDataLength int64  `json:"encodedDataLength"`
				Response          struct {
					URL    string `json:"url"`
					Status int    `json:"status"`
				} `json:"response"`
			} `json:"params,omitempty"`
		}
		if err := json.Unmarshal(payload, &msg); err != nil {
			continue
		}
		// Received something: keep listening for up to 2 more seconds.
		deadline = time.Now().Add(2 * time.Second)

		switch {
		case msg.ID == navID:
			// navigate acknowledged; keep reading for network events.
			continue
		case bodyReq != nil && msg.ID == bodyReq.id:
			var res struct {
				Body string `json:"body"`
			}
			_ = json.Unmarshal(msg.Result, &res)
			if len(res.Body) > 0 && isJSONish([]byte(res.Body)) {
				out = append(out, NamedBody{URL: bodyReq.url, Body: res.Body})
			}
			delete(collected, bodyReq.reqID)
			bodyReq = nil
		case msg.Method == "Network.responseReceived":
			if msg.Params.Response.Status >= 200 && msg.Params.Response.Status < 300 {
				collected[msg.Params.RequestID] = msg.Params.Response.URL
			}
		case msg.Method == "Network.loadingFinished":
			reqID := msg.Params.RequestID
			u := collected[reqID]
			if u == "" || msg.Params.EncodedDataLength > maxBytes || !isInterestingURL(u) {
				delete(collected, reqID)
				continue
			}
			if bodyReq != nil {
				continue // already fetching another body; skip
			}
			bodyReq = &pendingBody{id: c.nextID + 1, reqID: reqID, url: u}
			c.nextID = bodyReq.id
			p, _ := json.Marshal(map[string]any{"requestId": reqID})
			if err := writeCommand(bodyReq.id, "Network.getResponseBody", p); err != nil {
				return out, err
			}
		default:
			// other events (Page.loadEventFired, Runtime.*, ...) ignored.
		}
	}
}

// NamedBody is a captured HTTP response (URL + body) for exposure scanning.
type NamedBody struct {
	URL  string `json:"url"`
	Body string `json:"body"`
}

// isInterestingURL reports whether a response URL is worth pulling the body
// for: config/API/settings endpoints and JS bundles (keys often hide there).
func isInterestingURL(u string) bool {
	low := strings.ToLower(u)
	for _, frag := range []string{"/api/", "config", "settings", ".js", "profile", "user", "admin", "auth", "token", "key"} {
		if strings.Contains(low, frag) {
			return true
		}
	}
	return false
}

// isJSONish reports whether a body begins with a JSON object/array token.
func isJSONish(b []byte) bool {
	t := strings.TrimSpace(string(b))
	return strings.HasPrefix(t, "{") || strings.HasPrefix(t, "[")
}

// waitStable polls the rendered outerHTML until its length stabilizes (two
// consecutive identical reads) or ctx is done. It is the same settling probe
// used by Render, extracted for reuse.
func (c *Client) waitStable(ctx context.Context) error {
	evalParams, _ := json.Marshal(map[string]any{
		"expression":    "document.documentElement.outerHTML",
		"returnByValue": true,
	})
	lastLen := -1
	stableCount := 0
	for i := 0; i < 15; i++ {
		time.Sleep(1 * time.Second)
		var eval struct {
			Result struct {
				Value string `json:"value"`
			} `json:"result"`
		}
		if err := c.call(ctx, "Runtime.evaluate", evalParams, &eval); err != nil {
			return err
		}
		l := len(eval.Result.Value)
		if l == lastLen && l > 0 {
			stableCount++
			if stableCount >= 2 {
				return nil
			}
		} else {
			lastLen = l
			stableCount = 0
		}
		if ctx.Err() != nil {
			return ctx.Err()
		}
	}
	return fmt.Errorf("page did not settle within polling window")
}

// evaluateString runs a JS expression via Runtime.evaluate and returns the
// string value (or "" on failure). returnByValue is always set.
func (c *Client) evaluateString(ctx context.Context, expr string) string {
	params, _ := json.Marshal(map[string]any{
		"expression":    expr,
		"returnByValue": true,
	})
	var eval struct {
		Result struct {
			Value string `json:"value"`
		} `json:"result"`
	}
	if err := c.call(ctx, "Runtime.evaluate", params, &eval); err != nil {
		return ""
	}
	return eval.Result.Value
}

// EvalJS is the exported form of evaluateString — runs a JS expression in the
// live page and returns its string result. Used by the mapping panel to locate
// DOM elements (e.g. login doors) for coordinate-based clicking.
func (c *Client) EvalJS(ctx context.Context, expr string) string {
	return c.evaluateString(ctx, expr)
}

// Screenshot captures the current page viewport as a PNG and returns its
// base64-encoded payload. Used by the interactive mapping panel (L3) so a
// human can see the live page before/after each interaction.
func (c *Client) Screenshot(ctx context.Context) (string, error) {
	var res struct {
		Data string `json:"data"`
	}
	params, _ := json.Marshal(map[string]any{
		"format": "png",
		"fromSurface": true,
		"captureBeyondViewport": false,
	})
	if err := c.call(ctx, "Page.captureScreenshot", params, &res); err != nil {
		return "", err
	}
	return res.Data, nil
}

// Click dispatches a real mouse press+release at viewport coordinates (x, y).
// Lightpanda's Input domain mirrors Chrome CDP; coordinates are CSS pixels.
func (c *Client) Click(ctx context.Context, x, y float64) error {
	press, _ := json.Marshal(map[string]any{
		"type": "mousePressed", "x": x, "y": y,
		"button": "left", "clickCount": 1,
	})
	if err := c.call(ctx, "Input.dispatchMouseEvent", press, nil); err != nil {
		return err
	}
	release, _ := json.Marshal(map[string]any{
		"type": "mouseReleased", "x": x, "y": y,
		"button": "left", "clickCount": 1,
	})
	return c.call(ctx, "Input.dispatchMouseEvent", release, nil)
}

// ScrollBy scrolls the page by a relative (dx, dy) offset in CSS pixels and
// waits for the DOM to settle so lazy content has a chance to load.
func (c *Client) ScrollBy(ctx context.Context, dx, dy float64) {
	c.evaluateString(ctx, fmt.Sprintf("window.scrollBy(%v, %v); 'scrolled'", dx, dy))
	_ = c.waitStable(ctx)
}

// CurrentURL returns the page's current location.href (may change after
// client-side navigation / clicks).
func (c *Client) CurrentURL(ctx context.Context) string {
	return c.evaluateString(ctx, "location.href")
}

// PageTitle returns document.title of the live page.
func (c *Client) PageTitle(ctx context.Context) string {
	return c.evaluateString(ctx, "document.title")
}

// WaitSettle waits until the rendered DOM length stabilizes (two identical
// reads) or ctx is done. Exported for the interactive mapping panel.
func (c *Client) WaitSettle(ctx context.Context) error { return c.waitStable(ctx) }

// DOM returns the current rendered outerHTML of the live page.
func (c *Client) DOM(ctx context.Context) string {
	return c.evaluateString(ctx, "document.documentElement.outerHTML")
}

// Interact triggers a few common interactions on the rendered page so that
// lazy-loaded sections (settings panels, user menus, dashboards) render, then
// returns the final outerHTML. Interactions are best-effort and bounded:
//  - scroll to bottom then back to top (triggers infinite-scroll / lazy lists)
//  - click the first element matching settings/admin/account/menu/profile
//    selectors, if visible
// Each step waits for the DOM to settle before the next. This surfaces content
// that only appears after interaction, which is where keys often hide.
func (c *Client) Interact(ctx context.Context) string {
	// 1. Scroll to bottom and back — many frameworks lazy-load on scroll.
	c.evaluateString(ctx, `window.scrollTo(0, document.body.scrollHeight); 'scrolled'`)
	_ = c.waitStable(ctx)
	c.evaluateString(ctx, `window.scrollTo(0, 0); 'top'`)
	_ = c.waitStable(ctx)

	// 2. Try to click a settings/account-style element if visible. The
	//    selector list is deliberately broad but conservative: we only click
	//    elements that are actually visible and not disabled.
	clickExpr := `(() => {
		const sels = [
			'a[href*="settings"], a[href*="admin"], a[href*="account"], a[href*="profile"], a[href*="dashboard"], a[href*="config"]',
			'button[data-testid*="settings"], button[aria-label*="settings" i], button[aria-label*="menu" i]',
			'[class*="settings" i] button, [class*="account" i] button, [class*="profile" i] button',
			'button:has(svg), [role="button"][aria-haspopup="true"]'
		];
		for (const s of sels) {
			const els = document.querySelectorAll(s);
			for (const el of els) {
				if (el.offsetWidth > 0 && el.offsetHeight > 0 && !el.disabled) {
					try { el.click(); return 'clicked:' + s; } catch(e) {}
				}
			}
		}
		return 'no-click';
	})()`
	clicked := c.evaluateString(ctx, clickExpr)
	_ = clicked
	_ = c.waitStable(ctx)

	return c.evaluateString(ctx, "document.documentElement.outerHTML")
}

// Render navigates to url, waits for the page to settle, and returns the
// rendered document HTML (outerHTML).
func (c *Client) Render(ctx context.Context, url string) (string, error) {
	for _, m := range []string{"Page.enable", "Network.enable"} {
		if err := c.call(ctx, m, nil, nil); err != nil {
			return "", err
		}
	}

	// Lightpanda's default UA is "Lightpanda/1.0", which many sites' WAFs
	// flag as a crawler. Override it with a normal browser UA before the
	// navigation so target sites serve their real content.
	uaParams, _ := json.Marshal(map[string]any{
		"userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
	})
	if err := c.call(ctx, "Emulation.setUserAgentOverride", uaParams, nil); err != nil {
		return "", err
	}

	// Some WAFs (e.g. Aliyun Shield in front of freebuf.com) also inspect the
	// request header set — a bare UA alone gets blocked. Send a full browser
	// header profile so normal sites treat Lightpanda as a real Chrome.
	extraHeaders := map[string]any{
		"Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
		"Accept-Language":           "zh-CN,zh;q=0.9,en;q=0.8",
		"Accept-Encoding":           "gzip, deflate, br",
		"Cache-Control":             "no-cache",
		"Connection":                "keep-alive",
		"Sec-Fetch-Dest":            "document",
		"Sec-Fetch-Mode":            "navigate",
		"Sec-Fetch-Site":            "none",
		"Sec-Fetch-User":            "?1",
		"Upgrade-Insecure-Requests": "1",
	}
	hdrParams, _ := json.Marshal(map[string]any{"headers": extraHeaders})
	if err := c.call(ctx, "Network.setExtraHTTPHeaders", hdrParams, nil); err != nil {
		return "", err
	}

	navParams, _ := json.Marshal(map[string]any{"url": url})
	if err := c.call(ctx, "Page.navigate", navParams, nil); err != nil {
		return "", err
	}

	// 等 DOM 稳定：轮询取 outerHTML 长度，连续两次一致才认为页面加载完成。
	// 固定等待窗口对慢站/动态站不够（会拿到半加载的页面），改成轮询直到稳定。
	if err := c.waitStable(ctx); err != nil {
		return "", err
	}

	evalParams, _ := json.Marshal(map[string]any{
		"expression":    "document.documentElement.outerHTML",
		"returnByValue": true,
	})
	var eval struct {
		Result struct {
			Value string `json:"value"`
		} `json:"result"`
	}
	if err := c.call(ctx, "Runtime.evaluate", evalParams, &eval); err != nil {
		return "", err
	}
	html := eval.Result.Value

	// 导航失败时 Lightpanda 会把错误渲染成页面内容，这里识别并报错，
	// 避免把「Navigation failed」错误页当成正文（否则会产生空↔残留的抖动误报）。
	if strings.Contains(html, "Navigation failed") {
		return "", fmt.Errorf("navigation failed (page did not load)")
	}
	return html, nil
}

// call sends one CDP command (session-scoped once attached) and blocks for
// the matching response, skipping over unrelated events.
func (c *Client) call(ctx context.Context, method string, params json.RawMessage, out any) error {
	c.nextID++
	id := c.nextID

	data, err := json.Marshal(cdpMsg{
		ID:        id,
		Method:    method,
		Params:    params,
		SessionID: c.session,
	})
	if err != nil {
		return err
	}
	if err := wsutil.WriteClientText(c.conn, data); err != nil {
		return err
	}

	deadline := time.Now().Add(30 * time.Second)
	for {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if err := c.conn.SetReadDeadline(deadline); err != nil {
			return err
		}
		payload, err := wsutil.ReadServerText(c.conn)
		if err != nil {
			return err
		}
		var r cdpResp
		if err := json.Unmarshal(payload, &r); err != nil {
			continue
		}
		if r.ID != id {
			continue
		}
		if r.Error != nil {
			return fmt.Errorf("cdp error %d: %s", r.Error.Code, r.Error.Message)
		}
		if out != nil && r.Result != nil {
			return json.Unmarshal(r.Result, out)
		}
		return nil
	}
}

// Close closes the underlying WebSocket connection.
func (c *Client) Close() error {
	if c == nil || c.conn == nil {
		return nil
	}
	return c.conn.Close()
}
