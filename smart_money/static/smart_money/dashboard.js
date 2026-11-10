(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (v) => String(v ?? "-").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const num = (v, digits=2) => v == null ? "-" : Number(v).toLocaleString("en-US", {maximumFractionDigits:digits});
  const money = v => v == null ? "-" : "$" + num(v);
  const compact = v => v == null ? "-" : new Intl.NumberFormat("en-US", {style:"currency",currency:"USD",notation:"compact",maximumFractionDigits:1}).format(Number(v));
  const stamp = v => v ? new Date(v).toLocaleString("en-US", {timeZone:"America/Los_Angeles",month:"short",day:"numeric",hour:"2-digit",minute:"2-digit",second:"2-digit",timeZoneName:"short"}) : "-";
  const split = v => String(v).split(/[\s,]+/).filter(Boolean);
  const formField = (form, name) => form.elements.namedItem(name);
  const titles = {overview:"Overview",fundamentals:"Fundamental Analysis",flow:"Live Options Flow",scanner:"Unusual Activity Scanner",biggest:"Biggest Transactions",analysis:"CALL vs PUT Analysis",darkpool:"Dark Pool & Block Trades",holdings:"Institutional Holdings",insiders:"Insider Transactions",history:"Historical Activity",settings:"Alerts & Settings"};
  const state = {view:"overview", status:null, filters:{min_premium:50000}, cursor:null, next:null, pages:[], secPage:1, filing:"", group:"type", rules:[], request:0};
  const filtersForm = $("#filters");
  const today = new Intl.DateTimeFormat("en-CA", {timeZone:"America/New_York",year:"numeric",month:"2-digit",day:"2-digit"}).format(new Date());
  formField(filtersForm,"from").value=today;
  formField(filtersForm,"to").value=today;
  async function api(path, data, method="POST") {
    const opts = {credentials:"same-origin",headers:{"Accept":"application/json"}};
    if (data !== undefined) {
      opts.method=method; opts.body=JSON.stringify(data);
      opts.headers["Content-Type"]="application/json";
      opts.headers["X-CSRFToken"]=document.cookie.split("; ").find(v=>v.startsWith("csrftoken="))?.split("=").slice(1).join("=") || "";
    }
    const response=await fetch("/api/smart-money/"+path,opts);
    const result=await response.json().catch(()=>({error:"Server unavailable. Retry in a moment."}));
    if(!response.ok) throw Error(result.error || "Request failed.");
    return result;
  }
  function error(err) { $("#error").textContent=err.message; $("#error").hidden=false; }
  function toast(text) {$("#toast").textContent=text; $("#toast").hidden=false; clearTimeout(state.toast); state.toast=setTimeout(()=>$("#toast").hidden=true,3500);}
  function empty(title, text="") {return `<div class="sm-empty"><strong>${esc(title)}</strong><p>${esc(text)}</p></div>`;}
  function head(title,note="") {return `<div class="sm-section-head"><h2>${esc(title)}</h2><span>${esc(note)}</span></div>`;}
  function table(headers,rows,emptyTitle="No records yet",emptyText="") {
    return `<div class="sm-table-wrap"><table><thead><tr>${headers.map(h=>`<th scope="col">${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`+(rows.length?"":empty(emptyTitle,emptyText));
  }
  function cells(values) {return `<tr>${values.map(v=>`<td>${v}</td>`).join("")}</tr>`;}
  function facts(items) {return `<dl class="sm-facts">${Object.entries(items).map(([k,v])=>`<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl>`;}
  function readFilters(form) {
    const data={};
    for (const name of ["symbols","min_premium","option_type","min_quantity","min_score","min_repeats","strike","expiry_from","expiry_to","classification","structure","min_vol_oi","confirmation"]) {
      const element=formField(form,name);
      if (element && element.value !== "") data[name]=name==="symbols"?split(element.value):element.value;
    }
    return data;
  }
  function fillFilters(filters) {
    for(const element of filtersForm.elements) {
      if(!element.name || ["from","to"].includes(element.name)) continue;
      element.value=element.name==="symbols"?(filters.symbols||[]).join(", "):(filters[element.name] ?? (element.type==="number" && !["strike","min_vol_oi"].includes(element.name)?0:""));
    }
    if(!formField(filtersForm,"min_premium").value) formField(filtersForm,"min_premium").value="50000";
  }
  function query(cursor) {
    const q=new URLSearchParams({filters:JSON.stringify(state.filters),from:formField(filtersForm,"from").value,to:formField(filtersForm,"to").value,sort:state.view==="biggest"?"premium":state.view==="scanner"?"score":"time"});
    if(cursor) q.set("cursor",cursor);
    q.set("group",state.group);
    return q;
  }
  function paintStatus(s) {
    state.status=s;
    $("#provider-name").textContent=s.provider.name;
    $("#provider-mode").textContent=s.provider.ready?s.provider.mode:"Not connected";
    const stale=!s.worker.heartbeat || new Date(s.server_time)-new Date(s.worker.heartbeat)>300000;
    const label=!s.can_view?"Market-data sharing restricted":!s.provider.ready?s.provider.reason:s.collection.enabled?(stale?"Collector heartbeat unavailable or stale":s.provider_status):"Collection paused";
    $("#connection").innerHTML=`<strong>${esc(label)}</strong><small>${esc(s.provider.capabilities.coverage || "Verified options transaction feed required. No synthetic transactions.")}${s.provider.ready?" | "+esc(s.provider.mode):""}</small>`;
    $("#freshness").textContent=s.latest_received_at?`Latest print: ${stamp(s.latest_trade_at)} | Received: ${stamp(s.latest_received_at)}`:"No verified transactions received";
    $("#clock").textContent=stamp(s.server_time);
    const caps=s.provider.capabilities;
    for(const val of ["ASK_SIDE","BID_SIDE"]) formField(filtersForm,"classification").querySelector(`[value="${val}"]`).disabled=!caps.execution_quotes;
    for(const val of ["SWEEP","BLOCK"]) formField(filtersForm,"structure").querySelector(`[value="${val}"]`).disabled=!caps[val==="SWEEP"?"sweeps":"blocks"];
    formField(filtersForm,"min_vol_oi").disabled=!caps.historical_oi;
    formField(filtersForm,"confirmation").disabled=!caps.execution_quotes;
  }
  function metrics(summary) {
    $("#metrics").innerHTML=[["Observed CALL premium",compact(summary.call),"sm-call"],["Observed PUT premium",compact(summary.put),"sm-put"],["Transactions",num(summary.count,0),""],["Largest print",compact(summary.largest),""],["Unknown direction",compact(summary.unknown),""]].map(([k,v,c])=>`<div class="sm-metric"><span>${esc(k)}</span><strong class="${c}">${esc(v)}</strong></div>`).join("");
  }
  function rankingBars(rows) {
    const max=Math.max(1,...rows.map(r=>Number(r.total)));
    return rows.length?`<div class="sm-plot">${rows.slice(0,8).map(r=>`<button class="sm-bar" data-ticker="${esc(r.ticker)}" title="${esc(r.ticker)} activity"><span>${esc(r.ticker)}</span><span class="sm-bar-track"><i style="width:${Number(r.call||0)/max*100}%"></i><i class="sm-segment-put" style="width:${Number(r.put||0)/max*100}%"></i></span><b>${esc(compact(r.total))}</b></button>`).join("")}</div><div class="sm-legend"><span>CALL premium</span><span>PUT premium</span></div>`:empty("No premium distribution", "Waiting for verified transactions in this date range.");
  }
  function heatmap(rows,key) {
    return rows.length?`<div class="sm-heat">${rows.map(r=>`<button data-heat="${esc(key)}" data-value="${esc(r.label)}"><strong>${esc(r.label)}</strong><span>${esc(compact(r.total))}</span><small>${num(r.count,0)} prints</small></button>`).join("")}</div>`:empty("No concentration data");
  }
  function flowTable(data) {
    state.next=data.next;
    return head("Transaction tape",`${num(data.count,0)} matching prints / observed sample`)+table(["Ticker","Type","Premium","Strike / expiry","Contracts","Execution","Classification","Time (Pacific)"],data.results.map(e=>cells([
      `<button class="sm-link" data-detail="${e.id}">${esc(e.ticker)}</button>`, `<span class="${e.option_type==="CALL"?"sm-call":"sm-put"}">${esc(e.option_type)}</span>`,esc(money(e.premium)),`${esc(money(e.strike))} / ${esc(e.expiration)}`,esc(num(e.quantity,0)),esc(money(e.price)),`<span class="sm-tag">${esc(e.classification)}</span>`,esc(stamp(e.trade_at))
    ])),"No matching transactions",state.status?.provider.ready?"No verified prints meet these filters. Collection is watchlist-limited; an empty result is not proof of no market activity.":"Connect an entitled transaction feed to collect real prints. Option-chain snapshots are not treated as executed trades.")+
      `<div class="sm-pager"><button id="prev-page" ${state.pages.length?"":"disabled"}>Previous</button><span class="sm-muted">Page ${state.pages.length+1}</span><button id="next-page" ${data.next?"":"disabled"}>Next</button></div>`;
  }
  function rankings(data) {
    return head("Stock activity ranking","Observed premium, not institutional ownership")+table(["Ticker","CALL premium","PUT premium","Unknown intent","Net estimated direction","Prints","Repeated prints","Largest"],data.rankings.map(r=>cells([`<button class="sm-link" data-ticker="${esc(r.ticker)}">${esc(r.ticker)}</button>`,esc(compact(r.call)),esc(compact(r.put)),esc(compact(r.unknown)),esc(compact(r.net_directional)),num(r.count,0),num(r.repeats,0),esc(compact(r.largest))])),"No ranked activity");
  }
  function historical(data) {
    return `<div class="sm-section-head"><h2>Underlying returns after activity</h2><select id="history-group" aria-label="Group outcomes">${[["type","CALL / PUT"],["premium","Premium category"],["structure","Structure"]].map(([v,t])=>`<option value="${v}" ${state.group===v?"selected":""}>${t}</option>`).join("")}</select></div>`+table(["Horizon","Category","Observations","Avg. return","Avg. favorable","Avg. adverse","Directional accuracy"],data.history.map(r=>cells([esc(r.horizon),esc(r.category),num(r.count,0),num(r.average_return)+"%",r.favorable==null?"-":num(r.favorable)+"%",r.adverse==null?"-":num(r.adverse)+"%",r.classified?`${num(r.correct/r.classified*100)}% (${num(r.classified,0)})`:"Unknown direction"])),"No mature observations", "Outcomes appear only after the target time and an eligible underlying bar are available. Unclassified flow is excluded from directional accuracy.")+`<p class="sm-note">Historical reconstruction uses the last completed pre-trade close as baseline, not the exact execution-time stock price. Raw-price returns are not corporate-action adjusted. Missing or out-of-session bars are not interpolated. These are underlying returns, not option P&amp;L.</p>`;
  }
  async function renderFilings() {
    const kind=state.view==="insiders"?"insiders":"holdings";
    const data=await api(`filings/?kind=${kind}&page=${state.secPage}&filing=${encodeURIComponent(state.filing)}`);
    const selector=`<div class="sm-section-head"><h2>${kind==="insiders"?"Form 4 disclosures":"13F holdings"}</h2><select id="filing-select" aria-label="Filing"><option value="">All imported filings</option>${data.filings.map(f=>`<option value="${f.id}" ${String(f.id)===state.filing?"selected":""}>${esc(f.filer)} / ${esc(f.form)} / ${esc(f.report_date||"-")}</option>`).join("")}</select></div>`;
    const filingLink=r=>`<a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">${esc(r.form)}</a>`;
    const rows=data.results.map(r=>kind==="insiders"?cells([esc(r.ticker),esc(r.owner_name),esc(r.code==="P"?"P / purchase":r.code==="S"?"S / sale":r.code+" / other"),num(r.quantity),money(r.price),esc(r.transaction_date),esc(stamp(r.filed_at)),filingLink(r)]):cells([esc(r.issuer),esc(r.cusip),esc(r.filer),esc(r.security_class),esc(r.put_call||"Shares"),num(r.shares),`${num(r.reported_value)} (${esc(r.value_unit)})`,esc(r.report_date),esc(stamp(r.filed_at)),filingLink(r)]));
    const changes=kind==="holdings"&&state.filing?head("Reported position changes",data.changes.status)+table(["CUSIP","Class","Type","Prior","Current","Change","Status"],data.changes.rows.map(r=>cells([esc(r.cusip),esc(r.security_class),esc(r.put_call||"Shares"),num(r.previous),num(r.current),num(r.change),esc(r.status)])),"Comparison unavailable",data.changes.status):"";
    return selector+table(kind==="insiders"?["Ticker","Reporting owner","Code","Quantity","Price","Transaction date","Filed","Source"]:["Issuer","CUSIP","Filer","Class","Type","Shares / principal","Reported value","Reporting period","Filed","Source"],rows,"No imported disclosures",state.status?.collection.sec_status||"SEC collection is paused.")+changes+`<div class="sm-pager"><button id="sec-prev" ${state.secPage>1?"":"disabled"}>Previous</button><span>${num(data.count,0)} records</span><button id="sec-next" ${Math.max(data.count,data.changes?.count||0)>state.secPage*50?"":"disabled"}>Next</button></div><p class="sm-note">Disclosures are not live transactions. Amendments are displayed separately, not merged into original holdings. Filing values retain their reported units; derivative positions and insider award codes are not relabeled as stock purchases.</p>`;
  }
  async function settingsView(fill=true) {
    const s=state.status, form=$("#collection-form");
    if(fill) for(const key of ["enabled","symbols","min_premium","sec_enabled","sec_ciks"]) {
      const field=formField(form,key),value=s.collection[key];
      if(field.type==="checkbox") field.checked=value; else field.value=Array.isArray(value)?value.join(", "):value;
    }
    $("#capabilities").innerHTML=facts({"Feed":s.provider.name,"Mode":s.provider.mode,"Storage permission":s.provider.storage_allowed?"Confirmed":"Not confirmed","Shared data permission":s.provider.sharing_allowed?"Confirmed":"Not confirmed","Discord":s.discord_ready?"Ready":"Not configured / permission required","SEC identity":s.sec_ready?"Configured":"Not configured","Execution-time quotes":"Unavailable in this adapter","Sweep / block labels":"Unavailable in this adapter","Greeks / open interest":"Unavailable at execution time"});
    $("#cursor-list").innerHTML=table(["Ticker","Last check","Coverage / status"],s.cursors.map(c=>cells([esc(c.symbol),esc(stamp(c.checked_at)),esc(c.status)])),"Collector paused",s.worker.status);
    const a=await api("alerts/"); state.rules=a.rules;
    $("#alert-list").innerHTML=table(["Rule","Tickers","Premium","Status","Actions"],a.rules.map(r=>cells([esc(r.name),esc(r.filters.symbols.join(", ")||"All collected"),money(r.filters.min_premium),r.enabled?"Enabled":"Paused",`<button data-edit="${r.id}">Edit</button> <button data-toggle="${r.id}">${r.enabled?"Pause":"Enable"}</button> <button data-delete="${r.id}">Delete</button>`])),"No alert rules");
    $("#delivery-list").innerHTML=table(["Rule","Status","Created","Sent","Detail"],a.deliveries.map(d=>cells([esc(d.configuration__name),esc(d.status),esc(stamp(d.created_at)),esc(stamp(d.sent_at)),esc(d.error||"-")])),"No deliveries");
  }
  async function render(reset=true) {
    const version=++state.request;
    $("#error").hidden=true;
    $("#view-title").textContent=titles[state.view];
    for(const button of document.querySelectorAll("[data-view]")) {if(button.dataset.view===state.view)button.setAttribute("aria-current","page");else button.removeAttribute("aria-current");}
    window.SmartMoneyFundamentals.unmount();
    const isFundamentals=state.view==="fundamentals";
    $("#connection").hidden=isFundamentals;
    $("#freshness").hidden=isFundamentals;
    $("#context-note").textContent=isFundamentals?"Company fundamentals are periodic reports, not live trading signals.":"Premium is not bullish conviction. Unknown intent stays unknown.";
    $(".sm-eyebrow").textContent=isFundamentals?"COMPANY RESEARCH":"OPTIONS INTELLIGENCE";
    const special=["settings","holdings","insiders","darkpool","fundamentals"].includes(state.view);
    $("#filters").hidden=special; $("#metrics").hidden=special; $("#settings").hidden=state.view!=="settings"; $("#content").hidden=state.view==="settings";
    if(reset){state.cursor=null;state.pages=[];}
    $("#refresh").disabled=true;
    try {
      if(isFundamentals){await window.SmartMoneyFundamentals.mount($("#content"));return;}
      const status=await api("status/"); if(version!==state.request)return; paintStatus(status);
      if(state.view==="settings") {await settingsView();return;}
      if(state.view==="darkpool") {$("#content").innerHTML=empty("Off-exchange feed not connected","The selected adapter does not provide verified dark-pool prints or block classifications. No accumulation or buyer identity is inferred from missing data.");return;}
      if(["holdings","insiders"].includes(state.view)){const html=await renderFilings();if(version===state.request)$("#content").innerHTML=html;return;}
      if(!status.can_view){$("#metrics").innerHTML="";$("#content").innerHTML=empty("Shared data permission required","Market-data redistribution has not been enabled for this server.");return;}
      const analytics=await api("analytics/?"+query()); if(version!==state.request)return; metrics(analytics.summary);
      let html="";
      if(state.view==="history") html=historical(analytics);
      else if(state.view==="analysis") html=`<div class="sm-visuals"><section><h3>Premium by ticker</h3>${rankingBars(analytics.rankings)}</section><section><h3>CALL / PUT premium</h3>${heatmap(analytics.heatmaps.type,"option_type")}<p class="sm-note">Contract type does not establish trade direction.</p></section></div><div class="sm-visuals"><section><h3>Strike concentration</h3>${heatmap(analytics.heatmaps.strike,"strike")}</section><section><h3>Expiration concentration</h3>${heatmap(analytics.heatmaps.expiry,"expiry")}</section></div>`+rankings(analytics);
      else {
        if(state.view==="overview") html=`<div class="sm-visuals"><section><h3>Observed premium by ticker</h3>${rankingBars(analytics.rankings)}</section><section><h3>Expiration concentration</h3>${heatmap(analytics.heatmaps.expiry,"expiry")}</section></div>`;
        if(state.view==="scanner") html=rankings(analytics)+`<p class="sm-note">Score: observed premium and repeated prints. No normal-volume baseline, sweep attribution or institutional identity is assumed.</p>`;
        const data=await api("activity/?"+query(state.cursor)); if(version!==state.request)return; html+=flowTable(data);
      }
      if(version===state.request) $("#content").innerHTML=html;
    } catch(err){if(version===state.request)error(err);} finally {if(version===state.request)$("#refresh").disabled=false;}
  }
  async function showDetail(id) {
    const d=$("#detail-dialog"); $("#detail-title").textContent="Transaction"; $("#detail-body").innerHTML=empty("Loading transaction..."); if(!d.open)d.showModal();
    try {
      const e=await api(`activity/${id}/`);
      $("#detail-title").textContent=`${e.ticker} ${e.option_type} / ${money(e.premium)}`;
      $("#detail-body").innerHTML=facts({"Company":e.company||"Unavailable","Contract":e.contract_symbol,"Strike / expiration":`${money(e.strike)} / ${e.expiration}`,"Execution price":money(e.price),"Contracts":num(e.quantity,0),"Actual multiplier":num(e.multiplier,6),"Premium":money(e.premium),"Bid / ask":`${money(e.bid)} / ${money(e.ask)}`,"Quote timestamp":stamp(e.quote_at),"Exchange / condition":`${e.exchange||"-"} / ${e.condition||"-"}`,"Execution / structure":`${e.classification} / ${e.structure}`,"Estimated direction":e.direction,"IV / delta":`${num(e.iv,4)} / ${num(e.delta,4)}`,"Volume / open interest":`${num(e.volume,0)} / ${num(e.open_interest,0)}`,"Volume/OI":num(e.volume_oi),"Provider / feed":`${e.provider} / ${e.feed}${e.delayed?" / delayed":" / polled"}`,"Provider timestamp":e.timestamp_raw,"Ingested":stamp(e.received_at)})+
        head(`Evidence / ${e.score} of 100`,e.score_version)+`<ul>${[...(e.evidence.reasons||[]),e.evidence.execution,...(e.evidence.limitations||[]),e.evidence.identity,e.evidence.corrections].filter(Boolean).map(v=>`<li>${esc(v)}</li>`).join("")}</ul>`+
        head("Price action confirmation",e.technical_status)+(Object.keys(e.technical).length?facts(Object.fromEntries(Object.entries(e.technical).map(([k,v])=>[k.replaceAll("_"," "),typeof v==="number"?num(v,4):v]))):empty("Technical data unavailable"))+
        head("Subsequent underlying returns","Not option P&L")+table(["Horizon","Target","Observed","Return","Favorable","Adverse","Status"],e.outcomes.map(r=>cells([esc(r.horizon),esc(stamp(r.target_at)),esc(stamp(r.observed_at)),r.return_percent==null?"-":num(r.return_percent)+"%",r.favorable_percent==null?"-":num(r.favorable_percent)+"%",r.adverse_percent==null?"-":num(r.adverse_percent)+"%",esc(r.status)])),"No mature observations");
    } catch(err){$("#detail-body").innerHTML=empty("Transaction unavailable",err.message);}
  }
  function navigate(view){state.view=view;state.secPage=1;state.filing="";location.hash=view;render();}
  $("#refresh").addEventListener("click",()=>state.view==="fundamentals"?window.SmartMoneyFundamentals.refresh():render(false));
  $("#close-detail").addEventListener("click",()=>$("#detail-dialog").close());
  filtersForm.addEventListener("submit",e=>{e.preventDefault();state.filters=readFilters(filtersForm);render();});
  $("#clear-filter").addEventListener("click",()=>{state.filters={min_premium:50000};fillFilters(state.filters);render();});
  $("#save-filter").addEventListener("click",async()=>{try{const filters=readFilters(filtersForm);await api("preferences/",{symbols:filters.symbols||[],filters});toast("Filters saved");}catch(err){error(err);}});
  $("#collection-form").addEventListener("submit",async e=>{
    e.preventDefault();const f=e.currentTarget,b=f.querySelector("button");b.disabled=true;
    try{await api("collection/",{revision:state.status.collection.revision,enabled:formField(f,"enabled").checked,symbols:split(formField(f,"symbols").value),min_premium:formField(f,"min_premium").value,sec_enabled:formField(f,"sec_enabled").checked,sec_ciks:split(formField(f,"sec_ciks").value)});toast("Collection settings saved");await render(false);}catch(err){error(err);}finally{b.disabled=false;}
  });
  function clearAlert(){const f=$("#alert-form");f.reset();formField(f,"id").value="";formField(f,"revision").value="";}
  $("#cancel-alert").addEventListener("click",clearAlert);
  $("#alert-form").addEventListener("submit",async e=>{
    e.preventDefault();const f=e.currentTarget,b=f.querySelector("button");b.disabled=true;
    try{const id=formField(f,"id").value; const data={name:formField(f,"name").value,enabled:formField(f,"enabled").checked,filters:readFilters(f)};if(id)data.revision=Number(formField(f,"revision").value);await api(id?`alerts/${id}/`:"alerts/",data);clearAlert();toast("Alert saved");await settingsView(false);}catch(err){error(err);}finally{b.disabled=false;}
  });
  document.addEventListener("click",async e=>{
    const b=e.target.closest("button");if(!b)return;
    try {
      if(b.dataset.view){navigate(b.dataset.view);return;}
      if(b.dataset.detail){await showDetail(b.dataset.detail);return;}
      if(b.dataset.ticker || b.dataset.heat){
        if(b.dataset.ticker)state.filters.symbols=[b.dataset.ticker];
        else if(b.dataset.heat==="expiry")state.filters.expiry_from=state.filters.expiry_to=b.dataset.value;
        else state.filters[b.dataset.heat]=b.dataset.value;
        fillFilters(state.filters);navigate("flow");return;
      }
      if(b.id==="next-page"){state.pages.push(state.cursor);state.cursor=state.next;await render(false);}
      if(b.id==="prev-page"){state.cursor=state.pages.pop();await render(false);}
      if(b.id==="sec-next"||b.id==="sec-prev"){state.secPage+=b.id==="sec-next"?1:-1;await render(false);}
      if(b.dataset.edit){const rule=state.rules.find(r=>r.id===Number(b.dataset.edit)),f=$("#alert-form");formField(f,"id").value=rule.id;formField(f,"revision").value=rule.revision;formField(f,"name").value=rule.name;formField(f,"enabled").checked=rule.enabled;for(const [key,val] of Object.entries(rule.filters)){const element=formField(f,key);if(element)element.value=Array.isArray(val)?val.join(", "):val;}f.scrollIntoView({behavior:"smooth",block:"center"});}
      if(b.dataset.toggle||b.dataset.delete){const r=state.rules.find(r=>r.id===Number(b.dataset.toggle||b.dataset.delete));if(b.dataset.delete&&!confirm(`Delete alert "${r.name}" and its delivery history?`))return;b.disabled=true;await api(`alerts/${r.id}/`,b.dataset.delete?{revision:r.revision}:{name:r.name,filters:r.filters,enabled:!r.enabled,revision:r.revision},b.dataset.delete?"DELETE":"POST");await settingsView(false);}
    }catch(err){error(err);b.disabled=false;}
  });
  document.addEventListener("change",e=>{if(e.target.id==="filing-select"){state.filing=e.target.value;state.secPage=1;render(false);}if(e.target.id==="history-group"){state.group=e.target.value;render(false);}});
  window.addEventListener("hashchange",()=>{const view=location.hash.slice(1);if(titles[view]&&state.view!==view){state.view=view;render();}});
  (async()=>{try{const p=await api("preferences/");state.filters=p.filters;fillFilters(p.filters);}catch(err){error(err);}const view=location.hash.slice(1);if(titles[view])state.view=view;await render();})();
  setInterval(()=>{if(!document.hidden && !$("#detail-dialog").open && !["settings","holdings","insiders","fundamentals"].includes(state.view) && !state.cursor && !filtersForm.contains(document.activeElement))render(false);},60000);
})();
