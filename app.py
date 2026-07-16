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
FB_ACT_IDS    = ["act_2043458276522117", "act_1338744840870824", "act_554870820824463", "act_1763443588125609", "act_4425161567801548", "act_3511882642320376", "act_1654205562363513"]
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


def fetch_fb_ios_adgroup_spend(period):
    """拉取 Facebook iOS 账户 Adset 级消耗，返回 {campaign_name: {adset_name: spend}}（只保留 spend>0）"""
    import time
    now = time.time()
    ck = _adgroup_cache_key("fb_ios", period)
    if _gg_spend_cache["data"].get(ck) and now - _gg_spend_cache["ts"] < 60:
        return _gg_spend_cache["data"][ck]
    since, until = fb_date_range(period)
    token = get_fb_token()
    result = {}
    for act_id in FB_IOS_ACT_IDS:
        try:
            r = requests.get(f"{FB_BASE}/{act_id}/insights", timeout=30, params={
                "access_token": token,
                "fields":       "adset_name,campaign_name,spend",
                "time_range":   _json.dumps({"since": since, "until": until}),
                "level":        "adset",
                "limit":        200,
            })
            if r.status_code == 200:
                for row in r.json().get("data", []):
                    cname = row.get("campaign_name", "")
                    aname = row.get("adset_name", "")
                    spend = float(row.get("spend", 0))
                    if spend <= 0:          # ★ 只保留有消耗的 adgroup
                        continue
                    if cname not in result:
                        result[cname] = {}
                    result[cname][aname] = round(result[cname].get(aname, 0) + spend, 2)
        except Exception:
            pass
    _gg_spend_cache["data"][ck] = result
    _gg_spend_cache["ts"] = now
    return result


def fetch_tt_ios_adgroup_spend(period):
    """拉取 TikTok iOS Adgroup 级消耗，返回 {campaign_name: {adgroup_name: spend}}（只保留 spend>0）"""
    import time
    now = time.time()
    ck = _adgroup_cache_key("tt_ios", period)
    if _gg_spend_cache["data"].get(ck) and now - _gg_spend_cache["ts"] < 60:
        return _gg_spend_cache["data"][ck]
    since, until = tt_date_range(period)
    result = {}
    try:
        r = requests.get(f"{TT_BASE}/report/integrated/get/",
                         headers={"Access-Token": TT_ACCESS_TOKEN}, timeout=30,
                         params={
                             "advertiser_id": TT_IOS_ADV_ID,
                             "report_type":   "BASIC",
                             "data_level":    "AUCTION_ADGROUP",
                             "dimensions":    _json.dumps(["adgroup_id"]),
                             "metrics":       _json.dumps(["adgroup_name", "campaign_name", "spend"]),
                             "start_date":    since,
                             "end_date":      until,
                             "page_size":     200,
                         })
        d = r.json()
        if d.get("code") == 0:
            for row in d.get("data", {}).get("list", []):
                m     = row.get("metrics", {})
                cname = m.get("campaign_name", "")
                aname = m.get("adgroup_name", "")
                spend = float(m.get("spend", 0) or 0)
                if cname and aname and spend > 0:  # ★ 只保留有消耗的 adgroup
                    if cname not in result:
                        result[cname] = {}
                    result[cname][aname] = round(result[cname].get(aname, 0) + spend, 2)
    except Exception:
        pass
    _gg_spend_cache["data"][ck] = result
    _gg_spend_cache["ts"] = now
    return result

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

# ── Adgroup 级别消耗拉取（三大平台）────────────────────
def _adgroup_cache_key(platform, period):
    return f"adgroup_{platform}_{period}"

def fetch_fb_adgroup_spend(period):
    """拉取所有FB账户 Adset 级别消耗，返回 {campaign_name: {adset_name: spend}}"""
    import time
    now = time.time()
    ck = _adgroup_cache_key("fb", period)
    if _gg_spend_cache["data"].get(ck) and now - _gg_spend_cache["ts"] < 60:
        return _gg_spend_cache["data"][ck]
    since, until = fb_date_range(period)
    token = get_fb_token()
    result = {}
    for act_id in FB_ACT_IDS:
        try:
            r = requests.get(f"{FB_BASE}/{act_id}/insights", timeout=30, params={
                "access_token": token,
                "fields":       "adset_name,campaign_name,spend",
                "time_range":   _json.dumps({"since": since, "until": until}),
                "level":        "adset",
                "limit":        200,
            })
            if r.status_code == 200:
                for row in r.json().get("data", []):
                    cname = row.get("campaign_name", "")
                    aname = row.get("adset_name", "")
                    spend = float(row.get("spend", 0))
                    if spend <= 0:          # ★ 只保留有消耗的 adgroup
                        continue
                    if cname not in result:
                        result[cname] = {}
                    result[cname][aname] = round(result[cname].get(aname, 0) + spend, 2)
        except Exception:
            pass
    _gg_spend_cache["data"][ck] = result
    _gg_spend_cache["ts"] = now
    return result

