package com.local.stock.mobile;

import android.content.Context;
import android.content.SharedPreferences;

/**
 * 动态壁纸的服务器配置。
 *
 * 壁纸服务运行在原生层，读不到 WebView 的 localStorage，
 * 因此 App 保存服务器地址时会通过 {@link LiveWallpaperPlugin} 写入这里。
 */
final class WallpaperConfig {

    static final String PREFS = "stock_wallpaper";
    static final String KEY_HOST = "host";
    static final String KEY_PORT = "port";
    static final String KEY_TLS = "tls";

    private WallpaperConfig() {}

    static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    /** 返回 http(s)://host:port，未配置时返回空串。 */
    static String baseUrl(SharedPreferences prefs) {
        String host = prefs.getString(KEY_HOST, "");
        if (host == null || host.trim().isEmpty()) {
            return "";
        }
        String port = prefs.getString(KEY_PORT, "8000");
        boolean tls = prefs.getBoolean(KEY_TLS, false);
        return (tls ? "https" : "http") + "://" + host.trim() + ":" + port;
    }
}
