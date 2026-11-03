const $ = id => document.getElementById(id);
const state = {report:null, chapters:[], task:null, dirty:false, configured:false};
let fullContent=null, selectedChapter=null, bodyDirty=false, contentSignature='';
const names = {queued:'排队中',processing:'处理中',ready:'可用',no_text:'无文字，检索不到',failed:'失败',draft:'等待生成大纲',generating_outline:'大纲生成中',outline_ready:'待确认大纲',confirmed:'大纲已确认',writing:'全文生成中',paused:'全文已暂停',completed:'全文已生成',waiting:'等待中',auditing:'审计中',compressing:'压缩中',rewriting:'重写中',needs_manual:'待人工核对'};
function node(tag, text, cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function message(text=''){$('message').textContent=text;}
async function api(path, options={}){const response=await fetch('/api/v1'+path,options);const body=await response.json();if(!response.ok)throw new Error(body.error?.message+' ['+body.error?.code+']');return body;}
function json(body, method='POST'){return {method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)};}
function action(text, run){const b=node('button',text);b.type='button';b.onclick=()=>protect(run);return b;}
async function protect(run){try{message();await run();}catch(e){message(e.message);}}
async function refresh(){
  const [health,docs,reports,budget]=await Promise.all([api('/health'),api('/documents'),api('/reports'),api('/budget')]);
  state.configured=health.model_configured;
  $('config').textContent=health.model_configured?'模型 Key 已配置；真实兼容性需实际调用验证。':'模型 Key 未配置：请在 backend/.env 本地填写与平台匹配的Key后重启。中文资料向量在本机运行，不需云端Key。';
  $('budget').textContent=`当月本工具费用记录（含保守预占）：¥${budget.used_yuan.toFixed(4)} / ¥${budget.limit_yuan}。不代表供应商账号全局账单。`;
  const list=$('documents');list.replaceChildren();
  if(!docs.documents.length)list.append(node('p','资料库为空，请先上传资料。'));
  for(const d of docs.documents){const r=node('div',undefined,'row');r.append(node('span',`${d.filename} · ${names[d.status]||d.status} · ${d.chunks_count} 段${d.error_code?' · '+d.error_code:''}`));if(d.status==='failed'||d.status==='ready')r.append(action(d.status==='ready'?'重新处理':'重试',async()=>{await api(`/documents/${d.id}/retry`,{method:'POST'});await refresh();}));list.append(r);}
  const reportList=$('reports');reportList.replaceChildren();
  if(!reports.reports.length)reportList.append(node('p','还没有报告；资料可用后可以创建。'));
  for(const r of reports.reports){const row=node('div',undefined,'row');row.append(node('span',`${r.title} · ${new Date(r.created_at*1000).toLocaleString("zh-CN")} · ${names[r.status]||r.status}`),action('打开',()=>load(r.id)));reportList.append(row);}
  if(state.task){const t=await api('/tasks/'+state.task);$('task-status').textContent=`${t.progress}${t.error_code?' · '+t.error_code:''}`;if(t.status==='failed'||t.status==='succeeded'){state.task=null;await load(state.report.id);}}
  if(state.report)await refreshContent();
  buttons();
}
async function load(id){if(state.dirty&&!window.confirm('当前未保存的编辑会被重新载入覆盖，是否继续？'))return;state.report=await api('/reports/'+id);state.chapters=structuredClone(state.report.outline);state.dirty=false;bodyDirty=false;contentSignature='';localStorage.setItem('yangao-report',id);$('editor').hidden=false;render();await refreshContent();}
function buttons(){const locked=!state.report||state.report.confirmed_at!==null||state.report.status==='generating_outline'||!!state.task;for(const id of ['add','save','confirm'])$(id).disabled=locked;$('generate').disabled=locked||!state.configured;if(state.report)$('confirm').disabled=locked||state.dirty||sum()!==state.report.target_words||state.chapters.length<8||state.chapters.length>13;}
function sum(){return state.chapters.reduce((n,c)=>n+Number(c.suggested_words||0),0);}
function totals(){if(!state.report)return;const total=sum();$('total').textContent=`${state.chapters.length} 章 · ${total} / ${state.report.target_words} 字${state.dirty?'（未保存）':''}`;$('total').className=total===state.report.target_words?'':'bad';buttons();}
function change(){state.dirty=true;totals();}
function field(parent,label,value,onchange,kind='input'){const l=node('label',label),input=node(kind);input.value=value;input.oninput=()=>onchange(input.value);l.append(input);parent.append(l);return input;}
function render(){const r=state.report;$('report-title').textContent=r.title;$('report-status').textContent=names[r.status]||r.status;$('notices').textContent=r.notices.join('；');$('confirmed').textContent=r.confirmed_at!==null?'大纲已确认，可在下方写全文。':'';const list=$('chapters');list.replaceChildren();state.chapters.forEach((ch,i)=>{const card=node('article',undefined,'chapter');card.append(node('h4','第 '+(i+1)+' 章'));field(card,'章节标题',ch.title,v=>{ch.title=v;change();});field(card,'关键论点（每行一条）',ch.key_points.join('\n'),v=>{ch.key_points=v.split('\n').filter(x=>x.trim());change();},'textarea');field(card,'所需数据名称（每行一条，不填数值）',ch.data_needed.join('\n'),v=>{ch.data_needed=v.split('\n').filter(x=>x.trim());change();},'textarea');const count=field(card,'建议字数',ch.suggested_words,v=>{ch.suggested_words=Number(v);change();});count.type='number';count.min='1';const actions=node('div',undefined,'actions');actions.append(action('上移',()=>{if(i){[state.chapters[i-1],state.chapters[i]]=[ch,state.chapters[i-1]];change();render();}}),action('下移',()=>{if(i<state.chapters.length-1){[state.chapters[i+1],state.chapters[i]]=[ch,state.chapters[i+1]];change();render();}}),action('删除',()=>{state.chapters.splice(i,1);change();render();}));card.append(actions);if(r.confirmed_at!==null||r.status==='generating_outline')card.querySelectorAll('input,textarea,button').forEach(n=>n.disabled=true);list.append(card);});totals();}
$('upload').onsubmit=e=>{e.preventDefault();protect(async()=>{const files=$('files').files;if(files.length>10)throw new Error('一批最多10份文件');const form=new FormData();for(const f of files){if(f.size>100*1024*1024)throw new Error('单个文件最大100 MB');form.append('files',f);}await api('/documents',{method:'POST',body:form});$('files').value='';await refresh();});};
$('create').onsubmit=e=>{e.preventDefault();protect(async()=>{const data=new FormData(e.target);const r=await api('/reports',json({title:data.get('title'),target_words:Number(data.get('target_words')),user_requirements:data.get('user_requirements')}));await load(r.id);await refresh();});};
$('search').onsubmit=e=>{e.preventDefault();protect(async()=>{const r=await api('/search',json({query:new FormData(e.target).get('query'),limit:6}));$('hits').replaceChildren();for(const h of r.hits){const box=node('div');box.append(node('h4',h.filename+' · 相关度 '+h.score),node('pre',h.text));$('hits').append(box);}});};
$('generate').onclick=()=>protect(async()=>{if(state.dirty)throw new Error('请先保存修改或重新载入');const t=await api(`/reports/${state.report.id}/outline/generate`,{method:'POST'});state.task=t.id;state.report.status='generating_outline';render();await refresh();});
$('reload').onclick=()=>protect(()=>load(state.report.id));
$('add').onclick=()=>{state.chapters.push({chapter_id:state.chapters.length+1,title:'新增章节',key_points:['请填写论点'],data_needed:[],suggested_words:1});change();render();};
$('save').onclick=()=>protect(async()=>{state.report=await api(`/reports/${state.report.id}/outline`,json({chapters:state.chapters,version:state.report.version},'PUT'));state.dirty=false;render();await refresh();});
$('confirm').onclick=()=>protect(async()=>{state.report=await api(`/reports/${state.report.id}/outline/confirm`,json({version:state.report.version}));render();await refresh();});
async function refreshContent(){
  const data=await api(`/reports/${state.report.id}/content`);fullContent=data;
  $('writing').hidden=state.report.confirmed_at===null;
  state.report.status=data.status;$('report-status').textContent=names[data.status]||data.status;
  $('write-full').disabled=!state.configured||data.status==='writing'||data.status==='completed';
  $('write-full').textContent=data.status==='paused'?'从保存的检查点继续':'写全文';
  $('writing-progress').textContent=data.task?`${data.task.progress}${data.task.error_code?' · '+data.task.error_code:''}`:'尚未开始全文任务';
  const complete=data.chapters.filter(c=>['ready','needs_manual'].includes(c.status)).length;
  $('writing-summary').textContent=`已定稿 ${complete} / ${data.chapters.length} 章 · 含标题与引用共 ${data.words} 字 · 待人工 ${data.chapters.filter(c=>c.status==='needs_manual').length} 章 · 待补充 ${data.chapters.reduce((n,c)=>n+c.missing.length,0)} 项`;
  $('writing-notices').textContent=data.notices.join('；');
  $('download').hidden=data.status!=='completed';$('download').href=`/api/v1/reports/${state.report.id}/export`;
  const signature=JSON.stringify(data.chapters.map(c=>[c.chapter_id,c.status,c.version,c.words]));
  if(signature!==contentSignature){
    contentSignature=signature;const list=$('chapter-list');list.replaceChildren();
    for(const c of data.chapters)list.append(action(`第${c.chapter_id}章 ${c.title} · ${names[c.status]||c.status}${c.edited?' · 已编辑':''}`,()=>showChapter(c.chapter_id)));
    if(selectedChapter&&!bodyDirty)showChapter(selectedChapter);
  }
}
function highlighted(body,issues){
  const out=node('div',undefined,'prose');
  const tokens=[...body.matchAll(/\[待补充[^\]\n]*\]/g)].map(m=>({start:m.index,end:m.index+m[0].length,kind:'missing'}));
  for(const issue of issues.filter(i=>i.severity==='CRITICAL')){let at=0;while((at=body.indexOf(issue.quote,at))!==-1){tokens.push({start:at,end:at+issue.quote.length,kind:'critical'});at+=issue.quote.length;}}
  tokens.sort((a,b)=>a.start-b.start);let end=0;
  for(const t of tokens){if(t.start<end)continue;out.append(document.createTextNode(body.slice(end,t.start)),node('mark',body.slice(t.start,t.end),t.kind));end=t.end;}
  out.append(document.createTextNode(body.slice(end)));return out;
}
function showChapter(number){
  if(bodyDirty&&selectedChapter!==number&&!window.confirm('本章正文尚未保存，是否放弃本次修改？'))return;
  const c=fullContent.chapters.find(ch=>ch.chapter_id===number);if(!c)return;
  selectedChapter=number;bodyDirty=false;$('reader').hidden=false;
  $('chapter-heading').textContent=`第${c.chapter_id}章 ${c.title}`;
  $('chapter-info').textContent=`${names[c.status]||c.status} · ${c.words} / ${c.max_words} 字 · 重写 ${c.rewrite_count} 次${c.truncated?' · 已按字数上限截断，请核对末段':''}${c.edited?' · 已人工编辑，以下为生成时审计记录':''}`;
  $('audit').replaceChildren();
  for(const i of c.audit.issues){const box=node('div',undefined,i.severity==='CRITICAL'?'critical-box':'warning-box');box.append(node('strong',i.severity==='CRITICAL'?'关键事实问题':'术语提示'),node('p','原句：'+i.quote),node('p','应为：'+i.expected+'；依据：'+i.source));$('audit').append(box);}
  if(c.audit.unverifiable.length)$('audit').append(node('p','无法由固定资料核实：'+c.audit.unverifiable.join('；')));
  if(c.user_notice)$('audit').append(node('p',c.user_notice));
  $('body-preview').replaceChildren(highlighted(c.body,c.edited?[]:c.audit.issues));
  $('body-editor').value=c.body;$('body-editor').disabled=fullContent.status!=='completed';$('save-body').disabled=fullContent.status!=='completed';editCount();
  $('references').replaceChildren();for(const r of c.references){const box=node('div');box.append(node('h4',r.filename),node('pre',r.text));$('references').append(box);}
}
function editCount(){const c=fullContent?.chapters.find(ch=>ch.chapter_id===selectedChapter);if(c)$('edit-count').textContent=`${$('body-editor').value.replace(/\s/g,'').length} / ${c.max_words} 字${bodyDirty?'（未保存）':''}`;}
$('body-editor').oninput=()=>{bodyDirty=true;editCount();};
$('save-body').onclick=()=>protect(async()=>{const c=fullContent.chapters.find(ch=>ch.chapter_id===selectedChapter);await api(`/reports/${state.report.id}/chapters/${selectedChapter}`,json({body:$('body-editor').value,version:c.version},'PUT'));bodyDirty=false;await refreshContent();showChapter(selectedChapter);message('正文已保存；原稿和生成时审计记录仍保留。');});
$('write-full').onclick=()=>protect(async()=>{await api(`/reports/${state.report.id}/write`,json({version:state.report.version}));await refreshContent();});
window.addEventListener('beforeunload',e=>{if(bodyDirty||state.dirty){e.preventDefault();e.returnValue='';}});
protect(async()=>{await refresh();const last=localStorage.getItem('yangao-report');if(last)await load(last);});let updating=false;setInterval(async()=>{if(updating)return;updating=true;try{await refresh();}catch(e){message('状态刷新失败：'+e.message);}finally{updating=false;}},3000);
