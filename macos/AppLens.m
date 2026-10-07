#import <Cocoa/Cocoa.h>
#import <WebKit/WebKit.h>
#import <Security/Security.h>
@interface AppLens : NSObject <NSApplicationDelegate,WKScriptMessageHandler,WKNavigationDelegate>
@property NSWindow *window;
@property WKWebView *web;
@property NSString *deviceId;
@property NSArray *calls;
@property NSMutableDictionary *receipts;
@property NSString *receiptFile;
@property NSTextField *server,*code,*goal,*status;
@property NSString *token;
@property BOOL busy,active;
@property BOOL telemetryBusy,telemetryActive;
@property NSTimer *telemetryTimer;
@property NSTextField *telemetryStatus;
@property NSString *dashboardFile;
@property NSString *selectedRequestId;
@property NSDictionary *captureEvent;
@end
@implementation AppLens
- (NSTextField*)field:(NSString*)value y:(CGFloat)y {
 NSTextField *f=[[NSTextField alloc]initWithFrame:NSMakeRect(24,y,600,30)];f.stringValue=value;[self.window.contentView addSubview:f];return f;
}
- (void)applicationDidFinishLaunching:(NSNotification*)n {
 self.server=[NSTextField new];self.server.stringValue=[[NSUserDefaults standardUserDefaults] stringForKey:@"AppLensServer"]?:@"https://www.chuhaijian.com";self.code=[NSTextField new];self.goal=[NSTextField new];self.status=[NSTextField new];self.telemetryStatus=[NSTextField new];self.calls=@[];self.receipts=[NSMutableDictionary dictionary];
 NSDictionary *query=@{(__bridge id)kSecClass:(__bridge id)kSecClassGenericPassword,(__bridge id)kSecAttrService:@"AppLensDevice",(__bridge id)kSecAttrAccount:self.server.stringValue,(__bridge id)kSecReturnData:@YES};CFTypeRef saved=NULL;
 if(SecItemCopyMatching((__bridge CFDictionaryRef)query,&saved)==errSecSuccess){self.token=[[NSString alloc]initWithData:CFBridgingRelease(saved) encoding:NSUTF8StringEncoding];self.active=YES;}
 self.deviceId=[[NSUserDefaults standardUserDefaults]stringForKey:@"AppLensDeviceId"];
 NSString *storage=[NSHomeDirectory() stringByAppendingPathComponent:@"Library/Application Support/AppLens/telemetry"];[[NSFileManager defaultManager]createDirectoryAtPath:storage withIntermediateDirectories:YES attributes:@{NSFilePosixPermissions:@0700} error:nil];self.receiptFile=[storage stringByAppendingPathComponent:@"upload-receipts.json"];NSData *receiptData=[NSData dataWithContentsOfFile:self.receiptFile];NSDictionary *storedReceipts=receiptData?[NSJSONSerialization JSONObjectWithData:receiptData options:0 error:nil]:nil;if([storedReceipts isKindOfClass:NSDictionary.class]&&[storedReceipts[@"deviceId"] isEqual:self.deviceId]&&[storedReceipts[@"receipts"] isKindOfClass:NSDictionary.class])self.receipts=[storedReceipts[@"receipts"] mutableCopy];
 self.window=[[NSWindow alloc]initWithContentRect:NSMakeRect(0,0,1240,900) styleMask:NSWindowStyleMaskTitled|NSWindowStyleMaskClosable|NSWindowStyleMaskMiniaturizable|NSWindowStyleMaskResizable backing:NSBackingStoreBuffered defer:NO];self.window.title=@"AppLens — 上下文采集";self.window.minSize=NSMakeSize(940,680);
 WKWebViewConfiguration *configuration=[WKWebViewConfiguration new];[configuration.userContentController addScriptMessageHandler:self name:@"applens"];self.web=[[WKWebView alloc]initWithFrame:self.window.contentView.bounds configuration:configuration];self.web.navigationDelegate=self;self.web.autoresizingMask=NSViewWidthSizable|NSViewHeightSizable;[self.window.contentView addSubview:self.web];
 NSURL *url=[NSBundle.mainBundle URLForResource:@"collector" withExtension:@"html"];[self.web loadFileURL:url allowingReadAccessToURL:[url URLByDeletingLastPathComponent]];
 [self.window center];[self.window makeKeyAndOrderFront:nil];[NSApp activateIgnoringOtherApps:YES];
 [NSTimer scheduledTimerWithTimeInterval:2 target:self selector:@selector(publish:) userInfo:nil repeats:YES];
 [NSTimer scheduledTimerWithTimeInterval:30 target:self selector:@selector(tick:) userInfo:nil repeats:YES];
 if(self.active)[self perform:NO request:NO];
 NSArray*args=NSProcessInfo.processInfo.arguments;NSUInteger pairIndex=[args indexOfObject:@"--pair-code"];if(pairIndex!=NSNotFound&&pairIndex+1<args.count){self.code.stringValue=args[pairIndex+1];[self pair:nil];}
}
- (void)publish:(id)sender {
 BOOL running=NO;for(NSRunningApplication *app in NSWorkspace.sharedWorkspace.runningApplications)if([app.localizedName.lowercaseString containsString:@"workbuddy"])running=YES;
 NSMutableArray *calls=[NSMutableArray array];NSUInteger synced=0;for(NSDictionary*c in self.calls){NSMutableDictionary*m=[c mutableCopy];[m removeObjectForKey:@"body"];NSString *raw=c[@"body"]?:@"";m[@"preview"]=[raw substringToIndex:MIN(raw.length,1200)];BOOL ok=[self.receipts[c[@"id"]] isEqual:c[@"bodySHA256"]];m[@"receipt"]=@(ok);m[@"bodyBytes"]=@([c[@"body"] lengthOfBytesUsingEncoding:NSUTF8StringEncoding]);if(ok)synced++;[calls addObject:m];}
 NSDictionary *visible=self.calls.firstObject;for(NSDictionary *candidate in self.calls)if([candidate[@"id"] isEqual:self.selectedRequestId]){visible=candidate;break;}
 NSDictionary *state=@{@"localPath":[self.receiptFile stringByDeletingLastPathComponent]?:@"",@"canEnableNetworkCapture":@NO,@"server":self.server.stringValue,@"deviceId":self.deviceId?:@"",@"connected":@(self.token!=nil),@"active":@(self.telemetryActive),@"appRunning":@(running),@"status":[NSString stringWithFormat:@"%@ %@",self.status.stringValue,self.telemetryStatus.stringValue],@"calls":calls,@"synced":@(synced),@"captureEvent":self.captureEvent?:@{},@"captureBody":visible[@"body"]?:@"",@"captureBodyId":visible[@"id"]?:@"",@"captureBodySHA256":visible[@"bodySHA256"]?:@""};
 NSData *d=[NSJSONSerialization dataWithJSONObject:state options:0 error:nil];NSString *json=[[NSString alloc]initWithData:d encoding:NSUTF8StringEncoding];[self.web evaluateJavaScript:[NSString stringWithFormat:@"update(%@)",json] completionHandler:nil];
}
- (void)userContentController:(WKUserContentController*)controller didReceiveScriptMessage:(WKScriptMessage*)message {
 if(!message.frameInfo.mainFrame||!message.frameInfo.request.URL.isFileURL||![message.body isKindOfClass:NSDictionary.class])return;
 NSDictionary *b=message.body;NSString *action=b[@"action"];
 if([action isEqual:@"select"]&&[b[@"id"] isKindOfClass:NSString.class]){for(NSDictionary *call in self.calls)if([call[@"id"] isEqual:b[@"id"]]){self.selectedRequestId=b[@"id"];[self publish:nil];break;}}
 else if([action isEqual:@"open"])[self showTelemetry:nil];
 else if([action isEqual:@"records"])[NSWorkspace.sharedWorkspace openURL:[NSURL fileURLWithPath:[self.receiptFile stringByDeletingLastPathComponent]]];
 else if([action isEqual:@"call"]&&[b[@"id"] isKindOfClass:NSString.class]){NSString *identifier=b[@"id"];NSCharacterSet *invalid=[[NSCharacterSet characterSetWithCharactersInString:@"0123456789abcdef"]invertedSet];if(identifier.length==64&&[identifier rangeOfCharacterFromSet:invalid].location==NSNotFound)[NSWorkspace.sharedWorkspace openURL:[NSURL URLWithString:[NSString stringWithFormat:@"%@/model-data?device=%@&request=%@",self.server.stringValue,self.deviceId?:@"",identifier]]];}
 else if([action isEqual:@"retry"]){if(self.active)[self telemetry:nil];else {self.telemetryStatus.stringValue=@"采集已暂停，请先恢复再同步。";[self publish:nil];}}
 else if([action isEqual:@"pause"]){if(self.telemetryActive)[self pause:nil];else if(self.token){self.active=YES;[self telemetry:nil];}else {self.status.stringValue=@"请先连接平台并完成设备配对。";[self.web evaluateJavaScript:@"showDesktopPage('settings')" completionHandler:nil];[self publish:nil];}}
 else if([action isEqual:@"pair"]&&[b[@"server"] isKindOfClass:NSString.class]&&[b[@"code"] isKindOfClass:NSString.class]){
  NSURL *origin=[NSURL URLWithString:b[@"server"]];NSString *path=origin.path?:@"";
  if(![origin.scheme isEqual:@"https"]||!origin.host.length||origin.user||origin.password||origin.query||origin.fragment||(path.length&&![path isEqual:@"/"])){self.status.stringValue=@"请输入有效的 HTTPS 平台根地址。";[self publish:nil];return;}
  if(self.busy||self.telemetryBusy){self.status.stringValue=@"正在处理采集或同步，请稍后更改连接。";[self publish:nil];return;}
  [self pause:nil];self.token=nil;self.server.stringValue=[b[@"server"] stringByTrimmingCharactersInSet:[NSCharacterSet characterSetWithCharactersInString:@"/"]];self.code.stringValue=b[@"code"];[self pair:nil];
 }
}
- (void)webView:(WKWebView*)webView decidePolicyForNavigationAction:(WKNavigationAction*)action decisionHandler:(void (^)(WKNavigationActionPolicy))handler {
 NSURL *url=action.request.URL;NSString *root=NSBundle.mainBundle.resourcePath;
 BOOL local=(url.isFileURL&&[url.path hasPrefix:[root stringByAppendingString:@"/"]])||(!action.targetFrame.mainFrame&&[url.absoluteString isEqual:@"about:blank"]);
 handler(local?WKNavigationActionPolicyAllow:WKNavigationActionPolicyCancel);
}
- (void)showTelemetry:(id)sender{[NSWorkspace.sharedWorkspace openURL:[NSURL URLWithString:[self.server.stringValue stringByAppendingString:[NSString stringWithFormat:@"/model-data?device=%@",self.deviceId?:@""]]]];}
- (NSString*)metadataSignature:(NSDictionary*)c {return [NSString stringWithFormat:@"%@|%@|%@|%@",c[@"model"]?:@"",c[@"recordStatus"]?:@"",c[@"sessionId"]?:@"",c[@"modelEvidence"]?:@""];}
- (void)capturePhase:(NSString*)phase call:(NSDictionary*)call {
 dispatch_async(dispatch_get_main_queue(),^{self.captureEvent=@{@"id":call[@"id"]?:@"",@"bodySHA256":call[@"bodySHA256"]?:@"",@"phase":phase};[self publish:nil];});
}
- (void)telemetry:(id)sender {
 if(self.telemetryBusy)return;self.telemetryBusy=YES;self.telemetryActive=YES;
 if(!self.telemetryTimer)self.telemetryTimer=[NSTimer scheduledTimerWithTimeInterval:10 target:self selector:@selector(refreshTelemetry:) userInfo:nil repeats:YES];
 dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY,0),^{
  NSString*script=[NSBundle.mainBundle.resourcePath stringByAppendingPathComponent:@"workbuddy_context.py"];
  NSTask*t=[NSTask new];t.launchPath=@"/usr/bin/python3";t.arguments=@[script,@"--since",[NSString stringWithFormat:@"%.0f",NSDate.date.timeIntervalSince1970-86400]];NSPipe*p=[NSPipe pipe];t.standardOutput=p;t.standardError=[NSFileHandle fileHandleWithNullDevice];NSError*error=nil;BOOL launched=[t launchAndReturnError:&error];NSData*d=launched?[p.fileHandleForReading readDataToEndOfFile]:nil;if(launched)[t waitUntilExit];NSDictionary*r=d?[NSJSONSerialization JSONObjectWithData:d options:0 error:nil]:nil;
  NSError*uploadError=nil;
  BOOL contextUploaded=NO;if(r){NSData*e=[NSData dataWithContentsOfFile:r[@"contextFile"]];NSDictionary*batch=e?[NSJSONSerialization JSONObjectWithData:e options:0 error:nil]:nil;if(batch){self.calls=batch[@"requests"]?:@[];contextUploaded=self.token&&self.active;NSMutableArray *pending=[NSMutableArray array];for(NSDictionary *c in self.calls)if(![self.receipts[c[@"id"]] isEqual:c[@"bodySHA256"]]||![self.receipts[[c[@"id"] stringByAppendingString:@"-metadata"]] isEqual:[self metadataSignature:c]])[pending addObject:c];for(NSUInteger start=0;start<pending.count&&self.token&&self.active;start++){[self capturePhase:@"queued" call:pending[start]];NSArray *part=[pending subarrayWithRange:NSMakeRange(start,1)];[self capturePhase:@"uploading" call:pending[start]];NSDictionary *response=[self api:@"/api/applens/model-context" body:@{@"requests":part} base:self.server.stringValue error:&uploadError];if(!response){contextUploaded=NO;[self capturePhase:@"failed" call:pending[start]];break;}for(NSDictionary *receipt in response[@"receipts"])self.receipts[receipt[@"id"]]=receipt[@"bodySHA256"];for(NSDictionary *c in part){if(![self.receipts[c[@"id"]] isEqual:c[@"bodySHA256"]]){contextUploaded=NO;[self capturePhase:@"failed" call:c];}else {self.receipts[[c[@"id"] stringByAppendingString:@"-metadata"]]=[self metadataSignature:c];[self capturePhase:@"received" call:c];}}}}}
  NSData *receiptsData=[NSJSONSerialization dataWithJSONObject:@{@"deviceId":self.deviceId?:@"",@"receipts":self.receipts} options:0 error:nil];[receiptsData writeToFile:self.receiptFile atomically:YES];[[NSFileManager defaultManager]setAttributes:@{NSFilePosixPermissions:@0600} ofItemAtPath:self.receiptFile error:nil];
  dispatch_async(dispatch_get_main_queue(),^{self.telemetryBusy=NO;self.telemetryStatus.stringValue=r?[NSString stringWithFormat:@"%@ 条模型输入记录 · %@",r[@"requests"],contextUploaded?@"采集原文与平台回执哈希一致":uploadError?@"上传失败，保留本地":@"等待配对上传"]:@"模型上下文采集失败。";});
 });
}
- (void)refreshTelemetry:(id)sender{if(self.telemetryActive)[self telemetry:nil];}
- (NSDictionary*)api:(NSString*)path body:(NSDictionary*)body base:(NSString*)base error:(NSError**)error {
 NSURL*u=[NSURL URLWithString:[base stringByAppendingString:path]];
 if(![u.scheme isEqual:@"https"]||u.user||u.password||u.query||u.fragment){*error=[NSError errorWithDomain:@"HTTPS 来源地址无效" code:1 userInfo:nil];return nil;}
 NSMutableURLRequest*r=[NSMutableURLRequest requestWithURL:u];r.timeoutInterval=20;r.HTTPMethod=body?@"POST":@"GET";
 if(self.token)[r setValue:[@"Bearer " stringByAppendingString:self.token] forHTTPHeaderField:@"Authorization"];
 if(body){r.HTTPBody=[NSJSONSerialization dataWithJSONObject:body options:0 error:error];[r setValue:@"application/json" forHTTPHeaderField:@"Content-Type"];}
 dispatch_semaphore_t s=dispatch_semaphore_create(0);__block NSData*d;__block NSError*failure;__block NSInteger status;
 NSURLSessionConfiguration*c=[NSURLSessionConfiguration ephemeralSessionConfiguration];
 NSURLSession*session=[NSURLSession sessionWithConfiguration:c];
 [[session dataTaskWithRequest:r completionHandler:^(NSData*data,NSURLResponse*response,NSError*e){d=data;failure=e;status=[(NSHTTPURLResponse*)response statusCode];dispatch_semaphore_signal(s);}] resume];
 dispatch_semaphore_wait(s,dispatch_time(DISPATCH_TIME_NOW,25*NSEC_PER_SEC));[session invalidateAndCancel];
 if(failure||status<200||status>=300){*error=failure?:[NSError errorWithDomain:@"平台请求失败" code:status userInfo:nil];return nil;}
 return [NSJSONSerialization JSONObjectWithData:d options:0 error:error];
}
- (NSDictionary*)inventory {
 NSTask*t=[NSTask new];t.launchPath=@"/bin/ps";t.arguments=@[@"-axo",@"pid=,ppid=,comm="];NSPipe*p=[NSPipe pipe];t.standardOutput=p;NSError*launchError=nil;BOOL launched=[t launchAndReturnError:&launchError];NSData*d=launched?[p.fileHandleForReading readDataToEndOfFile]:[NSData data];if(launched)[t waitUntilExit];
 NSMutableArray*processes=[NSMutableArray array];NSString*text=[[NSString alloc]initWithData:d encoding:NSUTF8StringEncoding];
 for(NSString*line in [text componentsSeparatedByString:@"\n"]){NSScanner*s=[NSScanner scannerWithString:line];int pid=0,parent=0;if(![s scanInt:&pid]||![s scanInt:&parent])continue;NSString*name=[[line substringFromIndex:s.scanLocation] stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceCharacterSet].lastPathComponent;if(processes.count<2000)[processes addObject:@{@"name":name?:@"unknown",@"pid":@(pid),@"parentPid":@(parent)}];}
 NSMutableDictionary*caps=[NSMutableDictionary dictionary];for(NSString*k in @[@"process_inventory",@"application_inventory",@"process_details",@"process_tcp",@"process_events",@"network_events",@"file_events",@"cloud_requests"])caps[k]=@"unsupported";
 caps[@"process_inventory"]=launched&&t.terminationStatus==0?@"available":@"unavailable";caps[@"cloud_requests"]=@"available";
 return @{@"os":@"macOS",@"architecture":@"native",@"processes":processes,@"applications":@[],@"errors":@[@"应用清单、网络和内核行为采集尚不支持。"],@"applens":@{@"product":@"AppLens",@"protocolVersion":@1,@"capabilities":caps}};
}
- (NSString*)installationIdentity {
 NSString *value=[[NSUserDefaults standardUserDefaults]stringForKey:@"AppLensInstallationId"];
 if(!value){value=NSUUID.UUID.UUIDString;[[NSUserDefaults standardUserDefaults]setObject:value forKey:@"AppLensInstallationId"];}
 return value;
}
- (void)perform:(BOOL)pair request:(BOOL)request {
 if(self.busy)return;self.busy=YES;NSString*base=[self.server.stringValue stringByTrimmingCharactersInSet:[NSCharacterSet characterSetWithCharactersInString:@"/"]];NSString*code=self.code.stringValue;NSString*goal=self.goal.stringValue;
 dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY,0),^{NSError*error=nil;NSString*message;
  if(pair){NSDictionary*i=[self api:@"/api/endpoint/enroll" body:@{@"code":code,@"name":NSProcessInfo.processInfo.hostName,@"installationId":[self installationIdentity]} base:base error:&error];if(i){self.token=i[@"token"];self.deviceId=i[@"deviceId"];self.receipts=[NSMutableDictionary dictionary];self.active=YES;
 NSDictionary *q=@{(__bridge id)kSecClass:(__bridge id)kSecClassGenericPassword,(__bridge id)kSecAttrService:@"AppLensDevice",(__bridge id)kSecAttrAccount:base};SecItemDelete((__bridge CFDictionaryRef)q);NSMutableDictionary *saved=[q mutableCopy];saved[(__bridge id)kSecValueData]=[self.token dataUsingEncoding:NSUTF8StringEncoding];SecItemAdd((__bridge CFDictionaryRef)saved,NULL);
 [[NSUserDefaults standardUserDefaults]setObject:base forKey:@"AppLensServer"];[[NSUserDefaults standardUserDefaults]setObject:self.deviceId forKey:@"AppLensDeviceId"];}}
  if(!error&&self.token&&self.active)[self api:@"/api/endpoint/report" body:[self inventory] base:base error:&error];
  if(!error&&request){NSDictionary*r=[self api:@"/api/endpoint/requests" body:@{@"title":@"AppLens macOS 分析需求",@"message":goal} base:base error:&error];message=[NSString stringWithFormat:@"需求已提交，云端任务 %@。请在平台查看结果。",r[@"id"]?:@""];}
  if(!self.token&&!error)message=@"请先配对。";
 dispatch_async(dispatch_get_main_queue(),^{self.busy=NO;if(pair&&!error){self.code.stringValue=@"";[self.web evaluateJavaScript:@"document.getElementById('code').value=''" completionHandler:nil];}self.status.stringValue=error?[NSString stringWithFormat:@"同步失败：%@ (%ld)",error.domain,(long)error.code]:(message?:@"进程清单已同步；每 30 秒更新，关闭程序停止。");if(!error&&self.active&&!self.telemetryActive)[self telemetry:nil];});
 });
}
- (void)pair:(id)sender{[self perform:YES request:NO];}
- (void)request:(id)sender{if(!self.goal.stringValue.length)return;[self perform:NO request:YES];}
- (void)pause:(id)sender{self.active=NO;self.telemetryActive=NO;self.status.stringValue=@"采集已暂停；本机原文保留，可恢复采集。";self.telemetryStatus.stringValue=@"WorkBuddy 遥测已暂停。";}
- (void)tick:(id)sender{if(self.active)[self perform:NO request:NO];}
- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication*)app{return YES;}
@end
// Self-contained native rendering acceptance: synthetic records only; no collector,
// UserDefaults, keychain, network client, process inventory, or installed app changes.
@interface AppLensUIAcceptance : NSObject <NSApplicationDelegate,WKNavigationDelegate,WKScriptMessageHandler>
@property NSWindow *window;
@property WKWebView *web;
@property NSMutableDictionary *fixture;
@property NSMutableDictionary *second;
@property NSString *body2;
@property NSString *outputPath;
@property BOOL begun;
@end
@implementation AppLensUIAcceptance
- (void)finish:(BOOL)success {fprintf(stderr,"AppLens native UI fixture: %s\n",success?"PASS":"FAIL");exit(success?0:3);}
- (void)check:(NSString*)script next:(void (^)(void))next {
 [self.web evaluateJavaScript:script completionHandler:^(id value,NSError *error){if(error||![value boolValue]){fprintf(stderr,"UI assertion failed: %s; error=%s; value=%s\n",script.UTF8String,error.description.UTF8String?:"none",[value description].UTF8String?:"nil");[self finish:NO];return;}next();}];
}
- (void)publish:(void (^)(void))next {
 NSData *data=[NSJSONSerialization dataWithJSONObject:self.fixture options:0 error:nil];NSString *json=[[NSString alloc]initWithData:data encoding:NSUTF8StringEncoding];
 [self.web evaluateJavaScript:[NSString stringWithFormat:@"update(%@)",json] completionHandler:^(id value,NSError *error){if(error){fprintf(stderr,"UI fixture injection failed: %s\n",error.description.UTF8String);[self finish:NO];return;}next();}];
}
- (void)applicationDidFinishLaunching:(NSNotification*)note {
 self.window=[[NSWindow alloc]initWithContentRect:NSMakeRect(0,0,1240,860) styleMask:NSWindowStyleMaskTitled|NSWindowStyleMaskClosable|NSWindowStyleMaskMiniaturizable|NSWindowStyleMaskResizable backing:NSBackingStoreBuffered defer:NO];self.window.title=@"AppLens — 本地界面验收（虚构数据）";
 WKWebViewConfiguration *config=[WKWebViewConfiguration new];[config.userContentController addScriptMessageHandler:self name:@"applens"];self.web=[[WKWebView alloc]initWithFrame:self.window.contentView.bounds configuration:config];self.web.navigationDelegate=self;self.web.autoresizingMask=NSViewWidthSizable|NSViewHeightSizable;[self.window.contentView addSubview:self.web];[self.window center];[self.window makeKeyAndOrderFront:nil];
 NSString *a=[@"" stringByPaddingToLength:64 withString:@"a" startingAtIndex:0],*b=[@"" stringByPaddingToLength:64 withString:@"b" startingAtIndex:0],*c=[@"" stringByPaddingToLength:64 withString:@"c" startingAtIndex:0],*d=[@"" stringByPaddingToLength:64 withString:@"d" startingAtIndex:0];
 NSString *body1=@"{\"messages\":[{\"role\":\"system\",\"content\":\"# MEMORY.md\\nfixture project memory\"},{\"role\":\"user\",\"content\":\"<user_query>查上海天气 · 虚构验收</user_query>\"},{\"role\":\"tool\",\"content\":\"five fixture weather records\"}]}";
 self.body2=@"{\"messages\":[{\"role\":\"user\",\"content\":\"生成 SSH 动画 · 虚构验收\"},{\"role\":\"tool\",\"content\":\"fixture-ssh-video.mp4\"}]}";
 NSDictionary *first=@{@"id":a,@"bodySHA256":b,@"source":@"workbuddy_network_context",@"sessionName":@"天气查询 · 虚构验收",@"timestamp":@1700000000,@"bodyBytes":@([body1 lengthOfBytesUsingEncoding:NSUTF8StringEncoding]),@"receipt":@NO};
 self.second=[@{@"id":c,@"bodySHA256":d,@"source":@"workbuddy_generation_context",@"sessionName":@"SSH 动画 · 虚构验收",@"timestamp":@1700000060,@"bodyBytes":@([self.body2 lengthOfBytesUsingEncoding:NSUTF8StringEncoding]),@"receipt":@NO} mutableCopy];
 self.fixture=[@{@"calls":@[first,self.second],@"server":@"https://fixture.invalid",@"deviceId":@"fixture-device",@"active":@YES,@"connected":@YES,@"appRunning":@YES,@"localPath":@"虚构验收目录 · 不读取本机记录",@"status":@"本机界面验收 · 虚构数据",@"captureBody":body1,@"captureBodyId":a,@"captureBodySHA256":b} mutableCopy];
 NSURL *url=[NSBundle.mainBundle URLForResource:@"collector" withExtension:@"html"];[self.web loadFileURL:url allowingReadAccessToURL:[url URLByDeletingLastPathComponent]];
 dispatch_after(dispatch_time(DISPATCH_TIME_NOW,20*NSEC_PER_SEC),dispatch_get_main_queue(),^{[self finish:NO];});
}
- (void)awaitReady:(NSUInteger)attempt {
 [self.web evaluateJavaScript:@"typeof window.classifyAppLensContext === 'function'" completionHandler:^(id ready,NSError *error){
  if(!error&&[ready boolValue]){[self publish:^{[self check:@"document.getElementById('request-task').textContent.indexOf('查上海天气') >= 0 && document.getElementById('categories').textContent.indexOf('工具返回') >= 0" next:^{[self.web evaluateJavaScript:@"document.querySelectorAll('.request-choice')[1].click()" completionHandler:nil];}];}];}
  else if(attempt<40)dispatch_after(dispatch_time(DISPATCH_TIME_NOW,150*NSEC_PER_MSEC),dispatch_get_main_queue(),^{[self awaitReady:attempt+1];});else {fprintf(stderr,"UI classifier readiness failed: %s\n",error.description.UTF8String?:"not ready");[self finish:NO];}
 }];
}
- (void)webView:(WKWebView*)webView didFinishNavigation:(WKNavigation*)navigation {if(self.begun)return;self.begun=YES;[self awaitReady:0];}
- (void)userContentController:(WKUserContentController*)controller didReceiveScriptMessage:(WKScriptMessage*)message {
 if(![message.body isKindOfClass:NSDictionary.class]||![message.body[@"action"] isEqual:@"select"]||![message.body[@"id"] isEqual:self.second[@"id"]])return;
 self.fixture[@"captureBody"]=self.body2;self.fixture[@"captureBodyId"]=self.second[@"id"];self.fixture[@"captureBodySHA256"]=self.second[@"bodySHA256"];
 [self publish:^{[self check:@"document.getElementById('request-task').textContent.indexOf('生成 SSH 动画') >= 0 && document.getElementById('categories').textContent.indexOf('five fixture weather') < 0" next:^{
  self.second[@"receipt"]=@YES;[self publish:^{[self.web evaluateJavaScript:@"document.getElementById('receipt-button').click()" completionHandler:^(id value,NSError *error){
   [self check:@"document.getElementById('proof').textContent.indexOf('平台回执一致') >= 0 && document.getElementById('proof').textContent.indexOf('cccccccc') >= 0 && document.getElementById('inspector').hidden === false" next:^{
    self.fixture[@"captureBodySHA256"]=@"mismatch";[self publish:^{[self check:@"document.getElementById('request-task').textContent.indexOf('生成 SSH 动画') < 0" next:^{
     self.fixture[@"captureBodySHA256"]=self.second[@"bodySHA256"];[self publish:^{
      if(!self.outputPath.length){[self finish:YES];return;}
      [self.web takeSnapshotWithConfiguration:nil completionHandler:^(NSImage *image,NSError *snapshotError){NSBitmapImageRep *bitmap=image?[[NSBitmapImageRep alloc]initWithData:image.TIFFRepresentation]:nil;NSData *png=[bitmap representationUsingType:NSBitmapImageFileTypePNG properties:@{}];BOOL saved=png&&[png writeToFile:self.outputPath atomically:YES];if(!saved||snapshotError)fprintf(stderr,"UI snapshot failed: %s\n",snapshotError.description.UTF8String?:"no image");[self finish:!snapshotError&&saved];}];
     }];
    }];}];
   }];
  }];}];
 }];}];
}
@end
int main(int argc,const char**argv){@autoreleasepool{if(argc>1&&!strcmp(argv[1],"--self-test")){AppLens*a=[AppLens new];NSDictionary*d=[a inventory];return [d[@"applens"][@"product"] isEqual:@"AppLens"]?0:1;}if(argc>1&&!strcmp(argv[1],"--ui-self-test")){NSApplication *app=[NSApplication sharedApplication];AppLensUIAcceptance *test=[AppLensUIAcceptance new];if(argc>2)test.outputPath=[NSString stringWithUTF8String:argv[2]];app.delegate=test;[app setActivationPolicy:NSApplicationActivationPolicyRegular];[app run];return 3;}NSApplication*app=[NSApplication sharedApplication];AppLens*delegate=[AppLens new];app.delegate=delegate;[app setActivationPolicy:NSApplicationActivationPolicyRegular];[app run];}return 0;}