def fetch_tt_adgroup_spend(period):
    """拉取 TikTok Adgroup 级别消耗，返回 {campaign_name: {adgroup_name: spend}}"""
    import time
    now = time.time()
    ck = _adgroup_cache_key("tt", period)
    if _gg_spend_cache["data"].get(ck) and now - _gg_spend_cache["ts"] < 60:
        return _gg_spend_cache["data"][ck]
    since, until = tt_date_range(period)
    result = {}
    try:
        r = requests.get(f"{TT_BASE}/report/integrated/get/",
                         headers={"Access-Token": TT_ACCESS_TOKEN}, timeout=30,
                         params={
                             "advertiser_id": TT_ADV_ID,
                             "report_type":   "BASIC",
                             "data_level":    "AUCTION_ADGROUP",
                             "dimensions":    _json.dumps(["adgroup_id"]),
                             "metrics":       _json.dumps(["adgroup_name", "campaign_name", "spend"]),
                             "start_date":    since,
                             "end_date":      until,
                             "page_size":     200,
                         })
        d = r.json()
        if d.get("code") == 0:
            for row in d.get("data", {}).get("list", []):
                m     = row.get("metrics", {})
                cname = m.get("campaign_name", "")
                aname = m.get("adgroup_name", "")
                spend = float(m.get("spend", 0) or 0)
                if cname and aname and spend > 0:  # ★ 只保留有消耗的 adgroup
                    if cname not in result:
                        result[cname] = {}
                    result[cname][aname] = round(result[cname].get(aname, 0) + spend, 2)
    except Exception:
        pass
    _gg_spend_cache["data"][ck] = result
    _gg_spend_cache["ts"] = now
    return result

def fetch_gg_adgroup_spend(period):
    """拉取 Google Ads Adgroup 级别消耗，返回 {campaign_name: {adgroup_name: spend}}"""
    import time
    now = time.time()
    ck = _adgroup_cache_key("gg", period)
    if _gg_spend_cache["data"].get(ck) and now - _gg_spend_cache["ts"] < 60:
        return _gg_spend_cache["data"][ck]
    since, until = _gg_date_range(period)
    query = f"""
        SELECT campaign.name, ad_group.name, metrics.cost_micros
        FROM ad_group
        WHERE segments.date BETWEEN '{since}' AND '{until}'
          AND metrics.cost_micros > 0
    """
    rows = _gg_query(query)
    result = {}
    for row in rows:
        cname = row.get("campaign", {}).get("name", "")
        aname = row.get("adGroup", {}).get("name", "") if "adGroup" in row else row.get("ad_group", {}).get("name", "")
        spend = int(row.get("metrics", {}).get("costMicros", 0)) / 1e6
        if cname and aname and spend > 0:  # ★ 只保留有消耗的 adgroup
            if cname not in result:
                result[cname] = {}
            result[cname][aname] = round(result[cname].get(aname, 0) + spend, 2)
    _gg_spend_cache["data"][ck] = result
    _gg_spend_cache["ts"] = now
    return result

def fetch_adjust_adgroup(period, channels=None, app_token=None):
    """拉取 Adjust adgroup 级转化数据。
    返回 {channel: {campaign_network: {adgroup_network: {installs,register,apply,loan,revenue}}}}"""
    import time
    now = time.time()
    ck = _adgroup_cache_key("adj_" + (app_token or APP_TOKEN), period)
    if _gg_spend_cache["data"].get(ck) and now - _gg_spend_cache["ts"] < 60:
        return _gg_spend_cache["data"][ck]
    start, end = date_range(period)
    result = {}
    try:
        resp = requests.get(BASE_URL, headers=HEADERS, timeout=55, params={
            "app_token__in": app_token or APP_TOKEN,
            "date_period":   f"{start}:{end}",
            "dimensions":    "channel,campaign_network,adgroup_network",
            **BASE_PARAMS,
        })
        resp.raise_for_status()
        for row in resp.json().get("rows", []):
            ch    = row.get("channel", "")
            camp  = row.get("campaign_network", "")
            ag    = row.get("adgroup_network", "")
            if not camp or not ag:
                continue
            loan = sf(row.get("loan_success_events"), int)
            conv = {
                "installs": sf(row.get("installs"), int),
                "register": sf(row.get("register_success_events"), int),
                "apply":    sf(row.get("apply_for_loan_events"), int),
                "loan":     loan,
                "revenue":  round(sf(row.get("first_loan_amount_revenue")), 2),
            }
            result.setdefault(ch, {}).setdefault(camp, {})
            # 同一 adgroup 可能多行，累加
            prev = result[ch][camp].get(ag)
            if prev:
                for k in conv:
                    prev[k] = round(prev[k] + conv[k], 2) if k == "revenue" else prev[k] + conv[k]
            else:
                result[ch][camp][ag] = conv
    except Exception:
        pass
    _gg_spend_cache["data"][ck] = result
    _gg_spend_cache["ts"] = now
    return result


def _norm_name(s):
    return (s or "").replace(" ", "").lower()


