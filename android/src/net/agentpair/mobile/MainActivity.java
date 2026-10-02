package net.agentpair.mobile;

import android.app.Activity;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.widget.*;
import android.text.InputType;
import org.json.JSONObject;
import org.json.JSONArray;
import java.net.URI;
import java.util.concurrent.Executors;
import java.util.concurrent.ExecutorService;

public final class MainActivity extends Activity {
    final ExecutorService worker=Executors.newSingleThreadExecutor();final Handler ui=new Handler(Looper.getMainLooper());
    LinearLayout panel,requestCards,taskActions;EditText server,pair;TextView state,connection,requests,cloudTask;boolean foreground=false,busy=false;String renderedTaskKey="";
    final Runnable poll=new Runnable(){public void run(){if(!foreground)return;refresh();ui.postDelayed(this,10000);}};
    @Override public void onCreate(Bundle saved){super.onCreate(saved);
        getWindow().setFlags(android.view.WindowManager.LayoutParams.FLAG_SECURE,android.view.WindowManager.LayoutParams.FLAG_SECURE);
        ScrollView scroll=new ScrollView(this);panel=new LinearLayout(this);panel.setOrientation(LinearLayout.VERTICAL);panel.setPadding(32,48,32,32);scroll.addView(panel);setContentView(scroll);
        text("◈ AppLens 应用透镜",26);text("设备采集 · 请求分析 · 查看结果",18);
        text("Android 不允许普通伴侣应用静默读取任意短信。收到登录请求后，请在此输入短信验证码并确认发送；验证码仅供本次短时请求使用。",15);
        server=field("HTTPS 平台地址");server.setText(Api.prefs(this).getString("server",""));
        pair=field("在「我的设备」生成的一次性配对码");
        button("配对手机",()->{String base=server.getText().toString().trim().replaceAll("/+$","");try{URI u=new URI(base);if(!"https".equals(u.getScheme())||u.getHost()==null||u.getUserInfo()!=null||!u.getPath().isEmpty()||u.getQuery()!=null||u.getFragment()!=null)throw new Exception();}catch(Exception ex){state.setText("请输入有效的 HTTPS 来源地址");return;}
            if(!Api.prefs(this).getString("token","").isEmpty()){state.setText("更换平台前请先断开当前配对");return;}
            Api.prefs(this).edit().putString("server",base).apply();String code=pair.getText().toString().trim();run(()->{JSONObject result=Api.call(this,"/api/endpoint/enroll",new JSONObject().put("code",code).put("name",android.os.Build.MANUFACTURER+" "+android.os.Build.MODEL),false);Api.prefs(this).edit().putString("token",result.getString("token")).putString("deviceId",result.getString("deviceId")).apply();Api.heartbeat(this);ui.post(()->pair.setText(""));return "配对完成；登录请求会显示在下方";});});
        connection=text(Api.prefs(this).getString("token","").isEmpty()?"尚未配对":"已保存配对 · 正在确认连接",16);
        state=text("",15);requests=text("等待云端登录请求",16);
        button("刷新连接和请求",this::refresh);
        button("断开本机配对",()->{Api.prefs(this).edit().clear().apply();state.setText("本机已断开；如需撤销云端设备，请到「我的设备」解除绑定");requests.setText("");requestCards.removeAllViews();});
        requestCards=new LinearLayout(this);requestCards.setOrientation(LinearLayout.VERTICAL);panel.addView(requestCards);
        cloudTask=text("当前没有云端任务",16);
        taskActions=new LinearLayout(this);taskActions.setOrientation(LinearLayout.VERTICAL);panel.addView(taskActions);
        text("向云端发布任务",22);
        EditText title=field("任务标题");EditText goal=field("描述问题、需要的结果和验收条件");goal.setSingleLine(false);goal.setMinLines(3);
        button("提交给 Navigator",()->run(()->{JSONObject task=Api.call(this,"/api/endpoint/requests",new JSONObject().put("title",title.getText().toString().trim()).put("message",goal.getText().toString().trim()),true);Api.prefs(this).edit().putString("cloudRequestId",task.getString("id")).apply();return "云端已接收任务："+task.getString("title")+"\n任务编号："+task.getString("id");}));
        text("若将来 FreeBuf 支持 Android SMS Retriever，可在网站配合后实现自动读取；第三方短信在未合作的情况下不能由普通 App 自动读取。验证码不会进入任务对话或模型。",14);
    }
    interface Job {String run() throws Exception;}
    void run(Job job){if(busy)return;busy=true;worker.execute(()->{String message;try{message=job.run();}catch(Exception e){message=e instanceof javax.net.ssl.SSLException?"HTTPS 证书不受信任，请为平台配置受信任证书":e.getMessage();}final String output=message;ui.post(()->{busy=false;if(!isDestroyed())state.setText(output);});});}
    void refresh(){if(Api.prefs(this).getString("token","").isEmpty()){connection.setText("尚未配对");return;}run(()->{try{Api.heartbeat(this);ui.post(()->connection.setText("已连接云端 · 心跳正常"));}catch(Exception ex){ui.post(()->connection.setText("连接确认失败 · "+ex.getMessage()));throw ex;}JSONArray list=Api.pending(this);JSONObject envelope=Api.pullTask(this);JSONObject remote=envelope.optJSONObject("task");
        if(remote!=null){String saved=Api.prefs(this).getString("activeTask","");JSONObject previous=saved.isEmpty()?null:new JSONObject(saved);if(previous==null||!remote.optString("taskId").equals(previous.optString("taskId"))){remote.put("state","received");Api.taskResult(this,remote.getString("taskId"),remote.getString("lease"),new JSONObject().put("state","received").put("summary","Android 设备已接收任务，等待用户确认执行。"));}Api.prefs(this).edit().putString("activeTask",remote.toString()).apply();}
        else {String saved=Api.prefs(this).getString("activeTask","");if(!saved.isEmpty()){JSONObject current=new JSONObject(saved);if(current.optString("state").equals("completed")||current.optString("state").equals("failed")||current.optString("state").equals("blocked"))Api.prefs(this).edit().remove("activeTask").apply();}}
        JSONObject active=Api.prefs(this).getString("activeTask","").isEmpty()?null:new JSONObject(Api.prefs(this).getString("activeTask",""));ui.post(()->{renderRequests(list);renderCloudTask(active);});return "设备已连接；云端任务和设备状态已同步。验证码需由你确认后发送。";});}
    void renderRequests(JSONArray list){requestCards.removeAllViews();requests.setText(list.length()==0?"等待云端登录请求":"收到 "+list.length()+" 个待处理登录请求");
        for(int i=0;i<list.length();i++)try{JSONObject request=list.getJSONObject(i);LinearLayout card=new LinearLayout(this);card.setOrientation(LinearLayout.VERTICAL);card.setPadding(18,8,18,16);
            TextView detail=new TextView(this);detail.setText(request.getString("brand")+" · "+request.getString("origin")+"\n任务："+request.getString("taskId")+"\n剩余 "+Math.max(0,(long)request.getDouble("expiresAt")-System.currentTimeMillis()/1000)+" 秒");card.addView(detail);
            EditText code=new EditText(this);code.setHint("输入短信中的 4–8 位验证码");code.setSingleLine(true);code.setInputType(InputType.TYPE_CLASS_NUMBER);card.addView(code);
            Button send=new Button(this);send.setText("确认并发送给本次登录");send.setOnClickListener(v->{String value=code.getText().toString();if(!value.matches("[0-9]{4,8}")){state.setText("请输入 4–8 位数字验证码");return;}send.setEnabled(false);worker.execute(()->{String message;try{Api.submitLoginCode(this,request.getString("id"),value);message="验证码已发送；平台仅记录本次请求状态";}catch(Exception e){message="发送失败："+String.valueOf(e.getMessage());}final String output=message;ui.post(()->{code.setText("");state.setText(output);send.setEnabled(true);refresh();});});});card.addView(send);requestCards.addView(card);
        }catch(Exception ignored){}
    }
    void renderCloudTask(JSONObject task){String key=task==null?"":task.optString("taskId")+":"+task.optString("state");if(key.equals(renderedTaskKey))return;renderedTaskKey=key;taskActions.removeAllViews();if(task==null){cloudTask.setText("当前没有云端任务");return;}JSONObject payload=task.optJSONObject("payload");String title=payload==null?"云端任务":payload.optString("title","云端任务");String goal=payload==null?"":payload.optString("goal","");String stateName=task.optString("state","received");
        cloudTask.setText(title+"\n"+goal+"\n任务编号："+task.optString("taskId")+"\n状态："+stateName);
        JSONObject resultData=task.optJSONObject("result");if(stateName.equals("completed")||stateName.equals("failed")||stateName.equals("blocked")){if(resultData!=null)cloudTask.append("\n回执："+resultData.optString("summary",""));return;}
        if(!stateName.equals("running")){button(taskActions,"确认开始执行",()->changeTaskState(task,"running","Android 用户已确认开始执行。"));return;}
        EditText result=new EditText(this);result.setHint("填写执行结果或观察记录（不得包含登录验证码）");result.setMinLines(2);taskActions.addView(result);
        button(taskActions,"提交结果给云端",()->{String summary=result.getText().toString().trim();if(summary.isEmpty()){state.setText("请先填写执行结果");return;}changeTaskState(task,"completed",summary);});
    }
    void changeTaskState(JSONObject task,String next,String summary){String id=task.optString("taskId"),lease=task.optString("lease");run(()->{JSONObject response=Api.taskResult(this,id,lease,new JSONObject().put("state",next).put("summary",summary));task.put("state",response.optString("state",next));task.put("result",new JSONObject().put("state",next).put("summary",summary));Api.prefs(this).edit().putString("activeTask",task.toString()).apply();ui.post(()->renderCloudTask(task));return next.equals("completed")?"结果已回传，平台执行记录已更新":"云端任务状态已更新为执行中";});}
    TextView text(String value,int size){TextView v=new TextView(this);v.setText(value);v.setTextSize(size);v.setPadding(0,12,0,18);panel.addView(v);return v;}
    EditText field(String hint){EditText v=new EditText(this);v.setHint(hint);v.setSingleLine(true);v.setInputType(InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);panel.addView(v);return v;}
    void button(String title,Runnable action){Button b=new Button(this);b.setText(title);b.setOnClickListener(v->action.run());panel.addView(b);}
    void button(LinearLayout parent,String title,Runnable action){Button b=new Button(this);b.setText(title);b.setOnClickListener(v->action.run());parent.addView(b);}
    @Override protected void onResume(){super.onResume();foreground=true;ui.post(poll);}
    @Override protected void onPause(){foreground=false;ui.removeCallbacks(poll);super.onPause();}
    @Override protected void onDestroy(){worker.shutdownNow();super.onDestroy();}
}
