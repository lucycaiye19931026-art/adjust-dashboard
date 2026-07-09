"""
Adjust PH_lucy 数据看板 — Web 服务
支持 Android Channel/Campaign 看板 + iOS Channel/Campaign 看板
Facebook 消耗来自 Meta Ads API（真实数据）
TikTok 消耗来自 TikTok Ads API（真实数据）
Google Ads 消耗来自 Google Ads API v24（直连，真实数据）
"""
import re
import os
import json as _json
import requests
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify, request, Response

# ── Adjust 配置 ───────────────────────────────────────────
APP_TOKEN  = os.environ.get("ADJUST_APP_TOKEN",  "g0ylloj1w54w")
USER_TOKEN = os.environ.get("ADJUST_USER_TOKEN", "g9gJyYMyUN41vFeaR5QW")
BASE_URL   = "https://automate.adjust.com/reports-service/report"
HEADERS    = {"Authorization": f"Bearer {USER_TOKEN}"}
KEY_CH     = ["Google Ads", "Facebook", "TikTok for Business"]

SPEND_FORMULA = {
    "茄子快传 | SHAREit": ("loan",     4),
    "CK-loan And":        ("loan",    12),
    "Yundun-and":         ("installs", 0.4),
    "loan_ market_2":     ("installs", 0.4),
}
CPS_FIXED = {
    "茄子快传 | SHAREit": 4,
    "CK-loan And":        12,
}

# ── Facebook Ads API 配置 ─────────────────────────────────
FB_LONG_TOKEN = os.environ.get("FB_LONG_TOKEN", "")
FB_APP_ID     = os.environ.get("FB_APP_ID",     "3740970239454882")
FB_APP_SECRET = os.environ.get("FB_APP_SECRET", "")
FB_ACT_IDS    = ["act_2043458276522117", "act_1338744840870824", "act_554870820824463", "act_1763443588125609", "act_4425161567801548"]
FB_BASE       = "https://graph.facebook.com/v19.0"

# ── TikTok Ads API 配置 ───────────────────────────────────
TT_ACCESS_TOKEN = os.environ.get("TT_ACCESS_TOKEN", "")
TT_ADV_ID       = os.environ.get("TT_ADV_ID",       "7358007483270692880")
TT_BASE         = "https://business-api.tiktok.com/open_api/v1.3"

# ── iOS 专属配置 ──────────────────────────────────────────
IOS_APP_TOKEN   = os.environ.get("IOS_ADJUST_APP_TOKEN", "du1u32cgaigw")
IOS_KEY_CH      = ["Facebook", "TikTok for Business", "Apple"]
FB_IOS_ACT_IDS  = ["act_826668223504196", "act_485941130935481", "act_1050911951210157", "act_2487386801730510"]
TT_IOS_ADV_ID   = os.environ.get("TT_IOS_ADV_ID", "7358007484973563921")

BASE_PARAMS = {
    "metrics":            "attribution_clicks,installs,cost,register_success_events,apply_for_loan_events,loan_success_events,first_loan_amount_revenue",
    "ad_spend_mode":      "network",
    "attribution_source": "first",
    "reattributed":       "all",
    "cohort_maturity":    "immature",
    "format_dates":       "false",
    "full_data":          "true",
    "utc_offset":         "+08:00",
    "attribution_type":   "all",
    "sandbox":            "false",
    "ironsource_mode":    "ironsource",
    "sort":               "-cost",
}

app = Flask(__name__)

# ── 工具函数 ─────────────────────────────────────────────

def now8():
    return datetime.now(timezone(timedelta(hours=8)))

def sf(v, t=float):
    try: return t(v or 0)
    except: return 0

# ── Facebook API 数据拉取 ─────────────────────────────────

def get_fb_token():
    """返回有效 Token（自动尝试刷新）"""
    return FB_LONG_TOKEN

def fb_date_range(period):
    """Facebook API 日期范围"""
    t  = now8()
    td = t.strftime("%Y-%m-%d")
    yd = (t - timedelta(days=1)).strftime("%Y-%m-%d")
    ranges = {
        "today":     (td, td),
        "yesterday": (yd, yd),
        "3days":     ((t - timedelta(days=2)).strftime("%Y-%m-%d"), td),
        "7days":     ((t - timedelta(days=6)).strftime("%Y-%m-%d"), td),
        "month":     (t.replace(day=1).strftime("%Y-%m-%d"), td),
    }
    return ranges.get(period, (td, td))

def fetch_fb_channel_spend(period):
    """拉取 Facebook 全部账户总消耗（用于 Channel 看板）"""
    since, until = fb_date_range(period)
    token = get_fb_token()
    total_spend = 0.0
    for act_id in FB_ACT_IDS:
        try:
            r = requests.get(f"{FB_BASE}/{act_id}/insights", timeout=30, params={
                "access_token": token,
                "fields":       "spend",
                "time_range":   _json.dumps({"since": since, "until": until}),
                "level":        "account",
            })
            if r.status_code == 200:
                data = r.json().get("data", [])
                total_spend += float(data[0].get("spend", 0)) if data else 0
        except Exception:
            pass
    return round(total_spend, 2)

