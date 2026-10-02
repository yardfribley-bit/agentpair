package lightpanda

import (
	"context"
	"encoding/json"
	"net"
	"testing"
	"time"

	"github.com/gobwas/ws"
	"github.com/gobwas/ws/wsutil"
)

// fakeCDP is a minimal in-memory CDP endpoint that answers session setup and
// a configurable set of method handlers. It lets us test Client methods
// without a real Lightpanda process.
type fakeCDP struct {
	ln       net.Listener
	handlers map[string]func(params json.RawMessage) (any, error)
	events   chan any // extra events to push (e.g. Network.responseReceived)
	// trigger maps a command method to a slice of events to push after that
	// command is handled. This models real CDP behavior where network events
	// arrive on the same connection after navigation.
	trigger map[string][]map[string]any
}

func newFakeCDP(t *testing.T, handlers map[string]func(params json.RawMessage) (any, error)) *fakeCDP {
	t.Helper()
	if handlers == nil {
		handlers = map[string]func(params json.RawMessage) (any, error){}
	}
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	f := &fakeCDP{ln: ln, handlers: handlers, events: make(chan any, 32), trigger: map[string][]map[string]any{}}
	go f.serve(t)
	t.Cleanup(func() { _ = ln.Close() })
	return f
}

func (f *fakeCDP) serve(t *testing.T) {
	for {
		conn, err := f.ln.Accept()
		if err != nil {
			return
		}
		go f.handleConn(t, conn)
	}
}

func (f *fakeCDP) handleConn(t *testing.T, conn net.Conn) {
	defer conn.Close()
	_, err := ws.Upgrade(conn)
	if err != nil {
		return
	}
	for {
		payload, err := wsutil.ReadClientText(conn)
		if err != nil {
			return
		}
		var msg struct {
			ID     int             `json:"id"`
			Method string          `json:"method"`
			Params json.RawMessage `json:"params"`
		}
		if err := json.Unmarshal(payload, &msg); err != nil {
			continue
		}
		// Reply to the matching command; session setup is implicit.
		var result any
		if h, ok := f.handlers[msg.Method]; ok {
			result, err = h(msg.Params)
			if err != nil {
				result = map[string]any{"error": map[string]any{"code": -32000, "message": err.Error()}}
			}
		} else {
			result = map[string]any{}
		}
		resp, _ := json.Marshal(map[string]any{"id": msg.ID, "result": result})
		_ = wsutil.WriteServerText(conn, resp)
		// Push any events triggered by this command (e.g. network events
		// after navigation), mimicking the real CDP event stream.
		for _, ev := range f.trigger[msg.Method] {
			data, _ := json.Marshal(ev)
			_ = wsutil.WriteServerText(conn, data)
		}
	}
}

func dialFake(t *testing.T, f *fakeCDP) *Client {
	t.Helper()
	addr := f.ln.Addr().String()
	conn, _, _, err := ws.Dial(context.Background(), "ws://"+addr+"/")
	if err != nil {
		t.Fatal(err)
	}
	c := &Client{conn: conn}
	// initSession needs Target.createBrowserContext/createTarget/attachToTarget.
	f.handlers["Target.createBrowserContext"] = func(json.RawMessage) (any, error) {
		return map[string]any{"browserContextId": "bc1"}, nil
	}
	f.handlers["Target.createTarget"] = func(json.RawMessage) (any, error) {
		return map[string]any{"targetId": "t1"}, nil
	}
	f.handlers["Target.attachToTarget"] = func(json.RawMessage) (any, error) {
		return map[string]any{"sessionId": "s1"}, nil
	}
	if err := c.initSession(context.Background()); err != nil {
		t.Fatal(err)
	}
	return c
}

func TestStorage(t *testing.T) {
	f := newFakeCDP(t, nil)
	f.handlers["Runtime.evaluate"] = func(json.RawMessage) (any, error) {
		return map[string]any{
			"result": map[string]any{
				"value": `{"localStorage":{"apikey":"sk-abcdefghijklmnopqrstuvwxyz123456"},"sessionStorage":{"t":"x"},"cookies":{"session":"abc123"}}`,
			},
		}, nil
	}
	c := dialFake(t, f)
	defer c.Close()

	snap, err := c.Storage(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if snap.LocalStorage["apikey"] != "sk-abcdefghijklmnopqrstuvwxyz123456" {
		t.Fatalf("unexpected localStorage: %+v", snap)
	}
	if snap.Cookies["session"] != "abc123" {
		t.Fatalf("unexpected cookies: %+v", snap)
	}
}

func TestNetworkCaptureCollectsResponses(t *testing.T) {
	f := newFakeCDP(t, nil)
	f.handlers["Network.enable"] = func(json.RawMessage) (any, error) { return map[string]any{}, nil }
	f.handlers["Page.navigate"] = func(json.RawMessage) (any, error) { return map[string]any{}, nil }
	f.handlers["Runtime.evaluate"] = func(json.RawMessage) (any, error) {
		return map[string]any{"result": map[string]any{"value": "<html></html>"}}, nil
	}
	f.handlers["Network.getResponseBody"] = func(params json.RawMessage) (any, error) {
		return map[string]any{"body": `{"api_key":"sk-abcdefghijklmnopqrstuvwxyz123456"}`}, nil
	}
	// After navigation, the server pushes the response + loadingFinished pair.
	f.trigger["Page.navigate"] = []map[string]any{
		{"method": "Network.responseReceived", "params": map[string]any{
			"requestId": "r1",
			"response":  map[string]any{"url": "https://x.example/api/v1/settings", "status": 200},
		}},
		{"method": "Network.loadingFinished", "params": map[string]any{
			"requestId": "r1", "encodedDataLength": 64,
		}},
	}
	c := dialFake(t, f)
	defer c.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	nb, err := c.NetworkCapture(ctx, "https://x.example/", 1<<20)
	if err != nil {
		t.Fatal(err)
	}
	if len(nb) == 0 {
		t.Fatal("expected captured response bodies")
	}
	if nb[0].URL != "https://x.example/api/v1/settings" {
		t.Fatalf("unexpected URL: %s", nb[0].URL)
	}
}
