"""Pesoloan双端素材表现看板模块。
Usage: from creative_dashboard_module import register_creative_dashboard; register_creative_dashboard(app)
参数全部从Render Environment读取；本模块不保存任何凭证。
Example: python creative_dashboard_app.py --port 5050
"""
import os, json, time, calendar, re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import requests
from flask import jsonify, request, Response

FB_BASE='https://graph.facebook.com/v19.0'
TT_BASE='https://business-api.tiktok.com/open_api/v1.3'
GG_VER='v24'
ADJ_URL='https://automate.adjust.com/reports-service/report'
CACHE={'data':{},'ts':{}}
TTL=300

def now8(): return datetime.now(timezone(timedelta(hours=8)))
def num(v):
 try:return float(v or 0)
 except:return 0.0
def norm(v): return re.sub(r'[^a-z0-9]+','',str(v or '').lower())
def month_range(month):
 if not re.fullmatch(r'\d{4}-\d{2}',month or ''): month=now8().strftime('%Y-%m')
 y,m=map(int,month.split('-')); last=calendar.monthrange(y,m)[1]
 start=f'{y:04d}-{m:02d}-01'; end=f'{y:04d}-{m:02d}-{last:02d}'
 if month==now8().strftime('%Y-%m'): end=now8().strftime('%Y-%m-%d')
 return start,end

def env_list(name): return [x.strip() for x in os.getenv(name,'').split(',') if x.strip()]
FB_ANDROID_DEFAULT=['2043458276522117','1338744840870824','554870820824463','1763443588125609','4425161567801548','3511882642320376','1654205562363513','1054117987058016','1842012880095946','1071912668521082','1016349321026924','893146393853948','1082060041158190','2468093726992507','1554822826379992']
FB_IOS_DEFAULT=['826668223504196','485941130935481','1050911951210157','2487386801730510']
def config():
 return {'fb_android':env_list('CREATIVE_FB_ANDROID_IDS') or env_list('FB_ACT_IDS') or FB_ANDROID_DEFAULT,'fb_ios':env_list('CREATIVE_FB_IOS_IDS') or env_list('FB_IOS_ACT_IDS') or FB_IOS_DEFAULT,'tt_android':os.getenv('TT_ADV_ID',''),'tt_ios':os.getenv('TT_IOS_ADV_ID',''),'gg_android':env_list('GG_CUSTOMER_IDS') or ['3375325268','4223410058']}

def base_row(side,channel,account_id,cid,name,day):
 return {'side':side,'channel':channel,'account_id':str(account_id),'creative_id':str(cid),'creative_name':name or str(cid),'day':day,'format':'unknown','spend':0.0,'impressions':0,'clicks':0,'loans':0,'preview_url':None,'media_url':None,'player_type':None,'download_url':None,'created_time':None,'source_status':'ok','attribution_status':'unmatched'}

def fb_rows(side,acts,start,end):
 out=[]; token=os.getenv('FB_LONG_TOKEN','')
 for act in acts:
  aid=str(act).replace('act_','')
  try:
   r=requests.get(f'{FB_BASE}/act_{aid}/insights',timeout=45,params={'access_token':token,'level':'ad','fields':'ad_id,ad_name,spend,impressions,clicks,date_start','time_increment':1,'time_range':json.dumps({'since':start,'until':end}),'limit':5000})
   if r.status_code!=200: raise RuntimeError(f'HTTP {r.status_code}')
   rows=r.json().get('data',[]); adids=list({str(x.get('ad_id')) for x in rows if x.get('ad_id')})
   meta={}
   for i in range(0,len(adids),50):
    q=requests.get(FB_BASE,timeout=45,params={'access_token':token,'ids':','.join(adids[i:i+50]),'fields':'name,created_time,creative{id,name,thumbnail_url,image_url,object_type,video_id}'})
    if q.status_code==200: meta.update(q.json())
   video_meta={}
   for c in [((v or {}).get('creative') or {}) for v in meta.values()]:
    vid=str(c.get('video_id') or '')
    if vid and vid not in video_meta:
     vr=requests.get(f'{FB_BASE}/{vid}',timeout=30,params={'access_token':token,'fields':'source,picture,permalink_url'}); video_meta[vid]=vr.json() if vr.status_code==200 else {}
   for x in rows:
    adid=str(x.get('ad_id','')); m=meta.get(adid,{}) or {}; c=m.get('creative') or {}; cid=str(c.get('id') or adid); vid=str(c.get('video_id') or ''); vm=video_meta.get(vid,{})
    z=base_row(side,'facebook',aid,cid,c.get('name') or x.get('ad_name'),x.get('date_start')); typ=str(c.get('object_type','')).upper(); is_video=bool(vid or 'VIDEO' in typ)
    z.update(spend=round(num(x.get('spend')),2),impressions=int(num(x.get('impressions'))),clicks=int(num(x.get('clicks'))),format='video' if is_video else 'image',preview_url=c.get('thumbnail_url') or vm.get('picture') or c.get('image_url'),media_url=vm.get('source') if is_video else c.get('image_url'),player_type='video' if is_video else 'image',download_url=vm.get('source') if is_video else c.get('image_url'),created_time=m.get('created_time'),ad_id=adid); out.append(z)
  except Exception as e: out.append({'side':side,'channel':'facebook','account_id':aid,'source_status':'error','source_error':str(e)[:120]})
 return out

