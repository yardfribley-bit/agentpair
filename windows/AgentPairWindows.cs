using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;
using System.Windows.Forms;

// Local desktop content only. There is no HTTP UI service or website shell.
[ComVisible(true), ClassInterface(ClassInterfaceType.AutoDispatch)]
public sealed class AppLensDesktopBridge {
 readonly Action<string> handler;
 public AppLensDesktopBridge(Action<string> callback){handler=callback;}
 public void Perform(string payload){handler(payload);}
}
class AgentPairWindows : Form {
 readonly WebBrowser desktop=new WebBrowser();
 readonly Timer timer=new Timer {Interval=2000};
 readonly JavaScriptSerializer json=new JavaScriptSerializer {MaxJsonLength=8388608};
 readonly string folder=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"AgentPair");
 readonly string pageFile=Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"collector.html");
 Dictionary<string,object> state=new Dictionary<string,object>();
 Process collector,capture;
 string server="https://www.chuhaijian.com",deviceId="",selectedId="",status="正在读取本机采集状态";
 DateTimeOffset awaitConnectionAfter=DateTimeOffset.MinValue;
 static string Script {get{return Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"agentpair-windows.ps1");}}
 static string Quote(string s){return "\""+s.Replace("\"","")+"\"";}
 static object Value(Dictionary<string,object> obj,string key){object result;return obj!=null&&obj.TryGetValue(key,out result)?result:null;}
 static string TextValue(Dictionary<string,object> obj,string key){return Convert.ToString(Value(obj,key));}
 static bool Flag(Dictionary<string,object> obj,string key){object value=Value(obj,key);return value!=null&&Convert.ToBoolean(value);}
 static bool MatchingContext(Dictionary<string,object> context,string origin,string identity){return !String.IsNullOrEmpty(identity)&&TextValue(context,"server").TrimEnd('/')==origin&&TextValue(context,"deviceId")==identity;}
 static bool Identifier(string id){return Regex.IsMatch(id??"","^[a-f0-9]{64}$");}
 static string Origin(string origin){Uri uri;if(!Uri.TryCreate(origin,UriKind.Absolute,out uri)||uri.Scheme!="https"||uri.UserInfo!=""||uri.Query!=""||uri.Fragment!=""||uri.AbsolutePath!="/")throw new ArgumentException("请输入有效的 HTTPS 平台根地址。");return uri.GetLeftPart(UriPartial.Authority);}
 static ProcessStartInfo Info(string origin,string pairing,bool once){
  origin=Origin(origin);if(pairing.Length>0&&!Regex.IsMatch(pairing,"^[A-Za-z0-9_-]{20,100}$"))throw new ArgumentException("配对码格式不正确。");
  string args="-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "+Quote(Script)+" -Server "+Quote(origin);
  if(pairing.Length>0)args+=" -PairCode "+Quote(pairing);if(once)args+=" -Once";
  return new ProcessStartInfo(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),"WindowsPowerShell\\v1.0\\powershell.exe"),args){UseShellExecute=false,CreateNoWindow=true,RedirectStandardOutput=true,RedirectStandardError=true};
 }
 AgentPairWindows(){
  Text="AppLens — 上下文采集";Icon=Icon.ExtractAssociatedIcon(Application.ExecutablePath);Size=new Size(1240,900);MinimumSize=new Size(940,680);Font=new Font("Segoe UI",10);BackColor=Color.FromArgb(247,248,250);StartPosition=FormStartPosition.CenterScreen;AutoScaleMode=AutoScaleMode.Dpi;
  try{var path=Path.Combine(folder,"desktop-preferences.json");if(File.Exists(path))server=Origin(TextValue(json.Deserialize<Dictionary<string,object>>(File.ReadAllText(path)),"server"));else {path=Path.Combine(folder,"connection-state.json");if(File.Exists(path))server=Origin(TextValue(json.Deserialize<Dictionary<string,object>>(File.ReadAllText(path)),"server"));}}catch(Exception){status="连接设置未能读取，请检查平台地址。";}
  desktop.Dock=DockStyle.Fill;desktop.ScriptErrorsSuppressed=true;desktop.AllowWebBrowserDrop=false;desktop.IsWebBrowserContextMenuEnabled=false;desktop.WebBrowserShortcutsEnabled=false;desktop.ObjectForScripting=new AppLensDesktopBridge(HandleAction);Controls.Add(desktop);
  desktop.Navigating+=(s,e)=>{string file=e.Url.IsFile?e.Url.LocalPath:"";if(!String.Equals(file,pageFile,StringComparison.OrdinalIgnoreCase)&&!String.Equals(file,Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"capture.html"),StringComparison.OrdinalIgnoreCase))e.Cancel=true;};
  desktop.DocumentCompleted+=(s,e)=>RefreshState();
  timer.Tick+=(s,e)=>RefreshState();timer.Start();
  FormClosing+=(s,e)=>{timer.Stop();StopCollection();if(capture!=null&&!capture.HasExited)File.WriteAllText(Path.Combine(folder,"capture-stop"),"stop");};
  Shown+=(s,e)=>{desktop.Navigate(pageFile);if(Directory.Exists(folder)&&Directory.GetFiles(folder,"*.identity").Length>0)StartCollection("");};
 }
 void HandleAction(string payload){
  if(desktop.Url==null||!desktop.Url.IsFile||!String.Equals(desktop.Url.LocalPath,pageFile,StringComparison.OrdinalIgnoreCase))return;
  try{var action=json.Deserialize<Dictionary<string,object>>(payload);string name=TextValue(action,"action");
   if(name=="select"){string id=TextValue(action,"id");if(Identifier(id)&&Find(id)!=null){selectedId=id;Publish();}}
   else if(name=="pause"){if(collector==null||collector.HasExited)StartCollection("");else StopCollection();RefreshState();}
   else if(name=="retry"){if(collector==null||collector.HasExited){status="采集已暂停，请先恢复再同步。";Publish();}else {StopCollection();StartCollection("");RefreshState();}}
   else if(name=="records"){Directory.CreateDirectory(folder);Process.Start(folder);}
   else if(name=="open")OpenPlatform(false);
   else if(name=="call"){string id=TextValue(action,"id");if(Identifier(id)&&Find(id)!=null)selectedId=id;OpenPlatform(true);}
   else if(name=="enableCapture")EnableCapture();
   else if(name=="pair"){string origin=Origin(TextValue(action,"server")),pairing=TextValue(action,"code");Info(origin,pairing,false);StopCollection();deviceId="";selectedId="";state=new Dictionary<string,object>();awaitConnectionAfter=DateTimeOffset.UtcNow;server=origin;Directory.CreateDirectory(folder);File.WriteAllText(Path.Combine(folder,"desktop-preferences.json"),json.Serialize(new Dictionary<string,object>{{"server",server}}));StartCollection(pairing);}
  }catch(Exception ex){MessageBox.Show(ex.Message,"AppLens",MessageBoxButtons.OK,MessageBoxIcon.Warning);}
 }
 Dictionary<string,object> Find(string id){var records=Value(state,"calls") as IEnumerable;if(records!=null)foreach(object raw in records){var record=raw as Dictionary<string,object>;if(record!=null&&TextValue(record,"id")==id)return record;}return null;}
 void OpenPlatform(bool selected){string path="/model-data?device="+Uri.EscapeDataString(deviceId);if(selected&&Identifier(selectedId))path+="&request="+selectedId;Process.Start(new Uri(new Uri(Origin(server)),path).ToString());}
 void Log(string message){if(String.IsNullOrEmpty(message)||IsDisposed)return;try{BeginInvoke((Action)(()=>{status=message;Publish();}));}catch(InvalidOperationException){}}
 void StartCollection(string pairing){if(collector!=null){if(!collector.HasExited)return;collector.Dispose();collector=null;}try{
  collector=new Process {StartInfo=Info(server,pairing,false),EnableRaisingEvents=true};collector.OutputDataReceived+=(s,e)=>Log(e.Data);collector.ErrorDataReceived+=(s,e)=>{if(!String.IsNullOrEmpty(e.Data))Log("连接或采集失败，请检查配对及 HTTPS 连接。");};collector.Exited+=(s,e)=>Log("采集进程已停止；本机记录保留，可恢复采集。");collector.Start();collector.BeginOutputReadLine();collector.BeginErrorReadLine();if(capture!=null&&!capture.HasExited)File.WriteAllText(Path.Combine(folder,"capture-enabled"),"enabled");status="正在连接并采集……";
  if(pairing.Length>0&&desktop.Document!=null)desktop.Document.InvokeScript("eval",new object[]{"document.getElementById('code').value=''"});
 }catch(Exception ex){if(collector!=null)collector.Dispose();collector=null;status="未能启动采集，请检查连接设置。";MessageBox.Show(ex.Message,"无法启动 AppLens");}}
 void StopCollection(){string gate=Path.Combine(folder,"capture-enabled");if(File.Exists(gate))File.Delete(gate);if(collector!=null){try{if(!collector.HasExited)collector.Kill();}catch(InvalidOperationException){}collector.Dispose();collector=null;}status="采集已暂停；本机记录保留。";}
 void EnableCapture(){
  if(collector==null||collector.HasExited){MessageBox.Show("请先配对并恢复采集，再启用完整正文采集。");return;}
  if(capture!=null&&!capture.HasExited){Log("完整正文代理已运行；等待真实模型请求。");return;}
  if(MessageBox.Show("将备份并修改 WorkBuddy 自身代理设置，重启 WorkBuddy。保留模型请求原文，不保存鉴权请求头或响应。原文可能含敏感数据。退出 AppLens 时会恢复配置并尝试重启 WorkBuddy，请先保存工作。继续？","启用完整正文采集",MessageBoxButtons.OKCancel)!=DialogResult.OK)return;
  Directory.CreateDirectory(folder);string script=Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"workbuddy-network.ps1");var info=new ProcessStartInfo(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),"WindowsPowerShell\\v1.0\\powershell.exe"),"-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "+Quote(script)){UseShellExecute=false,CreateNoWindow=true,RedirectStandardOutput=true,RedirectStandardError=true};capture=new Process {StartInfo=info};capture.OutputDataReceived+=(s,e)=>Log(e.Data);capture.ErrorDataReceived+=(s,e)=>Log(e.Data);try{capture.Start();capture.BeginOutputReadLine();capture.BeginErrorReadLine();}catch(Exception ex){Log(ex.Message);}
 }
 void RefreshState(){try{
  string connectionPath=Path.Combine(folder,"connection-state.json");bool connected=false;
  if(File.Exists(connectionPath)){var connection=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(connectionPath));DateTimeOffset stamp;bool recent=awaitConnectionAfter==DateTimeOffset.MinValue||(DateTimeOffset.TryParse(TextValue(connection,"updatedAt"),out stamp)&&stamp>=awaitConnectionAfter);if(TextValue(connection,"server").TrimEnd('/')==server&&recent){deviceId=TextValue(connection,"deviceId");connected=Flag(connection,"connected");}}
  string path=Path.Combine(folder,"context-state.json");var fresh=File.Exists(path)?json.Deserialize<Dictionary<string,object>>(File.ReadAllText(path)):new Dictionary<string,object>();
  if(MatchingContext(fresh,server,deviceId))state=fresh;else if(!MatchingContext(state,server,deviceId))state=new Dictionary<string,object>();
  if(!state.ContainsKey("calls"))state["calls"]=new object[0];state["server"]=server;state["deviceId"]=deviceId;state["connected"]=connected;state["active"]=collector!=null&&!collector.HasExited;state["appRunning"]=Process.GetProcessesByName("WorkBuddy").Length>0;state["localPath"]=folder;state["canEnableNetworkCapture"]=true;state["status"]=TextValue(state,"error")!=""?TextValue(state,"error"):status;
  string eventPath=Path.Combine(folder,"capture-event.json");state["captureEvent"]=new Dictionary<string,object>();
  if(File.Exists(eventPath)){var captureEvent=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(eventPath));DateTimeOffset stamp;if(DateTimeOffset.TryParse(TextValue(captureEvent,"updatedAt"),out stamp)&&DateTimeOffset.UtcNow-stamp<TimeSpan.FromMinutes(2)&&TextValue(captureEvent,"server").TrimEnd('/')==server&&TextValue(captureEvent,"deviceId")==deviceId){captureEvent.Remove("captureBody");state["captureEvent"]=captureEvent;}}
  if(Find(selectedId)==null){selectedId="";foreach(object raw in (IEnumerable)state["calls"]){var c=raw as Dictionary<string,object>;if(c!=null){selectedId=TextValue(c,"id");break;}}}Publish();
 }catch(IOException){}catch(Exception){Log("本机状态暂时无法读取；未将失败显示为成功。");}}
 void Publish(){try{if(desktop.Document==null)return;var published=new Dictionary<string,object>(state);var record=Find(selectedId);string raw="",digest=TextValue(record,"bodySHA256");
  if(record!=null&&Identifier(selectedId)&&Identifier(digest)){string path=Path.Combine(folder,"contexts",selectedId+"-"+digest+".txt");try{if(File.Exists(path)){raw=File.ReadAllText(path,Encoding.UTF8);using(var hash=SHA256.Create()){string actual=BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(raw))).Replace("-","").ToLowerInvariant();if(actual!=digest){raw="";status="原文校验未通过；下次采集会从源记录重建本机缓存。";}}}else if(TextValue(state,"captureBodyId")==selectedId&&TextValue(state,"captureBodySHA256")==digest)raw=TextValue(state,"captureBody");}catch(IOException){raw="";status="原文暂时无法读取，请重试；未展示未校验的内容。";}}
  published["captureBody"]=raw;published["captureBodyId"]=selectedId;published["captureBodySHA256"]=digest;published["localPath"]=folder;published["server"]=server;published["status"]=TextValue(state,"error")!=""?TextValue(state,"error"):status;published["active"]=collector!=null&&!collector.HasExited;published["canEnableNetworkCapture"]=true;if(!published.ContainsKey("calls"))published["calls"]=new object[0];desktop.Document.InvokeScript("updateDesktopJSON",new object[]{json.Serialize(published)});
 }catch(IOException){}catch(Exception){}}
 static int VerifyCollectionView(){
  int result=3;using(var form=new Form {Text="AppLens 采集界面验收",Size=new Size(1000,1000)})using(var browser=new WebBrowser {Dock=DockStyle.Fill,ScriptErrorsSuppressed=true})using(var timeout=new Timer {Interval=15000}){
   form.Controls.Add(browser);timeout.Tick+=(s,e)=>{timeout.Stop();form.Close();};
   browser.DocumentCompleted+=(s,e)=>{if(!e.Url.IsFile)return;try{
    string id=new string('a',64),digest=new string('b',64),body="{\"messages\":[{\"role\":\"user\",\"content\":\"fixture task\"}]}";
    var record=new Dictionary<string,object>{{"id",id},{"bodySHA256",digest},{"source","workbuddy_network_context"},{"timestamp",1700000000},{"bodyBytes",body.Length},{"receipt",false}};
    var state=new Dictionary<string,object>{{"calls",new object[]{record}},{"active",true},{"connected",true},{"appRunning",true},{"captureBody",body},{"captureBodyId",id},{"captureBodySHA256",digest},{"captureEvent",new Dictionary<string,object>{{"id",id},{"phase","failed"}}}};
    var serializer=new JavaScriptSerializer();browser.Document.InvokeScript("updateCaptureJSON",new object[]{serializer.Serialize(state)});
    if(!browser.Document.GetElementById("inventory").InnerText.Contains("fixture task")||!browser.Document.GetElementById("status").InnerText.Contains("上传失败"))throw new Exception("Native collection rendering failed");
    record["receipt"]=true;state["captureEvent"]=new Dictionary<string,object>{{"id",id},{"phase","received"}};
    browser.Document.InvokeScript("updateCaptureJSON",new object[]{serializer.Serialize(state)});
    if(!browser.Document.GetElementById("inventory").InnerText.Contains("已同步到 AgentPair")||!browser.Document.GetElementById("receipt").InnerText.Contains("一致"))throw new Exception("Native acknowledgement rendering failed");
    result=0;
   }catch(Exception){result=3;}timeout.Stop();form.Close();};
   form.Shown+=(s,e)=>{timeout.Start();browser.Navigate(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"capture.html"));};Application.Run(form);
  }return result;
 }
 static int VerifyDesktopView(){
  int result=3;string page=Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"collector.html");
  var previous=new Dictionary<string,object>{{"server","https://fixture.invalid"},{"deviceId","old-device"}};if(MatchingContext(previous,"https://fixture.invalid","new-device"))return 3;previous["deviceId"]="new-device";if(!MatchingContext(previous,"https://fixture.invalid","new-device"))return 3;
  using(var form=new Form {Text="AppLens 桌面布局验收",Size=new Size(1240,900)})using(var browser=new WebBrowser {Dock=DockStyle.Fill,ScriptErrorsSuppressed=true})using(var timeout=new Timer {Interval=15000}){
   form.Controls.Add(browser);bool tested=false;timeout.Tick+=(s,e)=>{timeout.Stop();form.Close();};
   browser.DocumentCompleted+=(s,e)=>{if(tested||!e.Url.IsFile||!String.Equals(e.Url.LocalPath,page,StringComparison.OrdinalIgnoreCase))return;tested=true;try{
    string id=new string('a',64),digest=new string('b',64),body="{\"messages\":[{\"role\":\"user\",\"content\":\"desktop fixture task\"}]}";
    var record=new Dictionary<string,object>{{"id",id},{"bodySHA256",digest},{"source","workbuddy_generation_context"},{"timestamp",1700000000},{"sessionName","fixture session"},{"bodyBytes",body.Length},{"receipt",false}};
    var state=new Dictionary<string,object>{{"calls",new object[]{record}},{"active",true},{"connected",true},{"appRunning",true},{"server","https://fixture.invalid"},{"deviceId","fixture"},{"localPath","fixture local folder"},{"captureBody",body},{"captureBodyId",id},{"captureBodySHA256",digest}};
    var serializer=new JavaScriptSerializer();browser.Document.InvokeScript("updateDesktopJSON",new object[]{serializer.Serialize(state)});
    if(!browser.Document.GetElementById("request-task").InnerText.Contains("desktop fixture task")||!browser.Document.GetElementById("categories").InnerText.Contains("用户对话"))throw new Exception("Desktop classified content did not render");
    browser.Document.GetElementById("receipt-button").InvokeMember("click");
    if(!browser.Document.GetElementById("proof").InnerText.Contains(id)||browser.Document.GetElementById("proof").InnerText.Contains("平台回执一致"))throw new Exception("Desktop selected evidence mismatch");
    record["receipt"]=true;browser.Document.InvokeScript("updateDesktopJSON",new object[]{serializer.Serialize(state)});
    if(!browser.Document.GetElementById("proof").InnerText.Contains("平台回执一致"))throw new Exception("Desktop acknowledgement did not update");
    state["captureBodySHA256"]="mismatch";browser.Document.InvokeScript("updateDesktopJSON",new object[]{serializer.Serialize(state)});
    if(browser.Document.GetElementById("request-task").InnerText.Contains("desktop fixture task"))throw new Exception("Desktop mismatched body was retained");
    result=0;
   }catch(Exception){result=3;}timeout.Stop();form.Close();};form.Shown+=(s,e)=>{timeout.Start();browser.Navigate(page);};Application.Run(form);
  }return result;
 }
 [STAThread] static int Main(string[] args){
  if(args.Length==1&&args[0]=="--self-test")return File.Exists(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"context.js"))&&File.Exists(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"collector.html"))&&File.Exists(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"applens.svg"))&&File.Exists(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"capture.html"))&&File.Exists(Script)&&File.Exists(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"workbuddy-context.ps1"))&&File.Exists(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"workbuddy-network.ps1"))&&File.Exists(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"capture","mitmdump.exe"))?0:2;
  if(args.Length==3&&args[0]=="--once"){try{using(var p=Process.Start(Info(args[1],args[2],true))){p.OutputDataReceived+=(s,e)=>{};p.ErrorDataReceived+=(s,e)=>{};p.BeginOutputReadLine();p.BeginErrorReadLine();if(!p.WaitForExit(90000)){p.Kill();return 3;}return p.ExitCode;}}catch{return 2;}}
  try{using(var key=Microsoft.Win32.Registry.CurrentUser.CreateSubKey(@"Software\Microsoft\Internet Explorer\Main\FeatureControl\FEATURE_BROWSER_EMULATION")){key.SetValue(Path.GetFileName(Application.ExecutablePath),11001,Microsoft.Win32.RegistryValueKind.DWord);}}catch{}
  Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);if(args.Length==1&&args[0]=="--capture-ui-self-test")return VerifyCollectionView();if(args.Length==1&&args[0]=="--desktop-ui-self-test")return VerifyDesktopView();Application.Run(new AgentPairWindows());return 0;
 }
}
