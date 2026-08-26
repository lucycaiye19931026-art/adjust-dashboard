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
FB_ACT_IDS    = ["act_2043458276522117", "act_1338744840870824", "act_554870820824463", "act_1763443588125609", "act_4425161567801548", "act_3511882642320376", "act_1654205562363513", "act_1054117987058016", "act_1842012880095946", "act_1071912668521082", "act_1016349321026924", "act_893146393853948", "act_1082060041158190", "act_2468093726992507", "act_1554822826379992", "act_1172024374104199"]
FB_BASE       = "https://graph.facebook.com/v19.0"

# ── TikTok Ads API 配置 ───────────────────────────────────
TT_ACCESS_TOKEN = os.environ.get("TT_ACCESS_TOKEN", "")
TT_ADV_ID       = os.environ.get("TT_ADV_ID",       "7358007483270692880")
TT_BASE         = "https://business-api.tiktok.com/open_api/v1.3"

# ── iOS 专属配置 ──────────────────────────────────────────
IOS_APP_TOKEN   = os.environ.get("IOS_ADJUST_APP_TOKEN", "du1u32cgaigw")
IOS_KEY_CH      = ["Facebook", "TikTok for Business", "Apple"]
FB_IOS_ACT_IDS  = ["act_826668223504196", "act_485941130935481", "act_1050911951210157", "act_2487386801730510", "act_2547112895720531", "act_1032438845801223"]
TT_IOS_ADV_ID   = os.environ.get("TT_IOS_ADV_ID", "7358007484973563921")


# ── Apple Search Ads API 配置（凭证只读环境变量，勿硬编码）──
# 需要 Render Environment 配置：
#   ASA_CLIENT_ID / ASA_TEAM_ID / ASA_KEY_ID / ASA_ORG_ID / ASA_PRIVATE_KEY
# ASA_PRIVATE_KEY 为 PKCS8 私钥全文（含 BEGIN/END 行）
ASA_CLIENT_ID   = os.environ.get("ASA_CLIENT_ID", "")
ASA_TEAM_ID     = os.environ.get("ASA_TEAM_ID", "")
ASA_KEY_ID      = os.environ.get("ASA_KEY_ID", "")
ASA_ORG_ID      = os.environ.get("ASA_ORG_ID", "8038560")   # 飞书-Pesoloan-1211
ASA_PRIVATE_KEY = os.environ.get("ASA_PRIVATE_KEY", "")
ASA_BASE        = "https://api.searchads.apple.com/api/v5"
# 是否拉取 ASA adgroup 明细：每个 campaign 需 1 次串行请求，开启会显著变慢
# Render 免费实例建议关闭；需要 adgroup 细分时设为 "1"
ASA_FETCH_ADGROUP = os.environ.get("ASA_FETCH_ADGROUP", "0") == "1"
ASA_AUTH_URL    = "https://appleid.apple.com/auth/oauth2/token"

_asa_token_cache = {"token": None, "exp": 0}
_asa_spend_cache = {"data": {}, "ts": {}}
_ASA_TTL = 180


def asa_date_range(period):
    """ASA 日期范围（与其它渠道口径一致，UTC+8）"""
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


def _asa_get_token():
    """用 ES256 JWT 换 access_token；缓存至过期前 5 分钟"""
    import time as _t
    now = _t.time()
    if _asa_token_cache["token"] and now < _asa_token_cache["exp"] - 300:
        return _asa_token_cache["token"]
    if not (ASA_CLIENT_ID and ASA_TEAM_ID and ASA_KEY_ID and ASA_PRIVATE_KEY):
        return None
    try:
        import jwt as _jwt
        iat = int(now)
        client_secret = _jwt.encode(
            {"sub": ASA_CLIENT_ID, "aud": "https://appleid.apple.com",
             "iat": iat, "exp": iat + 86400 * 180, "iss": ASA_TEAM_ID},
            ASA_PRIVATE_KEY, algorithm="ES256",
            headers={"alg": "ES256", "kid": ASA_KEY_ID})
        r = requests.post(ASA_AUTH_URL,
            headers={"Host": "appleid.apple.com",
                     "Content-Type": "application/x-www-form-urlencoded"},
            params={"grant_type": "client_credentials",
                    "client_id": ASA_CLIENT_ID,
                    "client_secret": client_secret,
                    "scope": "searchadsorg"}, timeout=15)
        if r.status_code != 200:
            return None
        d = r.json()
        tok = d.get("access_token")
        if tok:
            _asa_token_cache["token"] = tok
            _asa_token_cache["exp"] = now + int(d.get("expires_in") or 3600)
        return tok
    except Exception:
        return None


def _asa_headers():
    tok = _asa_get_token()
    if not tok:
        return None
    return {"Authorization": f"Bearer {tok}",
            "X-AP-Context": f"orgId={ASA_ORG_ID}",
            "Content-Type": "application/json"}


