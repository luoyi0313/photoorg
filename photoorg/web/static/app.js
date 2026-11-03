/* photoorg local UI: no cloud requests; every mutation writes an auditable override. */
(() => {
  const $ = (s, e=document) => e.querySelector(s);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const q = new URLSearchParams(location.search);
  const S = { cls: q.get('class') || '', batch: q.get('batch') || '', page: 'overview', boot: null, data: null, review: null, range: { from: '', to: '' }, matrix: null, personId: null, personData: null, gallery: null, batchPhotos: null };
  const api = async (url, opt={}) => {
    // API 永远取最新数据库状态，不能让浏览器缓存旧的 batch / publish 响应。
    const isGet = !opt.method || opt.method === 'GET';
    const sep = url.includes('?') ? '&' : '?';
    const freshUrl = isGet ? `${url}${sep}_ts=${Date.now()}` : url;
    const res = await fetch(freshUrl, {cache:'no-store', headers:{'Content-Type':'application/json'}, ...opt});
    if (!res.ok) { const x = await res.json().catch(()=>({})); throw new Error(x.detail || `请求失败 (${res.status})`); }
    return res.headers.get('content-type')?.includes('application/json') ? res.json() : res.text();
  };
  const post = (url, body) => api(url, {method:'POST', body:JSON.stringify(body)});
  // 上传结束时页面可能刚好因 refresh() 重新渲染；所有状态文案都防御性写入，
  // 不让一个已关闭的弹窗触发第二个 JS 异常。
  const setText = (selector, text) => { const node=$(selector); if(node) node.textContent=text; return node; };
  const toast = (text, kind='') => { const x=document.createElement('div'); x.className='toast '+kind; x.textContent=text; const host=$('#toasts'); if(host) host.append(x); setTimeout(()=>x.remove(), 3200); };
  const clsQ = () => `class=${encodeURIComponent(S.cls)}`;
  const batchQ = () => `batch=${encodeURIComponent(S.batch)}&${clsQ()}`;
  const operator = () => localStorage.getItem('photoorg.operator') || '本机用户';
  const AV_COLORS = ['#3a7bff', '#16824c', '#7c5cff', '#c8860a', '#0f9b8e', '#c25a9c', '#4a6fb5', '#a06a2c'];
  const avatar = (name) => {
    const text = String(name || '?');
    const seed = Array.from(text).reduce((a, c) => a + c.codePointAt(0), 0);
    const color = AV_COLORS[seed % AV_COLORS.length];
    return `<span class="avatar" style="background:${color}">${esc(text.slice(0, 1))}</span>`;
  };
  async function refresh(keepPage=true) {
    S.boot = await api('/api/bootstrap?'+clsQ());
    const classNames=(S.boot.classes||[]).map(x=>x.name);
    // 班级被删掉后不能继续拿旧名字去请求，否则会指向一个不存在的班级
    if(!S.cls || (classNames.length && !classNames.includes(S.cls))) S.cls=classNames[0]||'';
    const batches=S.boot.batches||[];
    // 当前批次不存在（例如切换班级、刚创建活动）时，自动选择最新批次。
    if (!S.batch || !batches.some(x=>x.batch===S.batch)) S.batch=batches.length ? batches[batches.length-1].batch : '';
    S.data = S.batch ? await api('/api/batch?'+batchQ()) : null;
    if (keepPage) render(); renderHeader();
  }
  function renderHeader() {
    // 发布后 refresh 会替换部分页面；头部节点用安全更新，不能因为一个节点短暂缺失
    // 让“发布成功”之后再抛出前端异常。
    const cs=$('#classSelect'), bs=$('#batchSelect');
    if(cs) cs.innerHTML=(S.boot.classes||[]).map(x=>`<option ${x.name===S.cls?'selected':''}>${esc(x.name)}</option>`).join('') || '<option>还没有班级</option>';
    if(bs) bs.innerHTML=(S.boot.batches||[]).map(x=>`<option value="${esc(x.batch)}" ${x.batch===S.batch?'selected':''}>${esc(x.batch)} · ${esc(x.label)}</option>`).join('') || '<option>还没有批次</option>';
    const state=S.data?.state?.label||'—', pill=$('#statePill');
    if(pill) { pill.textContent=state; pill.className='pill '+({DRAFT:'draft',REVIEWED:'reviewed',PUBLISHED:'published'}[state]||'grey'); }
    const n=S.data?.review_count||0, badge=$('#reviewBadge');
    if(badge) { badge.textContent=n; badge.className='badge '+(n?'':'zero'); }
    const report=$('#outReport');
    // 「下载报告」= 全班「活动×人员」出勤表（CSV，Excel 可直接打开）
    if(report) { report.href=S.cls?`/api/export/matrix.csv?${clsQ()}&_ts=${Date.now()}`:'#'; report.title='下载全班出勤统计表（活动×人员，CSV）'; }
  }
  // Tab 由下方 #tabs 的稳定事件监听器处理；渲染时只替换按钮内容，不依赖行内 onclick。
  const TAB_LABEL={overview:'总览',review:'灰区复核',inspect:'结果审查',gallery:'底库管理',stats:'年度统计',photos:'活动照片',clusters:'冷启动聚类',settings:'设置与审计'};
  const TAB_ICON={
    overview:'<path d="M3 10.8 12 3l9 7.8"/><path d="M5.5 9.6V21h13V9.6"/>',
    review:'<circle cx="12" cy="12" r="8.6"/><path d="M9.7 9.2a2.4 2.4 0 1 1 3.5 2.1c-.8.5-1.2 1-1.2 1.9v.3"/><circle cx="12" cy="17.1" r=".7" fill="currentColor" stroke="none"/>',
    inspect:'<path d="M2.4 12S6 5.4 12 5.4 21.6 12 21.6 12 18 18.6 12 18.6 2.4 12 2.4 12Z"/><circle cx="12" cy="12" r="3"/>',
    gallery:'<circle cx="9" cy="8.2" r="3.4"/><path d="M2.8 20c0-3.3 2.8-5.9 6.2-5.9s6.2 2.6 6.2 5.9"/><path d="M16.2 5.6a3.2 3.2 0 0 1 0 6.3"/><path d="M17.8 14.6c2 .8 3.4 2.6 3.4 5.4"/>',
    stats:'<path d="M4 20V11"/><path d="M10 20V4.5"/><path d="M16 20v-6"/><path d="M22 20H2"/>',
    photos:'<rect x="3" y="4.5" width="18" height="15" rx="2.2"/><circle cx="8.4" cy="9.4" r="1.5"/><path d="m20.5 16.2-4.8-4.8-6.2 6.2"/>',
    clusters:'<rect x="3" y="3" width="7" height="7" rx="1.6"/><rect x="14" y="3" width="7" height="7" rx="1.6"/><rect x="3" y="14" width="7" height="7" rx="1.6"/><rect x="14" y="14" width="7" height="7" rx="1.6"/>',
    settings:'<circle cx="12" cy="12" r="3.2"/><path d="M12 2.6v2.9M12 18.5v2.9M2.6 12h2.9M18.5 12h2.9M5.4 5.4l2 2M16.6 16.6l2 2M18.6 5.4l-2 2M7.4 16.6l-2 2"/>',
  };
  const tabIcon=(name)=>`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${TAB_ICON[name]||''}</svg>`;
  function tab(name) {
    const badge = name==='review' ? '<span class="badge zero" id="reviewBadge">0</span>' : '';
    return `<button type="button" data-page="${name}" class="${S.page===name?'on':''}">${tabIcon(name)}<span>${TAB_LABEL[name]}</span>${badge}</button>`;
  }
  function empty(icon,title,detail='') { return `<div class="empty"><div class="big">${icon}</div><h2>${esc(title)}</h2><div>${detail}</div></div>`; }
  function noBatchPage(page) {
    const help={
      review:{icon:'🤔',title:'灰区复核：处理“系统拿不准”的脸',body:'活动照片识别后，只有相似度不够确定的人脸才会出现在这里。你将看到人脸截图和候选姓名，可确认是谁、标为不认识，或新建人员。',after:'上传活动照片后：这里会显示“第 1 / N 条待确认”，逐张处理即可。'},
      inspect:{icon:'🔍',title:'结果审查：检查“系统已经识别”的结果',body:'活动照片识别后，这里会按人员展示已经归档的照片。你可以打开原图查看脸框，发现识别错了就改判、标为不认识，或把照片移出该人员。',after:'上传活动照片后：这里会显示每个已出现人员及其归档照片。'},
      clusters:{icon:'🧩',title:'冷启动聚类：给重复出现的陌生人命名',body:'活动照片中有同一个“未知人员”重复出现时，这里会把这些脸组成一组。确认是同一人后输入姓名，就能建立该人的底库。',after:'上传活动照片后：点击“扫描并开始聚类”即可查找重复出现的未知脸。'},
      photos:{icon:'🖼',title:'活动照片：看这一批的全部照片',body:'这里列出当前活动里的每一张照片。你可以追加照片、删除单张照片，或者把整个活动删掉后重传。',after:'上传活动照片后：这里会显示该活动的全部照片，点击可看大图与人脸。'},
      settings:{icon:'⚙️',title:'设置与审计：追溯与高级维护',body:'这里用于查看人工改判记录、恢复归档、导出修正记录和校准识别阈值。正常的上传、识别、复核和发布流程不需要在这里操作。',after:'上传活动照片后：这里会显示当前活动的审计日志和归档维护操作。'},
    };
    const h=help[page]||help.review;
    $('#main').innerHTML=`<section class="card"><div class="empty"><div class="big">${h.icon}</div><h2>${esc(h.title)}</h2><p class="muted">${esc(h.body)}</p><div class="hint">${esc(h.after)}</div><div class="row" style="justify-content:center;margin-top:18px"><button class="btn primary" onclick="window.PO.openActivityUpload()">新建活动并上传照片</button><button class="btn" onclick="window.PO.go('gallery')">先去管理底库</button></div></div></section>`;
  }
  function render() {
    $('#tabs').innerHTML=['overview','review','inspect','gallery','stats','photos','clusters','settings'].map(tab).join('');
    // 年度统计与人员明细只看数据库，不依赖当前批次。
    if (S.page==='stats') { stats(); return; }
    if (S.page==='person') { personPage(); return; }
    // 底库管理是独立功能：即使还没有任何活动批次也必须可用。
    if (!S.data && S.page==='gallery') { gallery(); return; }
    if (!S.data && S.page!=='overview') { noBatchPage(S.page); return; }
    if (!S.data) { $('#main').innerHTML=`<section class="card"><h2>从这里开始</h2><p class="muted">这个班还没有处理过活动照片。先上传底库参考照片，再创建活动并上传整个活动照片文件夹；不需要打开终端。</p><div class="row"><button class="btn primary" onclick="window.PO.openGalleryUpload()">1 · 上传底库参考照片</button><button class="btn" onclick="window.PO.openActivityUpload()">2 · 新建活动并上传照片</button></div><div class="hint" style="margin-top:14px">推荐顺序：先为每位人员上传 3～10 张清晰参考照；然后上传活动照片文件夹。即使暂时没有底库，你也可以先上传活动图片，后续使用“冷启动聚类”给人物命名。</div></section>`; return; }
    ({overview,review,inspect,gallery,stats,photos:activityPhotos,person:personPage,clusters,settings}[S.page]||overview)();
  }
  function overview() {
    const d=S.data, st=d.stats, persons=d.persons;
    const absent=persons.filter(p=>p.absent), unconf=absent.filter(p=>!p.absent_confirmed);
    const firstGuide = d.state.label === 'DRAFT' ? `<section class="card"><div class="row"><h2>第一次怎么操作？</h2><button class="btn sm right" onclick="localStorage.setItem('photoorg.guide.dismiss','1');window.PO.go('overview')">我知道了</button></div><ol style="margin:6px 0 0;padding-left:22px"><li>先看上面的「待复核」：有数字时点击 <b>灰区复核</b>，给每张拿不准的脸选人、标不认识或新建人员。</li><li>点击 <b>结果审查</b>，按人查看缩略图；点照片看大图，发现错归即可改判或移出。</li><li>名单中有「未出现」时，先看反证照片，再逐人点“确认未出现”。</li><li>两个待办均清零后，点击 <b>发布正式名单</b>；报告和归档目录会一同更新。</li></ol><p class="hint">这次演示已没有灰区：可直接点击「结果审查」查看识别后的照片；真实照片跑完后按上面 4 步操作。</p></section>` : '';
    $('#main').innerHTML=`<section class="hero"><div class="hero-grid"><div class="hero-inner"><div class="eyebrow">本机处理 · 照片不出电脑</div><h1>上传照片，自动点名</h1><p>选中一个活动照片文件夹，系统会建批次、识别每个人、按人归档并生成出勤报告。拿不准的脸由你逐张确认，人工修正全程可追溯。</p><div class="row"><button class="btn primary" onclick="window.PO.openActivityUpload()">+ 新建活动并上传照片</button><button class="btn ghost" onclick="window.PO.go('gallery')">先管理底库</button></div></div><div class="hero-steps" aria-hidden="true"><div class="step"><b>1</b><span>上传活动照片</span></div><div class="step"><b>2</b><span>自动识别人脸</span></div><div class="step"><b>3</b><span>逐张复核灰区</span></div><div class="step"><b>4</b><span>发布出勤名单</span></div></div></div></section>${localStorage.getItem('photoorg.guide.dismiss') ? '' : firstGuide}${d.draft_banner?`<div class="banner ${d.state.label==='DRAFT'?'':'soft'}">⚠️ ${esc(d.draft_banner)}</div>`:''}
      ${st.incomplete?`<div class="banner">⚠️ 批次质量不达标：${esc(st.incomplete_reason)}。系统不会给出「未出现」结论。</div>`:''}
      ${d.orphan_files?`<div class="banner info">本批次文件夹里另有 ${d.orphan_files} 张照片已被归入其他批次（原始文件保留、未删除），所以这里只统计属于本批次的 ${st.n_photos} 张。</div>`:''}
      <div class="grid c4">
       ${stat('照片',st.n_photos,`失败 ${st.n_error||0} 张${st.n_no_face?` · ${st.n_no_face} 张无脸`:''}`,'blue')}${stat('检出人脸',st.n_faces,`质量达标 ${st.n_usable_faces||0} 张`,'violet')}${stat('出现',st.n_pairs_present,'按「照片×人」计数','green')}${stat('待复核',d.review_count, d.review_count?'请在灰区复核页确认':'本次数据很干净', d.review_count?'amber':'slate')}
      </div>
      <div class="split"><section class="card"><h2>处理进度</h2>${progressActions(d)}</section>
      <section class="card"><h2>未出现名单</h2>${absent.length?absent.map(p=>`<div class="row" style="padding:6px 0;border-bottom:1px solid var(--line)"><b>${esc(p.name)}</b><span class="muted small">${p.absent_confirmed?'已确认':'待确认'}</span><span class="right"><button class="btn sm" onclick="window.PO.openPersonUpload(${p.person_id})">补活动照片</button>${p.absent_confirmed?'✅':`<button class="btn sm" onclick="window.PO.confirmAbsent(${p.person_id},true)">确认未出现</button>`}</span></div>`).join(''):'<p class="muted">名单上的人都出现了。</p>'}</section></div>
      <section class="card"><div class="row"><h2>按人审查</h2><span class="muted small">点缩略图查看大图，移出错归或加模板</span><span class="right"><button class="btn sm" onclick="window.PO.go('inspect')">展开全部审查</button></span></div>${persons.slice(0,8).map(personRow).join('')}${persons.length>8?'<p class="muted small">仅显示前 8 人，完整列表在「结果审查」。</p>':''}</section>`;
  }
  // 发布状态决定按钮语义：已发布时不能再显示“去复核/发布”，否则界面自相矛盾。
  function progressActions(d) {
    const st = d.state || {}, last = (st.history || []).slice(-1)[0];
    const note = last && last.by === 'system' && last.note ? `<p class="hint">最近变更：${esc(last.note)}</p>` : '';
    if (st.label === 'PUBLISHED') {
      const pub = st.published || {}, at = (pub.at || '').slice(0,16).replace('T',' ');
      return `<div class="row"><span class="pill published">已发布</span><span class="muted small">确认人：${esc(pub.by || '—')}${at ? ' · ' + esc(at) : ''}</span></div>
        <p class="muted">正式名单已发布，报告页眉已写入确认人和时间。之后若再改判、补底库或批次照片有变动，系统会自动退回「待复核」，不会让旧名单继续冒充有效。</p>
        <button class="btn" onclick="window.PO.unpublish()">撤回发布</button>${note}`;
    }
    return `<div class="row"><span class="pill ${d.review_ready?'published':'draft'}">${d.review_ready?'灰区已清零':'灰区待复核'}</span><span class="pill ${d.absent_ready?'published':'draft'}">${d.absent_ready?'未出现已逐人确认':'未出现待逐人确认'}</span></div><p class="muted">只有灰区清零、每位「未出现」人员均被单独确认后，才可以发布正式名单。</p>${d.can_publish?`<button class="btn ok" onclick="window.PO.publish()">发布正式名单</button>`:`<button class="btn primary" onclick="window.PO.go('review')">去完成复核</button>`}${note}`;
  }
  const stat=(k,v,s,tone='blue')=>`<div class="stat ${tone}"><div class="k">${k}</div><div class="v">${v??0}</div><div class="s">${esc(s||'')}</div></div>`;
  function personRow(p) { return `<div class="person"><div class="who">${avatar(p.name)}<div><div class="nm">${esc(p.name)}</div><div class="muted small">${p.count} 张 · ${p.auto} 自动${p.manual?' · '+p.manual+' 人工':''}${p.review?' · '+p.review+' 灰区':''}</div>${p.flagged?.length?`<div class="small" style="color:var(--warn)">⚠ ${esc(p.flagged.join('；'))}</div>`:''}</div></div><div class="thumbs">${p.photos.map(x=>thumb(x,p)).join('')||'<span class="muted small">暂无出现照片</span>'}</div><div class="right">${p.absent?`<button class="btn sm" onclick="window.PO.openPersonUpload(${p.person_id})">补活动照片</button>`:''}${p.absent&&!p.absent_confirmed?`<button class="btn sm" onclick="window.PO.confirmAbsent(${p.person_id},true)">确认未出现</button>`:''}</div></div>`; }
  function thumb(x,p) { const idx = faceIndexFor(x.photo_hash,p.person_id); const crop=idx>=0?`/api/crop/${x.photo_hash}/${idx}?size=150`:`/api/image/${x.photo_hash}?max=300`; return `<div class="thumb ${x.manual?'manual':''}" onclick="window.PO.openPhoto('${x.photo_hash}')"><img src="${crop}"><span class="tag">${esc(x.source)}</span></div>`; }
  function faceIndexFor(hash,pid) { const f=S.photoFaces?.[hash]; return f?.faces?.find(x=>x.person_id===pid)?.face_idx ?? -1; }

  async function review() {
    $('#main').innerHTML='<div class="empty"><div class="big">⏳</div>正在整理灰区…</div>';
    const r=await api('/api/review?'+batchQ()); S.review=r;
    if (!r.items.length) { $('#main').innerHTML=`<div class="card">${empty('✅','没有灰区','这次数据很干净。接下来请审查「未出现」名单，确认后即可发布。')}<div style="text-align:center"><button class="btn primary" onclick="window.PO.go('inspect')">去结果审查</button></div></div>`; return; }
    renderReviewItem(0);
  }
  function renderReviewItem(i) {
    const r=S.review, x=r.items[i]; if(!x){review();return;} S.review.idx=i;
    const pct=Math.round(i/r.items.length*100), cands=x.candidates;
    $('#main').innerHTML=`<div class="card"><div class="row"><h2>灰区复核</h2><span class="muted">第 ${i+1} / ${r.items.length} 条</span><span class="right muted small">键盘：<span class="kbd">1</span>/<span class="kbd">2</span>/<span class="kbd">3</span> 选人 · <span class="kbd">X</span> 不认识 · <span class="kbd">+</span> 新人 · <span class="kbd">Z</span> 撤销 · <span class="kbd">Enter</span> 跳过</span></div><div class="progress"><i style="width:${pct}%"></i></div></div>
    <div class="review-wrap"><section class="card"><img class="face-big" src="/api/crop/${x.photo_hash}/${x.face_idx}?size=480"><div class="small muted" style="margin-top:8px">${esc(x.photo_name)} · 相似度 ${x.score.toFixed(2)} · 质量 ${x.quality.toFixed(2)} ${x.reasons?'· '+esc(x.reasons):''}</div><div class="orig-wrap"><img id="reviewOrig" src="/api/image/${x.photo_hash}?max=1000" onload="window.PO.boxReview(this,${JSON.stringify(x.bbox).replace(/"/g,'&quot;')})"><i class="box" id="reviewBox"></i></div></section>
    <section class="card"><h2>这是谁？</h2><p class="muted small">系统没把握，不会自动归档。请选择候选，或者说明不认识。</p>${cands.map((c,j)=>`<div class="cand" onclick="window.PO.decide(${j},${c.person_id})"><span class="key">${j+1}</span><span class="nm">${esc(c.name)}</span><span class="bar"><i style="width:${Math.max(0,Math.min(100,c.score*100))}%"></i></span><span class="sc">${c.score.toFixed(2)}</span></div>`).join('')}<div class="row" style="margin-top:14px"><button class="btn" onclick="window.PO.decideUnknown()">X · 不认识</button><button class="btn" onclick="window.PO.newPerson()">+ · 新人</button><button class="btn ghost" onclick="window.PO.nextReview()">Enter · 跳过</button></div><div class="hint" style="margin-top:16px">确认某人后，若脸清晰且不与既有模板重复，会自动回流到底库；下次相同角度更容易自动识别。</div></section></div>`;
  }
  async function decide(action,personId=null,newName=null) { const x=S.review.items[S.review.idx]; try { await post('/api/review/decide',{batch:S.batch,photo_hash:x.photo_hash,face_idx:x.face_idx,action,person_id:personId,new_name:newName,operator:operator()}); toast(action==='present'?'已确认并重建归档':'已标为不认识','ok'); await refresh(false); await review(); } catch(e){toast(e.message,'bad');} }

  function inspect() { const d=S.data; $('#main').innerHTML=`<div class="card"><div class="row"><h2>结果审查</h2><span class="muted small">按人看照片；点击照片可查看每张脸并改判。</span><span class="right"><button class="btn sm" onclick="window.PO.showDiff()">自动 vs 人工</button></span></div>${d.persons.map(personRow).join('')}</div>`; }

  async function gallery() { $('#main').innerHTML='<div class="empty"><div class="big">⏳</div>读取底库健康度…</div>'; const r=await api('/api/gallery/health?'+clsQ()); S.gallery=r; $('#main').innerHTML=`<div class="card"><div class="row"><div><h2>底库管理</h2><span class="muted small">每个人建议 3～10 张；多样性比数量更重要。</span></div><span class="right"><button class="btn" onclick="window.PO.openGalleryTreeUpload()">一键上传（人名文件夹）</button><button class="btn primary" onclick="window.PO.openGalleryUpload()">+ 上传底库参考照片</button></span></div><div class="hint">一个人一个人传：选择名下的多张清晰参考照即可。整班一起传：选一个父文件夹，<b>子文件夹名=人名</b>，里面的照片就是那个人的底库照。系统会自动找主脸、做质量门控和去重。</div><table class="tbl" style="margin-top:12px"><thead><tr><th>人员</th><th class="num">模板</th><th class="num">平均质量</th><th>健康提示</th><th style="text-align:right;white-space:nowrap">操作</th></tr></thead><tbody>${r.persons.map(p=>`<tr><td><b>${esc(p.name)}</b></td><td class="num">${p.n_templates}</td><td class="num">${p.quality.toFixed(2)}</td><td>${p.warnings?.length?`<span style="color:var(--warn)">⚠ ${esc(p.warnings.join('；'))}</span>`:'<span style="color:var(--ok)">✅ 健康</span>'}</td><td style="text-align:right;white-space:nowrap"><button class="btn sm" onclick="window.PO.openGallery(${p.person_id},'${esc(p.name)}')">查看模板</button> <button class="btn sm" onclick="window.PO.renamePerson(${p.person_id})">改名</button> <button class="btn sm danger" onclick="window.PO.confirmDeletePerson(${p.person_id})">删除</button></td></tr>`).join('')}</tbody></table></div>`; }

  async function clusters(threshold=0.55,minSize=2) { $('#main').innerHTML='<div class="empty"><div class="big">🧩</div>正在扫描本活动中的未知人脸…</div>'; try { const r=await api('/api/clusters?'+batchQ()+`&only_unknown=true&threshold=${threshold}&min_size=${minSize}`); $('#main').innerHTML=`<div class="card"><div class="row"><div><h2>冷启动聚类：给“陌生人”建底库</h2><p class="muted">适合第一次使用、还没有完整底库时：系统把本活动中反复出现但还不认识的脸放在一起。确认它们是同一个人后，给这一组输入姓名，系统就会创建人员并把这些脸加入底库。</p></div></div><div class="hint"><b>什么时候用？</b>活动照片里有人被标成“名单外 / 不认识”，但你希望下次能自动识别时使用。<br><b>什么时候不用？</b>如果已经有完整底库、也没有未知人脸，可以忽略此页。</div><div class="row" style="margin-top:12px"><label class="small">相似度要求<br><input id="clusterThreshold" type="number" min="0.30" max="0.95" step="0.05" value="${threshold}" style="width:100px"></label><label class="small">最少出现次数<br><input id="clusterMinSize" type="number" min="2" max="20" step="1" value="${minSize}" style="width:80px"></label><button class="btn primary" onclick="window.PO.runClusters()">扫描并开始聚类</button></div><p class="muted small">相似度越高，分组越严格；通常保持默认值即可。只扫描当前活动中尚未认识的人脸，不会改变已有识别结果。</p></div><div class="card" style="margin-top:14px"><div class="row"><h2>扫描结果</h2><span class="muted small">${r.clusters.length?'找到 '+r.clusters.length+' 组可命名人脸':'没有找到重复出现的未知人脸'}</span></div>${r.clusters.length?r.clusters.map(c=>`<div class="person"><div class="who"><div class="nm">同一人候选组 #${c.cid}</div><div class="muted small">在 ${c.size} 张脸中重复出现</div><button class="btn sm primary" onclick="window.PO.acceptCluster(${c.cid})">确认同一人并命名</button></div><div class="thumbs">${c.members.map(m=>`<div class="thumb"><img src="${m.crop}"><span class="tag">${esc(m.photo_name)}</span></div>`).join('')}</div></div>`).join(''):empty('🧩','当前没有可聚类的人脸','这通常表示：所有人已经识别完成，或未知人脸只各出现一次。你可以上传更多活动照片后再点“扫描并开始聚类”。')}</div>`; S.clusters=r.clusters; } catch(e) { $('#main').innerHTML=empty('🧩','暂时不能聚类',e.message); } }
  function runClusters(){const t=Number($('#clusterThreshold')?.value)||0.55, n=Number($('#clusterMinSize')?.value)||2;clusters(t,n);}

  async function settings() { $('#main').innerHTML=`<section class="card"><div class="row"><div><h2>班级管理</h2><span class="muted small">新建班级，或删除不再需要的班级。</span></div></div><div class="row" style="margin-top:10px"><input id="newClassName" type="text" placeholder="新班级名，例如 三年二班" style="width:240px"><button class="btn primary" onclick="window.PO.createClass()">新建班级</button></div><table class="tbl" style="margin-top:12px"><thead><tr><th>班级</th><th class="num">名单</th><th class="num">模板</th><th style="text-align:right">操作</th></tr></thead><tbody>${(S.boot.classes||[]).map(x=>`<tr><td><b>${esc(x.name)}</b>${x.name===S.cls?' <span class="pill reviewed" style="height:20px;font-size:11px">当前</span>':''}</td><td class="num">${x.n_persons}</td><td class="num">${x.n_templates}</td><td style="text-align:right;white-space:nowrap"><button class="btn sm danger" onclick="window.PO.confirmDeleteClass(this.dataset.cls)" data-cls="${esc(x.name)}">删除</button></td></tr>`).join('')}</tbody></table><p class="muted small" style="margin-top:8px">删除班级会移除名单关系、底库模板、识别数据与 out/ 归档；删除活动请到「活动照片」页。</p></section><section class="card"><h2>设置与审计：日常通常不需要操作</h2><p class="muted">这里放的是“纠错、追溯和重建”工具。正常流程只需上传底库、上传活动照片、复核后发布；只有出现下面的特殊情况时才需要来这里。</p><div class="grid c3"><div class="hint"><b>① 我手动改了归档文件夹</b><br>例如在资源管理器里把一张照片拖给了另一个人：点“吸收手动拖动”，把变化写回系统，避免下次重建丢失。</div><div class="hint"><b>② 想恢复整理后的归档</b><br>如果 out/ 文件夹被误删或混乱，点顶部“重建归档”。原始活动照片和识别结论不会丢。</div><div class="hint"><b>③ 想追查谁改过结果</b><br>在下面的审计日志查看人工确认、改判、移出和发布等操作；也可导出 CSV 留档。</div></div></section><div class="split" style="margin-top:14px"><section class="card"><h2>识别阈值校准（高级）</h2><p class="muted">当你已经人工复核过许多灰区，才建议生成阈值建议。它不会自动改动；采纳后会影响历史批次重新匹配的判断。</p><button class="btn" onclick="window.PO.calibrate()">生成校准建议</button><div id="calResult" style="margin-top:12px"></div></section><section class="card"><h2>手动文件调整</h2><p class="muted">通常直接在网页“结果审查”里改判更安全。只有你确实在 Finder / 资源管理器中手动移动 out/ 下的归档照片时，才使用此功能。</p><button class="btn" onclick="window.PO.adopt()">吸收手动拖动</button> <a class="btn" href="/api/export/overrides.csv?${batchQ()}">导出人工修正 CSV</a><p class="muted small">人工修正会保留完整时间和原因记录，方便撤销和追溯。</p></section></div><section class="card" style="margin-top:14px"><div class="row"><div><h2>最近审计日志</h2><span class="muted small">记录所有人工确认、改判、移出、吸收和发布操作。</span></div><button class="btn sm right" onclick="window.PO.showAudit()">刷新日志</button></div><div id="auditLog" class="muted">正在读取…</div></section>`; showAudit(); }

  async function openPhoto(hash) { try {
    const d=await api('/api/photo/'+hash+'?'+clsQ());
    S.photoFaces=S.photoFaces||{}; S.photoFaces[hash]=d;
    const faces=d.faces;
    const labels=faces.map(f=>`<div class="facechip"><img src="/api/crop/${hash}/${f.face_idx}?size=100"><span><b>${esc(f.person_name||'未知')}</b><br><small>${esc(f.status)} · ${f.score.toFixed(2)} ${f.manual?'· 人工':''}</small></span><div style="display:flex;flex-direction:column;gap:4px"><button class="btn sm" onclick="window.PO.openFaceDecision('${hash}',${f.face_idx})">改判</button><button class="btn sm" title="把这张脸加入所选人员的底库" onclick="window.PO.quickTemplate('${hash}',${f.face_idx},${f.person_id||0})">加为模板</button></div></div>`).join('');
    const present=(d.present||[]).map(x=>`<div class="row" style="padding:7px 0;border-bottom:1px solid var(--line)"><b>${esc(x.name)}</b><span class="right"><button class="btn sm danger" onclick="window.PO.removePresent('${hash}',${x.person_id},'${esc(d.batch||S.batch)}')">移出</button></span></div>`).join('');
    const batch=esc(d.batch||S.batch);
    modal(`<div class="head"><h2>${esc(d.name||hash.slice(0,8))}</h2><span class="right"><button class="btn sm" onclick="window.PO.closeModal()">关闭</button></span></div>
      <div class="photo-stage"><img id="photoStageImg" src="/api/image/${hash}?max=1500" onload="window.PO.drawFaces(this,'${hash}')"><div id="faceBoxes"></div></div>
      <h3 style="margin-top:12px">照片内人脸</h3><div>${labels||'未检出脸'}</div>
      <div class="hint">每张脸都可以「改判」或直接「加为模板」；人工标记的照片同样适用。</div>
      <h3 style="margin-top:14px">这张照片上的出现记录</h3>
      <div>${present||'<span class="muted small">还没有记为任何人出现。</span>'}</div>
      ${manualAssignSection(d)}
      <div class="row" style="margin-top:16px;justify-content:flex-end"><button class="btn sm danger" onclick="window.PO.deletePhotoFromModal('${hash}','${batch}')">从活动里删除这张照片</button></div>`);
  } catch(e){toast(e.message,'bad');} }
  async function quickTemplate(hash, idx, pid) {
    const d = S.photoFaces?.[hash];
    const batch = (d && d.batch) || S.batch;
    if (!pid) { openFaceDecision(hash, idx); return; }  // 没判定归属时先选人
    try {
      const r = await post('/api/gallery/add', { batch, photo_hash: hash, face_idx: idx, person_id: pid });
      const res = r.result || {};
      if (res.added) toast('已加入底库', 'ok');
      else toast('没有加入底库：' + (res.reason || '质量不达标或与已有模板太像'), 'bad');
      await refresh();
    } catch (e) { toast(e.message, 'bad'); }
  }
  async function removePresent(hash, pid, batch) {
    const d = S.photoFaces?.[hash];
    const name = (d?.present || []).find(x => x.person_id === pid)?.name || '这个人';
    if (!confirm(`取消「${name}」在这张照片里的出现记录？`)) return;
    try {
      await post('/api/pair', { batch: batch || S.batch, photo_hash: hash, person_id: pid, action: 'absent', operator: operator(), reason: '修正：取消这张照片的人工标记' });
      toast(`已把「${name}」从这张照片移出`, 'ok');
      await refresh();
      openPhoto(hash);
    } catch (e) { toast(e.message, 'bad'); }
  }
  async function deletePhotoFromModal(hash, batch) {
    if (!confirm('从活动里删除这张照片？原文件和它的识别结果都会删除，不可撤销。')) return;
    try {
      await post('/api/photo/delete', { batch: batch || S.batch, photo_hash: hash });
      closeModal();
      toast('照片已删除', 'ok');
      await refresh();
      if (S.page === 'photos') activityPhotos();
    } catch (e) { toast(e.message, 'bad'); }
  }
  // 人工标记优先：不要求系统检出或认出这张脸，用户选谁就记谁。
  function manualAssignSection(d) {
    const present=new Set((d.present||[]).map(x=>x.person_id));
    const opts=(d.persons||[]).map(p=>`<option value="${p.person_id}">${esc(p.name)}${present.has(p.person_id)?'（已出现）':''}</option>`).join('');
    return `<h3 style="margin-top:14px">人工标记（以人工为准）</h3>
      <p class="muted small">系统没认出、甚至没检出人脸也没关系：直接选择人员并点“记为此人出现”，这张照片就会算作他在本活动的出现照片，进入归档和出勤统计。</p>
      <div class="row"><select id="manualPerson">${opts||'<option value="">名单为空</option>'}</select><button class="btn primary" onclick="window.PO.manualAssign('${d.hash}')">记为此人出现</button></div>`;
  }
  async function manualAssign(hash) {
    const pid=Number($('#manualPerson')?.value);
    if(!pid){toast('请选择人员','bad');return;}
    const d=S.photoFaces?.[hash];
    const batch=(d&&d.batch)||S.batch;
    try{
      await post('/api/pair',{batch,photo_hash:hash,person_id:pid,action:'present',operator:operator(),reason:'活动照片：人工标记此人出现'});
      closeModal();
      S.batch=batch;
      toast('已记为出现（人工结论优先）','ok');
      await refresh();
    }catch(e){toast(e.message,'bad');}
  }
  async function openFaceDecision(hash,idx) { const d=S.photoFaces?.[hash] || await api('/api/photo/'+hash+'?'+clsQ()); const f=d.faces.find(x=>x.face_idx===idx); const roster=S.data.persons.map(p=>`<option value="${p.person_id}" ${p.person_id===f.person_id?'selected':''}>${esc(p.name)}</option>`).join(''); modal(`<div class="head"><h2>改判这张脸</h2><span class="right"><button class="btn sm" onclick="window.PO.closeModal()">关闭</button></span></div><div class="row"><img style="width:160px;height:160px;object-fit:cover;border-radius:10px" src="/api/crop/${hash}/${idx}?size=300"><div><p>自动候选：<b>${esc(f.person_name||'未知')}</b> · ${f.score.toFixed(2)}</p><select id="facePerson">${roster}</select><div class="row" style="margin-top:12px"><button class="btn primary" onclick="window.PO.setFace('${hash}',${idx})">确认/改判</button><button class="btn" onclick="window.PO.setFaceUnknown('${hash}',${idx})">不认识</button>${f.person_id?`<button class="btn danger" onclick="window.PO.setPair('${hash}',${f.person_id},'absent')">移出 ${esc(f.person_name)}</button>`:''}</div><div class="row" style="margin-top:8px"><button class="btn sm" onclick="window.PO.addTemplate('${hash}',${idx})">加为模板</button><span class="muted small">把这张脸加入所选人员的底库（会过质量门控与去重，不合格会说明原因）</span></div></div></div>`); }
  async function addTemplate(hash, idx) {
    const pid = Number($('#facePerson')?.value);
    if (!pid) { toast('请先选择人员', 'bad'); return; }
    try {
      const r = await post('/api/gallery/add', { batch: S.batch, photo_hash: hash, face_idx: idx, person_id: pid });
      const res = r.result || {};
      if (res.added) toast('已加入底库', 'ok');
      else toast('没有加入底库：' + (res.reason || '质量不达标或与已有模板太像'), 'bad');
      await refresh();
    } catch (e) { toast(e.message, 'bad'); }
  }
  async function setFace(hash,idx) { await decideModalFace(hash,idx,'present',Number($('#facePerson').value)); }
  async function setFaceUnknown(hash,idx) { await decideModalFace(hash,idx,'unknown'); }
  async function decideModalFace(hash,idx,action,personId=null) { try { await post('/api/review/decide',{batch:S.batch,photo_hash:hash,face_idx:idx,action,person_id:personId,operator:operator()}); closeModal(); toast(action==='present'?'已确认并重建归档':'已标为不认识','ok'); await refresh(); } catch(e){toast(e.message,'bad');} }

  function modal(html) { $('#modalRoot').innerHTML=`<div class="modal-mask" onclick="if(event.target===this)window.PO.closeModal()"><div class="modal">${html}</div></div>`; }
  function closeModal(){ $('#modalRoot').innerHTML=''; }

  function openActivityUpload() { const today=new Date().toISOString().slice(0,10); modal(`<div class="head"><h2>新建活动并上传照片</h2><span class="right"><button class="btn sm" onclick="window.PO.closeModal()">关闭</button></span></div><form id="activityUploadForm"><p class="muted">文件夹名称会自动变成 <b>日期-活动名</b>。例如：2026-10-31 秋游 → <code class="inline">20261031-秋游</code>。</p><div class="grid c3"><label>班级<br><input id="uploadClass" type="text" value="${esc(S.cls)}" required></label><label>活动日期<br><input id="activityDate" type="date" value="${today}" required></label><label>活动名称<br><input id="activityName" type="text" placeholder="例如：秋游" required></label></div><div style="margin-top:14px"><label><b>活动照片文件夹</b><br><input id="activityFiles" type="file" accept="image/*,.heic" multiple webkitdirectory directory required></label><div class="muted small">点击后请选择整个照片文件夹；也可以改为手动多选照片。照片只保存到本机的 <code class="inline">photos/</code>。</div></div><div id="activityUploadInfo" class="hint" style="margin-top:12px">尚未选择照片。</div><div class="row" style="margin-top:14px"><button class="btn primary" type="submit">上传并自动识别</button><button class="btn" type="button" onclick="window.PO.closeModal()">取消</button></div></form>`); const f=$('#activityFiles');f.onchange=()=>{const n=f.files.length;setText('#activityUploadInfo',n?`已选择 ${n} 个文件；上传后会自动检测、匹配、归档并生成报告。`:'尚未选择照片。');}; $('#activityUploadForm').onsubmit=submitActivityUpload; }
  async function submitActivityUpload(e) { e.preventDefault(); const files=$('#activityFiles').files; if(!files.length){toast('请选择活动照片文件夹或照片','bad');return;} const form=new FormData();form.append('class_name',$('#uploadClass').value.trim());form.append('activity',$('#activityName').value.trim());form.append('activity_date',$('#activityDate').value);for(const f of files)form.append('files',f,f.webkitRelativePath||f.name); const btn=e.target.querySelector('[type=submit]');btn.disabled=true;btn.textContent=`正在上传 ${files.length} 张并识别…`;setText('#activityUploadInfo','正在本机处理；照片数量多时请保持本页面打开。');try{const res=await fetch('/api/activity/upload',{method:'POST',body:form});const d=await res.json();if(!res.ok)throw new Error(d.detail||'上传失败');S.cls=d.class;S.batch=d.batch;closeModal();toast(`活动「${d.batch}」完成：${d.summary.present} 条出现，${d.summary.review} 条待复核`,'ok');if(d.summary?.no_face)toast(`有 ${d.summary.no_face} 张照片没检出人脸：已保留在活动里，但不会产生出勤结论`,'');const moved=d.run?.moved_out||[];if(moved.length)toast(`照片搬家：${moved.map(m=>`${m.n} 张原属「${m.batch}」，已移入本批次；该批次已退回待复核`).join('；')}`,'');await refresh();}catch(err){toast(err.message,'bad');if(btn?.isConnected){btn.disabled=false;btn.textContent='上传并自动识别';} } }

  // 从选择结果里推断「哪一层目录是人名」。
  // 用户选中的根目录可能多套了一层（例如 桌面/底库/张三/1.jpg），
  // 所以不能写死第二段，而是把每一层都算一遍，默认取「名字最多」的那层。
  const treeLevels = (files) => {
    const levels = new Map(); // depth -> Map(person -> count)
    let tooShallow = 0;
    for (const f of files) {
      const parts = (f.webkitRelativePath || f.name).split('/').filter(Boolean);
      if (parts.length < 3) { tooShallow += 1; continue; }
      for (let d = 1; d <= parts.length - 2; d += 1) {
        const name = parts[d];
        if (!levels.has(d)) levels.set(d, new Map());
        const m = levels.get(d);
        m.set(name, (m.get(name) || 0) + 1);
      }
    }
    let best = 1, bestCount = -1;
    for (const [d, m] of levels) {
      if (m.size > bestCount || (m.size === bestCount && d < best)) { best = d; bestCount = m.size; }
    }
    return { levels, best, tooShallow };
  };
  const renderTreeLevels = () => {
    const f = $('#treeFiles'), sel = $('#treeDepth');
    if (!f || !sel) return;
    const { levels, best, tooShallow } = treeLevels(f.files);
    if (!levels.size) {
      sel.style.display = 'none';
      setText('#treeInfo', '没有识别到子文件夹，请确认选择的是「包含人名子文件夹」的父文件夹。');
      return;
    }
    sel.style.display = '';
    sel.innerHTML = [...levels.entries()].sort((a, b) => a[0] - b[0]).map(([d, m]) => {
      const names = [...m.keys()];
      const total = [...m.values()].reduce((a, b) => a + b, 0);
      const preview = names.slice(0, 3).join('、') + (names.length > 3 ? ' 等' : '');
      return `<option value="${d}" ${d === best ? 'selected' : ''}>第 ${d} 层：${esc(preview)}（${names.length} 人 / ${total} 张）</option>`;
    }).join('');
    updateTreeInfo(tooShallow);
  };
  const updateTreeInfo = (tooShallow) => {
    const f = $('#treeFiles'), sel = $('#treeDepth');
    if (!f || !sel || !sel.value) return;
    const { levels, tooShallow: ts } = treeLevels(f.files);
    const m = levels.get(Number(sel.value));
    if (!m) return;
    const total = [...m.values()].reduce((a, b) => a + b, 0);
    const shallow = tooShallow === undefined ? ts : tooShallow;
    setText('#treeInfo', `按第 ${sel.value} 层分组：识别到 ${m.size} 个人、${total} 张图片${shallow ? `；另有 ${shallow} 张层级太浅将被忽略` : ''}。`);
  };
  function openGalleryTreeUpload() {
    modal(`<div class="head"><h2>一键上传底库文件夹</h2><span class="right"><button class="btn sm" onclick="window.PO.closeModal()">关闭</button></span></div>
      <p class="muted">选择整个父文件夹。系统把子文件夹的名字当作人名，把它里面的照片当作这个人的底库参考照，一次把所有人生成模板。</p>
      <pre class="tree">底库/
  张三/   1.jpg  2.jpg  3.jpg
  李四/   1.jpg  2.jpg</pre>
      <input id="treeFiles" type="file" accept="image/*,.heic" multiple webkitdirectory directory>
      <div id="treeDepthRow" class="row" style="margin-top:12px;display:none"><label class="small">按哪一层目录当人名<br><select id="treeDepth" onchange="window.PO.updateTreeInfo()"></select></label><button class="btn sm" type="button" onclick="window.PO.renderTreeLevels()">重新识别</button></div>
      <div id="treeInfo" class="hint" style="margin-top:10px">尚未选择文件夹。</div>
      <div class="hint" style="margin-top:8px">根目录下直接放的照片会被忽略；已存在的人员会追加模板，不会覆盖旧底库。如果多套了一层父目录（如 桌面/底库/张三/图），用上面的下拉选「第 2 层」即可。</div>
      <div class="row" style="margin-top:14px"><button class="btn primary" id="treeSubmit" onclick="window.PO.submitGalleryTree()">上传并注册</button><button class="btn" onclick="window.PO.closeModal()">取消</button></div>`);
    const f = $('#treeFiles');
    if (f) f.onchange = () => { const row = $('#treeDepthRow'); if (row) row.style.display = 'flex'; renderTreeLevels(); };
  }
  async function submitGalleryTree() {
    const f = $('#treeFiles');
    if (!f?.files?.length) { toast('请先选择包含人名子文件夹的文件夹', 'bad'); return; }
    const sel = $('#treeDepth');
    const depth = Number(sel?.value || 1);
    const { levels } = treeLevels(f.files);
    const m = levels.get(depth);
    if (!m) { toast('没有找到「人名/图片」结构', 'bad'); return; }
    const total = [...m.values()].reduce((a, b) => a + b, 0);
    const form = new FormData();
    form.append('class_name', S.cls);
    form.append('depth', String(depth));
    for (const file of f.files) form.append('files', file, file.webkitRelativePath || file.name);
    const btn = $('#treeSubmit');
    if (btn) { btn.disabled = true; btn.textContent = '正在处理…'; }
    setText('#treeInfo', `正在注册 ${m.size} 个人、${total} 张图片；首次识别较慢，请保持页面打开。`);
    try {
      const res = await fetch('/api/gallery/upload_tree', { method: 'POST', body: form });
      const d = await res.json();
      if (!res.ok) throw new Error(d.detail || '上传失败');
      closeModal();
      toast(`已入库 ${d.n_added} 张模板，覆盖 ${d.n_persons} 人`,'ok');
      if (d.n_rejected || d.skipped_files) setTimeout(() => showTreeResult(d), 0);
      await refresh();
      if (S.page === 'gallery') gallery();
    } catch (e) {
      toast(e.message, 'bad');
      if (btn) { btn.disabled = false; btn.textContent = '上传并注册'; }
    }
  }
  function showTreeResult(d) {
    const rows = d.persons.map(p => `<tr><td><b>${esc(p.person)}</b></td><td class="num">${p.added}</td><td>${p.n_rejected ? `<span style="color:var(--warn)">${p.n_rejected} 张未入库${p.rejected?.length ? '：' + esc(p.rejected[0]) : ''}</span>` : '<span style="color:var(--ok)">全部入库</span>'}</td></tr>`).join('');
    modal(`<div class="head"><h2>批量入库结果</h2><span class="right"><button class="btn sm" onclick="window.PO.closeModal()">关闭</button></span></div>
      <p class="muted">共 ${d.n_persons} 人、成功入库 ${d.n_added} 张${d.n_rejected ? `，${d.n_rejected} 张未通过质量检查` : ''}${d.skipped_files ? `，${d.skipped_files} 张因不在人名文件夹内被忽略` : ''}。</p>
      <table class="tbl"><thead><tr><th>人员</th><th class="num">入库</th><th>备注</th></tr></thead><tbody>${rows}</tbody></table>
      <p class="hint" style="margin-top:10px">未入库常见原因：脸太小、图片模糊、多人合照里主脸不明确，或同一个人重复照片。换更清晰的正脸照再传即可。</p>`);
  }
  function openGalleryUpload() { modal(`<div class="head"><h2>上传底库参考照片</h2><span class="right"><button class="btn sm" onclick="window.PO.closeModal()">关闭</button></span></div><form id="galleryUploadForm"><p class="muted">给同一个人一次选多张清晰照片。建议 3～10 张，包含正脸、轻微侧脸、不同光线；不要上传多人合照。</p><div class="grid c3"><label>班级<br><input id="galleryUploadClass" type="text" value="${esc(S.cls)}" required></label><label>人员姓名<br><input id="galleryPersonName" type="text" placeholder="例如：张三" required></label><label><b>参考照片文件夹</b><br><input id="galleryFiles" type="file" accept="image/*,.heic" multiple webkitdirectory directory required></label></div><div class="muted small" style="margin-top:8px">点击后请选择该人员的整个照片文件夹；Chrome / Edge 支持。也可在弹出的选择器中手动多选图片。</div><div id="galleryUploadInfo" class="hint" style="margin-top:12px">每张图会选面积最大的脸入库；多人且大小接近的照片会被拒绝。</div><div class="row" style="margin-top:14px"><button class="btn primary" type="submit">上传并注册底库</button><button class="btn" type="button" onclick="window.PO.closeModal()">取消</button></div></form>`);const f=$('#galleryFiles');f.onchange=()=>setText('#galleryUploadInfo',f.files.length?`已选择 ${f.files.length} 张图片，准备注册模板。`:'请至少选择一个底库照片文件夹。');$('#galleryUploadForm').onsubmit=submitGalleryUpload; }
  async function submitGalleryUpload(e) {e.preventDefault();const files=$('#galleryFiles').files;if(!files.length){toast('请选择底库照片文件夹或图片','bad');return;}const form=new FormData();form.append('class_name',$('#galleryUploadClass').value.trim());form.append('person_name',$('#galleryPersonName').value.trim());for(const f of files)form.append('files',f,f.webkitRelativePath||f.name);const btn=e.target.querySelector('[type=submit]');btn.disabled=true;btn.textContent='正在注册底库…';try{const res=await fetch('/api/gallery/upload',{method:'POST',body:form});const d=await res.json();if(!res.ok)throw new Error(d.detail||'上传失败');S.cls=d.class;const rejected=d.result.rejected||[];closeModal();const msg=`${d.person}：${d.result.message||('成功入库 '+d.result.added+' 张')}`;toast(msg,d.result.added?'ok':'bad');await refresh();S.page='gallery';render();if(rejected.length){setTimeout(()=>modal(`<div class="head"><h2>底库照片未入库</h2><span class="right"><button class="btn sm" onclick="window.PO.closeModal()">关闭</button></span></div><p>${esc(d.person)} 的照片没有全部通过质量检查：</p><ul>${rejected.map(x=>`<li>${esc(x)}</li>`).join('')}</ul><p class="hint">常见原因：脸太小、图片模糊、多人合照中主脸不明确，或同一个人的重复照片。请选择正脸更大、更清晰的单人照片后重新上传。</p>`),0);}}catch(err){toast(err.message,'bad');if(btn?.isConnected){btn.disabled=false;btn.textContent='上传并注册底库';}}}

  async function setPair(hash,pid,action) { try { await post('/api/pair',{batch:S.batch,photo_hash:hash,person_id:pid,action,operator:operator(),reason:action==='absent'?'结果审查：移出此人':''}); closeModal(); toast(action==='absent'?'已移出并重建':'已加入并重建','ok'); await refresh(); }catch(e){toast(e.message,'bad');} }
  async function confirmAbsent(pid,confirmed) { try{await post('/api/absent/confirm',{batch:S.batch,person_id:pid,confirmed,by:operator()}); toast(confirmed?'已确认未出现':'已取消确认','ok'); await refresh();}catch(e){toast(e.message,'bad');} }
  // 未出现的人多半是底库没照片：直接把这个人从活动照片里补进底库，再重跑本批次。
  function openPersonUpload(pid) {
    const p=(S.data?.persons||[]).find(x=>x.person_id===pid);
    if(!p){toast('找不到这个人员','bad');return;}
    const hasBatch = !!S.batch;
    modal(`<div class="head"><h2>给 ${esc(p.name)} 补本次活动照片</h2><span class="right"><button class="btn sm" onclick="window.PO.closeModal()">关闭</button></span></div>
      <p class="muted">选择 <b>${esc(S.batch||'当前活动')}</b> 里确实拍到 ${esc(p.name)} 的照片（1～10 张）。这些照片只加入本次活动，并直接记为「${esc(p.name)} 出现」，会计入归档和出勤统计。</p>
      <p class="hint">人工添加优先：即使系统没检出人脸或没认出 ${esc(p.name)}，也会按你的选择记为出现，不会拦截。</p>
      <input id="personPhotoFiles" type="file" accept="image/*,.heic" multiple>
      <div id="personPhotoInfo" class="hint" style="margin-top:10px">尚未选择照片。</div>
      <div class="hint" style="margin-top:10px">活动抓拍一般达不到底库要求，这里<b>不会</b>把照片加进底库。确实想补底库时，请在照片详情里对单张清晰正脸点「加为模板」。</div>
      <div class="row" style="margin-top:14px"><button class="btn primary" id="personPhotoSubmit" onclick="window.PO.submitPersonUpload(${pid})" ${hasBatch?'':'disabled'}>加入本次活动</button><button class="btn" onclick="window.PO.closeModal()">取消</button></div>
      ${hasBatch?'':'<p class="hint" style="margin-top:10px">当前没有选中活动，请先在顶部选择批次。</p>'}`);
    const f=$('#personPhotoFiles'); if(f) f.onchange=()=>setText('#personPhotoInfo', f.files.length?`已选择 ${f.files.length} 张照片。`:'尚未选择照片。');
  }
  async function submitPersonUpload(pid) {
    const f=$('#personPhotoFiles');
    if(!f?.files?.length){toast('请先选择照片','bad');return;}
    const form=new FormData();
    form.append('class_name',S.cls);
    form.append('batch',S.batch||'');
    form.append('person_id',String(pid));
    for(const file of f.files) form.append('files',file,file.name);
    const btn=$('#personPhotoSubmit'); if(btn){btn.disabled=true;btn.textContent='正在加入并重新归档…';}
    try {
      const res=await fetch('/api/person/photos/add',{method:'POST',body:form});
      const d=await res.json();
      if(!res.ok) throw new Error(d.detail||'上传失败');
      closeModal();
      const msg = `${d.person}：已加入 ${d.photos_added} 张活动照片；本活动现在有 ${d.present_after} 张他/她的照片`;
      toast(msg,'ok');
      if(d.no_face) toast(`有 ${d.no_face} 张没检出人脸，已按人工标记计入`, 'ok');
      if(d.photos_skipped) toast(`${d.photos_skipped} 张已在其他活动里，已跳过`, '');
      await refresh();
    } catch(e){ toast(e.message,'bad'); if(btn){btn.disabled=false;btn.textContent='加入本次活动';} }
  }
  async function copyWechat() {
    const text = 'luoyi8847';
    const legacyCopy = () => {
      try {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.setAttribute('readonly', '');
        ta.style.cssText = 'position:fixed;left:-9999px;top:0';
        document.body.appendChild(ta);
        ta.select();
        const ok = document.execCommand('copy');
        ta.remove();
        return ok;
      } catch (e) { return false; }
    };
    try {
      await navigator.clipboard.writeText(text);
      toast('微信号已复制：' + text, 'ok');
    } catch (e) {
      if (legacyCopy()) toast('微信号已复制：' + text, 'ok');
      else toast('微信号：' + text, '');
    }
  }
  async function unpublish() {
    if(!confirm('撤回发布后，报告会变回草稿，需要重新确认才能再次发布。确定？')) return;
    try { await post('/api/unpublish',{batch:S.batch}); toast('已撤回发布','ok'); await refresh(); }
    catch(e){ toast(e.message,'bad'); }
  }
  async function publish() { const by=prompt('请填写确认人（会写入正式报告页眉）：',operator()); if(!by)return; try{const r=await post('/api/publish',{batch:S.batch,by});localStorage.setItem('photoorg.operator',by);await refresh();toast(`已发布正式名单 · 当前状态 ${S.data?.state?.label||r.state?.label||'PUBLISHED'}`,'ok');}catch(e){toast(e.message,'bad');} }
  async function rebuild(){ try{const r=await post('/api/rebuild',{batch:S.batch});toast(`归档已重建：新增 ${r.n_add}，移除 ${r.n_remove}`,'ok');await refresh();}catch(e){toast(e.message,'bad');} }
  async function adopt(){try{const r=await post('/api/adopt',{batch:S.batch});toast(`已吸收手动调整：+${r.result.added} / -${r.result.removed}`,'ok');await refresh();}catch(e){toast(e.message,'bad');}}
  async function calibrate(){ try{const r=await post('/api/calibrate',{batch:S.batch}); const c=r.current,s=r.suggested; $('#calResult').innerHTML=`<div class="hint"><b>依据：</b>${esc(r.basis)}（${r.n_labels} 组）<br>当前：T_high=${c.t_high.toFixed(2)} / T_low=${c.t_low.toFixed(2)}<br><b>建议：</b>T_high=${s.t_high.toFixed(2)} / T_low=${s.t_low.toFixed(2)}<br>灰区 ${Math.round(c.grey*100)}% → ${Math.round(s.grey*100)}%，漏判 ${Math.round(c.miss*1000)/10}% → ${Math.round(s.miss*1000)/10}%<br><button class="btn primary sm" style="margin-top:8px" onclick="window.PO.applyCal(${s.t_high},${s.t_low},${s.margin})">采纳建议</button></div>`;}catch(e){toast(e.message,'bad');} }
  async function applyCal(hi,lo,margin){if(!confirm('阈值变化会影响历史批次的结论。确定采纳？'))return;try{await post('/api/calibrate/apply',{batch:S.batch,t_high:hi,t_low:lo,margin,by:operator()});toast('阈值已保存；重建或重跑匹配后生效','ok');}catch(e){toast(e.message,'bad');}}
  async function showAudit(){try{const r=await api('/api/audit?'+batchQ());const el=$('#auditLog');if(!el)return;el.innerHTML=r.items.length?`<table class="tbl"><thead><tr><th>时间</th><th>动作</th><th>照片</th><th>人员</th><th>来源 / 原因</th></tr></thead><tbody>${r.items.map(x=>`<tr><td>${esc(x.at.slice(0,16).replace('T',' '))}</td><td>${esc(x.scope)} · ${esc(x.action)}${x.superseded?'（已撤销）':''}</td><td>${esc(x.photo)}</td><td>${esc(x.person)}</td><td>${esc(x.source)} ${esc(x.reason||'')}</td></tr>`).join('')}</tbody></table>`:'暂无人工修正。';}catch(e){toast(e.message,'bad');}}
  async function showDiff(){try{const r=await api('/api/diff?'+batchQ());modal(`<div class="head"><h2>纯自动 vs 含人工</h2><span class="right"><button class="btn sm" onclick="window.PO.closeModal()">关闭</button></span></div><p>纯自动 <b>${r.diff.auto_total}</b> 条，含人工 <b>${r.diff.final_total}</b> 条。</p><table class="tbl"><thead><tr><th>人员</th><th>自动</th><th>最终</th><th>变化</th><th>人工加/减</th></tr></thead><tbody>${r.diff.persons.filter(x=>x.delta||x.manual_added||x.manual_removed).map(x=>`<tr><td>${esc(x.name)}</td><td>${x.auto}</td><td>${x.final}</td><td>${x.delta>0?'+':''}${x.delta}</td><td>${x.manual_added}/${x.manual_removed}</td></tr>`).join('')||'<tr><td colspan="5">尚无人工差异</td></tr>'}</tbody></table>`);}catch(e){toast(e.message,'bad');}}
  async function openGallery(pid,name){try{const r=await api('/api/gallery?person_id='+pid+'&'+clsQ());modal(`<div class="head"><h2>${esc(name)} 的模板</h2><span class="right"><button class="btn sm" onclick="window.PO.closeModal()">关闭</button></span></div><div class="thumbs">${r.templates.map(t=>`<div class="thumb"><img src="${t.url}?${clsQ()}"><span class="tag">${t.quality.toFixed(2)} · ${esc(t.source)}</span><button class="thumb-del" title="删除这张模板" onclick="event.stopPropagation();window.PO.removeTemplate(${t.id},${pid},'${esc(name)}')">✕</button></div>`).join('')||'<span class="muted">还没有模板</span>'}</div><p class="hint">如需从活动照片补模板，请在“结果审查”打开照片，选中对应脸，点“加为模板”。</p>`);}catch(e){toast(e.message,'bad');}}
  // 人名是唯一标识之外唯一会出现在报告和归档目录里的东西，改名后要重建 out/。
  function confirmDeletePerson(pid) {
    const p = (S.gallery?.persons||[]).find(x=>x.person_id===pid) || (S.data?.persons||[]).find(x=>x.person_id===pid);
    if (!p) { toast('找不到这个人员','bad'); return; }
    modal(`<div class="head"><h2>从本班删除「${esc(p.name)}」</h2><span class="right"><button class="btn sm" onclick="window.PO.closeModal()">关闭</button></span></div>
      <p>会删除这个人的 <b>${p.n_templates ?? '—'} 张底库模板</b>，并把他从当前班级名单移除；相关活动的出勤结论会重新计算。</p>
      <p class="muted small">历史照片和人工修正记录不会删除。如果这个人还在别的班级，只从当前班级移除。</p>
      <div class="row" style="margin-top:14px"><button class="btn danger" onclick="window.PO.doDeletePerson(${pid})">确认删除</button><button class="btn" onclick="window.PO.closeModal()">取消</button></div>`);
  }
  async function doDeletePerson(pid) {
    try {
      const r = await post('/api/person/delete',{person_id:pid,class_name:S.cls});
      closeModal();
      toast(`已从本班删除「${r.deleted}」，重建了 ${r.batches} 个批次`,'ok');
      await refresh();
      if (S.page === 'gallery') gallery();
    } catch(e) { toast(e.message,'bad'); }
  }
  async function removeTemplate(tid, pid, name) {
    if (!confirm('删除这张底库模板？删除后不能恢复。')) return;
    try {
      await post('/api/gallery/remove?template_id='+tid+'&'+clsQ(), {});
      toast('模板已删除','ok');
      await refresh();
      if (S.page === 'gallery') gallery();
      openGallery(pid, name);
    } catch(e) { toast(e.message,'bad'); }
  }
  async function renamePerson(pid) {
    const p = (S.gallery?.persons||[]).find(x=>x.person_id===pid) || (S.data?.persons||[]).find(x=>x.person_id===pid);
    if(!p){ toast('找不到这个人员','bad'); return; }
    const name = prompt(`把「${p.name}」改成：`, p.name);
    if(name === null) return;
    const next = name.trim();
    if(!next || next === p.name) return;
    try {
      const r = await post('/api/person/rename',{person_id:pid,new_name:next,class_name:S.cls});
      toast(`已改名为「${r.name}」${r.rebuilt?`，重建了 ${r.rebuilt} 个批次的归档`:''}`,'ok');
      await refresh();
      if(S.page==='gallery') gallery();
    } catch(e){ toast(e.message,'bad'); }
  }
  function acceptCluster(cid){const c=S.clusters?.find(x=>x.cid===cid);if(!c)return;const name=prompt(`给簇 #${cid}（${c.size} 张脸）取名：`);if(!name)return;post('/api/clusters/accept',{batch:S.batch,name,members:c.members,operator:operator()}).then(async()=>{toast(`已将簇命名为 ${name}`,'ok');await refresh();clusters();}).catch(e=>toast(e.message,'bad'));}
  function nextReview(){ const i=S.review.idx+1; if(i>=S.review.items.length){refresh().then(review);}else renderReviewItem(i); }
  function boxReview(img,b){ if(!b?.length)return; const e=$('#reviewBox'), sx=img.clientWidth/img.naturalWidth,sy=img.clientHeight/img.naturalHeight;e.style.left=(b[0]*sx)+'px';e.style.top=(b[1]*sy)+'px';e.style.width=((b[2]-b[0])*sx)+'px';e.style.height=((b[3]-b[1])*sy)+'px'; }
  function drawFaces(img,hash){const d=S.photoFaces?.[hash];if(!d)return;const out=$('#faceBoxes');out.innerHTML='';const sx=img.clientWidth/img.naturalWidth,sy=img.clientHeight/img.naturalHeight;d.faces.forEach(f=>{const b=f.bbox,e=document.createElement('i');e.className='fb '+f.status;e.title=`${f.person_name||'未知'} · ${f.score.toFixed(2)}`;e.style.cssText=`left:${b[0]*sx}px;top:${b[1]*sy}px;width:${(b[2]-b[0])*sx}px;height:${(b[3]-b[1])*sy}px`;e.onclick=()=>openFaceDecision(hash,f.face_idx);out.append(e);});}
  function go(p){S.page=p;render();}
  // 切回网页时重新从本机数据库取一次数据；同一浏览器开多个标签或刚执行发布时，
  // 页面会自动反映最新状态，而不是继续显示内存里的旧快照。
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh(true).catch(e=>toast('刷新最新数据失败：'+e.message,'bad'));});
  function newPerson(){const x=S.review.items[S.review.idx];const n=prompt('新人的名字：');if(n)decide('new',null,n);}
  // #tabs 自身不会被替换，因此监听器只注册一次；按钮每次 render 后仍可切页。
  $('#tabs').addEventListener('click',e=>{
    const button=e.target.closest('button[data-page]');
    if(!button || !$('#tabs').contains(button)) return;
    e.preventDefault();
    go(button.dataset.page);
  });
  $('#classSelect').addEventListener('change',async e=>{S.cls=e.target.value;S.batch='';await refresh();});
  $('#batchSelect').addEventListener('change',async e=>{S.batch=e.target.value;await refresh();});
  $('#rebuildBtn').onclick=rebuild;
  document.addEventListener('keydown',e=>{if(S.page!=='review'||!S.review?.items?.length||$('#modalRoot').innerHTML)return;const x=S.review.items[S.review.idx];if(e.key>='1'&&e.key<='3'){const c=x.candidates[Number(e.key)-1];if(c){e.preventDefault();decide('present',c.person_id);}}else if(e.key.toLowerCase()==='x'){e.preventDefault();decide('unknown');}else if(e.key==='+'){e.preventDefault();newPerson();}else if(e.key==='Enter'){e.preventDefault();nextReview();}else if(e.key.toLowerCase()==='z'){post('/api/undo',{batch:S.batch}).then(()=>refresh().then(review));}});

  // ---------------- 年度统计：活动 × 人员
  const rangeQS = () => `from=${encodeURIComponent((S.range&&S.range.from)||'')}&to=${encodeURIComponent((S.range&&S.range.to)||'')}`;
  function rangeInputs() {
    const f=(S.range&&S.range.from)||'', t=(S.range&&S.range.to)||'';
    return `<div class="row" style="margin-top:10px;align-items:flex-end"><label class="small">开始日期<br><input id="rangeFrom" type="date" value="${f}"></label><label class="small">结束日期<br><input id="rangeTo" type="date" value="${t}"></label><button class="btn" onclick="window.PO.applyRange()">筛选</button><button class="btn ghost" onclick="window.PO.clearRange()">全部时间</button><span class="muted small">${f||t?`当前范围：${f||'最早'} ～ ${t||'最新'}`:'当前范围：全部活动'}</span></div>`;
  }
  async function stats() {
    $('#main').innerHTML=`<section class="card"><div class="row"><div><h2>年度统计：活动 × 人员</h2><div class="muted small">行是人员、列是活动；每一格 = 这个人在该活动里出现的照片张数。点人名可以看他的全部照片。</div></div><span class="right"><a class="btn primary" download href="/api/export/matrix.csv?${clsQ()}&${rangeQS()}">下载 CSV（Excel）</a></span></div>${rangeInputs()}<div id="matrixWrap" class="matrix-wrap"><span class="muted" style="display:block;padding:14px">正在统计…</span></div></section>`;
    try {
      const r = await api('/api/matrix?'+clsQ()+'&'+rangeQS());
      S.matrix = r;
      const head=`<tr><th class="sticky">人员</th>${r.batches.map(b=>`<th class="num">${esc(b.activity||b.batch)}<br><span class="thdate">${esc(b.date||'—')}</span></th>`).join('')}<th class="num">合计</th></tr>`;
      const body=r.persons.map(p=>`<tr><td class="sticky"><button class="linkbtn" onclick="window.PO.openPerson(${p.person_id})">${esc(p.name)}</button></td>${r.batches.map(b=>{const n=p.counts[b.batch]||0;return `<td class="num${n?'':' zero'}">${n||''}</td>`;}).join('')}<td class="num"><b>${p.total}</b></td></tr>`).join('');
      const foot=`<tr><td class="sticky"><b>合计</b></td>${r.batches.map(b=>`<td class="num"><b>${r.batch_totals[b.batch]||0}</b></td>`).join('')}<td class="num"><b>${r.n_pairs_total}</b></td></tr><tr class="muted"><td class="sticky">照片数</td>${r.batches.map(b=>`<td class="num">${b.n_photos}</td>`).join('')}<td class="num">${r.n_photos_total}</td></tr>`;
      $('#matrixWrap').innerHTML = r.batches.length
        ? `<table class="tbl matrix"><thead>${head}</thead><tbody>${body}</tbody><tfoot>${foot}</tfoot></table>`
        : `<p class="muted">这个时间范围内还没有活动。</p>`;
    } catch(e) { $('#matrixWrap').innerHTML=`<p class="muted">${esc(e.message)}</p>`; }
  }
  function applyRange(){ S.range={from:$('#rangeFrom')?.value||'', to:$('#rangeTo')?.value||''}; if(S.page==='person') personPage(); else stats(); }
  function clearRange(){ S.range={from:'',to:''}; if(S.page==='person') personPage(); else stats(); }

  // ---------------- 人员明细：这个人在所有活动里的照片
  function openPerson(pid){ S.personId=pid; S.page='person'; render(); }
  async function personPage() {
    const pid=S.personId;
    $('#main').innerHTML='<div class="empty"><div class="big">⏳</div>正在汇总这个人的照片…</div>';
    try {
      const r = await api(`/api/person/photos?person_id=${pid}&${clsQ()}&${rangeQS()}`);
      S.personData=r;
      const groups=r.groups.map(g=>`<div class="activity-group"><div class="activity-head"><b>${esc(g.activity||g.batch)}</b><span class="muted small">${esc(g.date||'无日期')} · ${g.count} 张</span></div><div class="thumbs lg" style="margin-top:10px">${g.photos.map(x=>`<div class="thumb" title="${esc(x.name)}" onclick="window.PO.openPhoto('${x.photo_hash}')"><img loading="lazy" src="${x.face_idx>=0?`/api/crop/${x.photo_hash}/${x.face_idx}?size=200`:`/api/image/${x.photo_hash}?max=320`}"></div>`).join('')}</div></div>`).join('');
      $('#main').innerHTML=`<section class="card"><div class="row"><button class="btn sm" onclick="window.PO.go('stats')">← 返回年度统计</button><span class="right muted small">点照片看大图</span></div><h2 style="margin-top:12px;display:flex;align-items:center;gap:10px">${avatar(r.name)}<span>${esc(r.name)}</span><span class="chip">共 ${r.total} 张</span></h2>${rangeInputs()}</section><section class="card">${groups||'<p class="muted">这个时间范围内没有他/她的照片。</p>'}</section>`;
    } catch(e) {
      $('#main').innerHTML=`<section class="card"><button class="btn sm" onclick="window.PO.go('stats')">← 返回年度统计</button><p class="muted" style="margin-top:10px">${esc(e.message)}</p></section>`;
    }
  }

  // ---------------- 活动照片：查看 / 追加 / 删除
  async function activityPhotos() {
    const r = await api('/api/batch/photos?'+batchQ());
    S.batchPhotos=r;
    const photos=r.photos.map(x=>`<div class="thumb big"><img loading="lazy" src="/api/image/${x.photo_hash}?max=400" onclick="window.PO.openPhoto('${x.photo_hash}')"><span class="tag${x.n_faces?'':' warn'}">${x.n_faces ? x.n_faces + ' 张脸' : '未检出人脸'}</span><button class="thumb-del" title="从本活动删除这张照片" onclick="event.stopPropagation();window.PO.deletePhoto('${x.photo_hash}')">✕</button></div>`).join('');
    $('#main').innerHTML=`<section class="card"><div class="row"><div><h2>活动照片 · ${esc(S.batch)}</h2><div class="muted small">共 ${r.count} 张。删除单张会删掉它的识别结果并重建归档。</div></div><span class="right"><button class="btn" onclick="window.PO.openBatchAdd()">+ 添加照片</button><button class="btn danger" onclick="window.PO.confirmDeleteBatch()">删除本活动</button></span></div><div class="row" style="margin-top:10px"><a class="btn sm" href="/api/export/report.md?${batchQ()}">下载本活动报告 (md)</a><a class="btn sm" href="/api/export/report.csv?${batchQ()}">下载明细 (csv)</a></div><div class="thumbs grid-photos">${photos||'<span class="muted">这个活动没有照片。</span>'}</div></section>`;
  }
  function openBatchAdd() {
    modal(`<div class="head"><h2>往「${esc(S.batch)}」添加照片</h2><span class="right"><button class="btn sm" onclick="window.PO.closeModal()">关闭</button></span></div><p class="muted">选择要追加进这个活动的照片（可多选）。添加后会重新识别整个活动并更新报告。</p><input id="batchAddFiles" type="file" accept="image/*,.heic" multiple><div id="batchAddInfo" class="hint" style="margin-top:10px">尚未选择照片。</div><div class="row" style="margin-top:14px"><button class="btn primary" id="batchAddSubmit" onclick="window.PO.submitBatchAdd()">上传并重新识别</button><button class="btn" onclick="window.PO.closeModal()">取消</button></div>`);
    const f=$('#batchAddFiles'); if(f) f.onchange=()=>setText('#batchAddInfo', f.files.length?`已选择 ${f.files.length} 张。`:'尚未选择照片。');
  }
  async function submitBatchAdd() {
    const f=$('#batchAddFiles'); if(!f?.files?.length){toast('请先选择照片','bad');return;}
    const form=new FormData(); form.append('class_name',S.cls); form.append('batch',S.batch);
    for(const file of f.files) form.append('files',file,file.name);
    const btn=$('#batchAddSubmit'); if(btn){btn.disabled=true;btn.textContent='正在识别…';}
    try {
      const res=await fetch('/api/batch/photos',{method:'POST',body:form});
      const d=await res.json();
      if(!res.ok) throw new Error(d.detail||'上传失败');
      closeModal(); toast(`已添加 ${d.saved} 张，本活动现有 ${d.run.n_new} 张新增 / ${d.run.n_skipped} 张复用`,'ok');
      await refresh(); activityPhotos();
    } catch(e){ toast(e.message,'bad'); if(btn){btn.disabled=false;btn.textContent='上传并重新识别';} }
  }
  async function deletePhoto(hash) {
    if(!confirm('从本活动删除这张照片？原文件、识别结果和归档会一起删掉，不可撤销。')) return;
    try { await post('/api/photo/delete',{batch:S.batch,photo_hash:hash}); toast('照片已删除','ok'); await refresh(); activityPhotos(); }
    catch(e){ toast(e.message,'bad'); }
  }
  function confirmDeleteBatch() {
    modal(`<div class="head"><h2>删除活动「${esc(S.batch)}」</h2><span class="right"><button class="btn sm" onclick="window.PO.closeModal()">关闭</button></span></div><p>会删除这个活动的识别结果、人工复核记录、状态和 out/ 归档，不可撤销。</p><label class="row" style="gap:8px;justify-content:flex-start"><input id="delBatchPhotos" type="checkbox" checked> 同时删除 photos/${esc(S.batch)}/ 下的原始照片文件</label><div class="hint" style="margin-top:10px">不勾选则只删数据库记录与归档，原始照片文件保留在磁盘上。</div><div class="row" style="margin-top:14px"><button class="btn danger" onclick="window.PO.doDeleteBatch()">确认删除</button><button class="btn" onclick="window.PO.closeModal()">取消</button></div>`);
  }
  async function doDeleteBatch() {
    const del=!!$('#delBatchPhotos')?.checked;
    try {
      const r=await post('/api/batch/delete',{batch:S.batch,delete_photos:del});
      closeModal(); toast(`已删除活动「${r.batch}」，${r.deleted_photos} 张照片记录`,'ok');
      S.batch=''; await refresh(); go('overview');
    } catch(e){ toast(e.message,'bad'); }
  }

  // ---------------- 删除班级
  async function _createClass(name) {
    name = (name || '').trim();
    if (!name) { toast('请输入班级名', 'bad'); return; }
    try {
      const r = await post('/api/class/create', { name });
      closeModal();
      toast(r.created ? `已创建班级「${r.name}」` : `班级「${r.name}」已存在`, r.created ? 'ok' : '');
      S.cls = r.name; S.batch = '';
      await refresh();
      if (S.page === 'settings') settings();
    } catch(e) { toast(e.message, 'bad'); }
  }
  async function createClass() { await _createClass($('#newClassName')?.value); }
  function openCreateClass() {
    modal(`<div class="head"><h2>新建班级</h2><span class="right"><button class="btn sm" onclick="window.PO.closeModal()">关闭</button></span></div>
      <p class="muted">班级之间的数据完全独立：名单、底库、活动、出勤统计都分开。创建后会立即切换到新班级。</p>
      <input id="newClassModalName" type="text" placeholder="例如 三年二班" style="width:280px" onkeydown="if(event.key==='Enter'){event.preventDefault();window.PO.createClassFromModal();}">
      <div class="row" style="margin-top:14px"><button class="btn primary" onclick="window.PO.createClassFromModal()">创建并切换</button><button class="btn" onclick="window.PO.closeModal()">取消</button></div>`);
    setTimeout(() => $('#newClassModalName')?.focus(), 50);
  }
  async function createClassFromModal() { await _createClass($('#newClassModalName')?.value); }
  function confirmDeleteClass(name) {
    const cls = name || S.cls;
    if (!cls) { toast('还没有班级','bad'); return; }
    S.deleteClass = cls;
    modal(`<div class="head"><h2>删除班级「${esc(cls)}」</h2><span class="right"><button class="btn sm" onclick="window.PO.closeModal()">关闭</button></span></div><p>会删除这个班的：名单关系、底库模板、全部活动的识别数据与 out/ 归档。此操作不可撤销。</p><label class="row" style="gap:8px;justify-content:flex-start"><input id="delClassPhotos" type="checkbox"> 同时删除该班 photos/ 下的原始照片文件（更危险）</label><div class="row" style="margin-top:14px"><button class="btn danger" onclick="window.PO.doDeleteClass()">确认删除</button><button class="btn" onclick="window.PO.closeModal()">取消</button></div>`);
  }
  async function doDeleteClass() {
    const cls = S.deleteClass || S.cls;
    const del = !!$('#delClassPhotos')?.checked;
    try {
      const r = await post('/api/class/delete', { class_name: cls, delete_photos: del });
      closeModal(); toast(`已删除班级「${r.deleted}」`,'ok');
      if (S.cls === cls) { S.cls = ''; S.batch = ''; }
      await refresh();
      if (S.page === 'settings') settings();
    } catch(e){ toast(e.message,'bad'); }
  }

  window.PO={go,decide:(i,pid)=>decide('present',pid),decideUnknown:()=>decide('unknown'),newPerson,nextReview,boxReview,openPhoto,openFaceDecision,setFace,setFaceUnknown,setPair,manualAssign,closeModal,openActivityUpload,copyWechat,quickTemplate,removePresent,deletePhotoFromModal,openGalleryUpload,openGalleryTreeUpload,submitGalleryTree,showTreeResult,renderTreeLevels,updateTreeInfo,addTemplate,confirmAbsent,openPersonUpload,submitPersonUpload,renamePerson,confirmDeletePerson,doDeletePerson,removeTemplate,createClass,openCreateClass,createClassFromModal,unpublish,publish,openPerson,applyRange,clearRange,openBatchAdd,submitBatchAdd,deletePhoto,confirmDeleteBatch,doDeleteBatch,confirmDeleteClass,doDeleteClass,rebuild,adopt,calibrate,applyCal,showAudit,showDiff,openGallery,runClusters,acceptCluster,drawFaces};
  refresh().catch(e=>{$('#main').innerHTML=empty('⚠️','无法读取本地服务',e.message);});
})();
