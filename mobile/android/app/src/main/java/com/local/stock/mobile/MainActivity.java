package com.local.stock.mobile;

import android.os.Bundle;

import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {

    @Override
    public void onCreate(Bundle savedInstanceState) {
        // 注册自定义插件：动态壁纸设置与服务器地址同步
        registerPlugin(LiveWallpaperPlugin.class);
        super.onCreate(savedInstanceState);
    }
}