def _asa_fetch(period):
    """
    一次拉取 ASA campaign + adgroup 两层消耗（60 秒缓存）
    返回 {"total": float, "campaign": {name: spend}, "adgroup": {camp: {ag: spend}}}
    """
    import time as _t
    since, until = asa_date_range(period)
    ck = f"{period}:{since}:{until}"
    now = _t.time()
    if ck in _asa_spend_cache["data"] and now - _asa_spend_cache["ts"].get(ck, 0) < _ASA_TTL:
        return _asa_spend_cache["data"][ck]

    empty = {"total": 0.0, "campaign": {}, "adgroup": {}}
    H = _asa_headers()
    if not H:
        return empty

    campaign, adgroup, cmap = {}, {}, {}
    try:
        rc = requests.post(f"{ASA_BASE}/reports/campaigns", headers=H, json={
            "startTime": since, "endTime": until,
            "selector": {"orderBy": [{"field": "localSpend", "sortOrder": "DESCENDING"}],
                         "pagination": {"offset": 0, "limit": 1000}},
            "groupBy": ["countryOrRegion"],
            "timeZone": "ORTZ", "returnRecordsWithNoMetrics": False,
            "returnRowTotals": True, "returnGrandTotals": True}, timeout=30)
        if rc.status_code != 200:
            return empty
        for row in ((rc.json().get("data") or {}).get("reportingDataResponse") or {}).get("row") or []:
            md  = row.get("metadata") or {}
            tot = row.get("total") or {}
            sp  = float((tot.get("localSpend") or {}).get("amount") or 0)
            cid, cname = md.get("campaignId"), md.get("campaignName")
            if cname and sp > 0:
                cmap[cid] = cname
                campaign[cname] = round(campaign.get(cname, 0) + sp, 2)
    except Exception:
        return empty

    # adgroup：仅对有消耗的 campaign 展开（与 FB/TikTok 规则一致）
    # 性能开关：关闭时跳过（每 campaign 需 1 次串行请求）
    for cid, cname in (cmap.items() if ASA_FETCH_ADGROUP else []):
        try:
            r = requests.post(f"{ASA_BASE}/reports/campaigns/{cid}/adgroups", headers=H, json={
                "startTime": since, "endTime": until,
                "selector": {"orderBy": [{"field": "localSpend", "sortOrder": "DESCENDING"}],
                             "pagination": {"offset": 0, "limit": 1000}},
                "timeZone": "ORTZ", "returnRecordsWithNoMetrics": False,
                "returnRowTotals": True, "returnGrandTotals": False}, timeout=30)
            if r.status_code != 200:
                continue
            for row in ((r.json().get("data") or {}).get("reportingDataResponse") or {}).get("row") or []:
                md  = row.get("metadata") or {}
                tot = row.get("total") or {}
                sp  = float((tot.get("localSpend") or {}).get("amount") or 0)
                aname = md.get("adGroupName")
                if aname and sp > 0:      # 只保留有消耗的 adgroup
                    adgroup.setdefault(cname, {})[aname] = round(
                        adgroup.get(cname, {}).get(aname, 0) + sp, 2)
        except Exception:
            continue

    result = {"total": round(sum(campaign.values()), 2),
              "campaign": campaign, "adgroup": adgroup}
    _asa_spend_cache["data"][ck] = result
    _asa_spend_cache["ts"][ck] = now
    return result


def fetch_asa_channel_spend(period):
    """ASA 渠道总消耗（真实，替代 Adjust 回传）"""
    return _asa_fetch(period)["total"]


def fetch_asa_campaign_spend(period):
    """ASA Campaign 级消耗 {campaign_name: spend}"""
    return _asa_fetch(period)["campaign"]


def fetch_asa_adgroup_spend(period):
    """ASA Ad Group 级消耗 {campaign_name: {adgroup_name: spend}}"""
    return _asa_fetch(period)["adgroup"]


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

# ══════════════════════════════════════════════════════════════════
# 数据快照层（后台预拉 + 接口秒回）
# ------------------------------------------------------------------
# 背景：原实现每次用户访问都实时调 Adjust/FB/TikTok/Google/ASA，
#       在 Render 免费实例（0.1 CPU）下 Channel 需 28s、Campaign 超时，
#       页面报「数据加载失败」。
# 方案：后台线程定时把 4 个接口 × 5 个 period 全部拉好存快照，
#       用户请求直接读快照（<0.1s）。数据最多滞后 SNAPSHOT_INTERVAL 秒。
# ══════════════════════════════════════════════════════════════════
import threading as _th
import time as _time2

# 预拉间隔（秒）。可用环境变量覆盖；免费实例建议 300~600
SNAPSHOT_INTERVAL = int(os.environ.get("SNAPSHOT_INTERVAL", "300"))
# 快照文件路径（Render 容器内 /tmp 可写；重启会丢，属预期）
SNAPSHOT_FILE = os.environ.get("SNAPSHOT_FILE", "/tmp/dash_snapshot.json")
# 是否启用后台预拉（设为 "0" 可关闭，退回纯实时模式）
SNAPSHOT_ENABLED = os.environ.get("SNAPSHOT_ENABLED", "1") == "1"
# 预拉哪些 period（顺序即优先级，today 最先保证最新）
SNAPSHOT_PERIODS = ["today", "yesterday", "3days", "7days", "month"]

_snap = {"data": {}, "ts": {}, "lock": _th.Lock(), "started": False,
         "last_round": None, "rounds": 0, "errors": 0}


def _snap_key(view, period):
    return f"{view}|{period}"


