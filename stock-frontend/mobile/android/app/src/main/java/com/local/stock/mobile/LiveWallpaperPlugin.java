package com.local.stock.mobile;

import android.app.WallpaperManager;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;

import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

/**
 * 安卓动态壁纸插件。
 *
 * - setServer：把后端地址写入 SharedPreferences，供原生壁纸服务读取；
 * - open：跳转系统动态壁纸预览页，由用户确认应用。
 */
@CapacitorPlugin(name = "LiveWallpaper")
public class LiveWallpaperPlugin extends Plugin {

    @PluginMethod
    public void setServer(PluginCall call) {
        String host = call.getString("host", "");
        String port = call.getString("port", "8000");
        Boolean tlsValue = call.getBoolean("tls", Boolean.FALSE);
        boolean tls = tlsValue != null && tlsValue;

        SharedPreferences prefs = WallpaperConfig.prefs(getContext());
        prefs.edit()
                .putString(WallpaperConfig.KEY_HOST, host == null ? "" : host.trim())
                .putString(WallpaperConfig.KEY_PORT, port == null ? "8000" : port.trim())
                .putBoolean(WallpaperConfig.KEY_TLS, tls)
                .apply();
        call.resolve();
    }

    @PluginMethod
    public void open(PluginCall call) {
        Context context = getContext();
        Intent intent = new Intent(WallpaperManager.ACTION_CHANGE_LIVE_WALLPAPER);
        intent.putExtra(
                WallpaperManager.EXTRA_LIVE_WALLPAPER_COMPONENT,
                new ComponentName(context, StockWallpaperService.class)
        );
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        try {
            context.startActivity(intent);
            call.resolve();
            return;
        } catch (Exception ignored) {
            // 部分机型不支持直接预览，退回系统动态壁纸列表
        }

        try {
            Intent chooser = new Intent(WallpaperManager.ACTION_LIVE_WALLPAPER_CHOOSER);
            chooser.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            context.startActivity(chooser);
            call.resolve();
        } catch (Exception e) {
            call.reject("无法打开壁纸设置：" + e.getMessage());
        }
    }
}
