package com.nurion.macol.caller;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Intent;
import android.net.Uri;
import android.telecom.Call;
import android.telecom.CallScreeningService;
import org.json.JSONObject;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.concurrent.Executors;
import java.util.concurrent.ExecutorService;

public final class DialScreeningService extends CallScreeningService {
    private static final ExecutorService LOOKUPS = Executors.newSingleThreadExecutor();
    @Override public void onScreenCall(Call.Details details) {
        if (details.getCallDirection() == Call.Details.DIRECTION_INCOMING) {
            respondToCall(details, new CallResponse.Builder().build());
            return;
        }
        if (details.getCallDirection() != Call.Details.DIRECTION_OUTGOING) return;
        Uri handle = details.getHandle();
        if (handle == null || !"tel".equals(handle.getScheme())) return;
        String number = handle.getSchemeSpecificPart().replaceAll("[^0-9]", "");
        if (!number.matches("01[016789][0-9]{7,8}")) return;
        LOOKUPS.execute(() -> lookup(number));
    }

    private void lookup(String number) {
        HttpURLConnection conn = null;
        try {
            URL endpoint = new URL(BuildConfig.API_BASE + "/public/templates/" + number);
            conn = (HttpURLConnection) endpoint.openConnection();
            conn.setConnectTimeout(2500);
            conn.setReadTimeout(2500);
            conn.setInstanceFollowRedirects(false);
            if (conn.getResponseCode() != 200) return;
            byte[] bytes = conn.getInputStream().readNBytes(4096);
            JSONObject body = new JSONObject(new String(bytes, java.nio.charset.StandardCharsets.UTF_8));
            String target = body.getString("template_url");
            Uri uri = Uri.parse(target);
            if (!"https".equals(uri.getScheme()) ||
                !Uri.parse(BuildConfig.API_BASE).getHost().equals(uri.getHost())) return;
            NotificationManager manager = getSystemService(NotificationManager.class);
            manager.createNotificationChannel(new NotificationChannel("macol_call", "통화 중 마컬 화면", NotificationManager.IMPORTANCE_HIGH));
            Intent open = new Intent(this, MainActivity.class).putExtra("template_url", target);
            PendingIntent pending = PendingIntent.getActivity(this, 1, open,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
            Notification notification = new Notification.Builder(this, "macol_call")
                .setSmallIcon(android.R.drawable.ic_menu_call)
                .setContentTitle(body.optString("display_name", "마컬") + "의 전화응대")
                .setContentText("통화 중 템플릿 열기")
                .setContentIntent(pending).setAutoCancel(true).build();
            manager.notify(100, notification);
        } catch (Exception ignored) {
            // 네트워크 오류는 일반 전화 흐름에 영향을 주지 않는다.
        } finally {
            if (conn != null) conn.disconnect();
        }
    }
}