def _snap_load_disk():
    """冷启动时载入磁盘快照，避免实例重启后首屏无数据"""
    try:
        if os.path.exists(SNAPSHOT_FILE):
            with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:
                obj = _json.load(f)
            with _snap["lock"]:
                _snap["data"].update(obj.get("data") or {})
                _snap["ts"].update(obj.get("ts") or {})
            return True
    except Exception:
        pass
    return False


def _snap_save_disk():
    try:
        tmp = SNAPSHOT_FILE + ".tmp"
        with _snap["lock"]:
            payload = {"data": _snap["data"], "ts": _snap["ts"],
                       "saved_at": now8().strftime("%Y-%m-%d %H:%M:%S")}
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, SNAPSHOT_FILE)
    except Exception:
        pass


def snapshot_get(view, period, max_age=None):
    """取快照。max_age=None 表示不限年龄（stale 也返回）"""
    k = _snap_key(view, period)
    with _snap["lock"]:
        if k not in _snap["data"]:
            return None, None
        val = _snap["data"][k]
        ts = _snap["ts"].get(k, 0)
    age = _time2.time() - ts
    if max_age is not None and age > max_age:
        return None, age
    return val, age


def snapshot_put(view, period, payload):
    k = _snap_key(view, period)
    with _snap["lock"]:
        _snap["data"][k] = payload
        _snap["ts"][k] = _time2.time()


def snapshot_first(view):
    """
    路由装饰器：优先返回快照，未命中则实时执行原逻辑并顺手写入快照。
    ?fresh=1 可强制绕过快照实时拉取（排错用）。
    """
    def deco(fn):
        import functools

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            period = request.args.get("period", "today")
            if SNAPSHOT_ENABLED and request.args.get("fresh") != "1":
                val, age = snapshot_get(view, period)
                if val is not None:
                    out = dict(val)
                    out["_cache"] = "snapshot"
                    out["_age_sec"] = int(age or 0)
                    return jsonify(out)
            # 未命中：实时执行原逻辑
            resp = fn(*args, **kwargs)
            try:
                # resp 可能是 Response 或 (Response, code)
                r0 = resp[0] if isinstance(resp, tuple) else resp
                obj = r0.get_json(silent=True)
                if obj and obj.get("ok"):
                    snapshot_put(view, period, obj)
            except Exception:
                pass
            return resp
        return wrapper
    return deco


def _snap_refresh_once():
    """后台串行拉取全部 view×period（串行以免压垮弱 CPU）
    view 名 / endpoint 名 / URL 路径三者对应；按 endpoint 直取视图函数最稳。"""
    views = [("channel",      "api_channel",      "/api/channel"),
             ("campaign",     "api_campaign",     "/api/campaign"),
             ("ios_channel",  "api_ios_channel",  "/api/ios/channel"),
             ("ios_campaign", "api_ios_campaign", "/api/ios/campaign")]
    # today 全部先刷一轮（保证最新数据最快可用），再刷其余 period
    plans = [(v, ep, p, "today") for (v, ep, p) in views]
    plans += [(v, ep, p, per) for (v, ep, p) in views
              for per in SNAPSHOT_PERIODS if per != "today"]
    for view, endpoint, path, period in plans:
        try:
            # 用 test_request_context 复用原接口逻辑，业务代码零改动
            with app.test_request_context(f"{path}?period={period}&fresh=1"):
                fn = app.view_functions.get(endpoint)
                if fn is None:
                    _snap["errors"] += 1
                    continue
                resp = fn()
                r0 = resp[0] if isinstance(resp, tuple) else resp
                obj = r0.get_json(silent=True)
                if obj and obj.get("ok"):
                    snapshot_put(view, period, obj)
                else:
                    _snap["errors"] += 1
        except Exception:
            _snap["errors"] += 1
        _time2.sleep(1)          # 每次之间喘口气，降低 CPU 峰值（弱实例必需）
    _snap["rounds"] += 1
    _snap["last_round"] = now8().strftime("%Y-%m-%d %H:%M:%S")
    _snap_save_disk()


def _snap_worker():
    # 启动稍作延迟，避免与 Web 首次请求抢 CPU
    _time2.sleep(5)
    while True:
        try:
            _snap_refresh_once()
        except Exception:
            _snap["errors"] += 1
        _time2.sleep(SNAPSHOT_INTERVAL)


def snapshot_start():
    if not SNAPSHOT_ENABLED or _snap["started"]:
        return
    _snap["started"] = True
    _snap_load_disk()
    t = _th.Thread(target=_snap_worker, name="snapshot-worker", daemon=True)
    t.start()


@app.route("/internal/snapshot/status")
def snapshot_status():
    """快照健康状态（排错用，无敏感信息）"""
    with _snap["lock"]:
        items = []
        for k, ts in sorted(_snap["ts"].items()):
            items.append({"key": k, "age_sec": int(_time2.time() - ts),
                          "has_data": k in _snap["data"]})
    return jsonify({"enabled": SNAPSHOT_ENABLED,
                    "interval_sec": SNAPSHOT_INTERVAL,
                    "rounds": _snap["rounds"],
                    "errors": _snap["errors"],
                    "last_round": _snap["last_round"],
                    "count": len(items), "items": items})