def tt_rows(side,adv,start,end):
 if not adv:return []
 out=[]; token=os.getenv('TT_ACCESS_TOKEN',''); headers={'Access-Token':token}
 try:
  r=requests.get(f'{TT_BASE}/report/integrated/get/',headers=headers,timeout=45,params={'advertiser_id':adv,'report_type':'BASIC','data_level':'AUCTION_AD','dimensions':json.dumps(['ad_id','stat_time_day']),'metrics':json.dumps(['ad_name','spend','impressions','clicks']),'start_date':start,'end_date':end,'page_size':1000})
  d=r.json()
  if d.get('code')!=0: raise RuntimeError(f"code {d.get('code')}: {d.get('message','')}")
  rows=(d.get('data') or {}).get('list',[]); ids=list({str((x.get('dimensions') or {}).get('ad_id')) for x in rows})
  meta={}
  for i in range(0,len(ids),100):
   q=requests.get(f'{TT_BASE}/ad/get/',headers=headers,timeout=45,params={'advertiser_id':adv,'filtering':json.dumps({'ad_ids':ids[i:i+100]}),'fields':json.dumps(['ad_id','ad_name','create_time','image_ids','video_id']),'page_size':100})
   if q.json().get('code')==0:
    for a in (q.json().get('data') or {}).get('list',[]): meta[str(a.get('ad_id'))]=a
  video_ids=list({str(a.get('video_id')) for a in meta.values() if a.get('video_id')}); video_meta={}
  for i in range(0,len(video_ids),100):
   vq=requests.get(f'{TT_BASE}/file/video/ad/info/',headers=headers,timeout=45,params={'advertiser_id':adv,'video_ids':json.dumps(video_ids[i:i+100])}); vd=vq.json()
   if vd.get('code')==0:
    for v in (vd.get('data') or {}).get('list',[]): video_meta[str(v.get('video_id'))]=v
  for x in rows:
   dim=x.get('dimensions') or {}; m=x.get('metrics') or {}; cid=str(dim.get('ad_id','')); a=meta.get(cid,{}); vid=str(a.get('video_id') or ''); vm=video_meta.get(vid,{}); images=a.get('image_ids') or []; is_video=bool(vid)
   z=base_row(side,'tiktok',adv,cid,a.get('ad_name') or m.get('ad_name'),str(dim.get('stat_time_day',''))[:10]); z.update(spend=round(num(m.get('spend')),2),impressions=int(num(m.get('impressions'))),clicks=int(num(m.get('clicks'))),format='video' if is_video else 'image',preview_url=vm.get('video_cover_url'),media_url=vm.get('play_url') or vm.get('video_url'),player_type='video' if is_video else 'image',download_url=vm.get('play_url') or vm.get('video_url'),created_time=a.get('create_time'),image_ids=images); out.append(z)
 except Exception as e: out.append({'side':side,'channel':'tiktok','account_id':adv,'source_status':'error','source_error':str(e)[:120]})
 return out

def gg_token():
 try:
  r=requests.post('https://oauth2.googleapis.com/token',timeout=20,data={'client_id':os.getenv('GG_CLIENT_ID',''),'client_secret':os.getenv('GG_CLIENT_SECRET',''),'refresh_token':os.getenv('GG_REFRESH_TOKEN',''),'grant_type':'refresh_token'}); return r.json().get('access_token','')
 except:return ''
