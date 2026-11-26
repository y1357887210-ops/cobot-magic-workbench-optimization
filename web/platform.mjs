export const SNAPSHOT_MAX_AGE_MS = 2500;
export const POLL_INTERVAL_MS = 1000;
export const REQUEST_TIMEOUT_MS = 4000;
const STATUS_SCHEMA = 'agilex-platform-status-v1';
const HEALTH_SCHEMA = 'agilex-platform-health-v1';
const labels = {READY:'可用',BLOCKED:'受阻',UNKNOWN:'未知',OFF:'未加载',ACTIVE:'使用中',IDLE:'空闲',OBSERVED:'已观测',VALID:'已有验收',NOT_VALIDATED:'未验收',UNVERIFIED:'未校验',VERIFIED:'已核验',VERIFIED_AT_STARTUP:'启动时已核验'};
const allowed = new Set(Object.keys(labels));
const object = value => value && typeof value === 'object' && !Array.isArray(value) ? value : {};
const status = (value, choices = allowed) => choices.has(value) ? value : 'UNKNOWN';
const componentStates = new Set(['READY','BLOCKED','UNKNOWN']);
const modelStates = new Set(['READY','OFF','ACTIVE','UNKNOWN']);
const ownerStates = new Set(['ACTIVE','IDLE','UNKNOWN']);
const bootStates = new Set(['OBSERVED','UNKNOWN']);
const validationStates = new Set(['VALID','NOT_VALIDATED','UNKNOWN']);
const releaseStates = new Set(['UNVERIFIED','VERIFIED','VERIFIED_AT_STARTUP','UNKNOWN']);
const text = value => typeof value === 'string' || typeof value === 'number' ? String(value) : '';
export const escapeHTML = value => text(value).replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const short = (value, length = 12) => text(value) ? text(value).slice(0,length) : '未提供';
const isAgeValid = age => typeof age === 'number' && Number.isFinite(age) && age >= 0 && age <= SNAPSHOT_MAX_AGE_MS;
const componentNames = {robot_link:'AgileX 设备连接',hub:'共享采集 Hub',shared_runner:'共享运行器',v2_runner:'V2 运行器',storage:'USB 运行存储'};
const actionNames = {connect:'连接设备',hub_start:'启动共享采集',precision_load:'加载模型',precision_release:'释放模型',precision_start:'开始模型任务',h50_run:'开始模型任务',v2_start:'开始 V2 任务',v2_apply:'V2 任务',stop:'停止任务',record_start:'开始录制'};
const failureMessages = {
  UPSTREAM_OFFLINE:'4090 的既有任务后台尚不可达；先检查后台进程和本机通路。',
  UPSTREAM_PROTOCOL_ERROR:'任务后台响应格式不符合约定，当前运行状态无法判定。',
  STATUS_REQUEST_FAILED:'未获得新的状态响应，请检查 PC 到 4090 的 SSH 通路。',
  STATUS_REQUEST_TIMEOUT:'状态请求超时。连接恢复后会重新读取，不会重发控制操作。',
  SNAPSHOT_STALE:'最近一次状态已超过 2.5 秒，模型、设备和验收已停止显示为实时。',
  PROTOCOL_MISMATCH:'控制中心接口版本不匹配，需要核对客户端与4090服务版本。',
  STOP_LATCHED:'设备 STOP 状态仍需在原工作台核对；此页不自动解除。',
  UPSTREAM_UNAVAILABLE:'既有任务后台当前不可达，先核对4090后台服务和本机连接。',
  BOOT_SESSION_CHANGED:'检测到启动身份变化，请核对本次启动绑定与当前实例。',
  SOURCE_IDENTITY_MISMATCH:'实际源码或文件指纹与绑定不一致，请核对发布版本和运行副本。',
  USB_IDENTITY_UNCONFIRMED:'尚未确认当前USB物理身份与实际挂载。请在工作台检查存储信息。',
  TIMEQP_UNAVAILABLE:'轨迹优化服务当前不可确认，请在工作台检查该路线的服务与连接。',
  MODEL_UNCONFIRMED:'当前模型实例尚未确认，请核对实际加载状态和对应路线。',
  LEGACY_GUARD_BLOCKED:'既有后台守卫报告当前条件不满足，请在工作台查看具体原因。',
  COMPONENT_STALE:'设备或运行器的状态快照已过期，先恢复对应状态通路。',
  NOT_READY:'后台报告运行条件尚未满足，请在工作台查看对应设备或服务。',
  UNKNOWN:'当前状态不可确认，请在连接恢复后核对工作台。'
};
function unknownComponent(){return {status:'UNKNOWN'};}
function normalizedComponent(value, live){
  const entry = object(value);
  return live ? {...entry,status:status(entry.status,componentStates)} : unknownComponent();
}
export function deriveView(snapshot, options = {}){
  const {now=0,receivedAt=null,transportError=null,health=null,healthReceivedAt=null,previousBoot=null,client=null} = options;
  const source = object(snapshot);
  const protocolOK = source.schema === STATUS_SCHEMA;
  const age = typeof receivedAt === 'number' ? now-receivedAt : Infinity;
  const snapshotFresh = protocolOK && isAgeValid(age) && !transportError;
  const upstream = object(source.upstream);
  const upstreamAgeOK = typeof upstream.age_s === 'number' && isAgeValid(upstream.age_s*1000);
  const live = snapshotFresh && upstream.status === 'ONLINE' && upstream.fresh === true && upstreamAgeOK;
  const runtime = object(source.runtime_session);
  const entries = object(runtime.components);
  const components = Object.fromEntries(Object.keys(componentNames).map(key => [key,normalizedComponent(entries[key],live)]));
  const storageSource = object(entries.storage);
  components.storage = live && storageSource.verified === true && storageSource.status === 'READY' && text(storageSource.uuid) && text(storageSource.mountpoint)
    ? {...storageSource,status:'READY',verified:true}
    : {status:live && storageSource.status === 'BLOCKED' ? 'BLOCKED':'UNKNOWN',verified:false,uuid:null,mountpoint:null,free_bytes:null};
  const models = Object.fromEntries(['FP32','BF16'].map(key=>{
    const entry = object(object(entries.models)[key]);
    const modelStatus = live ? status(entry.status,modelStates):'UNKNOWN';
    return [key,{status:modelStatus,step:['READY','ACTIVE'].includes(modelStatus) && Number.isSafeInteger(entry.step) && entry.step>=0 ? entry.step:null}];
  }));
  const ownershipSource = object(runtime.ownership);
  const ownership = {status:live ? status(ownershipSource.status,ownerStates):'UNKNOWN',active_jobs:live && Array.isArray(ownershipSource.active_jobs) ? ownershipSource.active_jobs:[]};
  const bootSource = object(runtime.boot);
  const boot = {host:snapshotFresh ? text(bootSource.host):'',host_boot_id:snapshotFresh ? text(bootSource.host_boot_id):'',robot_boot_id:live ? text(bootSource.robot_boot_id):'',status:live ? status(bootSource.status,bootStates):'UNKNOWN'};
  const bootChanged = Boolean(live && previousBoot && boot.robot_boot_id && previousBoot !== boot.robot_boot_id);
  const validationSource = object(source.validation_record);
  const validation = {status:live && !bootChanged ? status(validationSource.status,validationStates):'UNKNOWN',source:text(validationSource.source),motion_verified:live && validationSource.motion_verified === true};
  const healthFresh = object(health).schema === HEALTH_SCHEMA && typeof healthReceivedAt === 'number' && isAgeValid(now-healthReceivedAt);
  const facadeOnline = snapshotFresh || healthFresh;
  let failure = live ? source.current_failure || null : null;
  if (!live) {
    let code = transportError || (!protocolOK && snapshot ? 'PROTOCOL_MISMATCH' : !isAgeValid(age) ? 'SNAPSHOT_STALE' : upstream.error_code || (upstream.status === 'PROTOCOL_ERROR' ? 'UPSTREAM_PROTOCOL_ERROR':'UPSTREAM_OFFLINE'));
    if (!snapshot && !transportError) code = 'WAITING_FOR_STATUS';
    failure = {code,component:facadeOnline ? 'upstream':'connection'};
  }
  const release = object(source.software_release);
  const capabilities = live && Array.isArray(object(source.legacy_guard).capabilities) ? source.legacy_guard.capabilities.map(item=>({action:text(item.action),available:typeof item.available==='boolean'?item.available:null,reason_code:text(item.reason_code)})):[];
  return {
    live,snapshotFresh,facadeOnline,bootChanged,components,models,ownership,boot,validation,capabilities,failure,
    historical:source.historical_failure || null,
    release:{release_id:snapshotFresh ? text(release.release_id) : healthFresh ? text(health.release_id):'',integrity:snapshotFresh ? status(release.integrity,releaseStates):'UNKNOWN',scope:snapshotFresh?text(release.scope):''},
    serverInstance:healthFresh ? text(health.server_instance_id):'',
    client:object(client).schema === 'agilex-thin-client-health-v1' ? client:null,
    workbenchAvailable:live,
    initial:!snapshot && !transportError,
    rawSnapshot:source
  };
}
function pill(value, overrideLabel){
  const tone = ['READY','ACTIVE','VALID','VERIFIED','VERIFIED_AT_STARTUP','OBSERVED'].includes(value) ? 'blue' : value === 'BLOCKED' ? 'amber':'neutral';
  return '<span class="pill '+tone+'">'+escapeHTML(overrideLabel || labels[value] || '未知')+'</span>';
}
const value = data => escapeHTML(data || '未提供');
const kv = (label,data,code=false) => '<div><dt>'+escapeHTML(label)+'</dt><dd>'+(code?'<code>'+value(data)+'</code>':value(data))+'</dd></div>';
function size(bytes){return typeof bytes === 'number' && Number.isFinite(bytes) && bytes>=0 ? (bytes/1024**3).toFixed(1)+' GiB':'未提供';}
function describeFailure(failure){
  return failureMessages[failure?.code] || '后台报告当前阻挡：'+text(failure?.code || 'UNKNOWN')+'。请在工作台核对对应条件。';
}
function statusStrip(view){
  if(view.initial) return '<div class="status-strip"><span class="status-mark">…</span><div><h2>正在获取当前状态</h2><p>实时连接、模型、设备与验收均等待服务端确认。</p></div></div>';
  if(!view.live) return '<div class="status-strip warning"><span class="status-mark">!</span><div><h2>'+ (view.facadeOnline?'控制中心在线，运行状态不可用':'连接暂不可用')+'</h2><p>'+escapeHTML(describeFailure(view.failure))+'</p></div><span class="strip-end">运行状态已标为未知</span></div>';
  if(view.failure) return '<div class="status-strip warning"><span class="status-mark">!</span><div><h2>当前存在待处理条件</h2><p>'+escapeHTML(describeFailure(view.failure))+'</p></div><span class="strip-end">'+escapeHTML(view.failure.component || '当前运行')+'</span></div>';
  if(view.bootChanged) return '<div class="status-strip warning"><span class="status-mark">↻</span><div><h2>检测到新的 AgileX 启动会话</h2><p>已更新本次启动身份；请核对运行器绑定、当前模型和本次验收，不沿用上次开机结果。</p></div></div>';
  const blocked = Object.entries(view.components).filter(([,v])=>v.status==='BLOCKED').map(([k])=>componentNames[k]);
  if(blocked.length) return '<div class="status-strip warning"><span class="status-mark">!</span><div><h2>状态已同步，部分运行条件受阻</h2><p>'+escapeHTML(blocked.join('、'))+'需要处理。进入工作台查看原始阻挡与可用操作。</p></div></div>';
  const modelsOff = Object.values(view.models).every(model=>model.status==='OFF');
  const unknown = Object.entries(view.components).filter(([,v])=>v.status==='UNKNOWN');
  return '<div class="status-strip"><span class="status-mark">↗</span><div><h2>'+ (view.ownership.status==='ACTIVE'?'已有任务占用设备控制权':modelsOff?'状态已同步，模型当前未加载':unknown.length?'状态已同步，部分设备条件待核对':'当前状态已同步')+'</h2><p>'+(view.ownership.status==='ACTIVE'?'在原工作台查看当前任务；其他访问端共用同一设备控制权。':modelsOff?'可进入工作台选择并显式加载模型。模型未加载是当前状态，不属于连接故障。':'软件版本、启动会话与无运动验收分别核对；实际执行以工作台的实时准入为准。')+'</p></div><span class="strip-end">只读观测</span></div>';
}
function connectPipeline(connectState){
  const cs = object(connectState);
  const stages = Array.isArray(cs.stages) ? cs.stages : [];
  if(!stages.length || cs.status === 'IDLE') return '';
  const cls = cs.status === 'READY' ? 'connect-banner ready' : cs.status === 'BLOCKED' ? 'connect-banner blocked' : 'connect-banner running';
  const headline = cs.status === 'READY' ? '4090 ↔ AgileX 链路已打通（未自动加载模型或解除 STOP）' : cs.status === 'BLOCKED' ? '链路打通受阻：' + escapeHTML(cs.summary_code || 'UNKNOWN') : '正在执行四阶段一键连接…';
  return '<div class="'+cls+'"><div class="connect-banner-head"><strong>'+headline+'</strong></div><div class="connect-stages">'+
    stages.map(st => '<div class="connect-stage '+escapeHTML(String(st.status || 'PENDING').toLowerCase())+'"><div class="connect-stage-top"><span>0'+escapeHTML(st.id)+'</span><span>'+escapeHTML(st.status === 'PASSED' ? '通过' : st.status === 'BLOCKED' ? '受阻' : st.status === 'RUNNING' ? '执行中' : '等待')+'</span></div><strong>'+escapeHTML(st.title)+'</strong><small>'+escapeHTML(st.detail || st.reason_code || '')+'</small></div>').join('')+
    '</div></div>';
}
function topology(view){
  const robot = view.components.robot_link;
  const pcRoute = view.client ? (view.facadeOnline ? 'SSH 网关已连接' : 'SSH 通路待确认'):'浏览器访问';
  const connectBusy = view.connectState?.status === 'RUNNING';
  const connectBtn = '<button id="connect-robot-button" class="button '+(view.live ? 'button-secondary' : 'button-primary')+'" type="button" data-action="connect-robot"'+(connectBusy ? ' disabled' : '')+'>'+(connectBusy ? '正在打通链路…' : view.live ? '↻ 重新校验连接' : '⚡ 一键连接机械臂')+'</button>';
  return '<section class="panel" aria-labelledby="topology-title"><div class="panel-header"><div><h2 id="topology-title">系统链路</h2><p>页面、模型与控制后台统一由 4090 管理</p></div><div class="panel-actions">'+connectBtn+'</div></div><div class="topology">'+
  '<article class="node"><div class="node-header"><span class="node-number" data-name=" · PC">01</span>'+pill('OBSERVED','页面已打开')+'</div><h3>PC 访问端</h3><p class="node-description">浏览器与 SSH 入口</p><div class="node-fields"><div class="node-field"><span>访问方式</span><strong>'+escapeHTML(pcRoute)+'</strong></div><div class="node-field"><span>网页资源</span><strong>4090 服务端</strong></div><div class="node-field"><span>本地模型</span><strong>无需复制</strong></div></div></article>'+
  '<div class="connector" aria-hidden="true"><span>SSH</span><span class="connector-line"></span></div>'+
  '<article class="node central"><div class="node-header"><span class="node-number" data-name=" · 4090">02</span>'+pill(view.facadeOnline?'READY':'UNKNOWN',view.facadeOnline?'状态服务在线':'状态服务未知')+'</div><h3>4090 中央服务</h3><p class="node-description">网页 · 模型权重 · 控制后台源码</p><div class="node-fields"><div class="node-field"><span>实际主机</span><strong>'+value(view.boot.host)+'</strong></div><div class="node-field"><span>任务后台</span><strong>'+ (view.live?'当前可达':'当前不可确认')+'</strong></div><div class="node-field"><span>设备控制权</span><strong>'+escapeHTML(labels[view.ownership.status])+'</strong></div></div></article>'+
  '<div class="connector" aria-hidden="true"><span>设备链路</span><span class="connector-line"></span></div>'+
  '<article class="node"><div class="node-header"><span class="node-number" data-name=" · AgileX">03</span>'+pill(robot.status)+'</div><h3>AgileX 设备端</h3><p class="node-description">设备代理 · 相机 · CAN</p><div class="node-fields"><div class="node-field"><span>共享采集</span><strong>'+escapeHTML(labels[view.components.hub.status])+'</strong></div><div class="node-field"><span>USB 身份</span><strong>'+ (view.components.storage.verified?value(view.components.storage.uuid):'尚未核验')+'</strong></div><div class="node-field"><span>启动会话</span><strong>'+escapeHTML(short(view.boot.robot_boot_id))+'</strong></div></div></article></div>'+connectPipeline(view.connectState)+'<div class="topology-footnote"><span>当前来源 <strong>/platform/status</strong></span><span>设备端保留实际硬件代理</span><span>SSH 连接与执行准入独立判断</span></div></section>';
}
function componentList(view){
  const descriptions = {robot_link:'4090 到既有设备服务的当前连接',hub:'复用既有相机与关节采集生产者',shared_runner:'共享执行运行器的当前准入状态',v2_runner:'FP32 + RTC 路线的运行器状态'};
  const armMode = view.armMode || 'dual';
  const armTabs = [['dual','双臂协同'],['left','单左臂'],['right','单右臂']].map(([id,lbl])=>'<button type="button" class="arm-chip'+(armMode===id?' active':'')+'" data-arm-mode="'+id+'">'+lbl+'</button>').join('');
  return '<section class="panel"><div class="panel-header"><div><h2>运行条件</h2><p>设备链接与执行绑定分别读取</p></div><div class="arm-mode-group" role="group" aria-label="单双臂构型">'+armTabs+'</div></div><div class="component-list">'+['robot_link','hub','shared_runner','v2_runner'].map((key,index)=>'<div class="component-row"><span class="component-index">0'+(index+1)+'</span><div><div class="component-label">'+componentNames[key]+'</div><div class="component-description">'+descriptions[key]+'</div></div>'+pill(view.components[key].status)+'</div>').join('')+'</div></section>';
}
function modelsPanel(view){
  return '<section class="panel"><div class="panel-header"><div><h2>模型实例</h2><p>此处只读取已加载状态</p></div></div><div class="card-body"><div class="model-grid">'+Object.entries(view.models).map(([name,model])=>'<article class="model-slot"><div class="model-slot-header"><h3>'+name+'</h3>'+pill(model.status)+'</div><div class="model-step"><span>STEP</span>'+(model.step===null?'—':model.step)+'</div><div class="model-hint">'+(model.status==='OFF'?'可在工作台显式加载':model.status==='UNKNOWN'?'无可确认的实时实例':'服务端报告的当前实例')+'</div></article>').join('')+'</div><p class="inline-note">加载、释放与任务参数统一在原工作台；页面连接恢复不会自动重新加载模型。</p></div></section>';
}
function storagePanel(view){
  const storage = view.components.storage;
  return '<section class="panel"><div class="panel-header"><h2>USB 运行存储</h2>'+pill(storage.status,storage.verified?'身份已核验':storage.status==='BLOCKED'?'存储受阻':'待核验')+'</div><div class="card-body"><dl class="key-values">'+kv('实际 UUID',storage.uuid,true)+kv('剩余空间',storage.verified?size(storage.free_bytes):null)+kv('实际挂载点',storage.mountpoint,true)+'</dl><p class="inline-note">原始录制与机器人运行诊断使用设备端实际核验的 USB。</p></div></section>';
}
function ownershipPanel(view){
  const jobs = view.ownership.active_jobs;
  return '<section class="panel"><div class="panel-header"><h2>设备控制权</h2>'+pill(view.ownership.status)+'</div><div class="card-body"><dl class="key-values">'+kv('当前活动任务',view.live ? String(jobs.length)+' 项':'未确认')+kv('任务入口','4090 既有任务后台')+kv('并行访问','共用当前任务、模型与 STOP')+'</dl>'+(jobs.length?'<div class="inline-note">'+jobs.map(job=>'<code>'+value(job.id)+'</code> · '+value(actionNames[job.action] || job.action)+' · '+value(job.state)).join('<br>')+'</div>':'')+'</div></section>';
}
function historyPanel(view){
  const history = object(view.historical);
  const exists = Object.keys(history).length > 0;
  return '<details class="panel history-panel" data-expand="history"><summary><strong>历史失败记录</strong><span class="history-label">'+(exists?'保留用于排查，不影响当前连接判断':'本次快照未附历史失败')+'</span></summary><div class="history-body"><p>此处显示已结束任务的历史结果，与上方当前连接和运行条件分开。'+(!view.snapshotFresh?' 当前连接不可用，以下仅为最后收到的历史记录。':'')+'</p>'+(exists?'<div class="history-event"><strong>'+value(actionNames[history.action] || history.action)+' · '+value(history.state)+'</strong><code>'+value(history.id)+'</code><small>历史任务记录 · 不代表当前故障</small></div>':'<div class="empty-state">未获得历史失败条目；这不代表全部历史任务均成功。</div>')+'</div></details>';
}
function overview(view){return statusStrip(view)+topology(view)+'<div class="overview-grid"><div class="stack">'+componentList(view)+ownershipPanel(view)+'</div><div class="stack">'+modelsPanel(view)+storagePanel(view)+'</div></div>'+historyPanel(view);}
function evidenceCards(view){
  return '<div class="evidence-grid"><article class="evidence-card"><div class="evidence-number">01 / SOFTWARE</div><h2>软件发布</h2><p>描述服务端源码和静态资源的版本，不等同于设备可执行。</p>'+pill(view.release.integrity)+'<div class="evidence-value">'+value(view.release.release_id)+'</div><small>范围：管理入口 · 完整性以清单核验为准</small></article>'+
  '<article class="evidence-card"><div class="evidence-number">02 / SESSION</div><h2>本次启动会话</h2><p>主机启动身份、模型实例和运行器绑定，随重启重新核对。</p>'+pill(view.boot.status)+'<div class="evidence-value">AgileX · '+escapeHTML(short(view.boot.robot_boot_id,20))+'</div><small>4090 · '+escapeHTML(short(view.boot.host_boot_id,20))+'</small></article>'+
  '<article class="evidence-card"><div class="evidence-number">03 / VALIDATION</div><h2>无运动验收（NO_SEND）</h2><p>真实观测与模型的无发送验收；不代表物理任务成功。</p>'+pill(view.validation.status)+'<div class="evidence-value">'+(view.validation.status==='VALID'?'后台报告已有验收':'当前未确认有效验收')+'</div><small>物理任务成功需独立证据</small></article></div>';
}
function deployment(view){
  return statusStrip(view)+evidenceCards(view)+'<section class="panel"><div class="panel-header"><div><h2>更新与恢复流程</h2><p>此处展示流程边界；没有自动发布或设备恢复操作</p></div><span class="minor">服务端统一管理</span></div><div class="process-list"><div class="process-item"><h3>固定服务端版本</h3><p>核对版本清单、文件指纹与当前源码。PC 只更新访问入口。</p></div><div class="process-item"><h3>检查占用与影响</h3><p>确认任务、录制、保存及模型状态。活动控制权不会被新页面抢占。</p></div><div class="process-item"><h3>按变更范围更新</h3><p>页面更新只影响页面；后台变更仅重载受影响服务，保留回退记录。</p></div><div class="process-item"><h3>读取本次运行证据</h3><p>重新核对启动身份和准入；需要新验收时取得对应版本的真实回执。</p></div></div></section>'+
  '<div class="diagnostic-grid"><section class="panel"><div class="panel-header"><h2>当前版本身份</h2></div><div class="card-body"><dl class="key-values">'+kv('发布版本',view.release.release_id,true)+kv('完整性',labels[view.release.integrity])+kv('服务实例',view.serverInstance,true)+kv('PC 入口版本',view.client?.client_version,true)+'</dl><p class="inline-note">版本号不是验收结果。未提供或未校验的字段按真实状态保留。</p></div></section><section class="panel"><div class="panel-header"><h2>重启后的状态</h2></div><div class="diagnostic-message"><strong>重新读取当前会话，再进入工作台。</strong><br>PC 断线或重启后重新访问 4090。4090 / AgileX 重启后，旧模型实例与旧启动绑定不能沿用为实时状态。<br><br>本管理页只重新获取状态；模型加载、STOP 处理及真机任务由工作台的明确操作和后台守卫决定。</div></section></div>';
}
function diagnostics(view){
  const capabilities = view.capabilities;
  return statusStrip(view)+'<section class="panel"><div class="panel-header"><div><h2>原工作台准入摘要</h2><p>只读展示既有守卫的结果；不在此页授予执行权限</p></div><span class="minor">LEGACY GUARD</span></div><div class="table-wrap"><table class="status-table"><thead><tr><th>操作</th><th>当前准入</th><th>阻挡说明</th></tr></thead><tbody>'+
  (capabilities.length ? capabilities.map(item=>'<tr><td>'+value(actionNames[item.action] || item.action)+'</td><td>'+pill(item.available===true?'READY':item.available===false?'BLOCKED':'UNKNOWN',item.available===true?'后台允许':item.available===false?'后台阻挡':'未确认')+'</td><td><code>'+value(item.reason_code || (item.available===true?'本次摘要未报告阻挡':'未提供原因'))+'</code></td></tr>').join(''):'<tr><td colspan="3">当前没有可确认的准入摘要。连接未知不能按空闲或可执行处理。</td></tr>')+'</tbody></table></div></section>'+
  '<div class="diagnostic-grid"><section class="panel"><div class="panel-header"><h2>当前启动身份</h2></div><div class="card-body"><dl class="key-values">'+kv('4090 主机',view.boot.host)+kv('4090 Boot ID',view.boot.host_boot_id,true)+kv('AgileX Boot ID',view.boot.robot_boot_id,true)+kv('无运动验收',labels[view.validation.status])+'</dl></div></section><section class="panel"><div class="panel-header"><h2>状态说明</h2></div><div class="diagnostic-message"><strong>未知</strong>：未获得及时且符合协议的响应，不能推断设备已停止。<br><strong>未加载</strong>：模型服务明确报告关闭，设备连接可仍然正常。<br><strong>受阻</strong>：后台报告当前条件不满足；先处理实际原因。<br><strong>设备控制权（Owner）</strong>：负责本轮硬件任务的既有后台，所有访问端共用。</div></section></div>'+
  historyPanel(view)+'<details class="panel history-panel" data-expand="snapshot"><summary><strong>接口状态快照</strong><span class="history-label">'+(view.snapshotFresh?'当前响应':'最后收到的数据 · 不作为实时状态')+'</span></summary><div class="history-body"><p>仅展示管理接口返回的脱敏摘要。没有凭据或原始观测流。</p><pre class="snapshot">'+escapeHTML(JSON.stringify(view.rawSnapshot,null,2))+'</pre></div></details>';
}
export function renderPage(view, tab='overview'){return tab==='deployment'?deployment(view):tab==='diagnostics'?diagnostics(view):overview(view);}
const tabMeta = {
  overview:['01','运行总览','查看当前链路与运行条件，在同一工作台继续模型和设备操作。'],
  deployment:['02','版本与更新','软件版本、启动会话与验收记录各自独立，更新只作用于受影响的组件。'],
  diagnostics:['03','状态诊断','从当前阻挡追溯实际条件，历史失败单独保留。']
};
export function startUI(document, environment = globalThis){
  const state = {snapshot:null,receivedAt:null,health:null,healthReceivedAt:null,error:null,client:null,connectState:null,armMode:'dual',lastBoot:null,comparisonBoot:null,polling:false,stopped:false,timer:null,abort:null,lastRender:null,lastAnnouncement:null};
  const now = () => environment.performance.now();
  const content = document.getElementById('content');
  const refreshButton = document.getElementById('refresh-button');
  const workbenchLink = document.getElementById('workbench-link');
  const currentTab = () => Object.hasOwn(tabMeta,environment.location.hash.slice(1)) ? environment.location.hash.slice(1):'overview';
  function update(){
    const view = {...deriveView(state.snapshot,{now:now(),receivedAt:state.receivedAt,transportError:state.error,health:state.health,healthReceivedAt:state.healthReceivedAt,previousBoot:state.comparisonBoot,client:state.client}),connectState:state.connectState,armMode:state.armMode};
    const tab = currentTab();
    const meta = tabMeta[tab];
    document.getElementById('page-title').textContent = meta[1];
    document.getElementById('page-description').textContent = meta[2];
    document.getElementById('section-number').textContent = meta[0];
    document.querySelectorAll('[data-tab]').forEach(link=>{
      if(link.dataset.tab===tab) link.setAttribute('aria-current','page'); else link.removeAttribute('aria-current');
    });
    const badge = document.getElementById('connection-badge');
    badge.className = 'pill '+(view.live?'blue':view.initial?'neutral':'amber');
    badge.textContent = view.live?'状态已同步':view.initial?'正在读取':'运行状态未知';
    const stamp = document.getElementById('sync-time');
    stamp.textContent = state.receivedAt===null?'尚未收到快照':view.snapshotFresh?'最近同步 '+state.receivedWallTime:'上次响应 '+state.receivedWallTime;
    refreshButton.disabled = state.polling;
    workbenchLink.setAttribute('aria-disabled',String(!view.workbenchAvailable));
    workbenchLink.title = view.workbenchAvailable?'进入既有完整工作台':'当前无法确认任务后台在线';
    const signature = JSON.stringify([view,tab]);
    if(signature!==state.lastRender){
      const expanded = new Set([...content.querySelectorAll('details[open][data-expand]')].map(node=>node.dataset.expand));
      content.innerHTML = renderPage(view,tab);
      content.querySelectorAll('details[data-expand]').forEach(node=>{node.open=expanded.has(node.dataset.expand);});
      state.lastRender=signature;
    }
    const announcement = view.live ? '当前状态已同步' : view.initial ? '正在获取当前状态' : '当前状态不可确认，运行状态已标为未知';
    if(state.lastAnnouncement!==announcement){document.getElementById('announcement').textContent=announcement;state.lastAnnouncement=announcement;}
  }
  async function readJSON(path,signal){
    const response = await environment.fetch(path,{method:'GET',cache:'no-store',credentials:'same-origin',signal,headers:{Accept:'application/json'}});
    if(!response.ok) throw new Error('HTTP_'+response.status);
    return response.json();
  }
  async function poll(){
    if(state.polling || state.stopped) return;
    environment.clearTimeout(state.timer);
    state.polling=true;
    const controller=new AbortController();
    state.abort=controller;
    const timeout=environment.setTimeout(()=>controller.abort(),REQUEST_TIMEOUT_MS);
    update();
    try{
      const results=await Promise.allSettled([readJSON('/platform/status',controller.signal),readJSON('/platform/health',controller.signal)]);
      if(state.stopped) return;
      const received=now();
      if(results[1].status==='fulfilled' && results[1].value.schema===HEALTH_SCHEMA){state.health=results[1].value;state.healthReceivedAt=received;}
      if(results[0].status==='fulfilled'){
        const next=results[0].value;
        if(next.schema!==STATUS_SCHEMA){state.error='PROTOCOL_MISMATCH';}
        else{
          state.comparisonBoot=state.lastBoot;
          state.snapshot=next;state.receivedAt=received;state.error=null;
          state.receivedWallTime=new Date().toLocaleTimeString('zh-CN',{hour12:false});
          if(next.upstream?.status==='ONLINE' && next.upstream?.fresh===true && next.runtime_session?.boot?.robot_boot_id) state.lastBoot=next.runtime_session.boot.robot_boot_id;
        }
      } else {state.error=controller.signal.aborted?'STATUS_REQUEST_TIMEOUT':'STATUS_REQUEST_FAILED';}
    } catch {state.error=controller.signal.aborted?'STATUS_REQUEST_TIMEOUT':'STATUS_REQUEST_FAILED';}
    finally{
      environment.clearTimeout(timeout);state.polling=false;state.abort=null;
      if(!state.stopped){update();state.timer=environment.setTimeout(poll,POLL_INTERVAL_MS);}
    }
  }
  async function triggerOneClickConnect(){
    state.connectState = {status:'RUNNING',stages:[{id:1,title:'网口与 SSH 探测',status:'RUNNING',detail:'正在请求 /platform/connect'},{id:2,title:'USB 物理核验',status:'PENDING',detail:''},{id:3,title:'会话与源码绑定',status:'PENDING',detail:''},{id:4,title:'代理 / Hub / 隧道',status:'PENDING',detail:''}]};
    update();
    try{
      const resp = await environment.fetch('/platform/connect',{method:'POST',cache:'no-store',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Tiger-Token':'platform-connect'},body:JSON.stringify({arm_mode:state.armMode})});
      const data = await resp.json();
      state.connectState = data && Array.isArray(data.stages) ? data : {status:'BLOCKED',summary_code:data?.error_code || 'HTTP_'+resp.status,stages:state.connectState.stages};
    } catch {
      state.connectState = {status:'BLOCKED',summary_code:'STATUS_REQUEST_FAILED',stages:state.connectState.stages};
    }
    update();
    poll();
  }
  content.addEventListener('click',event=>{
    const connectEl = event.target.closest('[data-action="connect-robot"]');
    if(connectEl){ triggerOneClickConnect(); return; }
    const armEl = event.target.closest('[data-arm-mode]');
    if(armEl){ state.armMode = armEl.dataset.armMode; update(); }
  });
  refreshButton.addEventListener('click',poll);
  workbenchLink.addEventListener('click',event=>{if(workbenchLink.getAttribute('aria-disabled')==='true')event.preventDefault();});
  environment.addEventListener('hashchange',update);
  const watchdog=environment.setInterval(update,250);
  const stop=()=>{state.stopped=true;state.abort?.abort();environment.clearTimeout(state.timer);environment.clearInterval(watchdog);};
  environment.addEventListener('pagehide',stop,{once:true});
  // A 404 is normal on a native 4090 browser. The optional PC endpoint never gates server status.
  const clientAbort=new AbortController();
  const clientTimeout=environment.setTimeout(()=>clientAbort.abort(),REQUEST_TIMEOUT_MS);
  readJSON('/__client/health',clientAbort.signal).then(data=>{if(data.schema==='agilex-thin-client-health-v1'){state.client=data;update();}}).catch(()=>{}).finally(()=>environment.clearTimeout(clientTimeout));
  poll();
  return {stop,refresh:poll};
}
if(typeof document!=='undefined') startUI(document);
