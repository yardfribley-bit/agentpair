// Loopback-only path adapter. Preserves actual CDP replies; fabricates none.
package main

import (
 "context"
 "encoding/json"
 "time"
 "github.com/gobwas/ws"
 "github.com/gobwas/ws/wsutil"
 "log"
 "net/http"
 "net/http/httputil"
 "net/url"
)

func main() {
 upstream, _ := url.Parse("http://127.0.0.1:9222")
 proxy := httputil.NewSingleHostReverseProxy(upstream)
 director := proxy.Director
 proxy.Director = func(r *http.Request) {
  director(r)
  r.URL.Path = "/"
  r.URL.RawPath = ""
  r.URL.RawQuery = ""
  r.Header.Del("Origin")
 }
 http.HandleFunc("/devtools/browser/lightpanda", func(w http.ResponseWriter,r *http.Request) {proxy.ServeHTTP(w,r)})
 http.HandleFunc("/json/version", func(w http.ResponseWriter,r *http.Request) {
  ctx,cancel:=context.WithTimeout(r.Context(),5*time.Second); defer cancel()
  conn,_,_,err:=ws.DefaultDialer.Dial(ctx,"ws://127.0.0.1:9222/")
  if err!=nil {http.Error(w,err.Error(),502);return}; defer conn.Close()
  conn.SetDeadline(time.Now().Add(5*time.Second))
  if err=wsutil.WriteClientText(conn,[]byte(`{"id":1,"method":"Browser.getVersion"}`));err!=nil {http.Error(w,err.Error(),502);return}
  for {
   data,err:=wsutil.ReadServerText(conn);if err!=nil {http.Error(w,err.Error(),502);return}
   var reply struct {ID int `json:"id"`; Result map[string]interface{} `json:"result"`; Error interface{} `json:"error"`}
   if err=json.Unmarshal(data,&reply);err!=nil {http.Error(w,err.Error(),502);return}
   if reply.ID!=1 {continue}
   if reply.Error!=nil || reply.Result==nil {http.Error(w,string(data),502);return}
   w.Header().Set("Content-Type","application/json")
   json.NewEncoder(w).Encode(map[string]interface{}{"Browser":reply.Result["product"],"Protocol-Version":reply.Result["protocolVersion"],"User-Agent":reply.Result["userAgent"],"V8-Version":reply.Result["jsVersion"],"webSocketDebuggerUrl":"ws://127.0.0.1:9223/devtools/browser/lightpanda"})
   return
  }
 })
 log.Fatal(http.ListenAndServe("127.0.0.1:9223",nil))
}