def gg_rows(customers,start,end):
 out=[]; token=gg_token(); headers={'Authorization':f'Bearer {token}','developer-token':os.getenv('GG_DEVELOPER_TOKEN',''),'login-customer-id':os.getenv('GG_MCC_ID',''),'Content-Type':'application/json'}
 q=f"SELECT customer.id, ad_group_ad.ad.id, asset.id, asset.name, asset.type, asset.image_asset.full_size.url, asset.youtube_video_asset.youtube_video_id, segments.date, metrics.cost_micros, metrics.impressions, metrics.clicks FROM ad_group_ad_asset_view WHERE segments.date BETWEEN '{start}' AND '{end}' AND metrics.cost_micros > 0"
 for cid in customers:
  try:
   r=requests.post(f'https://googleads.googleapis.com/{GG_VER}/customers/{cid}/googleAds:searchStream',headers=headers,json={'query':q},timeout=50)
   if r.status_code!=200: raise RuntimeError(f'HTTP {r.status_code}')
   batches=r.json() if isinstance(r.json(),list) else [r.json()]
   for b in batches:
    for x in b.get('results',[]):
     ad=x.get('adGroupAd',{}).get('ad',{}); asset=x.get('asset',{}); met=x.get('metrics',{}); seg=x.get('segments',{}); aid=str(ad.get('id','')); cid2=str(asset.get('id') or aid); typ=str(asset.get('type','')).upper()
     image=((asset.get('imageAsset') or {}).get('fullSize') or {}).get('url'); video=(asset.get('youtubeVideoAsset') or {}).get('youtubeVideoId'); preview=image or (f'https://i.ytimg.com/vi/{video}/hqdefault.jpg' if video else None)
     z=base_row('android','google',cid,cid2,asset.get('name') or cid2,seg.get('date')); z.update(spend=round(num(met.get('costMicros'))/1e6,2),impressions=int(num(met.get('impressions'))),clicks=int(num(met.get('clicks'))),format='video' if video or 'VIDEO' in typ else 'image',preview_url=preview,media_url=f'https://www.youtube.com/embed/{video}?autoplay=1&rel=0' if video else image,player_type='youtube' if video else 'image',download_url=image,ad_id=aid); out.append(z)
  except Exception as e: out.append({'side':'android','channel':'google','account_id':cid,'source_status':'error','source_error':str(e)[:120]})
 return out

def adjust_rows(side,start,end):
 token=os.getenv('ADJUST_APP_TOKEN' if side=='android' else 'IOS_ADJUST_APP_TOKEN',''); user=os.getenv('ADJUST_USER_TOKEN','')
 if not token or not user:return [],'missing adjust credentials'
 try:
  params={'app_token__in':token,'date_period':f'{start}:{end}','dimensions':'channel,campaign_network,adgroup_network,creative_network,day','metrics':'attribution_clicks,loan_success_events','utc_offset':'+08:00','attribution_source':'first','reattributed':'all','full_data':'true'}
  r=requests.get(ADJ_URL,headers={'Authorization':f'Bearer {user}'},params=params,timeout=60); r.raise_for_status(); return r.json().get('rows',[]),None
 except Exception as e:return [],str(e)[:120]

def channel_key(v):
 s=str(v or '').lower()
 if 'facebook' in s or 'meta' in s:return 'facebook'
 if 'google' in s:return 'google'
 if 'tiktok' in s:return 'tiktok'
 return s

def merge_adjust(rows,adj):
 idx=defaultdict(lambda:{'clicks':0,'loans':0})
 for a in adj:
  key=(channel_key(a.get('channel')),norm(a.get('creative_network')),str(a.get('day',''))[:10]); idx[key]['clicks']+=int(num(a.get('attribution_clicks'))); idx[key]['loans']+=int(num(a.get('loan_success_events')))
 for x in rows:
  if x.get('source_status')!='ok':continue
  k=(x['channel'],norm(x.get('creative_name')),x['day']); a=idx.get(k)
  if a:
   x['attribution_clicks']=a['clicks']; x['loans']=a['loans']; x['attribution_status']='matched'
 return rows

