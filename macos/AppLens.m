#import <Cocoa/Cocoa.h>
@interface AppLens : NSObject <NSApplicationDelegate>
@property NSWindow *window;
@property NSTextField *server,*code,*goal,*status;
@property NSString *token;
@property BOOL busy,active;
@end
@implementation AppLens
- (NSTextField*)field:(NSString*)value y:(CGFloat)y {
 NSTextField *f=[[NSTextField alloc]initWithFrame:NSMakeRect(24,y,600,30)];f.stringValue=value;[self.window.contentView addSubview:f];return f;
}
- (void)applicationDidFinishLaunching:(NSNotification*)n {
 self.window=[[NSWindow alloc]initWithContentRect:NSMakeRect(0,0,650,380) styleMask:NSWindowStyleMaskTitled|NSWindowStyleMaskClosable|NSWindowStyleMaskMiniaturizable backing:NSBackingStoreBuffered defer:NO];
 self.window.title=@"AppLens · macOS 应用透镜";self.server=[self field:@"https://50.118.187.180" y:320];self.code=[self field:@"" y:275];self.code.placeholderString=@"一次性配对码（仅本次会话保存设备凭证）";
 self.goal=[self field:@"" y:220];self.goal.placeholderString=@"分析需求：仅提交你授权的观察信息";
 self.status=[NSTextField wrappingLabelWithString:@"基础版本：进程清单、云端请求；不具备内核行为采集。"];self.status.frame=NSMakeRect(24,25,600,90);[self.window.contentView addSubview:self.status];
 NSArray *titles=@[@"配对并开始同步",@"提交分析需求",@"暂停采集"];
 SEL actions[]={@selector(pair:),@selector(request:),@selector(pause:)};
 for(int i=0;i<3;i++){NSButton*b=[NSButton buttonWithTitle:titles[i] target:self action:actions[i]];b.frame=NSMakeRect(24+i*205,150,195,36);[self.window.contentView addSubview:b];}
 [self.window center];[self.window makeKeyAndOrderFront:nil];[NSApp activateIgnoringOtherApps:YES];
 [NSTimer scheduledTimerWithTimeInterval:30 target:self selector:@selector(tick:) userInfo:nil repeats:YES];
}
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
- (void)perform:(BOOL)pair request:(BOOL)request {
 if(self.busy)return;self.busy=YES;NSString*base=[self.server.stringValue stringByTrimmingCharactersInSet:[NSCharacterSet characterSetWithCharactersInString:@"/"]];NSString*code=self.code.stringValue;NSString*goal=self.goal.stringValue;
 dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY,0),^{NSError*error=nil;NSString*message;
  if(pair){NSDictionary*i=[self api:@"/api/endpoint/enroll" body:@{@"code":code,@"name":NSProcessInfo.processInfo.hostName} base:base error:&error];if(i){self.token=i[@"token"];self.active=YES;}}
  if(!error&&self.token&&self.active)[self api:@"/api/endpoint/report" body:[self inventory] base:base error:&error];
  if(!error&&request){NSDictionary*r=[self api:@"/api/endpoint/requests" body:@{@"title":@"AppLens macOS 分析需求",@"message":goal} base:base error:&error];message=[NSString stringWithFormat:@"需求已提交，云端任务 %@。请在平台查看结果。",r[@"id"]?:@""];}
  if(!self.token&&!error)message=@"请先配对。";
  dispatch_async(dispatch_get_main_queue(),^{self.busy=NO;if(pair&&!error)self.code.stringValue=@"";self.status.stringValue=error?[NSString stringWithFormat:@"同步失败：%@ (%ld)",error.domain,(long)error.code]:(message?:@"进程清单已同步；每 30 秒更新，关闭程序停止。");});
 });
}
- (void)pair:(id)sender{[self perform:YES request:NO];}
- (void)request:(id)sender{if(!self.goal.stringValue.length)return;[self perform:NO request:YES];}
- (void)pause:(id)sender{self.active=NO;self.status.stringValue=@"已暂停采集；重新配对可恢复。";}
- (void)tick:(id)sender{if(self.active)[self perform:NO request:NO];}
- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication*)app{return YES;}
@end
int main(int argc,const char**argv){@autoreleasepool{if(argc>1&&!strcmp(argv[1],"--self-test")){AppLens*a=[AppLens new];NSDictionary*d=[a inventory];return [d[@"applens"][@"product"] isEqual:@"AppLens"]?0:1;}NSApplication*app=[NSApplication sharedApplication];AppLens*delegate=[AppLens new];app.delegate=delegate;[app setActivationPolicy:NSApplicationActivationPolicyRegular];[app run];}return 0;}
