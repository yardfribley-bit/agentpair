package lightpanda

import (
	"context"
	"encoding/json"
	"testing"
)

func TestVerifiedJSError(t *testing.T) {
	f := newFakeCDP(t, nil)
	f.handlers["Runtime.evaluate"] = func(json.RawMessage) (any, error) {
		return map[string]any{"exceptionDetails": map[string]any{"text": "selector missing"}}, nil
	}
	c := dialFake(t, f)
	defer c.Close()
	if _, err := c.EvaluateVerified(context.Background(), "test"); err == nil {
		t.Fatal("JavaScript failure was hidden")
	}
}
func TestVerifiedNavigationError(t *testing.T) {
	f := newFakeCDP(t, nil)
	f.handlers["Page.navigate"] = func(json.RawMessage) (any, error) { return map[string]any{"errorText": "network failed"}, nil }
	c := dialFake(t, f)
	defer c.Close()
	if err := c.NavigateVerified(context.Background(), "https://example.org"); err == nil {
		t.Fatal("navigation failure was hidden")
	}
	if err := c.NavigateVerified(context.Background(), "file:///etc/passwd"); err == nil {
		t.Fatal("non HTTP URL accepted")
	}
}
