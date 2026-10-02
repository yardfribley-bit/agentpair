package net.agentpair.mobile;

import android.content.Context;
import android.content.SharedPreferences;
import org.json.JSONObject;
import org.json.JSONArray;
import javax.net.ssl.HttpsURLConnection;
import java.net.URL;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;

final class Api {
    static SharedPreferences prefs(Context c) { return c.getSharedPreferences("pair",Context.MODE_PRIVATE); }
    static JSONObject call(Context c,String path,JSONObject data,boolean authenticated) throws Exception {
        String base=prefs(c).getString("server","");
        URL url=new URL(base+path);
        if(!"https".equals(url.getProtocol()) || url.getUserInfo()!=null)throw new Exception("必须使用 HTTPS 平台地址");
        HttpsURLConnection connection=(HttpsURLConnection)url.openConnection();
        connection.setInstanceFollowRedirects(false);connection.setConnectTimeout(8000);connection.setReadTimeout(8000);
        if(authenticated)connection.setRequestProperty("Authorization","Bearer "+prefs(c).getString("token",""));
        try {
            if(data!=null){connection.setRequestMethod("POST");connection.setDoOutput(true);connection.setRequestProperty("Content-Type","application/json");try(java.io.OutputStream out=connection.getOutputStream()){out.write(data.toString().getBytes(StandardCharsets.UTF_8));}}
            int status=connection.getResponseCode();
            if(status<200||status>=300)throw new Exception("平台请求失败（"+status+"），请检查配对、请求有效期和权限");
            try(InputStream input=connection.getInputStream();ByteArrayOutputStream output=new ByteArrayOutputStream()){
                byte[] chunk=new byte[2048];int count;while((count=input.read(chunk))!=-1){output.write(chunk,0,count);if(output.size()>65536)throw new Exception("平台响应过大");}
                return new JSONObject(output.toString("UTF-8"));
            }
        } finally {connection.disconnect();}
    }
    static JSONArray pending(Context c) throws Exception {return call(c,"/api/endpoint/login-requests",null,true).getJSONArray("items");}
    static JSONObject submitLoginCode(Context c,String id,String code) throws Exception {
        return call(c,"/api/endpoint/login-code",new JSONObject().put("id",id).put("code",code),true);
    }
    static JSONObject pullTask(Context c) throws Exception {return call(c,"/api/endpoint/tasks",null,true);}
    static JSONObject taskResult(Context c,String id,String lease,JSONObject result) throws Exception {
        return call(c,"/api/endpoint/tasks/result",new JSONObject().put("taskId",id).put("lease",lease).put("result",result),true);
    }
    static void heartbeat(Context c) throws Exception {
        JSONObject report=new JSONObject().put("os","Android").put("architecture",android.os.Build.SUPPORTED_ABIS[0]).put("processes",new JSONArray()).put("applications",new JSONArray()).put("errors",new JSONArray());
        JSONObject capabilities=new JSONObject();
        for(String key:new String[]{"process_inventory","application_inventory","process_details","process_tcp","process_events","network_events","file_events"})capabilities.put(key,"unsupported");
        capabilities.put("cloud_requests","available");
        report.put("applens",new JSONObject().put("product","AppLens").put("protocolVersion",1).put("capabilities",capabilities));
        call(c,"/api/endpoint/report",report,true);
    }
}
