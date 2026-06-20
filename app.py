"""
Adjust PH_lucy 数据看板 — Web 服务
支持 Channel 看板 + Campaign 看板，实时拉取 Adjust API
"""
import re
import requests
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify, request, Response

# ── 配置 ─────────────────────────────────────────────────
APP_TOKEN  = "g0ylloj1w54w"
USER_TOKEN = "g9gJyYMyUN41vFeaR5QW"
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
    start, end = date_range(period)
    dims = "channel,campaign,day" if has_day(period) else "channel,campaign"
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
        for ch in ch_totals:
            tl = ch_totals[ch]["loan"]
            tc = ch_totals[ch]["cost"]
            ch_totals[ch]["cps"] = round(tc/tl, 2) if tl > 0 else None

        key_cost = sum(ch_totals.get(ch,{}).get("cost",0) for ch in KEY_CH)
        key_loan = sum(ch_totals.get(ch,{}).get("loan",0) for ch in KEY_CH)
        total = {
            "clicks":   sum(ch_totals.get(ch,{}).get("clicks",0)   for ch in KEY_CH),
            "installs": sum(ch_totals.get(ch,{}).get("installs",0) for ch in KEY_CH),
            "cost":     round(key_cost, 2),
            "register": sum(ch_totals.get(ch,{}).get("register",0) for ch in KEY_CH),
            "apply":    sum(ch_totals.get(ch,{}).get("apply",0)    for ch in KEY_CH),
            "loan":     key_loan,
            "revenue":  round(sum(ch_totals.get(ch,{}).get("revenue",0) for ch in KEY_CH), 2),
            "cps":      round(key_cost/key_loan, 2) if key_loan > 0 else None,
        }

        return jsonify({
            "ok": True, "period": period, "start": start, "end": end,
            "has_day": has_day(period),
            "pulled_at": now8().strftime("%Y-%m-%d %H:%M:%S"),
            "total": total, "channel_totals": ch_totals, "by_campaign": rows,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/")
def index():
    return Response(CHANNEL_HTML, mimetype="text/html")

@app.route("/campaign")
def campaign_page():
    return Response(CAMPAIGN_HTML, mimetype="text/html")

# ── 前端页面（内嵌 HTML）────────────────────────────────

CHANNEL_HTML = open("channel.html", encoding="utf-8").read() if __import__("os").path.exists("channel.html") else "<h1>channel.html not found</h1>"
CAMPAIGN_HTML = open("campaign.html", encoding="utf-8").read() if __import__("os").path.exists("campaign.html") else "<h1>campaign.html not found</h1>"

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5050)
    args = parser.parse_args()
    print(f"✅ 启动成功 → http://localhost:{args.port}")
    print(f"   Channel 看板: http://localhost:{args.port}/")
    print(f"   Campaign 看板: http://localhost:{args.port}/campaign")
    app.run(host="0.0.0.0", port=args.port, debug=False)
