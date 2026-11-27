package com.local.stock.mobile;

import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.graphics.Shader;
import android.graphics.Typeface;
import android.os.Handler;
import android.os.Looper;
import android.service.wallpaper.WallpaperService;
import android.view.MotionEvent;
import android.view.SurfaceHolder;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Calendar;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * 安卓动态壁纸：把首页的「行情走势 + 秒薪计算器」绘制到桌面上。
 *
 * 说明：
 * - 壁纸在原生层用 Canvas 绘制，不依赖 WebView，性能更稳；
 * - 服务器地址由 App 通过 LiveWallpaperPlugin 写入 SharedPreferences；
 * - 数据来自后端 HTTP 接口：/api/symbols、/api/history、/api/settings，
 *   每 5 秒刷新一次，秒薪金额每秒重算；
 * - 点击壁纸可快速打开 App。
 */
public class StockWallpaperService extends WallpaperService {

    @Override
    public Engine onCreateEngine() {
        return new StockEngine();
    }

    private class StockEngine extends Engine
            implements SharedPreferences.OnSharedPreferenceChangeListener {

        private static final long TICK_MS = 1000L;
        private static final long REFRESH_MS = 5000L;
        // 非交易时段的重试间隔：只在「当天还没成功从网络刷新过」时使用
        private static final long CLOSED_RETRY_MS = 30000L;
        // 交易时段：09:30-11:30、13:00-15:00（累计交易分钟 0 -> 240）
        private static final int SESSION_OPEN = 9 * 60 + 30;
        private static final int SESSION_NOON = 11 * 60 + 30;
        private static final int SESSION_RESUME = 13 * 60;
        private static final int SESSION_CLOSE = 15 * 60;
        private static final int SESSION_MINUTES = 240;

        private static final int COLOR_TEXT = 0xFFE2E8F0;
        private static final int COLOR_MUTED = 0xFF94A3B8;
        private static final int COLOR_UP = 0xFFF87171;
        private static final int COLOR_DOWN = 0xFF4ADE80;
        private static final int COLOR_FLAT = 0xFF94A3B8;

        private final int[] palette = {
                0xFF38BDF8, 0xFFFBBF24, 0xFFA78BFA, 0xFF34D399,
                0xFFF472B6, 0xFF22D3EE, 0xFFFB923C, 0xFFE879F9,
        };

        private final Handler main = new Handler(Looper.getMainLooper());
        private final ExecutorService net = Executors.newSingleThreadExecutor();
        private final Object lock = new Object();
        private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Paint bgPaint = new Paint();

        private final SimpleDateFormat clockFormat = new SimpleDateFormat("HH:mm", Locale.CHINA);

        private float density = 2f;
        private boolean visible;
        private boolean loading;
        private long lastRefreshAt;
        private int surfaceW;
        private int surfaceH;

        private String baseUrl = "";
        private boolean connected;
        private String errorText = "";
        // 当前内存中行情对应的交易日（空表示还没有当天数据）
        private volatile String loadedDay = "";
        // 当天数据是否已经由网络成功刷新过；缓存恢复的数据为 false，只用于先画出曲线
        private volatile boolean loadedFromNet;

        private final List<Card> cards = new ArrayList<>();
        private double salaryDaily = 300;
        private int salaryStart = 9 * 60;
        private int salaryEnd = 18 * 60;

        private final Runnable ticker = new Runnable() {
            @Override
            public void run() {
                if (!visible) return;
                long now = System.currentTimeMillis();
                // 交易时段实时刷新；非交易时段只在「当天数据还没从网络刷新过」时重试，
                // 避免旧版本留下的残缺缓存（例如收盘后一根直线）被永久冻结
                long interval = isMarketOpen() ? REFRESH_MS : CLOSED_RETRY_MS;
                if (now - lastRefreshAt >= interval) {
                    lastRefreshAt = now;
                    if (isMarketOpen() || !hasFreshDay()) {
                        refreshData();
                    }
                }
                drawFrame();
                main.postDelayed(this, TICK_MS);
            }
        };

        @Override
        public void onCreate(SurfaceHolder surfaceHolder) {
            super.onCreate(surfaceHolder);
            density = StockWallpaperService.this.getResources().getDisplayMetrics().density;
            readServer();
            loadCache();
            WallpaperConfig.prefs(StockWallpaperService.this)
                    .registerOnSharedPreferenceChangeListener(this);
        }

        @Override
        public void onDestroy() {
            main.removeCallbacks(ticker);
            net.shutdownNow();
            WallpaperConfig.prefs(StockWallpaperService.this)
                    .unregisterOnSharedPreferenceChangeListener(this);
            super.onDestroy();
        }

        @Override
        public void onVisibilityChanged(boolean visible) {
            this.visible = visible;
            main.removeCallbacks(ticker);
            if (visible) {
                readServer();
                // 跨日后清掉缓存的旧行情，避免锁屏上继续显示昨天的走势
                if (!todayString().equals(loadedDay)) loadCache();
                lastRefreshAt = 0;
                main.post(ticker);
            }
        }

        @Override
        public void onSurfaceChanged(SurfaceHolder holder, int format, int width, int height) {
            super.onSurfaceChanged(holder, format, width, height);
            surfaceW = width;
            surfaceH = height;
            bgPaint.setShader(null);
            drawFrame();
        }

        @Override
        public void onSharedPreferenceChanged(SharedPreferences sharedPreferences, String key) {
            readServer();
            main.post(() -> {
                lastRefreshAt = 0;
                drawFrame();
            });
        }

        @Override
        public void onTouchEvent(MotionEvent event) {
            if (event.getAction() == MotionEvent.ACTION_UP) {
                try {
                    Intent intent = new Intent(Intent.ACTION_MAIN);
                    intent.setComponent(new ComponentName(
                            StockWallpaperService.this, MainActivity.class));
                    intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                    StockWallpaperService.this.startActivity(intent);
                } catch (Exception ignored) {
                    // 无法打开 App 时保持壁纸正常显示
                }
            }
            super.onTouchEvent(event);
        }

        /* ---------------- 数据 ---------------- */

        private void readServer() {
            SharedPreferences prefs = WallpaperConfig.prefs(StockWallpaperService.this);
            baseUrl = WallpaperConfig.baseUrl(prefs);
        }

        private void refreshData() {
            if (loading) return;
            loading = true;

            final String base = baseUrl;
            net.execute(() -> {
                boolean ok = false;
                String error = "";
                List<Card> fresh = new ArrayList<>();
                double daily = 300;
                int start = 9 * 60;
                int end = 18 * 60;

                if (base == null || base.isEmpty()) {
                    error = "未配置服务器";
                } else {
                    try {
                        JSONObject root = new JSONObject(httpGet(base + "/api/symbols"));
                        JSONArray items = root.optJSONArray("items");
                        if (items == null) throw new IOException("bad symbols");

                        for (int i = 0; i < items.length(); i += 1) {
                            JSONObject item = items.optJSONObject(i);
                            if (item == null) continue;

                            Card card = new Card();
                            card.kind = item.optString("kind", "stock");
                            card.name = item.optString("name", item.optString("symbol", ""));
                            card.cost = item.optDouble("cost", 0);
                            card.shares = item.optDouble("shares", 0);
                            card.id = item.optString(
                                    "id", card.kind + ":" + item.optString("symbol", ""));

                            // step=9：抽样覆盖整段交易时段；day 只取当天，避免混入昨天
                            String historyUrl = base + "/api/history?id="
                                    + URLEncoder.encode(card.id, "UTF-8")
                                    + "&limit=700&step=9&day=" + todayString();
                            JSONArray points = new JSONObject(httpGet(historyUrl))
                                    .optJSONArray("items");
                            if (points != null) {
                                for (int j = 0; j < points.length(); j += 1) {
                                    JSONObject point = points.optJSONObject(j);
                                    if (point == null) continue;
                                    double price = point.optDouble("price", Double.NaN);
                                    if (Double.isNaN(price)) continue;
                                    card.points.add(new Point(point.optLong("ts", 0), price));
                                    if (point.has("prev_close") && !point.isNull("prev_close")) {
                                        card.prevClose = point.optDouble("prev_close");
                                    }
                                    if (point.has("change_pct") && !point.isNull("change_pct")) {
                                        card.changePct = point.optDouble("change_pct");
                                    }
                                }
                            }
                            if (!card.points.isEmpty()) {
                                card.price = card.points.get(card.points.size() - 1).price;
                            }
                            fresh.add(card);
                        }

                        try {
                            JSONObject settings = new JSONObject(httpGet(base + "/api/settings"));
                            if (settings.has("daily")) {
                                daily = settings.optDouble("daily", daily);
                            }
                            if (settings.has("start")) {
                                start = parseClock(settings.optString("start", "09:00"), start);
                            }
                            if (settings.has("end")) {
                                end = parseClock(settings.optString("end", "18:00"), end);
                            }
                        } catch (Exception ignored) {
                            // 秒薪参数使用默认值，不影响行情显示
                        }

                        ok = true;
                    } catch (Exception e) {
                        error = "连接失败";
                    }
                }

                synchronized (lock) {
                    if (ok) {
                        cards.clear();
                        cards.addAll(fresh);
                        salaryDaily = daily;
                        salaryStart = start;
                        salaryEnd = end;
                    }
                    connected = ok;
                    errorText = error;
                }
                if (ok) {
                    loadedDay = todayString();
                    loadedFromNet = true;
                    // 持久化当天走势：锁屏下次重建时无需等待网络即可还原曲线
                    saveCache(fresh);
                }
                loading = false;
                main.post(StockEngine.this::drawFrame);
            });
        }

        private String httpGet(String url) throws IOException {
            HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
            conn.setConnectTimeout(3500);
            conn.setReadTimeout(4500);
            conn.setRequestProperty("Accept", "application/json");
            conn.setRequestProperty("User-Agent", "AStockWallpaper/1.0");
            try {
                int code = conn.getResponseCode();
                InputStream input = code >= 400 ? conn.getErrorStream() : conn.getInputStream();
                if (input == null) throw new IOException("HTTP " + code);
                ByteArrayOutputStream output = new ByteArrayOutputStream();
                byte[] buffer = new byte[4096];
                int read;
                while ((read = input.read(buffer)) != -1) {
                    output.write(buffer, 0, read);
                }
                input.close();
                if (code >= 400) throw new IOException("HTTP " + code);
                return output.toString("UTF-8");
            } finally {
                conn.disconnect();
            }
        }

        private int parseClock(String text, int fallback) {
            try {
                String[] parts = text.split(":");
                if (parts.length < 2) return fallback;
                int hour = Integer.parseInt(parts[0].trim());
                int minute = Integer.parseInt(parts[1].trim());
                if (hour < 0 || hour > 23 || minute < 0 || minute > 59) return fallback;
                return hour * 60 + minute;
            } catch (Exception e) {
                return fallback;
            }
        }

        /* ---------------- 交易日与缓存 ---------------- */

        /** 当前本地日期，格式 YYYY-MM-DD */
        private String todayString() {
            return new SimpleDateFormat("yyyy-MM-dd", Locale.CHINA).format(new Date());
        }

        /** 是否处于 A 股交易时段（工作日 09:30-11:30、13:00-15:00） */
        private boolean isMarketOpen() {
            Calendar now = Calendar.getInstance();
            int weekday = now.get(Calendar.DAY_OF_WEEK);
            if (weekday == Calendar.SATURDAY || weekday == Calendar.SUNDAY) return false;
            int minutes = now.get(Calendar.HOUR_OF_DAY) * 60 + now.get(Calendar.MINUTE);
            return (minutes >= SESSION_OPEN && minutes <= SESSION_NOON)
                    || (minutes >= SESSION_RESUME && minutes <= SESSION_CLOSE);
        }

        /** 当天行情是否已经由网络成功刷新过（仅从缓存还原的数据不算） */
        private boolean hasFreshDay() {
            return loadedFromNet && todayString().equals(loadedDay);
        }

        /** 时间戳 -> 当天累计交易分钟（09:30 为 0，15:00 为 240，午休折叠） */
        private double tradingMinutes(long ts) {
            Calendar moment = Calendar.getInstance();
            moment.setTimeInMillis(ts);
            double minutes = moment.get(Calendar.HOUR_OF_DAY) * 60
                    + moment.get(Calendar.MINUTE) + moment.get(Calendar.SECOND) / 60.0;
            if (minutes <= SESSION_OPEN) return 0;
            if (minutes <= SESSION_NOON) return minutes - SESSION_OPEN;
            if (minutes < SESSION_RESUME) return SESSION_NOON - SESSION_OPEN;
            if (minutes <= SESSION_CLOSE) {
                return (SESSION_NOON - SESSION_OPEN) + (minutes - SESSION_RESUME);
            }
            return SESSION_MINUTES;
        }

        /** 累计交易分钟 -> "HH:MM"，与 App 折线图横轴保持一致 */
        private String clockText(int minutes) {
            if (minutes <= 0) return String.format(Locale.CHINA, "%02d:%02d",
                    SESSION_OPEN / 60, SESSION_OPEN % 60);
            if (minutes < SESSION_NOON - SESSION_OPEN) {
                int clock = SESSION_OPEN + minutes;
                return String.format(Locale.CHINA, "%02d:%02d", clock / 60, clock % 60);
            }
            if (minutes == SESSION_NOON - SESSION_OPEN) {
                return String.format(Locale.CHINA, "%02d:%02d",
                        SESSION_NOON / 60, SESSION_NOON % 60);
            }
            if (minutes >= SESSION_MINUTES) {
                return String.format(Locale.CHINA, "%02d:%02d",
                        SESSION_CLOSE / 60, SESSION_CLOSE % 60);
            }
            int clock = SESSION_RESUME + (minutes - (SESSION_NOON - SESSION_OPEN));
            return String.format(Locale.CHINA, "%02d:%02d", clock / 60, clock % 60);
        }

        private SharedPreferences cachePrefs() {
            return StockWallpaperService.this
                    .getSharedPreferences("stock_wallpaper_cache", Context.MODE_PRIVATE);
        }

        /** 读取当天缓存的全天走势：锁屏不依赖 App 和网络也能画出完整曲线 */
        private void loadCache() {
            String raw = cachePrefs().getString("today", "");
            if (raw == null || raw.isEmpty()) return;
            try {
                JSONObject root = new JSONObject(raw);
                String day = root.optString("day", "");
                if (!todayString().equals(day)) return;
                JSONArray items = root.optJSONArray("cards");
                if (items == null) return;

                List<Card> cached = new ArrayList<>();
                for (int i = 0; i < items.length(); i += 1) {
                    JSONObject item = items.optJSONObject(i);
                    if (item == null) continue;
                    Card card = new Card();
                    card.id = item.optString("id", "");
                    card.kind = item.optString("kind", "stock");
                    card.name = item.optString("name", "");
                    card.cost = item.optDouble("cost", 0);
                    card.shares = item.optDouble("shares", 0);
                    card.price = item.isNull("price") ? null : item.optDouble("price");
                    card.prevClose = item.isNull("prevClose") ? null : item.optDouble("prevClose");
                    card.changePct = item.isNull("changePct") ? null : item.optDouble("changePct");

                    JSONArray points = item.optJSONArray("points");
                    if (points != null) {
                        for (int j = 0; j < points.length(); j += 1) {
                            JSONArray one = points.optJSONArray(j);
                            if (one == null || one.length() < 2) continue;
                            card.points.add(new Point(one.optLong(0), one.optDouble(1)));
                        }
                    }
                    if (!card.points.isEmpty()) cached.add(card);
                }
                if (cached.isEmpty()) return;

                synchronized (lock) {
                    cards.clear();
                    cards.addAll(cached);
                }
                loadedDay = day;
                // 缓存只用来先画出曲线，标记为「未刷新」，进入壁纸后仍会补拉一次当天数据
                loadedFromNet = false;
                connected = true;
                errorText = "";
                main.post(this::drawFrame);
            } catch (Exception ignored) {
                // 缓存损坏时忽略，等待网络刷新
            }
        }

        /** 把最近一次成功获取的当日走势写入本地缓存 */
        private void saveCache(List<Card> source) {
            try {
                JSONArray items = new JSONArray();
                for (Card card : source) {
                    if (card.points.isEmpty()) continue;
                    JSONObject item = new JSONObject();
                    item.put("id", card.id);
                    item.put("kind", card.kind);
                    item.put("name", card.name);
                    item.put("cost", card.cost);
                    item.put("shares", card.shares);
                    item.put("price", card.price == null ? JSONObject.NULL : card.price);
                    item.put("prevClose", card.prevClose == null ? JSONObject.NULL : card.prevClose);
                    item.put("changePct", card.changePct == null ? JSONObject.NULL : card.changePct);
                    JSONArray points = new JSONArray();
                    for (Point point : card.points) {
                        JSONArray one = new JSONArray();
                        one.put(point.ts);
                        one.put(point.price);
                        points.put(one);
                    }
                    item.put("points", points);
                    items.put(item);
                }
                JSONObject root = new JSONObject();
                root.put("day", todayString());
                root.put("cards", items);
                cachePrefs().edit().putString("today", root.toString()).apply();
            } catch (Exception ignored) {
                // 缓存写入失败不影响实时显示
            }
        }

        /* ---------------- 绘制 ---------------- */

        private void drawFrame() {
            if (!visible) return;
            SurfaceHolder holder = getSurfaceHolder();
            Canvas canvas = null;
            try {
                canvas = holder.lockCanvas();
                if (canvas != null) drawScene(canvas);
            } catch (Exception ignored) {
                // 锁屏、切换桌面等情况下可能拿不到画布，跳过这一帧
            } finally {
                if (canvas != null) {
                    try {
                        holder.unlockCanvasAndPost(canvas);
                    } catch (Exception ignored) {
                        // 忽略解锁异常
                    }
                }
            }
        }

        private void drawScene(Canvas canvas) {
            int width = canvas.getWidth();
            int height = canvas.getHeight();
            if (width <= 0 || height <= 0) return;

            if (bgPaint.getShader() == null || surfaceW != width || surfaceH != height) {
                surfaceW = width;
                surfaceH = height;
                bgPaint.setShader(new LinearGradient(
                        0, 0, 0, height,
                        Color.parseColor("#0B1220"),
                        Color.parseColor("#0F172A"),
                        Shader.TileMode.CLAMP));
            }
            canvas.drawRect(0, 0, width, height, bgPaint);

            List<Card> snapshot;
            double daily;
            int start;
            int end;
            boolean ok;
            String error;
            synchronized (lock) {
                snapshot = new ArrayList<>(cards);
                daily = salaryDaily;
                start = salaryStart;
                end = salaryEnd;
                ok = connected;
                error = errorText;
            }

            float margin = dp(16);
            paint.setShader(null);
            paint.setStyle(Paint.Style.FILL);

            // 顶部品牌与时间
            float topBase = height * 0.055f;
            paint.setTypeface(Typeface.DEFAULT_BOLD);
            paint.setTextSize(dp(15));
            paint.setTextAlign(Paint.Align.LEFT);
            paint.setColor(ok ? COLOR_DOWN : 0xFF64748B);
            canvas.drawCircle(margin + dp(3), topBase - dp(5), dp(3), paint);
            paint.setColor(COLOR_TEXT);
            canvas.drawText("A股实时行情", margin + dp(12), topBase, paint);

            paint.setTextAlign(Paint.Align.RIGHT);
            canvas.drawText(clockFormat.format(new Date()), width - margin, topBase, paint);

            if (baseUrl.isEmpty()) {
                paint.setTypeface(Typeface.DEFAULT);
                paint.setTextAlign(Paint.Align.CENTER);
                paint.setColor(COLOR_MUTED);
                paint.setTextSize(dp(13));
                canvas.drawText("尚未配置后端服务器", width / 2f, height * 0.45f, paint);
                paint.setTextSize(dp(11));
                canvas.drawText("请打开 App → 实时连接 → 设为动态壁纸",
                        width / 2f, height * 0.45f + dp(22), paint);
                return;
            }

            float cardX = margin;
            float cardW = width - margin * 2;
            float chartTop = height * 0.085f;
            float chartBottom = height * 0.55f;
            drawCardBg(canvas, cardX, chartTop, cardW, chartBottom - chartTop);

            // 标题
            float pad = dp(10);
            float titleBase = chartTop + dp(19);
            paint.setTypeface(Typeface.DEFAULT_BOLD);
            paint.setTextSize(dp(12));
            paint.setTextAlign(Paint.Align.LEFT);
            paint.setColor(COLOR_TEXT);
            canvas.drawText("行情走势", cardX + pad, titleBase, paint);

            if (!ok && !error.isEmpty()) {
                paint.setTypeface(Typeface.DEFAULT);
                paint.setTextSize(dp(9));
                paint.setTextAlign(Paint.Align.RIGHT);
                paint.setColor(0xFFF59E0B);
                canvas.drawText(error, cardX + cardW - pad, titleBase, paint);
            }

            // 标的图例：每行一个标的，左侧名称、右侧价格与涨跌幅，避免文字互相挤压
            float legendBase = titleBase + dp(15);
            float rowHeight = dp(14);
            int legendRows = Math.min(snapshot.size(), 3);
            for (int i = 0; i < legendRows; i += 1) {
                Card card = snapshot.get(i);
                float baseline = legendBase + i * rowHeight;
                String priceText = card.price == null
                        ? "--" : String.format(Locale.CHINA, "%.2f", card.price);
                String pctText = card.changePct == null
                        ? "--" : String.format(Locale.CHINA, "%+.2f%%", card.changePct);

                paint.setTypeface(Typeface.DEFAULT);
                paint.setTextSize(dp(9.5f));

                paint.setColor(seriesColor(i));
                canvas.drawCircle(cardX + pad + dp(3), baseline - dp(3), dp(3), paint);

                paint.setColor(0xFFCBD5E1);
                paint.setTextAlign(Paint.Align.LEFT);
                canvas.drawText(card.name, cardX + pad + dp(9), baseline, paint);

                paint.setColor(trendColor(card.changePct));
                paint.setTextAlign(Paint.Align.RIGHT);
                canvas.drawText(pctText, cardX + cardW - pad, baseline, paint);
                float pctWidth = paint.measureText(pctText);
                canvas.drawText(priceText, cardX + cardW - pad - pctWidth - dp(6),
                        baseline, paint);
            }
            paint.setTextAlign(Paint.Align.LEFT);

            boolean hasIndex = false;
            boolean hasStock = false;
            for (Card card : snapshot) {
                if ("index".equals(card.kind)) hasIndex = true;
                else hasStock = true;
            }

            float plotLeft = cardX + (hasIndex ? dp(40) : pad);
            float plotRight = cardX + cardW - (hasStock ? dp(44) : pad);
            float plotTop = legendBase + legendRows * rowHeight + dp(4);
            float plotBottom = chartBottom - dp(18);

            if (snapshot.isEmpty()) {
                paint.setTypeface(Typeface.DEFAULT);
                paint.setTextSize(dp(11));
                paint.setTextAlign(Paint.Align.CENTER);
                paint.setColor(COLOR_MUTED);
                canvas.drawText("暂无标的，请在 App 中添加",
                        cardX + cardW / 2f, (plotTop + plotBottom) / 2f, paint);
            } else if (plotBottom > plotTop + dp(20)) {
                drawChart(canvas, snapshot, plotLeft, plotRight, plotTop, plotBottom);
            }

            drawSalary(canvas, snapshot, daily, start, end, cardX, cardW,
                    chartBottom + dp(12), height * 0.855f);
        }

        private void drawChart(Canvas canvas, List<Card> snapshot,
                               float plotLeft, float plotRight, float plotTop, float plotBottom) {
            double[] indexRange = null;
            double[] stockRange = null;
            for (Card card : snapshot) {
                if (card.points.size() < 2) continue;
                if ("index".equals(card.kind) && indexRange == null) {
                    indexRange = seriesRange(card);
                } else if ("stock".equals(card.kind) && stockRange == null) {
                    stockRange = seriesRange(card);
                }
            }

            // 网格与左右两侧价格刻度
            paint.setStyle(Paint.Style.STROKE);
            paint.setStrokeWidth(dp(0.6f));
            for (int i = 0; i <= 4; i += 1) {
                float fraction = i / 4f;
                float y = plotBottom - fraction * (plotBottom - plotTop);
                paint.setColor(0x1F94A3B8);
                canvas.drawLine(plotLeft, y, plotRight, y, paint);

                if (indexRange != null) {
                    double value = indexRange[0] + fraction * (indexRange[1] - indexRange[0]);
                    paint.setStyle(Paint.Style.FILL);
                    paint.setTextSize(dp(8));
                    paint.setTextAlign(Paint.Align.RIGHT);
                    paint.setColor(0xFF64748B);
                    canvas.drawText(String.format(Locale.CHINA, "%.2f", value),
                            plotLeft - dp(4), y + dp(3), paint);
                    paint.setStyle(Paint.Style.STROKE);
                }
                if (stockRange != null) {
                    double value = stockRange[0] + fraction * (stockRange[1] - stockRange[0]);
                    paint.setStyle(Paint.Style.FILL);
                    paint.setTextSize(dp(8));
                    paint.setTextAlign(Paint.Align.LEFT);
                    paint.setColor(0xFF64748B);
                    canvas.drawText(String.format(Locale.CHINA, "%.2f", value),
                            plotRight + dp(4), y + dp(3), paint);
                    paint.setStyle(Paint.Style.STROKE);
                }
            }

            // 折线：横轴按「累计交易分钟」铺开（09:30 -> 0，15:00 -> 240），
            // 午休被折叠，抽样不均或数据有缺口也不会把曲线挤在一侧
            paint.setStrokeCap(Paint.Cap.ROUND);
            paint.setStrokeJoin(Paint.Join.ROUND);
            paint.setStrokeWidth(dp(1.3f));
            float plotWidth = plotRight - plotLeft;
            for (int i = 0; i < snapshot.size(); i += 1) {
                Card card = snapshot.get(i);
                if (card.points.size() < 2) continue;

                double[] range = "index".equals(card.kind) ? indexRange : stockRange;
                if (range == null) range = seriesRange(card);

                Path path = new Path();
                float lastX = plotLeft;
                float lastY = plotBottom;
                for (int j = 0; j < card.points.size(); j += 1) {
                    Point point = card.points.get(j);
                    float x = plotLeft + plotWidth
                            * (float) (tradingMinutes(point.ts) / (double) SESSION_MINUTES);
                    float y = plotBottom
                            - (float) ((point.price - range[0]) / (range[1] - range[0]))
                            * (plotBottom - plotTop);
                    if (j == 0) path.moveTo(x, y);
                    else path.lineTo(x, y);
                    lastX = x;
                    lastY = y;
                }
                paint.setStyle(Paint.Style.STROKE);
                paint.setColor(seriesColor(i));
                canvas.drawPath(path, paint);
                paint.setStyle(Paint.Style.FILL);
                canvas.drawCircle(lastX, lastY, dp(2.2f), paint);
            }

            // 底部时间刻度：固定 09:30 / 10:30 / 11:30 / 14:00 / 15:00
            paint.setStyle(Paint.Style.FILL);
            paint.setTextSize(dp(8));
            paint.setColor(0xFF64748B);
            paint.setTextAlign(Paint.Align.CENTER);
            int[] ticks = {0, 60, 120, 180, 240};
            for (int minutes : ticks) {
                float x = plotLeft + plotWidth * minutes / (float) SESSION_MINUTES;
                float textX = Math.max(plotLeft + dp(12), Math.min(plotRight - dp(12), x));
                canvas.drawText(clockText(minutes), textX, plotBottom + dp(12), paint);
            }
        }

        private void drawSalary(Canvas canvas, List<Card> snapshot,
                                double daily, int start, int end,
                                float cardX, float cardW, float cardTop, float cardBottom) {
            if (cardBottom <= cardTop + dp(80)) return;
            drawCardBg(canvas, cardX, cardTop, cardW, cardBottom - cardTop);

            Salary salary = computeSalary(daily, start, end, snapshot);
            float pad = dp(12);
            float innerX = cardX + pad;
            float innerRight = cardX + cardW - pad;

            paint.setShader(null);
            paint.setTypeface(Typeface.DEFAULT_BOLD);
            paint.setTextSize(dp(12));
            paint.setTextAlign(Paint.Align.LEFT);
            paint.setColor(COLOR_TEXT);
            canvas.drawText("秒薪计算器", innerX, cardTop + dp(19), paint);

            paint.setTypeface(Typeface.DEFAULT);
            paint.setTextSize(dp(10));
            paint.setColor(COLOR_MUTED);
            canvas.drawText("今日已赚", innerX, cardTop + dp(36), paint);

            paint.setTypeface(Typeface.DEFAULT_BOLD);
            paint.setTextSize(dp(28));
            paint.setShader(new LinearGradient(
                    innerX, 0, innerX + dp(220), 0,
                    new int[]{0xFF38BDF8, 0xFFA78BFA, 0xFFF472B6},
                    null, Shader.TileMode.CLAMP));
            canvas.drawText(money(salary.total, 4), innerX, cardTop + dp(68), paint);
            paint.setShader(null);

            paint.setTypeface(Typeface.DEFAULT);
            paint.setTextSize(dp(10));
            float breakdownBase = cardTop + dp(86);
            if (salary.holdings > 0) {
                paint.setColor(COLOR_MUTED);
                paint.setTextAlign(Paint.Align.LEFT);
                canvas.drawText("工资", innerX, breakdownBase, paint);
                paint.setColor(COLOR_TEXT);
                canvas.drawText(money(salary.earned, 4), innerX + dp(28), breakdownBase, paint);

                paint.setColor(COLOR_MUTED);
                paint.setTextAlign(Paint.Align.RIGHT);
                canvas.drawText("投资", innerRight - dp(74), breakdownBase, paint);
                paint.setColor(trendColor(salary.invest));
                canvas.drawText(signedMoney(salary.invest, 2), innerRight, breakdownBase, paint);
            } else {
                paint.setColor(COLOR_MUTED);
                paint.setTextAlign(Paint.Align.LEFT);
                canvas.drawText("填写持仓后可叠加投资收益", innerX, breakdownBase, paint);
            }

            // 进度条
            float barTop = cardTop + dp(96);
            float barHeight = dp(6);
            RectF bar = new RectF(innerX, barTop, innerRight, barTop + barHeight);
            paint.setStyle(Paint.Style.FILL);
            paint.setColor(0x2E94A3B8);
            canvas.drawRoundRect(bar, barHeight / 2f, barHeight / 2f, paint);

            float progress = (float) Math.max(0, Math.min(1, salary.percent / 100));
            if (progress > 0) {
                RectF filled = new RectF(innerX, barTop,
                        innerX + bar.width() * progress, barTop + barHeight);
                paint.setShader(new LinearGradient(
                        innerX, 0, innerRight, 0,
                        new int[]{0xFF38BDF8, 0xFFA78BFA},
                        null, Shader.TileMode.CLAMP));
                canvas.drawRoundRect(filled, barHeight / 2f, barHeight / 2f, paint);
                paint.setShader(null);
            }

            float footBase = cardTop + dp(121);
            paint.setTextSize(dp(9.5f));
            paint.setColor(COLOR_MUTED);
            paint.setTextAlign(Paint.Align.LEFT);
            canvas.drawText(String.format(Locale.CHINA, "已完成 %.2f%%", salary.percent),
                    innerX, footBase, paint);
            paint.setTextAlign(Paint.Align.RIGHT);
            canvas.drawText(
                    salary.remainSec <= 0
                            ? "今日已收工"
                            : "已工作 " + duration(salary.workedSec),
                    innerRight, footBase, paint);
        }

        private void drawCardBg(Canvas canvas, float x, float y, float width, float height) {
            RectF rect = new RectF(x, y, x + width, y + height);
            paint.setShader(null);
            paint.setStyle(Paint.Style.FILL);
            paint.setColor(0xCC1E293B);
            canvas.drawRoundRect(rect, dp(9), dp(9), paint);
            paint.setStyle(Paint.Style.STROKE);
            paint.setStrokeWidth(dp(1));
            paint.setColor(0x332E3A4F);
            canvas.drawRoundRect(rect, dp(9), dp(9), paint);
            paint.setStyle(Paint.Style.FILL);
        }

        /* ---------------- 计算辅助 ---------------- */

        private Salary computeSalary(double daily, int startMin, int endMin, List<Card> snapshot) {
            Salary salary = new Salary();

            Calendar now = Calendar.getInstance();
            int nowMin = now.get(Calendar.HOUR_OF_DAY) * 60 + now.get(Calendar.MINUTE);

            Calendar start = Calendar.getInstance();
            start.set(Calendar.HOUR_OF_DAY, startMin / 60);
            start.set(Calendar.MINUTE, startMin % 60);
            start.set(Calendar.SECOND, 0);
            start.set(Calendar.MILLISECOND, 0);

            Calendar end = (Calendar) start.clone();
            end.set(Calendar.HOUR_OF_DAY, endMin / 60);
            end.set(Calendar.MINUTE, endMin % 60);

            boolean overnight = endMin <= startMin;
            if (overnight) {
                if (nowMin < startMin) {
                    start.add(Calendar.DAY_OF_MONTH, -1);
                } else {
                    end.add(Calendar.DAY_OF_MONTH, 1);
                }
            }

            long totalSec = (end.getTimeInMillis() - start.getTimeInMillis()) / 1000;
            if (totalSec <= 0) return salary;

            long nowMillis = now.getTimeInMillis();
            long workedSec = Math.min(Math.max((nowMillis - start.getTimeInMillis()) / 1000, 0), totalSec);

            double earned = daily * workedSec / (double) totalSec;
            double invest = 0;
            int holdings = 0;
            for (Card card : snapshot) {
                if (!"stock".equals(card.kind)) continue;
                if (!(card.cost > 0) || !(card.shares > 0)) continue;
                holdings += 1;
                if (card.price == null || card.prevClose == null) continue;
                invest += (card.price - card.prevClose) * card.shares;
            }

            salary.earned = earned;
            salary.invest = invest;
            salary.total = earned + invest;
            salary.holdings = holdings;
            salary.percent = workedSec * 100.0 / totalSec;
            salary.workedSec = workedSec;
            salary.remainSec = totalSec - workedSec;
            return salary;
        }

        private double[] seriesRange(Card card) {
            double min = Double.MAX_VALUE;
            double max = -Double.MAX_VALUE;
            for (Point point : card.points) {
                min = Math.min(min, point.price);
                max = Math.max(max, point.price);
            }
            if (!(min < max)) {
                double base = min == Double.MAX_VALUE ? 0 : min;
                min = base - 1;
                max = base + 1;
            }
            double padding = (max - min) * 0.08;
            return new double[]{min - padding, max + padding};
        }

        private int seriesColor(int index) {
            return palette[index % palette.length];
        }

        private int trendColor(Double percent) {
            if (percent == null) return COLOR_FLAT;
            if (percent > 0) return COLOR_UP;
            if (percent < 0) return COLOR_DOWN;
            return COLOR_FLAT;
        }

        private String money(double value, int digits) {
            return "¥" + String.format(Locale.CHINA, "%,." + digits + "f", value);
        }

        private String signedMoney(double value, int digits) {
            return (value >= 0 ? "+" : "-") + money(Math.abs(value), digits);
        }

        private String duration(long seconds) {
            long total = Math.max(0, seconds);
            long hour = total / 3600;
            long minute = (total % 3600) / 60;
            long second = total % 60;
            return String.format(Locale.CHINA, "%02d:%02d:%02d", hour, minute, second);
        }

        private float dp(float value) {
            return value * density;
        }
    }

    private static class Point {
        final long ts;
        final double price;

        Point(long ts, double price) {
            this.ts = ts;
            this.price = price;
        }
    }

    private static class Card {
        String id = "";
        String kind = "stock";
        String name = "";
        double cost;
        double shares;
        Double price;
        Double prevClose;
        Double changePct;
        final List<Point> points = new ArrayList<>();
    }

    private static class Salary {
        double earned;
        double invest;
        double total;
        double percent;
        int holdings;
        long workedSec;
        long remainSec;
    }
}
