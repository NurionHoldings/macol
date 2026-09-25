package com.nurion.macol.caller;

import android.Manifest;
import android.app.Activity;
import android.app.role.RoleManager;
import android.os.Build;
import android.os.Bundle;
import android.content.Intent;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.net.Uri;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

public final class MainActivity extends Activity {
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        String url = getIntent().getStringExtra("template_url");
        if (url != null) {
            Uri base = Uri.parse(BuildConfig.API_BASE);
            Uri target = Uri.parse(url);
            if (!"https".equals(target.getScheme()) || !base.getHost().equals(target.getHost())) {
                finish(); return;
            }
            WebView web = new WebView(this);
            web.getSettings().setJavaScriptEnabled(true);
            web.setWebViewClient(new WebViewClient() {
                @Override public boolean shouldOverrideUrlLoading(WebView view, String next) {
                    Uri uri = Uri.parse(next);
                    return !"https".equals(uri.getScheme()) || !base.getHost().equals(uri.getHost());
                }
            });
            setContentView(web);
            web.loadUrl(url);
            return;
        }
        LinearLayout layout = new LinearLayout(this);
        layout.setPadding(32, 48, 32, 32);
        layout.setOrientation(LinearLayout.VERTICAL);
        TextView explanation = new TextView(this);
        explanation.setText("발신자 시험판: 통화 선별 역할을 선택하면 등록 번호로 발신할 때 마컬 화면 알림을 표시합니다. 전화 통화는 기존 전화 앱에서 계속됩니다. 앱을 열기 전에는 자동 팝업을 보장하지 않습니다.");
        layout.addView(explanation);
        Button enable = new Button(this);
        enable.setText("통화 선별 역할 선택");
        enable.setOnClickListener(v -> {
            RoleManager roles = getSystemService(RoleManager.class);
            if (roles != null && roles.isRoleAvailable(RoleManager.ROLE_CALL_SCREENING))
                startActivityForResult(roles.createRequestRoleIntent(RoleManager.ROLE_CALL_SCREENING), 1);
        });
        layout.addView(enable);
        setContentView(layout);
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
                != android.content.pm.PackageManager.PERMISSION_GRANTED)
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 2);
    }
}