def aggregate(rows,month):
 good=[x for x in rows if x.get('source_status')=='ok']; errors=[x for x in rows if x.get('source_status')!='ok']; groups={}
 for x in good:
  k=(x['side'],x['channel'],x['account_id'],x['creative_id']); g=groups.setdefault(k,{z:x.get(z) for z in ['side','channel','account_id','creative_id','creative_name','format','preview_url','media_url','player_type','download_url','created_time','attribution_status']}); g.setdefault('daily',{}); d=g['daily'].setdefault(x['day'],{'spend':0,'impressions':0,'clicks':0,'loans':0,'attribution_clicks':0});
  for f in d:d[f]+=num(x.get(f))
 out=[]
 for g in groups.values():
  ds=g['daily']; md={d:v for d,v in ds.items() if d.startswith(month)}; g['spend']=round(sum(v['spend'] for v in md.values()),2); g['impressions']=int(sum(v['impressions'] for v in md.values())); g['clicks']=int(sum(v['clicks'] for v in md.values())); g['loans']=int(sum(v['loans'] for v in md.values())); ac=int(sum(v['attribution_clicks'] for v in md.values())); g['ctr']=round(g['clicks']/g['impressions']*100,2) if g['impressions'] else None; g['cr']=round(g['loans']/ac*100,2) if ac else None; g['cps']=round(g['spend']/g['loans'],2) if g['loans'] else None; g['first_spend_date']=min(ds) if ds else None; g['is_new']=bool(g['first_spend_date'] and g['first_spend_date'].startswith(month)); out.append(g)
 rank=defaultdict(list)
 for g in out:rank[(g['side'],g['channel'],g['format'])].append(g)
 for arr in rank.values():
  for i,g in enumerate(sorted(arr,key=lambda x:-x['spend']),1):g['month_rank']=i
 _,month_end=month_range(month); end_date=datetime.strptime(month_end,'%Y-%m-%d').date(); anchor=now8().date() if month==now8().strftime('%Y-%m') else end_date+timedelta(days=1); last=[(anchor-timedelta(days=i)).isoformat() for i in range(1,4)]; prev=[(anchor-timedelta(days=i)).isoformat() for i in range(4,7)]
 for g in out:
  a=sum(g['daily'].get(d,{}).get('spend',0) for d in last); b=sum(g['daily'].get(d,{}).get('spend',0) for d in prev); growth=((a/3)/(b/3)-1)*100 if b>0 else (999 if a>0 else 0); g['recent3_spend']=round(a,2); g['previous3_spend']=round(b,2); g['spend_growth_pct']=round(growth,2); g['is_surge']=growth>=50 and a>=50 and g.get('month_rank',999)<=10
 out.sort(key=lambda x:-x['spend']); return out,errors

def collect(month):
 start,end=month_range(month); pool=os.getenv('CREATIVE_POOL_START','2026-06-01'); cfg=config(); rows=[]
 rows+=fb_rows('android',cfg['fb_android'],pool,end); rows+=fb_rows('ios',cfg['fb_ios'],pool,end); rows+=tt_rows('android',cfg['tt_android'],pool,end); rows+=tt_rows('ios',cfg['tt_ios'],pool,end); rows+=gg_rows(cfg['gg_android'],pool,end)
 aa,ae=adjust_rows('android',pool,end); ia,ie=adjust_rows('ios',pool,end); rows=merge_adjust([x for x in rows if x.get('side')=='android'],aa)+merge_adjust([x for x in rows if x.get('side')=='ios'],ia)
 items,errors=aggregate(rows,month); errors += ([{'side':'android','channel':'adjust','source_status':'error','source_error':ae}] if ae else []) + ([{'side':'ios','channel':'adjust','source_status':'error','source_error':ie}] if ie else [])
 trend=defaultdict(lambda:{'spend':0,'impressions':0,'clicks':0,'loans':0})
 for x in items:
  for d,v in x['daily'].items():
   if d.startswith(month):
    for f in trend[d]:trend[d][f]+=v.get(f,0)
 summary={'creatives':len([x for x in items if x['spend']>0]),'new_creatives':sum(x['is_new'] and x['spend']>0 for x in items),'surge_creatives':sum(x['is_surge'] for x in items),'spend':round(sum(x['spend'] for x in items),2),'loans':sum(x['loans'] for x in items),'source_errors':len(errors)}; summary['cps']=round(summary['spend']/summary['loans'],2) if summary['loans'] else None
 return {'ok':True,'month':month,'range':{'start':start,'end':end,'pool_start':pool},'currency':'USD','data_time':now8().isoformat(),'summary':summary,'trend':dict(sorted(trend.items())),'items':items,'errors':errors}

def register_creative_dashboard(app):
 @app.route('/creative-dashboard')
 def creative_page():
  path=os.path.join(os.path.dirname(__file__),'creative_dashboard.html'); return Response(open(path,encoding='utf-8').read(),mimetype='text/html')
 @app.route('/dashboard-api/creative-performance')
 def creative_api():
  month=request.args.get('month') or now8().strftime('%Y-%m'); key=month; t=time.time()
  if key in CACHE['data'] and t-CACHE['ts'].get(key,0)<TTL:return jsonify({**CACHE['data'][key],'cached':True})
  p=collect(month); CACHE['data'][key]=p; CACHE['ts'][key]=t; return jsonify({**p,'cached':False})