# ── Campaign 名称规范化匹配（修复消耗丢失）────────────────
# 问题：Adjust 侧 campaign 名可能带 URL 编码（尾部 %20 等）或首尾空白，
#       媒体侧为干净名 → 精确匹配失败 → 该 campaign 消耗注入不上、凭空消失。
# 方案：精确匹配优先；失败则用规范化键（URL解码+去空白+小写）兜底匹配。
from urllib.parse import unquote as _unquote


def _norm_camp(name):
    """campaign 名规范化：URL解码 → 去首尾空白 → 折叠内部空白 → 小写"""
    if not name:
        return ""
    t = str(name)
    for _ in range(2):                 # 处理 %2520 这类二次编码
        try:
            u = _unquote(t)
        except Exception:
            break
        if u == t:
            break
        t = u
    t = t.replace("\u00a0", " ")       # 不换行空格
    t = re.sub(r"\s+", " ", t).strip()
    return t.lower()


def _norm_index(spend_map):
    """把 {name: spend} 建成 {规范化名: spend}（同名累加）"""
    idx = {}
    for k, v in (spend_map or {}).items():
        nk = _norm_camp(k)
        if nk:
            idx[nk] = round(idx.get(nk, 0) + (v or 0), 2)
    return idx


def _pick_spend(name, spend_map, norm_idx=None):
    """
    取某 campaign 的媒体侧消耗：精确匹配优先，规范化兜底。
    返回 (spend, matched) —— matched=False 表示两种方式都没匹配上。
    """
    if not spend_map:
        return (0.0, False)
    if name in spend_map:
        return (spend_map[name], True)
    idx = norm_idx if norm_idx is not None else _norm_index(spend_map)
    nk = _norm_camp(name)
    if nk and nk in idx:
        return (idx[nk], True)
    return (0.0, False)




# ── Adjust 报表请求缓存（60 秒）──────────────────────────
# Channel/Campaign 接口会多次请求 Adjust（主查询 + 全渠道汇总），
# 相同 params 在 60 秒内直接复用，避免重复等待。
_adjust_cache = {"data": {}, "ts": {}}
_ADJUST_TTL = 180


def _adjust_cached_get(url, headers=None, params=None, timeout=15):
    """带 60 秒缓存的 Adjust GET；缓存键 = 关键查询参数"""
    import time as _t
    try:
        ck = _json.dumps({
            "u": url,
            "p": params.get("date_period") if params else None,
            "d": params.get("dimensions") if params else None,
            "a": params.get("app_token__in") if params else None,
            "m": (params or {}).get("metrics"),
        }, sort_keys=True, ensure_ascii=False)
    except Exception:
        ck = None

    now = _t.time()
    if ck and ck in _adjust_cache["data"] and now - _adjust_cache["ts"].get(ck, 0) < _ADJUST_TTL:
        return _adjust_cache["data"][ck]

    resp = requests.get(url, headers=headers, params=params, timeout=timeout)
    if ck and resp is not None and resp.status_code == 200:
        _adjust_cache["data"][ck] = resp
        _adjust_cache["ts"][ck] = now
    return resp




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


# ── FB 统一并发拉取（性能优化）─────────────────────────────
# 原：channel/campaign/adgroup 三函数各串行遍历全部账户 = 3×N 次请求
# 现：一次 level=adset 并发请求，聚合三层数据共用，60 秒缓存
from concurrent.futures import ThreadPoolExecutor as _TPE

_fb_unified_cache = {"data": {}, "ts": {}}
_FB_CACHE_TTL = 180


def _fb_fetch_one_account(args):
    """拉单个账户 adset 级消耗（含 campaign_name），返回 (act_id, rows, ok)"""
    act_id, token, since, until = args
    try:
        r = requests.get(f"{FB_BASE}/{act_id}/insights", timeout=15, params={
            "access_token": token,
            "fields":       "campaign_name,adset_name,spend",
            "time_range":   _json.dumps({"since": since, "until": until}),
            "level":        "adset",
            "limit":        500,
        })
        if r.status_code == 200:
            return (act_id, r.json().get("data", []), True)
        return (act_id, [], False)
    except Exception:
        return (act_id, [], False)


def fb_unified_spend(period, act_ids=None, cache_tag="and"):
    """
    并发拉取全部 FB 账户消耗，一次请求聚合三层数据。
    返回 {"total": float, "campaign": {name: spend}, "adgroup": {camp: {adset: spend}}}
    """
    import time as _t
    ids = act_ids if act_ids is not None else FB_ACT_IDS
    since, until = fb_date_range(period)
    ck = f"{cache_tag}:{period}:{since}:{until}"
    now = _t.time()
    if ck in _fb_unified_cache["data"] and now - _fb_unified_cache["ts"].get(ck, 0) < _FB_CACHE_TTL:
        return _fb_unified_cache["data"][ck]

    token = get_fb_token()
    total = 0.0
    campaign = {}
    adgroup = {}

    tasks = [(a, token, since, until) for a in ids]
    # 并发：账户数不多，一次全开（上限 16 并发，避免触发 FB 限频）
    with _TPE(max_workers=min(6, max(1, len(tasks)))) as ex:
        for act_id, rows, ok in ex.map(_fb_fetch_one_account, tasks):
            for row in rows:
                try:
                    spend = float(row.get("spend", 0) or 0)
                except Exception:
                    spend = 0.0
                if spend <= 0:
                    continue
                cname = row.get("campaign_name", "") or ""
                aname = row.get("adset_name", "") or ""
                total += spend
                if cname:
                    campaign[cname] = round(campaign.get(cname, 0) + spend, 2)
                    if aname:
                        if cname not in adgroup:
                            adgroup[cname] = {}
                        adgroup[cname][aname] = round(adgroup[cname].get(aname, 0) + spend, 2)

    result = {"total": round(total, 2), "campaign": campaign, "adgroup": adgroup}
    _fb_unified_cache["data"][ck] = result
    _fb_unified_cache["ts"][ck] = now
    return result