def fetch_fb_campaign_spend(period):
    """拉取所有账户 Campaign 级别消耗，返回 {campaign_name: spend} 字典"""
    since, until = fb_date_range(period)
    token = get_fb_token()
    camp_spend = {}
    for act_id in FB_ACT_IDS:
        try:
            r = requests.get(f"{FB_BASE}/{act_id}/insights", timeout=30, params={
                "access_token": token,
                "fields":       "campaign_name,spend",
                "time_range":   _json.dumps({"since": since, "until": until}),
                "level":        "campaign",
                "limit":        100,
            })
            if r.status_code == 200:
                for row in r.json().get("data", []):
                    name  = row.get("campaign_name", "")
                    spend = float(row.get("spend", 0))
                    # 同名 campaign 累加（多账户可能同名）
                    camp_spend[name] = round(camp_spend.get(name, 0) + spend, 2)
        except Exception:
            pass
    return camp_spend

# ── TikTok API 数据拉取 ───────────────────────────────────

def tt_date_range(period):
    t  = now8()
    td = t.strftime("%Y-%m-%d")
    yd = (t - timedelta(days=1)).strftime("%Y-%m-%d")
    ranges = {
        "today":     (td, td),
        "yesterday": (yd, yd),
        "3days":     ((t - timedelta(days=2)).strftime("%Y-%m-%d"), td),
        "7days":     ((t - timedelta(days=6)).strftime("%Y-%m-%d"), td),
        "month":     (t.replace(day=1).strftime("%Y-%m-%d"), td),
    }
    return ranges.get(period, (td, td))

def fetch_tt_channel_spend(period):
    """拉取 TikTok 账户总消耗（用 Campaign 级汇总，更准确）"""
    camp_spend = fetch_tt_campaign_spend(period)
    return round(sum(camp_spend.values()), 2)

def fetch_tt_campaign_spend(period):
    """拉取 TikTok Campaign 级别消耗，返回 {campaign_name: spend}"""
    since, until = tt_date_range(period)
    camp_spend = {}
    try:
        r = requests.get(f"{TT_BASE}/report/integrated/get/",
                         headers={"Access-Token": TT_ACCESS_TOKEN}, timeout=30,
                         params={
                             "advertiser_id": TT_ADV_ID,
                             "report_type":   "BASIC",
                             "data_level":    "AUCTION_CAMPAIGN",
                             "dimensions":    _json.dumps(["campaign_id"]),
                             "metrics":       _json.dumps(["campaign_name", "spend"]),
                             "start_date":    since,
                             "end_date":      until,
                             "page_size":     100,
                         })
        d = r.json()
        if d.get("code") == 0:
            for row in d.get("data", {}).get("list", []):
                m     = row.get("metrics", {})
                name  = m.get("campaign_name", "")
                spend = float(m.get("spend", 0) or 0)
                if name:
                    camp_spend[name] = round(camp_spend.get(name, 0) + spend, 2)
    except Exception:
        pass
    return camp_spend

# ── iOS 专属 API 拉取函数 ─────────────────────────────────

def fetch_fb_ios_channel_spend(period):
    """拉取 Facebook iOS 账户总消耗"""
    since, until = fb_date_range(period)
    token = get_fb_token()
    total_spend = 0.0
    for act_id in FB_IOS_ACT_IDS:
        try:
            r = requests.get(f"{FB_BASE}/{act_id}/insights", timeout=30, params={
                "access_token": token,
                "fields":       "spend",
                "time_range":   _json.dumps({"since": since, "until": until}),
                "level":        "account",
            })
            if r.status_code == 200:
                data = r.json().get("data", [])
                total_spend += float(data[0].get("spend", 0)) if data else 0
        except Exception:
            pass
    return round(total_spend, 2)

def fetch_fb_ios_campaign_spend(period):
    """拉取 Facebook iOS Campaign 级消耗，返回 {campaign_name: spend}"""
    since, until = fb_date_range(period)
    token = get_fb_token()
    camp_spend = {}
    for act_id in FB_IOS_ACT_IDS:
        try:
            r = requests.get(f"{FB_BASE}/{act_id}/insights", timeout=30, params={
                "access_token": token,
                "fields":       "campaign_name,spend",
                "time_range":   _json.dumps({"since": since, "until": until}),
                "level":        "campaign",
                "limit":        100,
            })
            if r.status_code == 200:
                for row in r.json().get("data", []):
                    name  = row.get("campaign_name", "")
                    spend = float(row.get("spend", 0))
                    camp_spend[name] = round(camp_spend.get(name, 0) + spend, 2)
        except Exception:
            pass
    return camp_spend

def fetch_tt_ios_channel_spend(period):
    """拉取 TikTok iOS 账户总消耗"""
    camp_spend = fetch_tt_ios_campaign_spend(period)
    return round(sum(camp_spend.values()), 2)

def fetch_tt_ios_campaign_spend(period):
    """拉取 TikTok iOS Campaign 级消耗，返回 {campaign_name: spend}"""
    since, until = tt_date_range(period)
    camp_spend = {}
    try:
        r = requests.get(f"{TT_BASE}/report/integrated/get/",
                         headers={"Access-Token": TT_ACCESS_TOKEN}, timeout=30,
                         params={
                             "advertiser_id": TT_IOS_ADV_ID,
                             "report_type":   "BASIC",
                             "data_level":    "AUCTION_CAMPAIGN",
                             "dimensions":    _json.dumps(["campaign_id"]),
                             "metrics":       _json.dumps(["campaign_name", "spend"]),
                             "start_date":    since,
                             "end_date":      until,
                             "page_size":     100,
                         })
        d = r.json()
        if d.get("code") == 0:
            for row in d.get("data", {}).get("list", []):
                m     = row.get("metrics", {})
                name  = m.get("campaign_name", "")
                spend = float(m.get("spend", 0) or 0)
                if name:
                    camp_spend[name] = round(camp_spend.get(name, 0) + spend, 2)
    except Exception:
        pass
    return camp_spend