def _match_adgroup_map(adgroup_map, adjust_campaigns, adj_conv=None):
    """将平台 adgroup 消耗 {plat_camp: {adgroup_name: spend}} 映射到 Adjust campaign，
    并合并 Adjust 侧转化数据 adj_conv={campaign_network:{adgroup_network:{conv}}}。
    输出 {matched_campaign: [{name,spend,installs,register,apply,loan,revenue,cps}]}"""
    adj_conv = adj_conv or {}
    result = {}
    for plat_camp, adgroups in adgroup_map.items():
        matched = None
        for ac in adjust_campaigns:
            if plat_camp == ac:
                matched = ac; break
        if not matched:
            pn = _norm_name(plat_camp)
            for ac in adjust_campaigns:
                if pn == _norm_name(ac):
                    matched = ac; break
        if not matched:
            for ac in adjust_campaigns:
                if plat_camp.lower() in ac.lower() or ac.lower() in plat_camp.lower():
                    matched = ac; break
        if not matched:
            continue
        # 该 campaign 对应的 Adjust 转化数据（按 campaign_network 名匹配）
        conv_camp = adj_conv.get(plat_camp)
        if conv_camp is None:
            for cn in adj_conv:
                if _norm_name(cn) == _norm_name(plat_camp):
                    conv_camp = adj_conv[cn]; break
        conv_camp = conv_camp or {}
        # 转化数据的 adgroup 名 -> 归一化索引，便于匹配平台 adgroup 名
        conv_idx = {_norm_name(k): v for k, v in conv_camp.items()}

        rows_out = []
        for aname, sp in adgroups.items():
            c = conv_camp.get(aname) or conv_idx.get(_norm_name(aname)) or {}
            loan = c.get("loan", 0)
            rows_out.append({
                "name":     aname,
                "spend":    round(sp, 2),
                "installs": c.get("installs", 0),
                "register": c.get("register", 0),
                "apply":    c.get("apply", 0),
                "loan":     loan,
                "revenue":  round(c.get("revenue", 0), 2),
                "cps":      round(sp / loan, 2) if loan > 0 else None,
            })
        rows_out.sort(key=lambda x: -x["spend"])
        result[matched] = rows_out
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
    # Campaign 看板始终按 campaign 汇总（近3天/近7天/本月均为区间汇总，不分日拆行）
    dims = "channel,campaign"
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

        # ★ 拉取 Adgroup 级别消耗，供前端按 Campaign 展开
        # 规则：campaign spend>0 才展开 adgroup；spend=0（含 installs=0 被置0的）不展开
        adgroups = {}
        try:
            fb_ag = fetch_fb_adgroup_spend(period)
            tt_ag = fetch_tt_adgroup_spend(period)
            gg_ag = fetch_gg_adgroup_spend(period)
            # 以 rows 里注入后的真实 cost 为准，只保留 spend>0 的 campaign 展开 adgroup
            fb_spend_camps = {r["campaign"] for r in rows if r["channel"] == "Facebook"             and (r.get("cost") or 0) > 0}
            tt_spend_camps = {r["campaign"] for r in rows if r["channel"] == "TikTok for Business"  and (r.get("cost") or 0) > 0}
            gg_spend_camps = {r["campaign"] for r in rows if r["channel"] == "Google Ads"           and (r.get("cost") or 0) > 0}
            fb_ag = {c: ag for c, ag in fb_ag.items() if c in fb_spend_camps}
            tt_ag = {c: ag for c, ag in tt_ag.items() if c in tt_spend_camps}
            gg_ag = {c: ag for c, ag in gg_ag.items() if c in gg_spend_camps}
            # Adjust 侧 campaign 名称同样只取 spend>0 的
            fb_adj_camps = list(fb_spend_camps)
            tt_adj_camps = list(tt_spend_camps)
            gg_adj_camps = list(gg_spend_camps)
            # ★ 拉取 Adjust adgroup 级转化数据（installs/register/apply/loan/revenue）
            adj_conv = fetch_adjust_adgroup(period)
            adgroups = {
                "Facebook": _match_adgroup_map(fb_ag, fb_adj_camps, adj_conv.get("Facebook")),
                "TikTok for Business": _match_adgroup_map(tt_ag, tt_adj_camps, adj_conv.get("TikTok for Business")),
                "Google Ads": _match_adgroup_map(gg_ag, gg_adj_camps, adj_conv.get("Google Ads")),
            }
        except Exception:
            pass

        return jsonify({
            "ok": True, "period": period, "start": start, "end": end,
            "has_day": has_day(period),
            "pulled_at": now8().strftime("%Y-%m-%d %H:%M:%S"),
            "total": total, "key_total": key_total,
            "channel_totals": ch_totals, "by_campaign": rows,
            "adgroups": adgroups,
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
    # Campaign 看板始终按 campaign 汇总（近3天/近7天/本月均为区间汇总，不分日拆行）
    dims = "channel,campaign"
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

        # ★ 拉取 Adgroup 级别消耗，供前端按 Campaign 展开（与 Android 端逻辑一致）
        # 规则：campaign spend>0 才展开 adgroup；spend=0（含 installs=0 被置0的）不展开
        adgroups = {}
        try:
            fb_ag = fetch_fb_ios_adgroup_spend(period)
            tt_ag = fetch_tt_ios_adgroup_spend(period)
            # 以 rows 里注入后的真实 cost 为准，只保留 spend>0 的 campaign 展开 adgroup
            fb_spend_camps = {r["campaign"] for r in rows if r["channel"] == "Facebook"            and (r.get("cost") or 0) > 0}
            tt_spend_camps = {r["campaign"] for r in rows if r["channel"] == "TikTok for Business" and (r.get("cost") or 0) > 0}
            fb_ag = {c: ag for c, ag in fb_ag.items() if c in fb_spend_camps}
            tt_ag = {c: ag for c, ag in tt_ag.items() if c in tt_spend_camps}
            fb_adj_camps = list(fb_spend_camps)
            tt_adj_camps = list(tt_spend_camps)
            # ★ 拉取 Adjust adgroup 级转化数据（iOS app_token）
            adj_conv = fetch_adjust_adgroup(period, app_token=IOS_APP_TOKEN)
            adgroups = {
                "Facebook": _match_adgroup_map(fb_ag, fb_adj_camps, adj_conv.get("Facebook")),
                "TikTok for Business": _match_adgroup_map(tt_ag, tt_adj_camps, adj_conv.get("TikTok for Business")),
            }
        except Exception:
            pass

        return jsonify({
            "ok": True, "period": period, "start": start, "end": end,
            "has_day": has_day(period),
            "pulled_at": now8().strftime("%Y-%m-%d %H:%M:%S"),
            "total": total, "key_total": key_total,
            "channel_totals": ch_totals, "by_campaign": rows,
            "adgroups": adgroups,
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


# ══════════════════════════════════════════════════════════════════
# 内部数据代理接口（Internal Media Data Proxy）
# 安全边界：媒体 Secret 只从 Render Environment 读取，接口只返回业务数据，
#           绝不在响应/日志中输出任何 Token / Secret / RefreshToken / 私钥。
# 认证：请求头 X-API-Key 必须等于环境变量 DASHBOARD_API_KEY（恒定时间比较）。
# ══════════════════════════════════════════════════════════════════
import hmac as _hmac
import time as _time

DASHBOARD_API_KEY = os.environ.get("DASHBOARD_API_KEY", "")

# 账户清单：side / channel / 账户名 / 账户ID / 余额类型说明
# balance_type: limited(有限预算) / infinite(无限额度) / billing(账单结算) / unknown
MEDIA_ACCOUNTS = [
    # ---- Google Ads ----
    {"side": "android", "channel": "google", "account_name": "飞书-GG-pesoloan-0513-test-1",
     "account_id": "422-341-0058", "gg_customer_id": "4223410058", "balance_type": "limited"},
    {"side": "android", "channel": "google", "account_name": "GG-pesoloan-无限额度账户",
     "account_id": "337-532-5268", "gg_customer_id": "3375325268", "balance_type": "infinite"},
    # ---- Facebook（Android）----
    # 账户名由 API 实时获取，account_id 用 act_ 去前缀
    {"side": "android", "channel": "facebook", "account_name": None, "account_id": "2043458276522117", "fb_act": "act_2043458276522117", "balance_type": "unknown"},
    {"side": "android", "channel": "facebook", "account_name": None, "account_id": "1338744840870824", "fb_act": "act_1338744840870824", "balance_type": "unknown"},
    {"side": "android", "channel": "facebook", "account_name": None, "account_id": "554870820824463",  "fb_act": "act_554870820824463",  "balance_type": "unknown"},
    {"side": "android", "channel": "facebook", "account_name": None, "account_id": "1763443588125609", "fb_act": "act_1763443588125609", "balance_type": "unknown"},
    {"side": "android", "channel": "facebook", "account_name": None, "account_id": "4425161567801548", "fb_act": "act_4425161567801548", "balance_type": "unknown"},
    {"side": "android", "channel": "facebook", "account_name": None, "account_id": "3511882642320376", "fb_act": "act_3511882642320376", "balance_type": "unknown"},
    {"side": "android", "channel": "facebook", "account_name": None, "account_id": "1654205562363513", "fb_act": "act_1654205562363513", "balance_type": "unknown"},
    # ---- Facebook（iOS）----
    {"side": "ios", "channel": "facebook", "account_name": None, "account_id": "826668223504196",  "fb_act": "act_826668223504196",  "balance_type": "unknown"},
    {"side": "ios", "channel": "facebook", "account_name": None, "account_id": "485941130935481",  "fb_act": "act_485941130935481",  "balance_type": "unknown"},
    {"side": "ios", "channel": "facebook", "account_name": None, "account_id": "1050911951210157", "fb_act": "act_1050911951210157", "balance_type": "unknown"},
    {"side": "ios", "channel": "facebook", "account_name": None, "account_id": "2487386801730510", "fb_act": "act_2487386801730510", "balance_type": "unknown"},
    # ---- TikTok（Android / iOS）----
    {"side": "android", "channel": "tiktok", "account_name": None, "account_id": TT_ADV_ID,     "tt_adv": TT_ADV_ID,     "balance_type": "unknown"},
    {"side": "ios",     "channel": "tiktok", "account_name": None, "account_id": TT_IOS_ADV_ID, "tt_adv": TT_IOS_ADV_ID, "balance_type": "unknown"},
    # ---- Apple Search Ads ----
    {"side": "ios", "channel": "asa", "account_name": "Apple Search Ads", "account_id": "asa-org", "balance_type": "billing"},
]

_proxy_cache = {"balances": None, "balances_ts": 0}
_PROXY_CACHE_TTL = 300  # 5 分钟


def _proxy_now_iso():
    return now8().strftime("%Y-%m-%dT%H:%M:%S+08:00")


def _require_api_key():
    """校验 X-API-Key。缺失→401；不匹配→403；正确→None(放行)。"""
    provided = request.headers.get("X-API-Key", "")
    if not provided:
        return jsonify({"ok": False, "error": "missing X-API-Key"}), 401
    if not DASHBOARD_API_KEY or not _hmac.compare_digest(provided, DASHBOARD_API_KEY):
        return jsonify({"ok": False, "error": "invalid API key"}), 403
    return None


def _last7_range_manila():
    """最近7个完整自然日（不含今天），UTC+8/Asia/Manila 口径。返回 (since, until)。"""
    t = now8()
    until = (t - timedelta(days=1)).strftime("%Y-%m-%d")
    since = (t - timedelta(days=7)).strftime("%Y-%m-%d")
    return since, until


# ── 各媒体 7 日消耗（用于余额预警） ──────────────────────────
def _fb_spend_7d(act_id):
    since, until = _last7_range_manila()
    try:
        r = requests.get(f"{FB_BASE}/{act_id}/insights", timeout=20, params={
            "access_token": get_fb_token(),
            "fields": "spend",
            "time_range": _json.dumps({"since": since, "until": until}),
            "level": "account",
        })
        if r.status_code == 200:
            data = r.json().get("data", [])
            return round(float(data[0].get("spend", 0)), 2) if data else 0.0
    except Exception:
        pass
    return 0.0


def _tt_spend_7d(adv_id):
    since, until = _last7_range_manila()
    try:
        r = requests.get(f"{TT_BASE}/report/integrated/get/",
                         headers={"Access-Token": TT_ACCESS_TOKEN}, timeout=20,
                         params={
                             "advertiser_id": adv_id,
                             "report_type": "BASIC",
                             "data_level": "AUCTION_ADVERTISER",
                             "dimensions": _json.dumps(["advertiser_id"]),
                             "metrics": _json.dumps(["spend"]),
                             "start_date": since, "end_date": until,
                         })
        d = r.json()
        if d.get("code") == 0:
            lst = d.get("data", {}).get("list", [])
            return round(sum(float(x.get("metrics", {}).get("spend", 0) or 0) for x in lst), 2)
    except Exception:
        pass
    return 0.0


def _gg_spend_7d(customer_id):
    since, until = _last7_range_manila()
    token = _gg_get_access_token()
    if not token:
        return 0.0
    headers = {"Authorization": f"Bearer {token}", "developer-token": GG_DEVELOPER_TOKEN,
               "login-customer-id": GG_MCC_ID, "Content-Type": "application/json"}
    q = ("SELECT metrics.cost_micros FROM customer "
         f"WHERE segments.date BETWEEN '{since}' AND '{until}'")
    try:
        url = f"https://googleads.googleapis.com/{GG_API_VER}/customers/{customer_id}/googleAds:search"
        resp = requests.post(url, headers=headers, json={"query": q}, timeout=15)
        if resp.status_code == 200:
            rows = resp.json().get("results", [])
            return round(sum(int(x.get("metrics", {}).get("costMicros", 0)) / 1e6 for x in rows), 2)
    except Exception:
        pass
    return 0.0


def _calc_warning(balance, spend_7d, balance_supported):
    """统一计算 7日均消 / 预警阈值 / 可用天数 / 是否预警。"""
    avg = round(spend_7d / 7.0, 2) if spend_7d else 0.0
    out = {"spend_7d": round(spend_7d, 2), "avg_daily_spend_7d": avg,
           "warning_threshold": None, "available_days": None, "warning": False}
    if balance_supported and balance is not None and avg > 0:
        thr = round(avg * 7, 2)
        out["warning_threshold"] = thr
        out["available_days"] = round(balance / avg, 2)
        out["warning"] = balance < thr
    return out


# ── 各账户余额拉取（单账户失败不影响整体） ──────────────────
def _balance_facebook(acc):
    row = {**{k: acc.get(k) for k in ("side", "channel", "account_name", "account_id", "balance_type")},
           "data_time": _proxy_now_iso(), "source_status": "ok", "source_error": None}
    try:
        r = requests.get(f"{FB_BASE}/{acc['fb_act']}", timeout=20, params={
            "access_token": get_fb_token(),
            "fields": "name,account_id,account_status,currency,balance,amount_spent",
        })
        if r.status_code != 200:
            row["source_status"] = "error"
            row["source_error"] = f"fb http {r.status_code}"
            return row
        j = r.json()
        row["account_name"] = j.get("name") or acc.get("account_name")
        row["currency"] = j.get("currency")
        row["account_status"] = str(j.get("account_status", ""))
        # Facebook balance 单位为最小货币单位（分），需要 /100；部分结算方式不返回
        bal_raw = j.get("balance", None)
        if bal_raw is None:
            row["balance"] = None
            row["balance_supported"] = False
            row["balance_note"] = "该账户结算方式不支持通过API获取可充值余额"
        else:
            row["balance"] = round(int(bal_raw) / 100.0, 2)
            row["balance_supported"] = True
        sp = _fb_spend_7d(acc["fb_act"])
        row.update(_calc_warning(row.get("balance"), sp, row.get("balance_supported", False)))
    except Exception as e:
        row["source_status"] = "error"
        row["source_error"] = "fb exception"
    return row


def _balance_tiktok(acc):
    row = {**{k: acc.get(k) for k in ("side", "channel", "account_name", "account_id", "balance_type")},
           "data_time": _proxy_now_iso(), "source_status": "ok", "source_error": None}
    try:
        r = requests.get(f"{TT_BASE}/advertiser/info/",
                         headers={"Access-Token": TT_ACCESS_TOKEN}, timeout=20,
                         params={"advertiser_ids": _json.dumps([acc["tt_adv"]])})
        d = r.json()
        if d.get("code") != 0:
            row["source_status"] = "error"
            row["source_error"] = f"tt code {d.get('code')}"
            return row
        lst = d.get("data", {}).get("list", []) or d.get("data", [])
        info = lst[0] if lst else {}
        row["account_name"] = info.get("name") or acc.get("account_name")
        row["currency"] = info.get("currency")
        row["account_status"] = str(info.get("status", ""))
        bal = info.get("balance", None)
        if bal is None:
            row["balance"] = None
            row["balance_supported"] = False
            row["balance_note"] = "该账户结算方式不支持通过API获取可充值余额"
        else:
            row["balance"] = round(float(bal), 2)
            row["balance_supported"] = True
        sp = _tt_spend_7d(acc["tt_adv"])
        row.update(_calc_warning(row.get("balance"), sp, row.get("balance_supported", False)))
    except Exception:
        row["source_status"] = "error"
        row["source_error"] = "tt exception"
    return row


def _balance_google(acc):
    row = {**{k: acc.get(k) for k in ("side", "channel", "account_name", "account_id", "balance_type")},
           "data_time": _proxy_now_iso(), "source_status": "ok", "source_error": None,
           "currency": "USD", "account_status": "ENABLED"}
    cid = acc["gg_customer_id"]
    try:
        if acc.get("balance_type") == "infinite":
            # 无限额度账户：不拉余额、不预警，仅保留 7日均消
            row["balance"] = None
            row["balance_supported"] = True
            row["warning"] = False
            sp = _gg_spend_7d(cid)
            w = _calc_warning(None, sp, False)
            row["spend_7d"] = w["spend_7d"]
            row["avg_daily_spend_7d"] = w["avg_daily_spend_7d"]
            row["warning_threshold"] = None
            row["available_days"] = None
            return row
        # 有限预算账户：查 APPROVED 的 account_budget
        token = _gg_get_access_token()
        if not token:
            row["source_status"] = "error"; row["source_error"] = "gg oauth fail"; return row
        headers = {"Authorization": f"Bearer {token}", "developer-token": GG_DEVELOPER_TOKEN,
                   "login-customer-id": GG_MCC_ID, "Content-Type": "application/json"}
        q = ("SELECT account_budget.approved_spending_limit_micros, "
             "account_budget.amount_served_micros, account_budget.total_adjustments_micros, "
             "account_budget.status FROM account_budget "
             "WHERE account_budget.status = 'APPROVED'")
        url = f"https://googleads.googleapis.com/{GG_API_VER}/customers/{cid}/googleAds:search"
        resp = requests.post(url, headers=headers, json={"query": q}, timeout=15)
        if resp.status_code != 200:
            row["source_status"] = "error"; row["source_error"] = f"gg http {resp.status_code}"; return row
        results = resp.json().get("results", [])
        if not results:
            row["balance"] = None; row["balance_supported"] = False
            row["balance_note"] = "无 APPROVED 预算"
        else:
            ab = results[0].get("accountBudget", {})
            limit = int(ab.get("approvedSpendingLimitMicros", 0)) / 1e6
            served = int(ab.get("amountServedMicros", 0)) / 1e6
            adj = int(ab.get("totalAdjustmentsMicros", 0)) / 1e6
            row["balance"] = round(limit + adj - served, 2)
            row["balance_supported"] = True
        sp = _gg_spend_7d(cid)
        row.update(_calc_warning(row.get("balance"), sp, row.get("balance_supported", False)))
    except Exception:
        row["source_status"] = "error"; row["source_error"] = "gg exception"
    return row


def _balance_asa(acc):
    # ASA 账单结算，无通用可充值余额字段
    row = {**{k: acc.get(k) for k in ("side", "channel", "account_name", "account_id", "balance_type")},
           "data_time": _proxy_now_iso(), "source_status": "ok", "source_error": None,
           "balance": None, "balance_supported": False,
           "balance_note": "账单结算，无通用可充值余额字段",
           "spend_7d": None, "avg_daily_spend_7d": None,
           "warning_threshold": None, "available_days": None, "warning": False}
    return row


def _collect_balances():
    out = []
    for acc in MEDIA_ACCOUNTS:
        ch = acc.get("channel")
        try:
            if ch == "facebook":
                out.append(_balance_facebook(acc))
            elif ch == "tiktok":
                out.append(_balance_tiktok(acc))
            elif ch == "google":
                out.append(_balance_google(acc))
            elif ch == "asa":
                out.append(_balance_asa(acc))
        except Exception:
            out.append({**{k: acc.get(k) for k in ("side", "channel", "account_name", "account_id")},
                        "source_status": "error", "source_error": "collect exception",
                        "data_time": _proxy_now_iso()})
    return out


@app.route("/internal/media/health")
def internal_media_health():
    auth = _require_api_key()
    if auth:
        return auth
    # 环境变量完整性（只报是否齐全，不返回值）
    env_ok = {
        "facebook": bool(FB_LONG_TOKEN),
        "tiktok":   bool(TT_ACCESS_TOKEN),
        "google":   all([GG_CLIENT_ID, GG_CLIENT_SECRET, GG_REFRESH_TOKEN, GG_DEVELOPER_TOKEN]),
        "asa":      True,  # ASA 暂按账单结算，无需可充值凭证
        "dashboard_api_key": bool(DASHBOARD_API_KEY),
    }
    services = {}
    # Facebook 连通性
    try:
        r = requests.get(f"{FB_BASE}/me", timeout=10, params={"access_token": get_fb_token(), "fields": "id"})
        services["facebook"] = {"status": "ok" if r.status_code == 200 else "error"}
    except Exception:
        services["facebook"] = {"status": "error"}
    # TikTok 连通性
    try:
        r = requests.get(f"{TT_BASE}/advertiser/info/", headers={"Access-Token": TT_ACCESS_TOKEN},
                         timeout=10, params={"advertiser_ids": _json.dumps([TT_ADV_ID])})
        services["tiktok"] = {"status": "ok" if r.json().get("code") == 0 else "error"}
    except Exception:
        services["tiktok"] = {"status": "error"}
    # Google OAuth 刷新
    try:
        services["google"] = {"status": "ok" if _gg_get_access_token() else "error"}
    except Exception:
        services["google"] = {"status": "error"}
    # ASA
    services["asa"] = {"status": "ok", "note": "账单结算，无可充值余额接口"}

    return jsonify({
        "ok": True,
        "data_time": _proxy_now_iso(),
        "env_complete": env_ok,
        "services": services,
    })


@app.route("/internal/media/balances")
def internal_media_balances():
    auth = _require_api_key()
    if auth:
        return auth
    now = _time.time()
    if _proxy_cache["balances"] and now - _proxy_cache["balances_ts"] < _PROXY_CACHE_TTL:
        cached = _proxy_cache["balances"]
        return jsonify({"ok": True, "cached": True, "data_time": cached["data_time"], "accounts": cached["accounts"]})
    accounts = _collect_balances()
    payload = {"data_time": _proxy_now_iso(), "accounts": accounts}
    _proxy_cache["balances"] = payload
    _proxy_cache["balances_ts"] = now
    return jsonify({"ok": True, "cached": False, "data_time": payload["data_time"], "accounts": accounts})

# ══════════════════════════════════════════════════════════════════
# ── 被拒账户 / 拒登素材监控（Google / Facebook / TikTok）────────
_REJECTED_TTL = 300
_proxy_cache.update({"rejected": None, "rejected_ts": 0})

def _reject_base(acc):
    return {"side": acc.get("side"), "channel": acc.get("channel"),
            "account_name": acc.get("account_name"), "account_id": acc.get("account_id"),
            "account_status": None, "campaign_name": None, "campaign_id": None,
            "adgroup_name": None, "adgroup_id": None, "ad_name": None, "ad_id": None,
            "creative_id": None, "review_status": None, "reject_reason": None,
            "policy_topics": [], "first_seen_at": None, "updated_at": None,
            "preview_url": None, "source_status": "ok", "source_error": None,
            "data_time": _proxy_now_iso()}

def _fb_rejected(acc):
    base = _reject_base(acc); out = []
    try:
        info = requests.get(f"{FB_BASE}/{acc['fb_act']}", timeout=20, params={
            "access_token": get_fb_token(), "fields": "name,account_status,disable_reason"})
        if info.status_code != 200:
            base.update(source_status="error", source_error=f"fb account http {info.status_code}")
            return [base]
        ai = info.json(); base["account_name"] = ai.get("name"); base["account_status"] = str(ai.get("account_status", ""))
        params = {"access_token": get_fb_token(), "limit": 500,
                  "fields": "id,name,status,effective_status,updated_time,campaign{id,name},adset{id,name},creative{id,name,thumbnail_url}"}
        url = f"{FB_BASE}/{acc['fb_act']}/ads"
        while url:
            r = requests.get(url, timeout=30, params=params if url.endswith('/ads') else None)
            if r.status_code != 200:
                base.update(source_status="error", source_error=f"fb ads http {r.status_code}")
                return out or [base]
            j = r.json()
            for ad in j.get("data", []):
                st = str(ad.get("effective_status") or ad.get("status") or "")
                # 只预警明确拒登/存在问题；PENDING_REVIEW 属正常审核中，不计为拒登
                if st not in ("DISAPPROVED", "WITH_ISSUES"):
                    continue
                row = dict(base); camp = ad.get("campaign") or {}; aset = ad.get("adset") or {}; cr = ad.get("creative") or {}
                row.update(campaign_name=camp.get("name"), campaign_id=camp.get("id"), adgroup_name=aset.get("name"),
                           adgroup_id=aset.get("id"), ad_name=ad.get("name"), ad_id=ad.get("id"), creative_id=cr.get("id"),
                           review_status=st, reject_reason="Meta 审核未通过/存在问题；详细政策原因需账户具备审核反馈权限",
                           updated_at=ad.get("updated_time"), preview_url=cr.get("thumbnail_url"))
                out.append(row)
            url = (j.get("paging") or {}).get("next")
        return out
    except Exception:
        base.update(source_status="error", source_error="fb rejected query exception"); return [base]

def _tt_rejected(acc):
    base = _reject_base(acc); out = []
    try:
        ir = requests.get(f"{TT_BASE}/advertiser/info/", headers={"Access-Token": TT_ACCESS_TOKEN}, timeout=20,
                          params={"advertiser_ids": _json.dumps([acc['tt_adv']])})
        ij = ir.json(); infos = (ij.get("data") or {}).get("list", []) if isinstance(ij.get("data"), dict) else []
        if infos:
            base["account_name"] = infos[0].get("name"); base["account_status"] = str(infos[0].get("status", ""))
        page = 1
        while True:
            r = requests.get(f"{TT_BASE}/ad/get/", headers={"Access-Token": TT_ACCESS_TOKEN}, timeout=30,
                             params={"advertiser_id": acc["tt_adv"], "page": page, "page_size": 100,
                                     "fields": _json.dumps(["ad_id","ad_name","campaign_id","campaign_name","adgroup_id","adgroup_name","operation_status","secondary_status","reject_message","modify_time","image_ids","video_id"])})
            d = r.json()
            if d.get("code") != 0:
                base.update(source_status="error", source_error=f"tt code {d.get('code')}"); return out or [base]
            data = d.get("data") or {}; items = data.get("list", [])
            for ad in items:
                st = str(ad.get("secondary_status") or ad.get("operation_status") or "")
                # 只预警明确拒登/停用；普通 AUDIT/REVIEW 审核中不计为拒登
                if not any(x in st.upper() for x in ("REJECT", "DISAPPROV", "SUSPEND")):
                    continue
                row = dict(base); row.update(campaign_name=ad.get("campaign_name"), campaign_id=str(ad.get("campaign_id") or ""),
                    adgroup_name=ad.get("adgroup_name"), adgroup_id=str(ad.get("adgroup_id") or ""), ad_name=ad.get("ad_name"),
                    ad_id=str(ad.get("ad_id") or ""), creative_id=str(ad.get("video_id") or ((ad.get("image_ids") or [""])[0])),
                    review_status=st, reject_reason=ad.get("reject_message") or "TikTok 审核未通过", updated_at=ad.get("modify_time"))
                out.append(row)
            pi = data.get("page_info") or {}; total_page = int(pi.get("total_page", page) or page)
            if page >= total_page or not items: break
            page += 1
        return out
    except Exception:
        base.update(source_status="error", source_error="tt rejected query exception"); return [base]

def _gg_rejected(acc):
    base = _reject_base(acc); out = []; cid = acc["gg_customer_id"]
    token = _gg_get_access_token()
    if not token:
        base.update(source_status="error", source_error="gg oauth fail"); return [base]
    headers = {"Authorization": f"Bearer {token}", "developer-token": GG_DEVELOPER_TOKEN,
               "login-customer-id": GG_MCC_ID, "Content-Type": "application/json"}
    q = """SELECT customer.descriptive_name, customer.status, campaign.id, campaign.name,
    ad_group.id, ad_group.name, ad_group_ad.ad.id, ad_group_ad.ad.name,
    ad_group_ad.status, ad_group_ad.policy_summary.approval_status,
    ad_group_ad.policy_summary.policy_topic_entries
    FROM ad_group_ad
    WHERE ad_group_ad.policy_summary.approval_status != 'APPROVED'"""
    try:
        url = f"https://googleads.googleapis.com/{GG_API_VER}/customers/{cid}/googleAds:search"
        r = requests.post(url, headers=headers, json={"query": q, "pageSize": 10000}, timeout=45)
        if r.status_code != 200:
            base.update(source_status="error", source_error=f"gg http {r.status_code}"); return [base]
        for x in r.json().get("results", []):
            cu=x.get("customer",{}); ca=x.get("campaign",{}); ag=x.get("adGroup",{}); aga=x.get("adGroupAd",{}); ad=aga.get("ad",{}); ps=aga.get("policySummary",{})
            topics=[]
            for t in ps.get("policyTopicEntries",[]) or []:
                topics.append({"topic":t.get("topic"),"type":t.get("type"),"evidences":t.get("evidences",[])})
            row=dict(base); row.update(account_name=cu.get("descriptiveName") or acc.get("account_name"), account_status=cu.get("status"),
                campaign_name=ca.get("name"), campaign_id=str(ca.get("id") or ""), adgroup_name=ag.get("name"), adgroup_id=str(ag.get("id") or ""),
                ad_name=ad.get("name"), ad_id=str(ad.get("id") or ""), creative_id=str(ad.get("id") or ""),
                review_status=ps.get("approvalStatus"), reject_reason="; ".join(filter(None,[t.get("topic") for t in topics])) or "Google Ads 政策审核未通过",
                policy_topics=topics)
            out.append(row)
        return out
    except Exception:
        base.update(source_status="error", source_error="gg rejected query exception"); return [base]

def _collect_rejected():
    rows=[]; monitored=[]
    for acc in MEDIA_ACCOUNTS:
        ch=acc.get("channel")
        if ch not in ("facebook","tiktok","google"): continue
        monitored.append({k:acc.get(k) for k in ("side","channel","account_name","account_id")})
        rows.extend(_fb_rejected(acc) if ch=="facebook" else _tt_rejected(acc) if ch=="tiktok" else _gg_rejected(acc))
    return monitored, rows

@app.route("/internal/media/rejected-creatives")
def internal_media_rejected_creatives():
    auth=_require_api_key()
    if auth: return auth
    now=_time.time()
    if _proxy_cache["rejected"] and now-_proxy_cache["rejected_ts"]<_REJECTED_TTL:
        p=_proxy_cache["rejected"]; return jsonify({"ok":True,"cached":True,**p})
    accounts,rows=_collect_rejected()
    payload={"data_time":_proxy_now_iso(),"monitored_accounts":accounts,"items":rows,
             "summary":{"monitored":len(accounts),"rejected":sum(1 for x in rows if x.get("review_status")),
                        "source_errors":sum(1 for x in rows if x.get("source_status")!="ok")}}
    _proxy_cache["rejected"]=payload; _proxy_cache["rejected_ts"]=now
    return jsonify({"ok":True,"cached":False,**payload})

# 内部数据代理接口 END
# ══════════════════════════════════════════════════════════════════


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5050)
    args = parser.parse_args()
    print(f"✅ 启动成功 → http://localhost:{args.port}")
    print(f"   Channel 看板: http://localhost:{args.port}/")
    print(f"   Campaign 看板: http://localhost:{args.port}/campaign")
    app.run(host="0.0.0.0", port=args.port, debug=False)