def fetch_fb_channel_spend(period):
    """Facebook 全部账户总消耗（复用统一并发结果）"""
    return fb_unified_spend(period)["total"]

def fetch_fb_campaign_spend(period):
    """Campaign 级消耗 {campaign_name: spend}（复用统一并发结果）"""
    return fb_unified_spend(period)["campaign"]

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
    """Facebook iOS 账户总消耗（复用统一并发结果）"""
    return fb_unified_spend(period, act_ids=FB_IOS_ACT_IDS, cache_tag="ios")["total"]

def fetch_fb_ios_campaign_spend(period):
    """Facebook iOS Campaign 级消耗（复用统一并发结果）"""
    return fb_unified_spend(period, act_ids=FB_IOS_ACT_IDS, cache_tag="ios")["campaign"]

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
    """Facebook iOS Adset 级消耗（复用统一并发结果）"""
    return fb_unified_spend(period, act_ids=FB_IOS_ACT_IDS, cache_tag="ios")["adgroup"]


def fetch_tt_ios_adgroup_spend(period):
    """拉取 TikTok iOS Adgroup 级消耗，返回 {campaign_name: {adgroup_name: spend}}（只保留 spend>0）"""
    import time
    now = time.time()
    ck = _adgroup_cache_key("tt_ios", period)
    if _gg_spend_cache["data"].get(ck) and now - _gg_spend_cache["ts"] < 180:
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
    if _gg_spend_cache["data"].get(cache_key) and now - _gg_spend_cache["ts"] < 180:
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
    if _gg_spend_cache["data"].get(cache_key) and now - _gg_spend_cache["ts"] < 180:
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
    """FB Adset 级消耗 {campaign_name: {adset_name: spend}}（复用统一并发结果）"""
    return fb_unified_spend(period)["adgroup"]

