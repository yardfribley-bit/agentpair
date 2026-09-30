using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Text.RegularExpressions;
using System.Windows.Forms;

// No remote execution interface: this app runs only the bundled inventory collector.
class AgentPairWindows : Form {
    TextBox server = new TextBox(), code = new TextBox(), output = new TextBox();
    Button start = new Button(), stop = new Button();
    Process collector;
    static string Script { get { return Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "agentpair-windows.ps1"); } }
    static string Quote(string s) { return "\"" + s.Replace("\"", "") + "\""; }
    static ProcessStartInfo Info(string origin, string pairing, bool once) {
        Uri uri;
        if (!Uri.TryCreate(origin, UriKind.Absolute, out uri) || uri.Scheme != "https" || uri.UserInfo != "" || uri.Query != "" || uri.Fragment != "" || uri.AbsolutePath != "/")
            throw new ArgumentException("请输入可信 HTTPS 平台地址。");
        if (pairing.Length > 0 && !Regex.IsMatch(pairing, "^[A-Za-z0-9_-]{20,100}$")) throw new ArgumentException("配对码格式不正确。");
        var args = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File " + Quote(Script) + " -Server " + Quote(uri.GetLeftPart(UriPartial.Authority));
        if (pairing.Length > 0) args += " -PairCode " + Quote(pairing);
        if (once) args += " -Once";
        return new ProcessStartInfo(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "WindowsPowerShell\\v1.0\\powershell.exe"), args) {
            UseShellExecute=false, CreateNoWindow=true, RedirectStandardOutput=true, RedirectStandardError=true
        };
    }
    AgentPairWindows() {
        Text="AgentPair · Windows 设备接入"; Size=new Size(700,580); MinimumSize=new Size(650,530);
        Font=new Font("Microsoft YaHei UI",10); StartPosition=FormStartPosition.CenterScreen; BackColor=Color.FromArgb(246,248,252);
        var layout=new TableLayoutPanel { Dock=DockStyle.Fill, Padding=new Padding(24), ColumnCount=1, RowCount=10 };
        Controls.Add(layout);
        layout.Controls.Add(new Label {Text="连接设备，分析本机应用",AutoSize=true,Font=new Font(Font.FontFamily,18,FontStyle.Bold)});
        layout.Controls.Add(new Label {Text="采集进程名称 / PID、已安装应用；不采集命令行或文件内容。",AutoSize=true});
        layout.Controls.Add(new Label {Text="AgentPair 平台地址",AutoSize=true});server.Text="https://50.118.187.180/";server.Dock=DockStyle.Fill;layout.Controls.Add(server);
        layout.Controls.Add(new Label {Text="一次性配对码（平台「我的设备」获取，已配对可留空）",AutoSize=true});code.Dock=DockStyle.Fill;code.UseSystemPasswordChar=true;layout.Controls.Add(code);
        var buttons=new FlowLayoutPanel {AutoSize=true,Dock=DockStyle.Fill};start.Text="连接并开始采集";start.AutoSize=true;stop.Text="停止采集";stop.AutoSize=true;stop.Enabled=false;
        var open=new Button {Text="打开我的设备",AutoSize=true};buttons.Controls.Add(start);buttons.Controls.Add(stop);buttons.Controls.Add(open);layout.Controls.Add(buttons);
        output.Multiline=true;output.ReadOnly=true;output.ScrollBars=ScrollBars.Vertical;output.Dock=DockStyle.Fill;layout.Controls.Add(output);layout.RowStyles.Clear();
        for(int i=0;i<7;i++)layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));layout.RowStyles.Add(new RowStyle(SizeType.Percent,100));
        layout.Controls.Add(new Label {Text="每 30 秒更新。关闭窗口即停止采集；设备凭证使用 Windows DPAPI 保护。",AutoSize=true});
        start.Click+=(s,e)=>StartCollection();stop.Click+=(s,e)=>StopCollection();FormClosing+=(s,e)=>StopCollection();
        open.Click+=(s,e)=>{try {var i=Info(server.Text.Trim(),"",false);Process.Start(new Uri(new Uri(server.Text.Trim()),"/devices").ToString());}catch(Exception ex){MessageBox.Show(ex.Message);}};
    }
    void Log(string line) {if(String.IsNullOrEmpty(line)||IsDisposed)return;try {BeginInvoke((Action)(()=>output.AppendText(line+Environment.NewLine)));}catch(InvalidOperationException){}}
    void StartCollection() {
        try {
            collector=new Process {StartInfo=Info(server.Text.Trim(),code.Text.Trim(),false),EnableRaisingEvents=true};
            collector.OutputDataReceived+=(s,e)=>Log(e.Data);
            collector.ErrorDataReceived+=(s,e)=>{if(!String.IsNullOrEmpty(e.Data))Log("连接器报告错误：请检查配对码、设备绑定和 HTTPS 连接。");};
            collector.Exited+=(s,e)=>{if(!IsDisposed)try{BeginInvoke((Action)(()=>{start.Enabled=true;stop.Enabled=false;server.Enabled=true;code.Enabled=true;Log("采集已停止。");}));}catch(InvalidOperationException){}};
            collector.Start();collector.BeginOutputReadLine();collector.BeginErrorReadLine();code.Clear();start.Enabled=false;stop.Enabled=true;server.Enabled=false;code.Enabled=false;Log("正在连接平台……");
        }catch(Exception ex){MessageBox.Show(ex.Message,"无法启动");}
    }
    void StopCollection() {if(collector!=null){try{if(!collector.HasExited)collector.Kill();}catch(InvalidOperationException){}collector.Dispose();collector=null;}}
    [STAThread] static int Main(string[] args) {
        if(args.Length==1&&args[0]=="--self-test")return File.Exists(Script)?0:2;
        // Headless acceptance path uses the exact installed collector, no alternate implementation.
        if(args.Length==3&&args[0]=="--once") {
            try {using(var p=Process.Start(Info(args[1],args[2],true))){p.OutputDataReceived+=(s,e)=>{};p.ErrorDataReceived+=(s,e)=>{};p.BeginOutputReadLine();p.BeginErrorReadLine();if(!p.WaitForExit(90000)){p.Kill();return 3;}return p.ExitCode;}}catch{return 2;}
        }
        Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);Application.Run(new AgentPairWindows());return 0;
    }
}