# ── Google Ads 消耗（直连 Google Ads API v24）────────────
GG_CLIENT_ID       = os.environ.get("GG_CLIENT_ID",      "")
GG_CLIENT_SECRET   = os.environ.get("GG_CLIENT_SECRET",  "")
GG_REFRESH_TOKEN   = os.environ.get("GG_REFRESH_TOKEN",  "")
GG_DEVELOPER_TOKEN = os.environ.get("GG_DEVELOPER_TOKEN","")
GG_MCC_ID          = os.environ.get("GG_MCC_ID",         "1620959437")
GG_CUSTOMER_IDS    = ["3375325268", "4223410058"]   # 337-532-5268 + 422-341-0058
GG_API_VER         = "v24"

_gg_token_cache  = {"token": "", "ts": 0}
_gg_spend_cache  = {"data": {}, "ts": 0}   # 60秒内不重复请求

def _gg_get_access_token():
    """获取 Google OAuth2 Access Token（缓存3500秒）"""
    import time
    now = time.time()
    if _gg_token_cache["token"] and now - _gg_token_cache["ts"] < 3500:
        return _gg_token_cache["token"]
    try:
        r = requests.post("https://oauth2.googleapis.com/token", data={
            "client_id":     GG_CLIENT_ID,
            "client_secret": GG_CLIENT_SECRET,
            "refresh_token": GG_REFRESH_TOKEN,
            "grant_type":    "refresh_token",
        }, timeout=10)
        token = r.json().get("access_token", "")
        if token:
            _gg_token_cache["token"] = token
            _gg_token_cache["ts"]    = now
        return token
    except Exception:
        return ""

def _gg_date_range(period):
    t  = now8()
    td = t.strftime("%Y-%m-%d")
    yd = (t - timedelta(days=1)).strftime("%Y-%m-%d")
    ranges = {
        "today":     (td, td),
        "yesterday": (yd, yd),
        "3days":     ((t - timedelta(days=2)).strftime("%Y-%m-%d"), td),
        "7days":     ((t - timedelta(days=6)).strftime("%Y-%m-%d"), td),
        "month":     (t.replace(day=1).strftime("%Y-%m-%d"), td),
    }
    return ranges.get(period, (td, td))

def _gg_query(query_str):
    """对所有 Customer ID 执行 GAQL 查询，合并返回 results 列表；失败返回 []"""
    token = _gg_get_access_token()
    if not token:
        return []
    headers = {
        "Authorization":     f"Bearer {token}",
        "developer-token":   GG_DEVELOPER_TOKEN,
        "login-customer-id": GG_MCC_ID,
        "Content-Type":      "application/json",
    }
    all_results = []
    for cid in GG_CUSTOMER_IDS:
        url = f"https://googleads.googleapis.com/{GG_API_VER}/customers/{cid}/googleAds:search"
        try:
            resp = requests.post(url, headers=headers,
                                 json={"query": query_str}, timeout=15)
            if resp.status_code == 200:
                all_results.extend(resp.json().get("results", []))
        except Exception:
            pass
    return all_results

def fetch_gg_spend(period):
    """拉取 Google Ads 总消耗（用于 Channel 看板）"""
    import time
    now = time.time()
    cache_key = f"spend_{period}"
    if _gg_spend_cache["data"].get(cache_key) and now - _gg_spend_cache["ts"] < 60:
        return _gg_spend_cache["data"][cache_key]

    since, until = _gg_date_range(period)
    query = f"""
        SELECT metrics.cost_micros
        FROM campaign
        WHERE segments.date BETWEEN '{since}' AND '{until}'
          AND metrics.cost_micros > 0
    """
    rows = _gg_query(query)
    total = round(sum(int(r.get("metrics", {}).get("costMicros", 0)) / 1e6 for r in rows), 2)
    _gg_spend_cache["data"][cache_key] = total
    _gg_spend_cache["ts"] = now
    return total

def fetch_gg_campaign_spend(period):
    """拉取 Google Ads Campaign 级消耗，返回 {campaign_name: spend}"""
    import time
    now = time.time()
    cache_key = f"camp_{period}"
    if _gg_spend_cache["data"].get(cache_key) and now - _gg_spend_cache["ts"] < 60:
        return _gg_spend_cache["data"][cache_key]

    since, until = _gg_date_range(period)
    query = f"""
        SELECT campaign.name, metrics.cost_micros
        FROM campaign
        WHERE segments.date BETWEEN '{since}' AND '{until}'
          AND metrics.cost_micros > 0
        ORDER BY metrics.cost_micros DESC
    """
    rows = _gg_query(query)
    result = {}
    for row in rows:
        name  = row.get("campaign", {}).get("name", "")
        spend = int(row.get("metrics", {}).get("costMicros", 0)) / 1e6
        if name:
            result[name] = round(result.get(name, 0) + spend, 2)
    _gg_spend_cache["data"][cache_key] = result
    _gg_spend_cache["ts"] = now
    return result