def fetch_tt_adgroup_spend(period):
    """拉取 TikTok Adgroup 级别消耗，返回 {campaign_name: {adgroup_name: spend}}"""
    import time
    now = time.time()
    ck = _adgroup_cache_key("tt", period)
    if _gg_spend_cache["data"].get(ck) and now - _gg_spend_cache["ts"] < 180:
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
    if _gg_spend_cache["data"].get(ck) and now - _gg_spend_cache["ts"] < 180:
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
    if _gg_spend_cache["data"].get(ck) and now - _gg_spend_cache["ts"] < 180:
        return _gg_spend_cache["data"][ck]
    start, end = date_range(period)
    result = {}
    try:
        resp = _adjust_cached_get(BASE_URL, headers=HEADERS, timeout=15, params={
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
    """名称归一化（adgroup/campaign 模糊匹配用）
    含 URL 解码，兼容 Adjust 侧 %20 等编码差异（如 Pesoloan-adc-s-loan-2%20）"""
    return _norm_camp(s).replace(" ", "")


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



# ── 全渠道汇总缓存（避免 Channel/Campaign 接口重复请求 Adjust）──
_full_total_cache = {"data": {}, "ts": {}}
_FULL_TOTAL_TTL = 180


def _full_total_cached(fn, tag, period):
    import time as _t
    ck = f"{tag}:{period}"
    now = _t.time()
    if ck in _full_total_cache["data"] and now - _full_total_cache["ts"].get(ck, 0) < _FULL_TOTAL_TTL:
        return _full_total_cache["data"][ck]
    val = fn(period)
    _full_total_cache["data"][ck] = val
    _full_total_cache["ts"][ck] = now
    return val


def _compute_full_channel_total_raw(period):
    """拉取全渠道 channel 维度数据并汇总 Total（与 Adjust 后台总数口径一致，含长尾/自然量渠道）。
    复用 /api/channel 的合并 + 真实消耗注入逻辑，但只返回汇总后的 total 字典。"""
    start, end = date_range(period)
    dims = "channel,day" if has_day(period) else "channel"
    resp = _adjust_cached_get(BASE_URL, headers=HEADERS, timeout=15, params={
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
@snapshot_first("channel")
def api_channel():
    period = request.args.get("period", "today")
    start, end = date_range(period)
    dims = "channel,day" if has_day(period) else "channel"
    try:
        resp = _adjust_cached_get(BASE_URL, headers=HEADERS, timeout=15, params={
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
@snapshot_first("campaign")
def api_campaign():
    period = request.args.get("period", "yesterday")
    start = request.args.get("start") or date_range(period)[0]
    end   = request.args.get("end")   or date_range(period)[1]
    # Campaign 看板始终按 campaign 汇总（近3天/近7天/本月均为区间汇总，不分日拆行）
    dims = "channel,campaign"
    try:
        resp = _adjust_cached_get(BASE_URL, headers=HEADERS, timeout=15, params={
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
                else:
                    _sp, _ok = _pick_spend(camp_name, fb_camp_spend)
                    if _ok:
                        r["cost"] = _sp
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
                else:
                    _sp, _ok = _pick_spend(camp_name, tt_camp_spend)
                    if _ok:
                        r["cost"] = _sp
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
                else:
                    _sp, _ok = _pick_spend(camp_name, gg_camp_spend)
                    if _ok:
                        r["cost"] = _sp
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
@snapshot_first("ios_channel")
def api_ios_channel():
    period = request.args.get("period", "today")
    start, end = date_range(period)
    dims = "channel,day" if has_day(period) else "channel"
    try:
        resp = _adjust_cached_get(BASE_URL, headers={"Authorization": f"Bearer {USER_TOKEN}"},
                            timeout=15, params={
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

        # ★ 注入 Apple Search Ads 真实消耗（直连 ASA API，替代 Adjust 回传）
        asa_spend = fetch_asa_channel_spend(period)
        if asa_spend > 0:
            for r in rows:
                if r["channel"] == "Apple":
                    r["cost"] = asa_spend
                    r["cost_formula"] = "ASA API"
                    loan = r.get("loan") or 0
                    r["cps"] = round(asa_spend / loan, 2) if loan > 0 else None
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


def _compute_full_ios_channel_total_raw(period):
    """iOS 版全渠道 Total 汇总（与 Adjust 后台 iOS 总数口径一致，含 Organic/MOLOCO 等长尾渠道）。"""
    start, end = date_range(period)
    dims = "channel,day" if has_day(period) else "channel"
    resp = _adjust_cached_get(BASE_URL, headers={"Authorization": f"Bearer {USER_TOKEN}"},
                        timeout=15, params={
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
@snapshot_first("ios_campaign")
def api_ios_campaign():
    period = request.args.get("period", "yesterday")
    start, end = date_range(period)
    # Campaign 看板始终按 campaign 汇总（近3天/近7天/本月均为区间汇总，不分日拆行）
    dims = "channel,campaign"
    try:
        resp = _adjust_cached_get(BASE_URL, headers={"Authorization": f"Bearer {USER_TOKEN}"},
                            timeout=15, params={
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
                else:
                    _sp, _ok = _pick_spend(r.get("campaign",""), fb_ios_camp)
                    if _ok:
                        r["cost"] = _sp
                        loan = r.get("loan") or 0
                        r["cps"] = round(r["cost"] / loan, 2) if loan > 0 and r["cost"] > 0 else None

        # 注入 TikTok iOS Campaign 真实消耗（installs=0 置0）
        tt_ios_camp = fetch_tt_ios_campaign_spend(period)
        for r in rows:
            if r["channel"] == "TikTok for Business":
                installs = r.get("installs") or 0
                if installs == 0:
                    r["cost"] = 0.0; r["cps"] = None
                else:
                    _sp, _ok = _pick_spend(r.get("campaign",""), tt_ios_camp)
                    if _ok:
                        r["cost"] = _sp
                        loan = r.get("loan") or 0
                        r["cps"] = round(r["cost"] / loan, 2) if loan > 0 and r["cost"] > 0 else None

        # ★ 注入 Apple Search Ads 真实消耗（直连 ASA API，替代 Adjust 回传）
        #   Adjust 回传的 ASA 消耗实测漏报约 73%，故改为 ASA API 直连
        asa_camp = fetch_asa_campaign_spend(period)
        for r in rows:
            if r["channel"] == "Apple":
                cname    = r.get("campaign", "")
                installs = r.get("installs") or 0
                if installs == 0:
                    r["cost"] = 0.0; r["cps"] = None
                else:
                    _sp, _ok = _pick_spend(cname, asa_camp)
                    if _ok:
                        r["cost"] = _sp
                        loan = r.get("loan") or 0
                        r["cps"] = round(r["cost"] / loan, 2) if loan > 0 and r["cost"] > 0 else None

        # Google：installs=0 置0，其余保留 Adjust 归因消耗
        for r in rows:
            if r["channel"] == "Google Ads":
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
        asa_ch     = fetch_asa_channel_spend(period)      # ★ ASA 真实消耗
        if "Apple" in ch_totals and asa_ch > 0:
            ch_totals["Apple"]["cost"] = asa_ch
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
            asa_ag = fetch_asa_adgroup_spend(period)          # ★ ASA adgroup 细分
            # 以 rows 里注入后的真实 cost 为准，只保留 spend>0 的 campaign 展开 adgroup
            fb_spend_camps = {r["campaign"] for r in rows if r["channel"] == "Facebook"            and (r.get("cost") or 0) > 0}
            tt_spend_camps = {r["campaign"] for r in rows if r["channel"] == "TikTok for Business" and (r.get("cost") or 0) > 0}
            asa_spend_camps = {r["campaign"] for r in rows if r["channel"] == "Apple"              and (r.get("cost") or 0) > 0}
            fb_ag = {c: ag for c, ag in fb_ag.items() if c in fb_spend_camps}
            tt_ag = {c: ag for c, ag in tt_ag.items() if c in tt_spend_camps}
            asa_ag = {c: ag for c, ag in asa_ag.items() if c in asa_spend_camps}
            fb_adj_camps = list(fb_spend_camps)
            tt_adj_camps = list(tt_spend_camps)
            asa_adj_camps = list(asa_spend_camps)
            # ★ 拉取 Adjust adgroup 级转化数据（iOS app_token）
            adj_conv = fetch_adjust_adgroup(period, app_token=IOS_APP_TOKEN)
            adgroups = {
                "Facebook": _match_adgroup_map(fb_ag, fb_adj_camps, adj_conv.get("Facebook")),
                "TikTok for Business": _match_adgroup_map(tt_ag, tt_adj_camps, adj_conv.get("TikTok for Business")),
                "Apple": _match_adgroup_map(asa_ag, asa_adj_camps, adj_conv.get("Apple")),
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
    {"side": "android", "channel": "facebook", "account_name": None, "account_id": "1054117987058016", "fb_act": "act_1054117987058016", "balance_type": "unknown"},
    {"side": "android", "channel": "facebook", "account_name": None, "account_id": "1842012880095946", "fb_act": "act_1842012880095946", "balance_type": "unknown"},
    {"side": "android", "channel": "facebook", "account_name": None, "account_id": "1071912668521082", "fb_act": "act_1071912668521082", "balance_type": "unknown"},
    {"side": "android", "channel": "facebook", "account_name": None, "account_id": "1016349321026924", "fb_act": "act_1016349321026924", "balance_type": "unknown"},
    {"side": "android", "channel": "facebook", "account_name": None, "account_id": "893146393853948",  "fb_act": "act_893146393853948",  "balance_type": "unknown"},
    {"side": "android", "channel": "facebook", "account_name": None, "account_id": "1082060041158190", "fb_act": "act_1082060041158190", "balance_type": "unknown"},
    {"side": "android", "channel": "facebook", "account_name": None, "account_id": "2468093726992507", "fb_act": "act_2468093726992507", "balance_type": "unknown"},
    {"side": "android", "channel": "facebook", "account_name": None, "account_id": "1554822826379992", "fb_act": "act_1554822826379992", "balance_type": "unknown"},
    {"side": "android", "channel": "facebook", "account_name": None, "account_id": "1172024374104199", "fb_act": "act_1172024374104199", "balance_type": "unknown"},
    # ---- Facebook（iOS）----
    {"side": "ios", "channel": "facebook", "account_name": None, "account_id": "826668223504196",  "fb_act": "act_826668223504196",  "balance_type": "unknown"},
    {"side": "ios", "channel": "facebook", "account_name": None, "account_id": "485941130935481",  "fb_act": "act_485941130935481",  "balance_type": "unknown"},
    {"side": "ios", "channel": "facebook", "account_name": None, "account_id": "1050911951210157", "fb_act": "act_1050911951210157", "balance_type": "unknown"},
    {"side": "ios", "channel": "facebook", "account_name": None, "account_id": "2487386801730510", "fb_act": "act_2487386801730510", "balance_type": "unknown"},
    {"side": "ios", "channel": "facebook", "account_name": None, "account_id": "2547112895720531", "fb_act": "act_2547112895720531", "balance_type": "unknown"},
    {"side": "ios", "channel": "facebook", "account_name": None, "account_id": "1032438845801223", "fb_act": "act_1032438845801223", "balance_type": "unknown"},
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


# ── 被拒素材（Rejected Creatives）──────────────────────────
_proxy_cache["rejected"] = None
_proxy_cache["rejected_ts"] = 0


def _fetch_rejected_ads(act_id, side):
    """拉取单个 FB 广告账户下被拒/有问题的广告创意。"""
    out = []
    try:
        r = requests.get(f"{FB_BASE}/{act_id}/ads", timeout=15, params={
            "access_token": get_fb_token(),
            "fields": "name,effective_status,campaign{name},adset{name}",
            "effective_status": '["DISAPPROVED","WITH_ISSUES","PENDING_REVIEW","ADSET_PAUSED","CAMPAIGN_PAUSED","PAUSED","ACTIVE"]',
            "limit": 200,
        })
        if r.status_code != 200:
            return {"error": f"fb http {r.status_code}"}, []
        try:
            data = r.json().get("data", [])
        except Exception:
            return {"error": "fb non-json"}, []
        for ad in data:
            eff = ad.get("effective_status", "")
            if eff in ("DISAPPROVED", "WITH_ISSUES", "PENDING_REVIEW"):
                camp = ad.get("campaign") or {}
                adset = ad.get("adset") or {}
                out.append({
                    "side": side, "channel": "facebook",
                    "account_id": act_id.replace("act_", ""),
                    "ad_name": ad.get("name", ""),
                    "campaign_name": camp.get("name", "") if isinstance(camp, dict) else "",
                    "adset_name": adset.get("name", "") if isinstance(adset, dict) else "",
                    "effective_status": eff,
                })
        return None, out
    except Exception as e:
        return {"error": "fb exception"}, []


def _collect_rejected():
    accounts = []
    total_rejected = 0
    for act_id in FB_ACT_IDS:
        err, ads = _fetch_rejected_ads(act_id, "android")
        accounts.append({"side": "android", "channel": "facebook",
                         "account_id": act_id.replace("act_", ""),
                         "rejected_count": len(ads), "rejected_ads": ads,
                         "source_status": "error" if err else "ok",
                         "source_error": err.get("error") if err else None})
        total_rejected += len(ads)
    for act_id in FB_IOS_ACT_IDS:
        err, ads = _fetch_rejected_ads(act_id, "ios")
        accounts.append({"side": "ios", "channel": "facebook",
                         "account_id": act_id.replace("act_", ""),
                         "rejected_count": len(ads), "rejected_ads": ads,
                         "source_status": "error" if err else "ok",
                         "source_error": err.get("error") if err else None})
        total_rejected += len(ads)
    return total_rejected, accounts


@app.route("/internal/media/rejected-creatives")
def internal_media_rejected():
    auth = _require_api_key()
    if auth:
        return auth
    try:
        now = _time.time()
        if _proxy_cache.get("rejected") and now - _proxy_cache.get("rejected_ts", 0) < _PROXY_CACHE_TTL:
            c = _proxy_cache["rejected"]
            return jsonify({"ok": True, "cached": True, "data_time": c["data_time"],
                            "total_rejected": c["total_rejected"], "accounts": c["accounts"]})
        total_rejected, accounts = _collect_rejected()
        payload = {"data_time": _proxy_now_iso(), "total_rejected": total_rejected, "accounts": accounts}
        _proxy_cache["rejected"] = payload
        _proxy_cache["rejected_ts"] = now
        return jsonify({"ok": True, "cached": False, "data_time": payload["data_time"],
                        "total_rejected": total_rejected, "accounts": accounts})
    except Exception as e:
        return jsonify({"ok": False, "error": "rejected-creatives internal error", "detail": str(e)[:200]}), 200

# ══════════════════════════════════════════════════════════════════
# 内部数据代理接口 END
# ══════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════
# FB 广告账户巡检接口（防新开户/重启户消耗静默漏抓）
# GET /internal/media/fb-accounts   需 X-API-Key 鉴权
#   返回 Token 可见全部账户 + 今日消耗，side 标注 android/ios/UNCONFIGURED
#   missing_with_spend = 有消耗但未纳入任何看板的账户（需人工判断归属后加入配置）
# ══════════════════════════════════════════════════════════════════
@app.route("/internal/media/fb-accounts")
def internal_media_fb_accounts():
    auth = _require_api_key()
    if auth:
        return auth
    token  = get_fb_token()
    since, until = fb_date_range("today")
    configured = set(FB_ACT_IDS) | set(FB_IOS_ACT_IDS)
    out = {"since": since, "until": until,
           "configured_android": len(FB_ACT_IDS),
           "configured_ios": len(FB_IOS_ACT_IDS),
           "configured_count": len(configured),
           "visible": [], "missing_with_spend": [], "error": None}
    try:
        url = f"{FB_BASE}/me/adaccounts"
        params = {"access_token": token, "limit": 200,
                  "fields": "account_id,name,account_status,currency"}
        while url:
            r = requests.get(url, timeout=40, params=params)
            j = r.json()
            if "error" in j:
                out["error"] = str(j["error"])[:300]
                break
            for a in j.get("data", []):
                act = "act_" + str(a.get("account_id"))
                spend = None
                try:
                    ri = requests.get(f"{FB_BASE}/{act}/insights", timeout=30, params={
                        "access_token": token, "fields": "spend", "level": "account",
                        "time_range": _json.dumps({"since": since, "until": until})})
                    dd = ri.json().get("data", [])
                    spend = float(dd[0].get("spend", 0)) if dd else 0.0
                except Exception:
                    spend = None
                item = {"act_id": act, "name": a.get("name"),
                        "status": a.get("account_status"),
                        "currency": a.get("currency"),
                        "today_spend": spend,
                        "side": ("android" if act in set(FB_ACT_IDS)
                                 else "ios" if act in set(FB_IOS_ACT_IDS)
                                 else "UNCONFIGURED"),
                        "in_config": act in configured}
                out["visible"].append(item)
                if (not item["in_config"]) and spend and spend > 0:
                    out["missing_with_spend"].append(item)
            nxt = (j.get("paging") or {}).get("next")
            url, params = (nxt, None) if nxt else (None, None)
    except Exception as e:
        out["error"] = str(e)[:300]
    out["visible_count"] = len(out["visible"])
    out["missing_count"]  = len(out["missing_with_spend"])
    return jsonify(out)




def compute_full_channel_total(period):
    """全渠道汇总（60 秒缓存包装）"""
    return _full_total_cached(_compute_full_channel_total_raw, "and", period)


def compute_full_ios_channel_total(period):
    """iOS 全渠道汇总（60 秒缓存包装）"""
    return _full_total_cached(_compute_full_ios_channel_total_raw, "ios", period)


# 启动后台快照预拉（gunicorn 导入模块时即生效）
try:
    snapshot_start()
except Exception:
    pass


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5050)
    args = parser.parse_args()
    print(f"✅ 启动成功 → http://localhost:{args.port}")
    print(f"   Channel 看板: http://localhost:{args.port}/")
    print(f"   Campaign 看板: http://localhost:{args.port}/campaign")
    app.run(host="0.0.0.0", port=args.port, debug=False)
