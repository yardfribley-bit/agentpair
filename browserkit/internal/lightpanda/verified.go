package lightpanda

import (
	"context"
	"encoding/json"
	"fmt"
	"net/url"
)

// EvaluateVerified retains CDP and JavaScript errors, unlike best-effort reads.
func (c *Client) EvaluateVerified(ctx context.Context, expression string) (string, error) {
	params, _ := json.Marshal(map[string]any{"expression": expression, "returnByValue": true})
	var result struct {
		Result struct {
			Value string `json:"value"`
		} `json:"result"`
		Exception json.RawMessage `json:"exceptionDetails"`
	}
	if err := c.call(ctx, "Runtime.evaluate", params, &result); err != nil {
		return "", err
	}
	if len(result.Exception) > 0 && string(result.Exception) != "null" {
		return "", fmt.Errorf("page JavaScript failed: %s", result.Exception)
	}
	return result.Result.Value, nil
}

// NavigateVerified reuses the existing session and settling code without
// imposing the legacy site's fixed user-agent/header profile.
func (c *Client) NavigateVerified(ctx context.Context, raw string) error {
	u, err := url.Parse(raw)
	if err != nil || u.Hostname() == "" || (u.Scheme != "http" && u.Scheme != "https") || u.User != nil {
		return fmt.Errorf("valid HTTP(S) URL required")
	}
	if err = c.call(ctx, "Page.enable", nil, nil); err != nil {
		return err
	}
	params, _ := json.Marshal(map[string]any{"url": raw})
	var result struct {
		ErrorText string `json:"errorText"`
	}
	if err = c.call(ctx, "Page.navigate", params, &result); err != nil {
		return err
	}
	if result.ErrorText != "" {
		return fmt.Errorf("navigation failed: %s", result.ErrorText)
	}
	return c.waitStable(ctx)
}