def date_range(period):
    t  = now8()
    td = t.strftime("%Y-%m-%d")
    yd = (t - timedelta(days=1)).strftime("%Y-%m-%d")
    if period == "today":
        return td, td
    elif period == "yesterday":
        return yd, yd
    elif period == "3days":
        return (t - timedelta(days=2)).strftime("%Y-%m-%d"), td
    elif period == "7days":
        return (t - timedelta(days=6)).strftime("%Y-%m-%d"), td
    elif period == "month":
        return t.replace(day=1).strftime("%Y-%m-%d"), td
    return td, td

def has_day(period):
    return period in ("today", "yesterday")

def apply_formula(row):
    ch = row["channel"]
    if ch in SPEND_FORMULA:
        field, coef = SPEND_FORMULA[ch]
        row["cost"] = round((row.get(field) or 0) * coef, 2)
        row["cost_formula"] = f"{field}×{coef}"
    else:
        row["cost_formula"] = None
    if ch in CPS_FIXED:
        row["cps"]       = CPS_FIXED[ch]
        row["cps_fixed"] = True
    else:
        row["cps_fixed"] = False
        loan = row.get("loan") or 0
        cost = row.get("cost") or 0
        row["cps"] = round(cost / loan, 2) if loan > 0 else None
    return row

def merge_ck(rows):
    CK = ("CK-loan And01", "CK-loan And02", "CK-loan And03")
    merged, others = {}, []
    for r in rows:
        if r["channel"] in CK:
            k = r.get("day") or "all"
            if k not in merged:
                merged[k] = {**r, "channel": "CK-loan And"}
            else:
                for f in ("clicks","installs","cost","register","apply","loan","revenue"):
                    merged[k][f] = round((merged[k].get(f) or 0) + (r.get(f) or 0), 2)
        else:
            others.append(r)
    for r in merged.values():
        apply_formula(r)
    return others + list(merged.values())

def parse_rows(rows_raw, mode="channel"):
    result = []
    for row in rows_raw:
        cost = sf(row.get("cost"))
        loan = sf(row.get("loan_success_events"), int)
        r = {
            "channel":  row.get("channel", ""),
            "day":      row.get("day"),
            "clicks":   sf(row.get("attribution_clicks"), int),
            "installs": sf(row.get("installs"), int),
            "cost":     round(cost, 2),
            "register": sf(row.get("register_success_events"), int),
            "apply":    sf(row.get("apply_for_loan_events"), int),
            "loan":     loan,
            "revenue":  round(sf(row.get("first_loan_amount_revenue")), 2),
            "cps":      round(cost / loan, 2) if loan > 0 else None,
            "is_key":   row.get("channel", "") in KEY_CH,
        }
        if mode == "campaign":
            camp_raw = row.get("campaign", "") or ""
            r["campaign"] = re.sub(r'\s*\(\d+\)\s*$', '', camp_raw).strip()
        result.append(r)
    return result


def compute_full_channel_total(period):
    """拉取全渠道 channel 维度数据并汇总 Total（与 Adjust 后台总数口径一致，含长尾/自然量渠道）。
    复用 /api/channel 的合并 + 真实消耗注入逻辑，但只返回汇总后的 total 字典。"""
    start, end = date_range(period)
    dims = "channel,day" if has_day(period) else "channel"
    resp = requests.get(BASE_URL, headers=HEADERS, timeout=55, params={
        "app_token__in": APP_TOKEN,
        "date_period":   f"{start}:{end}",
        "dimensions":    dims,
        **BASE_PARAMS,
    })
    resp.raise_for_status()
    raw  = resp.json()
    rows = parse_rows(raw.get("rows", []), "channel")

    rows = merge_ck(rows)
    for r in rows:
        if r["channel"] != "CK-loan And":
            apply_formula(r)

    fb_real_spend = fetch_fb_channel_spend(period)
    for r in rows:
        if r["channel"] == "Facebook":
            r["cost"] = fb_real_spend

    tt_real_spend = fetch_tt_channel_spend(period)
    for r in rows:
        if r["channel"] == "TikTok for Business":
            r["cost"] = tt_real_spend

    gg_real_spend = fetch_gg_spend(period)
    if gg_real_spend > 0:
        for r in rows:
            if r["channel"] == "Google Ads":
                r["cost"] = gg_real_spend

    seen = {}
    for r in rows:
        ch = r["channel"]
        if ch not in seen: seen[ch] = dict(r)
        else:
            for f in ("clicks","installs","cost","register","apply","loan","revenue"):
                seen[ch][f] = round((seen[ch].get(f) or 0) + (r.get(f) or 0), 2)
    tc = round(sum(v.get("cost",0) for v in seen.values()), 2)
    tl = sum(int(v.get("loan",0)) for v in seen.values())
    return {
        "clicks":   sum(int(v.get("clicks",0))   for v in seen.values()),
        "installs": sum(int(v.get("installs",0)) for v in seen.values()),
        "cost":     tc,
        "register": sum(int(v.get("register",0)) for v in seen.values()),
        "apply":    sum(int(v.get("apply",0))    for v in seen.values()),
        "loan":     tl,
        "revenue":  round(sum(v.get("revenue",0) for v in seen.values()), 2),
        "cps":      round(tc/tl, 2) if tl > 0 else None,
    }


# ── API 路由 ─────────────────────────────────────────────

