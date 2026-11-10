(() => {
  "use strict";
  const escape = v => String(v ?? "-").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const number = (v,d=2) => v==null?"-":Number(v).toLocaleString("en-US",{maximumFractionDigits:d});
  const amount = (v,compact=false) => v==null?"-":new Intl.NumberFormat("en-US",{notation:compact?"compact":"standard",maximumFractionDigits:2}).format(Number(v));
  const percentage = (v,scale=1) => v==null?"-":number(v*scale)+"%";
  const multiple = v => v==null?"N/M or unavailable":number(v)+"x";
  const time = v => v?new Date(v).toLocaleString("en-US",{timeZone:"America/Los_Angeles",month:"short",day:"numeric",year:"numeric",hour:"2-digit",minute:"2-digit",timeZoneName:"short"}):"Unavailable";
  const heads={income:"Income statement",balance:"Balance sheet",cash_flow:"Cash flow statement"};
  const fields={income:[["revenue","Revenue"],["gross_profit","Gross profit"],["operating_income","Operating income"],["net_income","Net income"],["ebitda","EBITDA"],["diluted_eps","Diluted EPS (per share)"]],balance:[["assets","Total assets"],["liabilities","Total liabilities"],["equity","Shareholder equity"],["cash","Cash and equivalents"],["debt","Total debt"],["current_assets","Current assets"],["current_liabilities","Current liabilities"]],cash_flow:[["operating_cash_flow","Operating cash flow"],["capital_expenditure","Capital expenditure (signed)"],["free_cash_flow","Free cash flow"]]};
  const state={root:null,token:0,timer:null,symbol:"AAPL",frequency:"yearly",record:null,polls:0};
  function saved(key,fallback){try{return localStorage.getItem(key)||fallback;}catch{return fallback;}}
  function save(key,value){try{localStorage.setItem(key,value);}catch{}}
  const empty=(title,detail="")=>`<div class="sm-empty"><strong>${escape(title)}</strong><p>${escape(detail)}</p></div>`;
  const facts=items=>`<dl class="sm-facts">${items.map(([label,value,note])=>`<div><dt>${escape(label)}</dt><dd>${escape(value)}</dd>${note?`<small>${escape(note)}</small>`:""}</div>`).join("")}</dl>`;
  async function request(symbol,post=false,force=false){
    const options={credentials:"same-origin",headers:{Accept:"application/json"}};
    if(post){options.method="POST";options.headers["Content-Type"]="application/json";options.headers["X-CSRFToken"]=document.cookie.split("; ").find(c=>c.startsWith("csrftoken="))?.slice(10)||"";options.body=JSON.stringify({symbol,refresh:force});}
    const response=await fetch("/api/smart-money/research-ratings/"+(post?"":"?symbol="+encodeURIComponent(symbol)),options);
    const data=await response.json().catch(()=>({error:"Fundamentals service unavailable."}));
    if(!response.ok)throw Error(data.error||"Company lookup failed.");
    return data;
  }
  function chart(rows,key,title,currency){
    const sorted=[...rows].reverse();
    const max=Math.max(1,...sorted.map(r=>Math.abs(Number(r[key]||0))));
    return `<section><h3>${escape(title)} <small>${escape(currency)}</small></h3>${sorted.length?`<div class="fa-bars ${sorted.some(r=>r[key]<0)?"":"fa-positive-only"}" aria-label="${escape(title)}">${sorted.map(r=>{const v=r[key],width=Math.abs(Number(v||0))/max*100;return `<div class="fa-bar-row"><span>${escape(r.period_end)}</span><div class="fa-bar-neg">${v<0?`<i style="width:${width}%"></i>`:""}</div><div class="fa-bar-pos">${v>0?`<i style="width:${width}%"></i>`:""}</div><b>${escape(amount(v,true))}</b></div>`;}).join("")}</div>`:empty("No reported periods")}</section>`;
  }
  function statement(kind,rows,currency){
    return `<section class="fa-statement"><div class="sm-section-head"><h2>${heads[kind]}</h2><span>${escape(currency)} in millions, except per-share amounts</span></div>${rows.length?`<div class="sm-table-wrap"><table><thead><tr><th>Period ended</th>${rows.map(r=>`<th>${escape(r.period_end)}</th>`).join("")}</tr></thead><tbody>${fields[kind].map(([key,label])=>`<tr><td>${escape(label)}</td>${rows.map(r=>`<td class="${r[key]<0?"fa-negative":""}">${escape(number(r[key]==null?null:r[key]/(key==="diluted_eps"?1:1e6)))}</td>`).join("")}</tr>`).join("")}</tbody></table></div>${kind==="cash_flow"?`<p class="sm-note">Latest free cash flow: ${escape(rows[0].fcf_method)}.</p>`:""}`:empty("Statement unavailable","No values have been substituted for missing financial data.")}</section>`;
  }
  function ratings(record){
    const rating=record.ratings?.[state.frequency];
    if(!rating)return "";
    const {technical:t,fundamental:f,combined:c}=rating, price=record.technical;
    const tone=r=>r.score==null?"unrated":r.score>=70?"support":r.score>=40?"caution":"against";
    const list=items=>`<ul>${items.map(item=>`<li>${escape(item)}</li>`).join("")}</ul>`;
    const rows=[["Technicals",t,"Completed daily candles",t.as_of?"Session close "+time(t.as_of):"No completed daily data"],
      ["Fundamentals",f,state.frequency==="yearly"?"Annual business performance":"Quarterly business performance",f.as_of?"Period ended "+f.as_of:"No comparable financials"],
      ["Combined",c,"50% technical / 50% fundamental","Daily trend + reported financials"]];
    const supporting=[t,f].flatMap(r=>r.checks.filter(check=>check.state==="support").slice(0,3).map(check=>check.reason));
    let html=`<section class="fa-research" aria-label="Research ratings"><div class="sm-section-head"><h2>Research ratings</h2><span>${escape(c.version)} / 0-100 evidence score</span></div><div class="fa-ratings">${rows.map(([name,r,scope,date])=>`<div class="fa-rating" data-rating="${name.toLowerCase()}" data-tone="${tone(r)}"><h3>${name}</h3><p>${escape(scope)}</p><div class="fa-score"><strong>${r.score==null?"N/A":number(r.score,0)}</strong><span>${r.score==null?"":"/ 100"}</span></div><b class="fa-verdict">${escape(r.label)}</b><meter min="0" max="100" value="${r.score??0}" aria-label="${name} score ${r.score??'unavailable'}"></meter><small>${escape(date)}</small>${r.coverage!=null?`<small>Evidence coverage ${r.coverage}%</small>`:""}</div>`).join("")}</div>`;
    html+=`<div class="fa-assessment"><h3>${escape(c.label)}</h3><p>${escape(c.summary)}</p>${c.blockers.length?`<div class="fa-blockers"><strong>Assessment blocked</strong>${list(c.blockers)}</div>`:""}<div class="fa-evidence"><section><h3>What supports buying</h3>${supporting.length?list(supporting):"<p>No positive evidence available yet.</p>"}</section><section><h3>Reasons to wait / risks</h3>${c.against.length?list(c.against):"<p>No negative scored inputs; this does not mean there is no risk.</p>"}</section></div></div>`;
    html+=`<details class="fa-method"><summary>Score breakdown &amp; limitations</summary><p>Technical: bullish >=70, mixed 40-69, weak &lt;40. Fundamental: strong >=70, mixed 40-69, weak &lt;40. Combined buy candidate requires >=70 overall and >=60 in both; either score &lt;40 means a weak buy case. Scores are weighted heuristic evidence, not predicted returns or win probabilities.</p>${[t,f].map((r,index)=>`<h3>${index?"Fundamental":"Technical"} evidence</h3><div class="sm-table-wrap"><table><thead><tr><th>Input</th><th>Points / weight</th><th>Evidence</th></tr></thead><tbody>${r.checks.map(check=>`<tr><td>${escape(check.label)}</td><td>${check.points==null?"Missing":number(check.points)+" / "+check.weight}</td><td>${escape(check.reason)}</td></tr>`).join("")}</tbody></table></div>`).join("")}<p>Coverage is available weight out of 100. Missing inputs are excluded; remaining points are divided by available weight. Technicals require 90% coverage and 200 consecutive sessions; fundamentals require 80% and all four evidence groups.</p>${list(c.cautions)}<p>Daily prices: ${escape(price?.data?.source||"Unavailable")}. Retrieved ${escape(time(price?.fetched_at))}. ${escape(price?.data?.method||"")} No automatic orders or changes to trading strategies.</p></details><p class="fa-rating-note">Research only. Not a guaranteed return, price target, or immediate trade instruction. Valuation, news, earnings events and your risk limits still need review.</p></section>`;
    return html;
  }
  function paint(record){
    if(!state.root)return;
    const box=state.root.querySelector("#fa-results"),banner=state.root.querySelector("#fa-status");
    const data=record.data;
    const messages=[];
    if(record.pending)messages.push("Refreshing research data...");
    if(record.stale)messages.push("Stale cached snapshot");
    if(record.notice)messages.push(record.notice);
    if(record.error)messages.push(record.error);
    if(record.technical?.error)messages.push(record.technical.error);
    if(record.technical?.stale)messages.push("Daily price cache needs refreshing");
    if(record.retry_at&&(record.error||record.notice))messages.push("Retry after "+time(record.retry_at));
    if(data)messages.push("Yahoo Finance via yfinance | Retrieved "+time(record.fetched_at));
    banner.textContent=messages.join(" | ")||"Company data has not been retrieved yet.";
    banner.classList.toggle("fa-stale",Boolean(record.stale||record.error||record.technical?.stale||record.technical?.error));
    if(!data){box.innerHTML=ratings(record)+empty(record.pending?"Loading financial statements...":"No company fundamentals available",record.error||record.notice||"Company financials unavailable for this ticker.");return;}
    const currency=data.financial_currency||"Currency unavailable", quoteCurrency=data.quote_currency||"Currency unavailable";
    let html=`<div class="fa-company"><div><p class="sm-eyebrow">${escape(data.symbol)} / ${escape(data.exchange||data.asset_type)}</p><h2>${escape(data.company||data.symbol)}</h2><p>${escape([data.sector,data.industry,data.country].filter(Boolean).join(" / "))}</p></div><div class="fa-quote"><strong>${escape(amount(data.price))} <small>${escape(quoteCurrency)}</small></strong><span>Quote as of ${escape(time(data.quote_at))}</span></div></div>`;
    html+=ratings(record);
    if(!data.supported){box.innerHTML=html+empty("Company statements not applicable",data.warnings.join(" "));return;}
    const period=data.periods[state.frequency],a=period.analysis,income=period.statements.income[0]||{},v=data.valuation;
    const fiscalLabel=state.frequency==="yearly"?"Annual":"Quarterly";
    const summary=[["Market capitalization",amount(data.market_cap,true),quoteCurrency],
      [`${fiscalLabel} revenue`,amount(income.revenue,true),`${currency} / ${a.period_end||"Unavailable"}`],
      ["Revenue growth YoY",percentage(a.revenue_yoy),"Comparable prior-year period"],
      ["Net profit margin",percentage(a.net_margin,100),a.period_end||"Unavailable"],
      ["Free cash flow",amount(a.free_cash_flow,true),`${currency} / ${a.period_end||"Unavailable"}`],
      ["Trailing P/E",multiple(v.trailing_pe),"Provider TTM ratio"]];
    html+=`<div class="fa-summary">${summary.map(([label,value,note])=>`<div><span>${escape(label)}</span><strong>${escape(value)}</strong><small>${escape(note)}</small></div>`).join("")}</div>`;
    html+=`<div class="sm-section-head"><h2>Fundamental observations</h2><span>${fiscalLabel} period ended ${escape(a.period_end||"Unavailable")}</span></div>${a.observations.length?`<ul class="fa-observations">${a.observations.map(s=>`<li>${escape(s)}</li>`).join("")}</ul>`:empty("Insufficient comparable financials")}`;
    html+=`<div class="fa-charts">${chart(period.statements.income,"revenue","Revenue",currency)}${chart(period.statements.income,"net_income","Net income",currency)}</div>`;
    html+=`<div class="fa-ratios"><section><div class="sm-section-head"><h2>Valuation snapshot</h2><span>Provider ratios / not historical period valuations</span></div>${facts([["Trailing P/E",multiple(v.trailing_pe)],["Forward P/E",multiple(v.forward_pe),"Analyst estimate, not reported earnings"],["Price / sales (TTM)",multiple(v.price_sales)],["Price / book",multiple(v.price_book)],["EV / EBITDA",multiple(v.ev_ebitda)],["Enterprise value",amount(v.enterprise_value,true)+" "+quoteCurrency],["Trailing EPS",number(v.trailing_eps),"Provider trailing per-share earnings"],["Forward EPS",number(v.forward_eps),"Provider forecast, not guaranteed"]])}</section><section><div class="sm-section-head"><h2>Profitability &amp; financial position</h2><span>Statement-derived</span></div>${facts([["Gross margin",percentage(a.gross_margin,100),a.period_end],["Operating margin",percentage(a.operating_margin,100),a.period_end],["Net income growth YoY",percentage(a.net_income_yoy),"Not calculated from nonpositive bases"],["Diluted EPS growth YoY",percentage(a.diluted_eps_yoy),"Comparable prior-year period"],["Debt / equity",multiple(a.debt_equity),a.balance_period_end],["Current ratio",multiple(a.current_ratio),a.balance_period_end]])}</section></div>`;
    if(data.description)html+=`<details class="fa-description"><summary>Company profile</summary><p>${escape(data.description)}</p></details>`;
    for(const kind of ["income","balance","cash_flow"])html+=statement(kind,period.statements[kind],currency);
    html+=`<div class="fa-source"><a href="${escape(data.source_url)}" target="_blank" rel="noopener noreferrer">Yahoo Finance financial statements</a><p>Period-end dates are not filing dates. Data may be restated; this is not a point-in-time backtest. Missing or nonmeaningful ratios are not zero. Valuation metrics can use different trailing or forecast periods.</p>${data.warnings.length?`<ul>${data.warnings.map(w=>`<li>${escape(w)}</li>`).join("")}</ul>`:""}</div>`;
    box.innerHTML=html;
  }
  async function load(force=false){
    if(!state.root)return;
    clearTimeout(state.timer);const token=++state.token;
    state.polls=0;
    const field=state.root.querySelector('[name="symbol"]'),symbol=field.value.trim().toUpperCase();
    if(!/^[A-Z][A-Z0-9.\-]{0,14}$/.test(symbol)){state.root.querySelector("#fa-status").textContent="Enter a valid US stock ticker.";return;}
    state.symbol=symbol;field.value=symbol;save("smart-money-fundamental-symbol",symbol);
    state.root.querySelector("#fa-status").textContent="Loading "+symbol+" research...";
    state.root.querySelector("#fa-results").innerHTML=empty("Loading "+symbol+"...");
    const update=async(post)=>{
      try{
        const record=await request(symbol,post,force);
        if(token!==state.token||!state.root)return;
        state.record=record;paint(record);
        if(record.pending&&state.polls++<300)state.timer=setTimeout(()=>update(false),2000);
      }catch(err){if(token===state.token&&state.root){state.root.querySelector("#fa-status").textContent=err.message;state.root.querySelector("#fa-results").innerHTML=empty("Fundamentals unavailable",err.message);}}
    };
    await update(true);
  }
  window.SmartMoneyFundamentals={
    async mount(root){
      state.root=root;state.symbol=saved("smart-money-fundamental-symbol","AAPL");state.frequency=saved("smart-money-fundamental-period","yearly");
      if(!["yearly","quarterly"].includes(state.frequency))state.frequency="yearly";
      root.innerHTML=`<form id="fa-search" class="fa-toolbar"><label>Company ticker<input name="symbol" value="${escape(state.symbol)}" list="fa-symbols" maxlength="15" required autocomplete="off"></label><datalist id="fa-symbols">${["AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","SOFI","MU","ABNB","MCD","WMT","APP","HOOD"].map(s=>`<option value="${s}"></option>`).join("")}</datalist><button class="sm-primary" type="submit">Analyze</button><div class="fa-period" role="group" aria-label="Statement period"><button type="button" data-period="yearly" aria-pressed="${state.frequency==="yearly"}">Annual</button><button type="button" data-period="quarterly" aria-pressed="${state.frequency==="quarterly"}">Quarterly</button></div></form><div id="fa-status" class="sm-status" role="status"></div><div id="fa-results"></div>`;
      root.querySelector("#fa-search").addEventListener("submit",e=>{e.preventDefault();load();});
      for(const button of root.querySelectorAll("[data-period]"))button.addEventListener("click",()=>{state.frequency=button.dataset.period;save("smart-money-fundamental-period",state.frequency);for(const b of root.querySelectorAll("[data-period]"))b.setAttribute("aria-pressed",String(b===button));if(state.record&&state.record.symbol===state.symbol)paint(state.record);});
      await load();
    },
    refresh:()=>load(true),
    unmount(){clearTimeout(state.timer);state.token++;state.root=null;state.record=null;}
  };
})();
