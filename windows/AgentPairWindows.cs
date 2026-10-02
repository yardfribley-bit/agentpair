using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;
using System.Windows.Forms;

class AgentPairWindows : Form {
 TextBox server=new TextBox(),code=new TextBox(),output=new TextBox();
 Label connection=new Label(),appState=new Label(),collectorState=new Label(),uploadState=new Label(),stage=new Label(),detail=new Label();
 Button start=new Button(),stop=new Button();
 Panel overview=new Panel(),settings=new Panel();
 DataGridView calls=new DataGridView();
 WebBrowser captureScene=new WebBrowser();
 Dictionary<string,object> lastCaptureState=new Dictionary<string,object>();
 Process collector,capture;
 string deviceId="",selectedId="";
 readonly string folder=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"AgentPair");
 readonly JavaScriptSerializer json=new JavaScriptSerializer {MaxJsonLength=2097152};
 static string Script {get{return Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"agentpair-windows.ps1");}}
 static string Quote(string s){return "\"" + s.Replace("\"","") + "\"";}
 static ProcessStartInfo Info(string origin,string pairing,bool once){
  Uri uri;if(!Uri.TryCreate(origin,UriKind.Absolute,out uri)||uri.Scheme!="https"||uri.UserInfo!=""||uri.Query!=""||uri.Fragment!=""||uri.AbsolutePath!="/")throw new ArgumentException("请输入可信 HTTPS 平台地址。");
  if(pairing.Length>0&&!Regex.IsMatch(pairing,"^[A-Za-z0-9_-]{20,100}$"))throw new ArgumentException("配对码格式不正确。");
  var args="-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "+Quote(Script)+" -Server "+Quote(uri.GetLeftPart(UriPartial.Authority));
  if(pairing.Length>0)args+=" -PairCode "+Quote(pairing);if(once)args+=" -Once";
  return new ProcessStartInfo(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),"WindowsPowerShell\\v1.0\\powershell.exe"),args){UseShellExecute=false,CreateNoWindow=true,RedirectStandardOutput=true,RedirectStandardError=true};
 }
 Label LabelAt(Control parent,string text,int x,int y,int w,int h,int size=10){var l=new Label {Text=text,Location=new Point(x,y),Size=new Size(w,h),Font=new Font("Microsoft YaHei UI",size),ForeColor=Color.FromArgb(35,53,82)};parent.Controls.Add(l);return l;}
 Button ButtonAt(Control parent,string text,int x,int y,int w,Action action){var b=new Button {Text=text,Location=new Point(x,y),Size=new Size(w,34),FlatStyle=FlatStyle.Flat,BackColor=Color.White};parent.Controls.Add(b);b.Click+=(s,e)=>action();return b;}
 AgentPairWindows(){
  Text="AppLens · 模型上下文采集";Icon=Icon.ExtractAssociatedIcon(Application.ExecutablePath);Size=new Size(1220,930);MinimumSize=new Size(1100,850);Font=new Font("Microsoft YaHei UI",10);BackColor=Color.White;StartPosition=FormStartPosition.CenterScreen;
  var sidebar=new Panel {Dock=DockStyle.Left,Width=185,BackColor=Color.FromArgb(244,248,255)};Controls.Add(sidebar);
  LabelAt(sidebar,"◢ AppLens",18,28,160,44,20);
  ButtonAt(sidebar,"▣  采集总览",15,115,155,()=>Page(false));
  ButtonAt(sidebar,"▤  上传记录",15,160,155,()=>Page(false));
  ButtonAt(sidebar,"⚙  连接设置",15,205,155,()=>Page(true));
  LabelAt(sidebar,"本机 · Windows\n模型上下文采集",18,700,160,55);
  var main=new Panel {Dock=DockStyle.Fill,Padding=new Padding(25),AutoScroll=true};Controls.Add(main);main.BringToFront();
  LabelAt(main,"模型上下文采集",24,24,480,45,24);
  LabelAt(main,"发现本机模型输入，保留原文并同步至所属账号。",24,75,680,30);
  ButtonAt(main,"在平台查看 ↗",700,28,145,()=>OpenPlatform(false));
  stop=ButtonAt(main,"暂停采集",850,28,130,()=>{if(collector==null)StartCollection();else StopCollection();});
  overview.Location=new Point(24,116);overview.Size=new Size(950,1240);main.Controls.Add(overview);
  connection=LabelAt(overview,"尚未配对 · 请打开连接设置",0,0,730,40);connection.BackColor=Color.FromArgb(237,245,255);
  ButtonAt(overview,"连接设置",790,0,140,()=>Page(true));
  appState=LabelAt(overview,"应用\nWorkBuddy · 等待检测",0,60,285,75,12);
  collectorState=LabelAt(overview,"采集器\n等待连接",315,60,285,75,12);
  uploadState=LabelAt(overview,"上传同步\n尚未上传",630,60,290,75,12);
  foreach(var l in new[]{appState,collectorState,uploadState}){l.BackColor=Color.FromArgb(246,249,255);l.Padding=new Padding(12);}
  ButtonAt(overview,"重试",815,105,100,()=>{StopCollection();StartCollection();});
  var warning=LabelAt(overview,"源记录可能截断；回执哈希一致只确认采集字节。完整请求体通道需明确接入，不推断远端接收。",0,153,930,50);warning.BackColor=Color.FromArgb(255,247,233);warning.Padding=new Padding(10);
  captureScene.Location=new Point(0,215);captureScene.Size=new Size(930,520);captureScene.ScriptErrorsSuppressed=true;captureScene.AllowWebBrowserDrop=false;captureScene.IsWebBrowserContextMenuEnabled=false;captureScene.WebBrowserShortcutsEnabled=false;captureScene.DocumentCompleted+=(s,e)=>PublishCapture();captureScene.Navigating+=(s,e)=>{if(!e.Url.IsFile||!String.Equals(e.Url.LocalPath,Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"capture.html"),StringComparison.OrdinalIgnoreCase))e.Cancel=true;};overview.Controls.Add(captureScene);captureScene.Navigate(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"capture.html"));
  LabelAt(overview,"最近模型调用",0,750,700,30,14);
  calls.Location=new Point(0,785);calls.Size=new Size(930,215);calls.BackgroundColor=Color.White;calls.BorderStyle=BorderStyle.FixedSingle;calls.ReadOnly=true;calls.AllowUserToAddRows=false;calls.AllowUserToDeleteRows=false;calls.RowHeadersVisible=false;calls.AutoSizeColumnsMode=DataGridViewAutoSizeColumnsMode.Fill;calls.SelectionMode=DataGridViewSelectionMode.FullRowSelect;calls.MultiSelect=false;
  foreach(var title in new[]{"时间 / 调用","会话","模型","原文大小","完整性","上传"})calls.Columns.Add(title,title);
  overview.Controls.Add(calls);calls.SelectionChanged+=(s,e)=>SelectCall();
  LabelAt(overview,"选中调用 · 上传阶段",0,1017,800,30,14);
  stage=LabelAt(overview,"发现记录 → 本机保存 → 待上传 → 等待平台回执",0,1060,920,40,12);
  detail=LabelAt(overview,"请选择一条调用",0,1108,920,45);
  ButtonAt(overview,"查看这次调用 ↗",0,1165,180,()=>OpenPlatform(true));
  ButtonAt(overview,"打开本机记录",195,1165,150,()=>{Directory.CreateDirectory(folder);Process.Start(folder);});
  ButtonAt(overview,"启用完整正文采集",360,1165,185,()=>EnableCapture());
  settings.Location=new Point(24,116);settings.Size=new Size(930,500);main.Controls.Add(settings);settings.Visible=false;
  LabelAt(settings,"连接与首次配对",0,0,750,40,18);
  LabelAt(settings,"平台地址",0,60,750,30);server.Location=new Point(0,95);server.Size=new Size(740,30);server.Text="https://50.118.187.180/";settings.Controls.Add(server);
  LabelAt(settings,"一次性配对码（已配对可留空）",0,145,650,30);code.Location=new Point(0,180);code.Size=new Size(740,30);code.UseSystemPasswordChar=true;settings.Controls.Add(code);
  start=ButtonAt(settings,"配对并开始同步",0,235,190,()=>StartCollection());
  LabelAt(settings,"设备凭证由 Windows DPAPI 保护；关闭窗口停止采集。\n只采模型请求输入，不单独采工具事件。",0,290,820,70);
  output.Location=new Point(24,1370);output.Size=new Size(930,45);output.Multiline=true;output.ReadOnly=true;output.BorderStyle=BorderStyle.None;output.ForeColor=Color.FromArgb(112,130,156);main.Controls.Add(output);
  var timer=new Timer {Interval=2000};timer.Tick+=(s,e)=>RefreshState();timer.Start();
  FormClosing+=(s,e)=>{StopCollection();if(capture!=null&&!capture.HasExited)File.WriteAllText(Path.Combine(folder,"capture-stop"),"stop");};
  Shown+=(s,e)=>{if(Directory.Exists(folder)&&Directory.GetFiles(folder,"*.identity").Length>0)StartCollection();else Page(true);};
 }
 void Page(bool config){settings.Visible=config;overview.Visible=!config;}
 void EnableCapture(){
  if(collector==null||collector.HasExited){MessageBox.Show("请先配对并恢复采集，再启用完整正文采集。");return;}
  if(capture!=null&&!capture.HasExited){Log("完整正文代理已运行；等待真实模型请求。");return;}
  if(MessageBox.Show("将备份并修改 WorkBuddy 自身代理设置，重启 WorkBuddy。保留模型请求原文，不保存鉴权请求头或响应。原文可能含敏感数据。退出 AppLens 时会恢复配置并尝试重启 WorkBuddy，请先保存工作。继续？","启用完整正文采集",MessageBoxButtons.OKCancel)!=DialogResult.OK)return;
  Directory.CreateDirectory(folder);
  var script=Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"workbuddy-network.ps1");
  var info=new ProcessStartInfo(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),"WindowsPowerShell\\v1.0\\powershell.exe"),"-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "+Quote(script)){UseShellExecute=false,CreateNoWindow=true,RedirectStandardOutput=true,RedirectStandardError=true};
  capture=new Process {StartInfo=info};capture.OutputDataReceived+=(s,e)=>Log(e.Data);capture.ErrorDataReceived+=(s,e)=>Log(e.Data);
  try{capture.Start();capture.BeginOutputReadLine();capture.BeginErrorReadLine();}catch(Exception ex){Log(ex.Message);}
 }
 void OpenPlatform(bool selected){try{Info(server.Text.Trim(),"",false);var path="/model-data?device="+Uri.EscapeDataString(deviceId);if(selected&&selectedId!="")path+="&request="+selectedId;Process.Start(new Uri(new Uri(server.Text.Trim()),path).ToString());}catch(Exception ex){MessageBox.Show(ex.Message);}}
 void Log(string line){if(String.IsNullOrEmpty(line)||IsDisposed)return;try{BeginInvoke((Action)(()=>output.Text=line));}catch(InvalidOperationException){}}
 void StartCollection(){if(collector!=null){if(!collector.HasExited)return;collector.Dispose();collector=null;}try{
  collector=new Process {StartInfo=Info(server.Text.Trim(),code.Text.Trim(),false),EnableRaisingEvents=true};
  collector.OutputDataReceived+=(s,e)=>Log(e.Data);collector.ErrorDataReceived+=(s,e)=>{if(!String.IsNullOrEmpty(e.Data))Log("连接或采集失败，请检查配对及 HTTPS 连接。");};
  collector.Exited+=(s,e)=>Log("连接进程已停止，可重试；已配对时请留空配对码。");
  collector.Start();collector.BeginOutputReadLine();collector.BeginErrorReadLine();if(capture!=null&&!capture.HasExited)File.WriteAllText(Path.Combine(folder,"capture-enabled"),"enabled");code.Clear();Page(false);stop.Text="暂停采集";Log("正在连接并采集……");
 }catch(Exception ex){collector=null;MessageBox.Show(ex.Message,"无法启动");}}
 void StopCollection(){var gate=Path.Combine(folder,"capture-enabled");if(File.Exists(gate))File.Delete(gate);if(collector!=null){try{if(!collector.HasExited)collector.Kill();}catch(InvalidOperationException){}collector.Dispose();collector=null;}stop.Text="恢复采集";collectorState.Text="采集器\n已暂停";}
 void PublishCapture(){try{if(captureScene.Document!=null)captureScene.Document.InvokeScript("updateCaptureJSON",new object[]{json.Serialize(lastCaptureState)});}catch(Exception){}}
 void RefreshState(){try{
  var connectedPath=Path.Combine(folder,"connection-state.json");
  if(File.Exists(connectedPath)){
   var c=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(connectedPath));
   if(Convert.ToString(c["server"])==server.Text.TrimEnd('/')){
    deviceId=Convert.ToString(c["deviceId"]);DateTimeOffset updated;
    bool live=DateTimeOffset.TryParse(Convert.ToString(c["updatedAt"]),out updated)&&DateTimeOffset.UtcNow-updated<TimeSpan.FromSeconds(45)&&Convert.ToBoolean(c["connected"]);
    connection.Text=(live?"已连接平台 · 心跳正常 · ":"连接中断 · ")+deviceId;
   }
  }
  var path=Path.Combine(folder,"context-state.json");
  var state=File.Exists(path)?json.Deserialize<Dictionary<string,object>>(File.ReadAllText(path)):new Dictionary<string,object>{{"server",server.Text.TrimEnd('/')},{"deviceId",deviceId},{"appRunning",Process.GetProcessesByName("WorkBuddy").Length>0},{"calls",new object[0]},{"error",""}};
  if(Convert.ToString(state["server"])!=server.Text.TrimEnd('/'))return;
  deviceId=Convert.ToString(state["deviceId"]);
  state["active"]=collector!=null&&!collector.HasExited;state["connected"]=deviceId!="";
  var eventPath=Path.Combine(folder,"capture-event.json");if(File.Exists(eventPath)){var ev=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(eventPath));DateTimeOffset stamp;if(DateTimeOffset.TryParse(Convert.ToString(ev["updatedAt"]),out stamp)&&DateTimeOffset.UtcNow-stamp<TimeSpan.FromMinutes(2)&&Convert.ToString(ev["server"])==server.Text.TrimEnd('/')&&Convert.ToString(ev["deviceId"])==deviceId)state["captureEvent"]=ev;}lastCaptureState=state;PublishCapture();
  appState.Text="应用\nWorkBuddy · "+(Convert.ToBoolean(state["appRunning"])?"运行中":"未运行");
  collectorState.Text="采集器\n"+(collector!=null&&!collector.HasExited?"正在采集":"已暂停");
  var records=(IEnumerable)state["calls"];int synced=0,count=0;calls.Rows.Clear();
  foreach(Dictionary<string,object> c in records){count++;bool ok=Convert.ToBoolean(c["receipt"]);if(ok)synced++;string id=Convert.ToString(c["id"]);
   string integrity=Convert.ToString(c["recordStatus"]);integrity=integrity=="wire_length_matched"?"HTTP 正文 · 长度一致":integrity=="wire_length_unknown"?"HTTP 正文 · 长度未验证":integrity=="truncated"?"日志截断":integrity=="parseable"?"日志可解析":"未验证";
   int row=calls.Rows.Add(DateTimeOffset.FromUnixTimeMilliseconds((long)(Convert.ToDouble(c["timestamp"])*1000)).LocalDateTime.ToString("HH:mm:ss")+" · "+id.Substring(0,8),c["sessionName"],c["model"]??"未采集",(Convert.ToDouble(c["bodyBytes"])/1024).ToString("F1")+" KB",integrity,ok?"回执哈希一致":"待上传");calls.Rows[row].Tag=c;if(id==selectedId)calls.Rows[row].Selected=true;
  }uploadState.Text="上传同步\n"+synced+" / "+count+" 已确认";if(Convert.ToString(state["error"])!="")output.Text=Convert.ToString(state["error"]);SelectCall();
 }catch(IOException){}catch(Exception){Log("调用状态读取失败；未将失败显示为成功。");}}
 void SelectCall(){if(calls.SelectedRows.Count==0)return;var c=calls.SelectedRows[0].Tag as Dictionary<string,object>;if(c==null)return;selectedId=Convert.ToString(c["id"]);bool ok=Convert.ToBoolean(c["receipt"]);stage.Text="发现记录 → 本机保存 → "+(ok?"上传成功 → 平台回执一致":"待上传 → 等待平台回执");detail.Text="SHA256 "+c["bodySHA256"]+"\n"+c["modelEvidence"];}
 [STAThread] static int Main(string[] args){
  if(args.Length==1&&args[0]=="--self-test")return File.Exists(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"capture.html"))&&File.Exists(Script)&&File.Exists(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"workbuddy-context.ps1"))&&File.Exists(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"workbuddy-network.ps1"))&&File.Exists(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"capture","mitmdump.exe"))?0:2;
  if(args.Length==3&&args[0]=="--once"){try{using(var p=Process.Start(Info(args[1],args[2],true))){p.OutputDataReceived+=(s,e)=>{};p.ErrorDataReceived+=(s,e)=>{};p.BeginOutputReadLine();p.BeginErrorReadLine();if(!p.WaitForExit(90000)){p.Kill();return 3;}return p.ExitCode;}}catch{return 2;}}
  try{using(var key=Microsoft.Win32.Registry.CurrentUser.CreateSubKey(@"Software\Microsoft\Internet Explorer\Main\FeatureControl\FEATURE_BROWSER_EMULATION")){key.SetValue(Path.GetFileName(Application.ExecutablePath),11001,Microsoft.Win32.RegistryValueKind.DWord);}}catch{}
  Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);Application.Run(new AgentPairWindows());return 0;
 }
}
