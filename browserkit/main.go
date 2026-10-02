// AgentPair's private stdio browser worker, reusing the WebLens CDP client.
// One process owns one serial session. No HTTP control service is exposed.
package main

import (
	"agentpair/browserkit/internal/lightpanda"
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"time"
)

type request struct {
	ID       string `json:"id"`
	Tool     string `json:"tool"`
	URL      string `json:"url"`
	Selector string `json:"selector"`
}

func main() {
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	client, err := lightpanda.Connect(ctx, "127.0.0.1:9222")
	cancel()
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	defer client.Close()
	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 4096), 65536)
	out := json.NewEncoder(os.Stdout)
	for scanner.Scan() {
		var req request
		if err := json.Unmarshal(scanner.Bytes(), &req); err != nil {
			_ = out.Encode(map[string]any{"ok": false, "error": "invalid JSON"})
			continue
		}
		ctx, cancel := context.WithTimeout(context.Background(), 35*time.Second)
		result, err := execute(ctx, client, req)
		cancel()
		response := map[string]any{"id": req.ID, "ok": err == nil, "result": result}
		if err != nil {
			response["error"] = err.Error()
		}
		if err = out.Encode(response); err != nil {
			return
		}
	}
}

func execute(ctx context.Context, c *lightpanda.Client, r request) (any, error) {
	switch r.Tool {
	case "browser.open":
		if err := c.NavigateVerified(ctx, r.URL); err != nil {
			return nil, err
		}
	case "browser.text", "browser.snapshot":
	case "browser.click":
		if r.Selector == "" {
			return nil, fmt.Errorf("selector required")
		}
		selector, _ := json.Marshal(r.Selector)
		_, err := c.EvaluateVerified(ctx, `(()=>{const e=document.querySelector(`+string(selector)+`);if(!e)throw new Error('selector not found');e.click();return 'clicked'})()`)
		if err != nil {
			return nil, err
		}
		if err = c.WaitSettle(ctx); err != nil {
			return nil, err
		}
	default:
		return nil, fmt.Errorf("unknown tool")
	}
	return c.EvaluateVerified(ctx, `JSON.stringify({title:document.title,url:location.href,text:document.body?.innerText||'',elements:Array.from(document.querySelectorAll('a,button,input,select,textarea')).slice(0,100).map(e=>({tag:e.tagName,id:e.id,text:(e.innerText||e.getAttribute('aria-label')||'').slice(0,200)}))})`)
}