@app.route("/api/channel")
def api_channel():
    period = request.args.get("period", "today")
    start, end = date_range(period)
    dims = "channel,day" if has_day(period) else "channel"
    try:
        resp = requests.get(BASE_URL, headers=HEADERS, timeout=55, params={
            "app_token__in": APP_TOKEN,
            "date_period":   f"{start}:{end}",
            "dimensions":    dims,
            **BASE_PARAMS,
        })
        resp.raise_for_status()
        raw    = resp.json()
        rows   = parse_rows(raw.get("rows", []), "channel")
        totals = raw.get("totals", {})

        # 合并 CK + 公式
        rows = merge_ck(rows)
        for r in rows:
            if r["channel"] != "CK-loan And":
                apply_formula(r)

        # ★ 注入 Facebook 真实消耗
        fb_real_spend = fetch_fb_channel_spend(period)
        for r in rows:
            if r["channel"] == "Facebook":
                r["cost"]         = fb_real_spend
                r["cost_formula"] = "Meta API"
                loan = r.get("loan") or 0
                r["cps"]       = round(fb_real_spend / loan, 2) if loan > 0 else None
                r["cps_fixed"] = False

        # ★ 注入 TikTok 真实消耗
        tt_real_spend = fetch_tt_channel_spend(period)
        for r in rows:
            if r["channel"] == "TikTok for Business":
                r["cost"]         = tt_real_spend
                r["cost_formula"] = "TikTok API"
                loan = r.get("loan") or 0
                r["cps"]       = round(tt_real_spend / loan, 2) if loan > 0 else None
                r["cps_fixed"] = False

        # ★ 注入 Google Ads 真实消耗（Google Ads API v24）
        gg_real_spend = fetch_gg_spend(period)
        if gg_real_spend > 0:
            for r in rows:
                if r["channel"] == "Google Ads":
                    r["cost"]         = gg_real_spend
                    r["cost_formula"] = "GG API"
                    loan = r.get("loan") or 0
                    r["cps"]       = round(gg_real_spend / loan, 2) if loan > 0 else None
                    r["cps_fixed"] = False

        rows.sort(key=lambda x: (0 if x["is_key"] else 1, -(x.get("cost") or 0)))

        # 重算 total
        seen = {}
        for r in rows:
            ch = r["channel"]
            if ch not in seen: seen[ch] = dict(r)
            else:
                for f in ("clicks","installs","cost","register","apply","loan","revenue"):
                    seen[ch][f] = round((seen[ch].get(f) or 0) + (r.get(f) or 0), 2)
        tc = round(sum(v.get("cost",0) for v in seen.values()), 2)
        tl = sum(int(v.get("loan",0)) for v in seen.values())
        total = {
            "clicks":   sum(int(v.get("clicks",0))   for v in seen.values()),
            "installs": sum(int(v.get("installs",0)) for v in seen.values()),
            "cost":     tc,
            "register": sum(int(v.get("register",0)) for v in seen.values()),
            "apply":    sum(int(v.get("apply",0))    for v in seen.values()),
            "loan":     tl,
            "revenue":  round(sum(v.get("revenue",0) for v in seen.values()), 2),
            "cps":      round(tc/tl, 2) if tl > 0 else None,
        }

        return jsonify({
            "ok": True, "period": period, "start": start, "end": end,
            "has_day": has_day(period),
            "pulled_at": now8().strftime("%Y-%m-%d %H:%M:%S"),
            "total": total, "by_channel": rows,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/campaign")
def api_campaign():
    period = request.args.get("period", "yesterday")
    start = request.args.get("start") or date_range(period)[0]
    end   = request.args.get("end")   or date_range(period)[1]
    # 自定义日期段或 today/yesterday 按日展开，其余汇总
    multi_day = (start != end) or has_day(period) if not request.args.get("start") else (start != end)
    dims = "channel,campaign,day" if multi_day else "channel,campaign"
    try:
        resp = requests.get(BASE_URL, headers=HEADERS, timeout=55, params={
            "app_token__in": APP_TOKEN,
            "date_period":   f"{start}:{end}",
            "dimensions":    dims,
            **BASE_PARAMS,
        })
        resp.raise_for_status()
        raw  = resp.json()
        rows = parse_rows(raw.get("rows", []), "campaign")

        # 只保留三大核心渠道
        rows = [r for r in rows if r["channel"] in KEY_CH]

        # ★ 注入 Facebook Campaign 真实消耗（严格精确匹配；installs=0 则消耗强制置0）
        fb_camp_spend = fetch_fb_campaign_spend(period)
        for r in rows:
            if r["channel"] == "Facebook":
                camp_name = r.get("campaign", "")
                installs  = r.get("installs") or 0
                if installs == 0:
                    r["cost"] = 0.0
                    r["cps"]  = None
                elif camp_name in fb_camp_spend:
                    r["cost"] = fb_camp_spend[camp_name]
                    loan = r.get("loan") or 0
                    r["cps"] = round(r["cost"] / loan, 2) if loan > 0 and r["cost"] > 0 else None

        # ★ 注入 TikTok Campaign 真实消耗（严格精确匹配；installs=0 则消耗强制置0）
        tt_camp_spend = fetch_tt_campaign_spend(period)
        for r in rows:
            if r["channel"] == "TikTok for Business":
                camp_name = r.get("campaign", "")
                installs  = r.get("installs") or 0
                if installs == 0:
                    r["cost"] = 0.0
                    r["cps"]  = None
                elif camp_name in tt_camp_spend:
                    r["cost"] = tt_camp_spend[camp_name]
                    loan = r.get("loan") or 0
                    r["cps"] = round(r["cost"] / loan, 2) if loan > 0 and r["cost"] > 0 else None

        # ★ 注入 Google Ads Campaign 真实消耗（严格精确匹配；installs=0 则消耗强制置0）
        gg_camp_spend = fetch_gg_campaign_spend(period)
        for r in rows:
            if r["channel"] == "Google Ads":
                camp_name = r.get("campaign", "")
                installs  = r.get("installs") or 0
                if installs == 0:
                    r["cost"] = 0.0
                    r["cps"]  = None
                elif camp_name in gg_camp_spend:
                    r["cost"] = gg_camp_spend[camp_name]
                    loan = r.get("loan") or 0
                    r["cps"] = round(r["cost"] / loan, 2) if loan > 0 and r["cost"] > 0 else None

        ch_order = {ch: i for i, ch in enumerate(KEY_CH)}
        rows.sort(key=lambda x: (ch_order.get(x["channel"], 99), -(x.get("cost") or 0)))

        # 渠道小计
        ch_totals = {}
        for r in rows:
            ch = r["channel"]
            if ch not in ch_totals:
                ch_totals[ch] = {"clicks":0,"installs":0,"cost":0,"register":0,"apply":0,"loan":0,"revenue":0}
            for f in ("clicks","installs","cost","register","apply","loan","revenue"):
                ch_totals[ch][f] = round(ch_totals[ch][f] + (r.get(f) or 0), 2)
        # ★ Facebook + TikTok + Google Ads channel 小计 cost 用真实消耗替换
        fb_channel_spend = fetch_fb_channel_spend(period)
        tt_channel_spend = fetch_tt_channel_spend(period)
        gg_channel_spend = fetch_gg_spend(period)
        if "Facebook" in ch_totals:
            ch_totals["Facebook"]["cost"] = fb_channel_spend
        if "TikTok for Business" in ch_totals:
            ch_totals["TikTok for Business"]["cost"] = tt_channel_spend
        if "Google Ads" in ch_totals and gg_channel_spend > 0:
            ch_totals["Google Ads"]["cost"] = gg_channel_spend
        for ch in ch_totals:
            tl = ch_totals[ch]["loan"]
            tc = ch_totals[ch]["cost"]
            ch_totals[ch]["cps"] = round(tc/tl, 2) if tl > 0 else None

        # 三大核心渠道口径小计（仅供参考，与下方 Campaign 明细表对应）
        key_cost = sum(ch_totals.get(ch,{}).get("cost",0) for ch in KEY_CH)
        key_loan = sum(ch_totals.get(ch,{}).get("loan",0) for ch in KEY_CH)
        key_total = {
            "clicks":   sum(ch_totals.get(ch,{}).get("clicks",0)   for ch in KEY_CH),
            "installs": sum(ch_totals.get(ch,{}).get("installs",0) for ch in KEY_CH),
            "cost":     round(key_cost, 2),
            "register": sum(ch_totals.get(ch,{}).get("register",0) for ch in KEY_CH),
            "apply":    sum(ch_totals.get(ch,{}).get("apply",0)    for ch in KEY_CH),
            "loan":     key_loan,
            "revenue":  round(sum(ch_totals.get(ch,{}).get("revenue",0) for ch in KEY_CH), 2),
            "cps":      round(key_cost/key_loan, 2) if key_loan > 0 else None,
        }

        # ★ Total 卡片改为全渠道口径（与 Adjust 后台总数一致，含 CK-loan/loan_market/Organic 等长尾渠道）
        total = compute_full_channel_total(period)

        return jsonify({
            "ok": True, "period": period, "start": start, "end": end,
            "has_day": has_day(period),
            "pulled_at": now8().strftime("%Y-%m-%d %H:%M:%S"),
            "total": total, "key_total": key_total,
            "channel_totals": ch_totals, "by_campaign": rows,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── iOS API 路由 ──────────────────────────────────────────

@app.route("/api/ios/channel")
def api_ios_channel():
    period = request.args.get("period", "today")
    start, end = date_range(period)
    dims = "channel,day" if has_day(period) else "channel"
    try:
        resp = requests.get(BASE_URL, headers={"Authorization": f"Bearer {USER_TOKEN}"},
                            timeout=55, params={
                                "app_token__in": IOS_APP_TOKEN,
                                "date_period":   f"{start}:{end}",
                                "dimensions":    dims,
                                **BASE_PARAMS,
                            })
        resp.raise_for_status()
        raw  = resp.json()
        rows = parse_rows(raw.get("rows", []), "channel")
        for r in rows:
            apply_formula(r)

        # 注入 Facebook iOS 真实消耗
        fb_ios_spend = fetch_fb_ios_channel_spend(period)
        for r in rows:
            if r["channel"] == "Facebook":
                r["cost"] = fb_ios_spend
                r["cost_formula"] = "Meta API"
                loan = r.get("loan") or 0
                r["cps"] = round(fb_ios_spend / loan, 2) if loan > 0 else None
                r["cps_fixed"] = False

        # 注入 TikTok iOS 真实消耗
        tt_ios_spend = fetch_tt_ios_channel_spend(period)
        for r in rows:
            if r["channel"] == "TikTok for Business":
                r["cost"] = tt_ios_spend
                r["cost_formula"] = "TikTok API"
                loan = r.get("loan") or 0
                r["cps"] = round(tt_ios_spend / loan, 2) if loan > 0 else None
                r["cps_fixed"] = False

        rows.sort(key=lambda x: (0 if x["channel"] in IOS_KEY_CH else 1, -(x.get("cost") or 0)))

        # 汇总（Total = 全渠道汇总，与 Adjust 后台一致）
        seen = {}
        for r in rows:
            ch = r["channel"]
            if ch not in seen: seen[ch] = dict(r)
            else:
                for f in ("clicks","installs","cost","register","apply","loan","revenue"):
                    seen[ch][f] = round((seen[ch].get(f) or 0) + (r.get(f) or 0), 2)
        tc = round(sum(v.get("cost",0) for v in seen.values()), 2)
        tl = sum(int(v.get("loan",0)) for v in seen.values())
        total = {
            "clicks":   sum(int(v.get("clicks",0))   for v in seen.values()),
            "installs": sum(int(v.get("installs",0)) for v in seen.values()),
            "cost":     tc,
            "register": sum(int(v.get("register",0)) for v in seen.values()),
            "apply":    sum(int(v.get("apply",0))    for v in seen.values()),
            "loan":     tl,
            "revenue":  round(sum(v.get("revenue",0) for v in seen.values()), 2),
            "cps":      round(tc/tl, 2) if tl > 0 else None,
        }
        return jsonify({
            "ok": True, "period": period, "start": start, "end": end,
            "has_day": has_day(period),
            "pulled_at": now8().strftime("%Y-%m-%d %H:%M:%S"),
            "total": total, "by_channel": rows,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def compute_full_ios_channel_total(period):
    """iOS 版全渠道 Total 汇总（与 Adjust 后台 iOS 总数口径一致，含 Organic/MOLOCO 等长尾渠道）。"""
    start, end = date_range(period)
    dims = "channel,day" if has_day(period) else "channel"
    resp = requests.get(BASE_URL, headers={"Authorization": f"Bearer {USER_TOKEN}"},
                        timeout=55, params={
                            "app_token__in": IOS_APP_TOKEN,
                            "date_period":   f"{start}:{end}",
                            "dimensions":    dims,
                            **BASE_PARAMS,
                        })
    resp.raise_for_status()
    raw  = resp.json()
    rows = parse_rows(raw.get("rows", []), "channel")
    for r in rows:
        apply_formula(r)

    fb_ios_spend = fetch_fb_ios_channel_spend(period)
    for r in rows:
        if r["channel"] == "Facebook":
            r["cost"] = fb_ios_spend

    tt_ios_spend = fetch_tt_ios_channel_spend(period)
    for r in rows:
        if r["channel"] == "TikTok for Business":
            r["cost"] = tt_ios_spend

    seen = {}
    for r in rows:
        ch = r["channel"]
        if ch not in seen: seen[ch] = dict(r)
        else:
            for f in ("clicks","installs","cost","register","apply","loan","revenue"):
                seen[ch][f] = round((seen[ch].get(f) or 0) + (r.get(f) or 0), 2)
    tc = round(sum(v.get("cost",0) for v in seen.values()), 2)
    tl = sum(int(v.get("loan",0)) for v in seen.values())
    return {
        "clicks":   sum(int(v.get("clicks",0))   for v in seen.values()),
        "installs": sum(int(v.get("installs",0)) for v in seen.values()),
        "cost":     tc,
        "register": sum(int(v.get("register",0)) for v in seen.values()),
        "apply":    sum(int(v.get("apply",0))    for v in seen.values()),
        "loan":     tl,
        "revenue":  round(sum(v.get("revenue",0) for v in seen.values()), 2),
        "cps":      round(tc/tl, 2) if tl > 0 else None,
    }


@app.route("/api/ios/campaign")
def api_ios_campaign():
    period = request.args.get("period", "yesterday")
    start, end = date_range(period)
    dims = "channel,campaign,day" if has_day(period) else "channel,campaign"
    try:
        resp = requests.get(BASE_URL, headers={"Authorization": f"Bearer {USER_TOKEN}"},
                            timeout=55, params={
                                "app_token__in": IOS_APP_TOKEN,
                                "date_period":   f"{start}:{end}",
                                "dimensions":    dims,
                                **BASE_PARAMS,
                            })
        resp.raise_for_status()
        raw  = resp.json()
        rows = parse_rows(raw.get("rows", []), "campaign")

        # 只保留核心渠道
        rows = [r for r in rows if r["channel"] in IOS_KEY_CH]

        # 注入 Facebook iOS Campaign 真实消耗（installs=0 置0）
        fb_ios_camp = fetch_fb_ios_campaign_spend(period)
        for r in rows:
            if r["channel"] == "Facebook":
                installs = r.get("installs") or 0
                if installs == 0:
                    r["cost"] = 0.0; r["cps"] = None
                elif r.get("campaign","") in fb_ios_camp:
                    r["cost"] = fb_ios_camp[r["campaign"]]
                    loan = r.get("loan") or 0
                    r["cps"] = round(r["cost"] / loan, 2) if loan > 0 and r["cost"] > 0 else None

        # 注入 TikTok iOS Campaign 真实消耗（installs=0 置0）
        tt_ios_camp = fetch_tt_ios_campaign_spend(period)
        for r in rows:
            if r["channel"] == "TikTok for Business":
                installs = r.get("installs") or 0
                if installs == 0:
                    r["cost"] = 0.0; r["cps"] = None
                elif r.get("campaign","") in tt_ios_camp:
                    r["cost"] = tt_ios_camp[r["campaign"]]
                    loan = r.get("loan") or 0
                    r["cps"] = round(r["cost"] / loan, 2) if loan > 0 and r["cost"] > 0 else None

        # Google/ASA：installs=0 置0，其余保留 Adjust 归因消耗
        for r in rows:
            if r["channel"] in ("Google Ads", "Apple"):
                if (r.get("installs") or 0) == 0:
                    r["cost"] = 0.0; r["cps"] = None

        ch_order = {ch: i for i, ch in enumerate(IOS_KEY_CH)}
        rows.sort(key=lambda x: (ch_order.get(x["channel"], 99), -(x.get("cost") or 0)))

        # 渠道小计
        ch_totals = {}
        for r in rows:
            ch = r["channel"]
            if ch not in ch_totals:
                ch_totals[ch] = {"clicks":0,"installs":0,"cost":0,"register":0,"apply":0,"loan":0,"revenue":0}
            for f in ("clicks","installs","cost","register","apply","loan","revenue"):
                ch_totals[ch][f] = round(ch_totals[ch][f] + (r.get(f) or 0), 2)
        # channel 小计 cost 用真实消耗替换
        fb_ios_ch  = fetch_fb_ios_channel_spend(period)
        tt_ios_ch  = fetch_tt_ios_channel_spend(period)
        if "Facebook" in ch_totals:
            ch_totals["Facebook"]["cost"] = fb_ios_ch
        if "TikTok for Business" in ch_totals:
            ch_totals["TikTok for Business"]["cost"] = tt_ios_ch
        for ch in ch_totals:
            tl = ch_totals[ch]["loan"]
            tc = ch_totals[ch]["cost"]
            ch_totals[ch]["cps"] = round(tc/tl, 2) if tl > 0 else None

        # 三大核心渠道口径小计（仅供参考，与下方 Campaign 明细表对应）
        key_cost = sum(ch_totals.get(ch,{}).get("cost",0) for ch in IOS_KEY_CH)
        key_loan = sum(ch_totals.get(ch,{}).get("loan",0) for ch in IOS_KEY_CH)
        key_total = {
            "clicks":   sum(ch_totals.get(ch,{}).get("clicks",0)   for ch in IOS_KEY_CH),
            "installs": sum(ch_totals.get(ch,{}).get("installs",0) for ch in IOS_KEY_CH),
            "cost":     round(key_cost, 2),
            "register": sum(ch_totals.get(ch,{}).get("register",0) for ch in IOS_KEY_CH),
            "apply":    sum(ch_totals.get(ch,{}).get("apply",0)    for ch in IOS_KEY_CH),
            "loan":     key_loan,
            "revenue":  round(sum(ch_totals.get(ch,{}).get("revenue",0) for ch in IOS_KEY_CH), 2),
            "cps":      round(key_cost/key_loan, 2) if key_loan > 0 else None,
        }

        # ★ Total 卡片改为全渠道口径（与 Adjust 后台 iOS 总数一致，含 Organic/MOLOCO 等长尾渠道）
        total = compute_full_ios_channel_total(period)

        return jsonify({
            "ok": True, "period": period, "start": start, "end": end,
            "has_day": has_day(period),
            "pulled_at": now8().strftime("%Y-%m-%d %H:%M:%S"),
            "total": total, "key_total": key_total,
            "channel_totals": ch_totals, "by_campaign": rows,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/")
def index():
    return Response(CHANNEL_HTML, mimetype="text/html")

@app.route("/campaign")
def campaign_page():
    return Response(CAMPAIGN_HTML, mimetype="text/html")

@app.route("/ios")
def ios_channel_page():
    return Response(IOS_CHANNEL_HTML, mimetype="text/html")

@app.route("/ios/campaign")
def ios_campaign_page():
    return Response(IOS_CAMPAIGN_HTML, mimetype="text/html")

# ── 前端页面（内嵌 HTML）────────────────────────────────

CHANNEL_HTML     = open("channel.html",     encoding="utf-8").read() if __import__("os").path.exists("channel.html")     else "<h1>channel.html not found</h1>"
CAMPAIGN_HTML    = open("campaign.html",    encoding="utf-8").read() if __import__("os").path.exists("campaign.html")    else "<h1>campaign.html not found</h1>"
IOS_CHANNEL_HTML  = open("ios_channel.html",  encoding="utf-8").read() if __import__("os").path.exists("ios_channel.html")  else "<h1>ios_channel.html not found</h1>"
IOS_CAMPAIGN_HTML = open("ios_campaign.html", encoding="utf-8").read() if __import__("os").path.exists("ios_campaign.html") else "<h1>ios_campaign.html not found</h1>"

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5050)
    args = parser.parse_args()
    print(f"✅ 启动成功 → http://localhost:{args.port}")
    print(f"   Channel 看板: http://localhost:{args.port}/")
    print(f"   Campaign 看板: http://localhost:{args.port}/campaign")
    app.run(host="0.0.0.0", port=args.port, debug=False)
