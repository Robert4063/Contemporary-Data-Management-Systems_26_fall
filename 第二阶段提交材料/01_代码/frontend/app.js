'use strict';
const app = document.querySelector('#app'), notice = document.querySelector('#notice');
const AUTH_KEY='survey_token';
function savedToken() {
  try {
    const shared=localStorage.getItem(AUTH_KEY);
    if(shared){try{sessionStorage.removeItem(AUTH_KEY);}catch(_){}return shared;}
  }catch(_){}
  try {
    const legacy=sessionStorage.getItem(AUTH_KEY);
    if(legacy){try{localStorage.setItem(AUTH_KEY,legacy);sessionStorage.removeItem(AUTH_KEY);}catch(_){}}
    return legacy;
  }catch(_){return null;}
}
let token = savedToken(), user = null, dashboardSkip = 0, deadlineTimer = null;
function saveToken(value) {
  token=value;
  try {
    if(value)localStorage.setItem(AUTH_KEY,value);else localStorage.removeItem(AUTH_KEY);
    sessionStorage.removeItem(AUTH_KEY);
  }catch(_){try{if(value)sessionStorage.setItem(AUTH_KEY,value);else sessionStorage.removeItem(AUTH_KEY);}catch(_){}}
}
function clearLogin() {saveToken(null);user=null;account();}
async function restoreLogin() {
  const checking=token;
  if(!checking){user=null;account();return true;}
  try {
    const result=await api('/auth/me');
    if(token!==checking)return false;
    user=result;account();return true;
  }catch(error){
    if(token!==checking)return false;
    if(error.status!==401)throw error;
    clearLogin();return true;
  }
}
const types = {single:'单选题', multiple:'多选题', text:'文本填空', number:'数字填空'};
const statuses = {draft:'草稿', published:'已发布', closed:'已关闭'};
function node(tag, text, attrs={}) {
  const n = document.createElement(tag);
  if (text !== undefined && text !== null) n.textContent = text;
  for (const [k,v] of Object.entries(attrs)) {
    if (k.startsWith('on')) n.addEventListener(k.slice(2),v);
    else if (k === 'class') n.className = v;
    else n.setAttribute(k,v);
  }
  return n;
}
function box(cls, ...children) {const n = node('div',null,{class:cls}); n.append(...children); return n;}
function tell(message, good=false) {notice.textContent=message; notice.className=good?'success':'';}
function clear() {clearTimeout(deadlineTimer);deadlineTimer=null;app.replaceChildren(); tell('');}
function errorText(detail) {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map(e=>`${e.loc?.join('.') || ''}: ${e.msg}`).join('\n');
  if (detail?.errors) return detail.errors.map(e=>`${e.question_id}: ${e.message}`).join('\n');
  return '请求失败，请检查输入';
}
async function api(path, method='GET', body) {
  const requestToken=token;
  const headers = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers['Content-Type']='application/json';
  const res = await fetch('/api'+path,{method,headers,body:body===undefined?undefined:JSON.stringify(body)});
  if(token!==requestToken){const error=new Error('会话已切换，忽略旧请求结果');error.name='SessionChangedError';throw error;}
  if (res.status===204) return null;
  const data = await res.json();
  if(token!==requestToken){const error=new Error('会话已切换，忽略旧请求结果');error.name='SessionChangedError';throw error;}
  if (!res.ok) {
    const message=res.status===404&&data.detail==='Not Found'&&/^\/public\/surveys\/[a-fA-F0-9]{24}\/status$/.test(path)
      ?'当前后端未提供问卷状态接口，请从项目目录重启后端后再试。':errorText(data.detail);
    const error=new Error(message);error.status=res.status;throw error;
  }
  return data;
}
const questionModule={
  versions:id=>api('/questions/'+id+'/versions'),
  get:id=>api('/questions/'+id)
};
const surveyModule={
  saveDraft:(id,body)=>api('/surveys/'+id+'/draft-content','PATCH',body),
  fromQuestions:items=>api('/surveys/from-questions','POST',{items}),
  switchVersion:(id,localId,body)=>api(`/surveys/${id}/questions/${localId}/version`,'PUT',body),
  nextVersion:(id,body)=>api('/surveys/'+id+'/versions','POST',body)
};
function action(text, fn, cls='') {
  return node('button',text,{type:'button',class:cls,onclick:async e=>{
    const b=e.currentTarget; b.disabled=true; tell('');
    try {await fn();} catch(err) {await handleError(err);} finally {b.disabled=false;}
  }});
}
function confirmPublication(title) {
  return window.confirm(`确定发布问卷“${title||'未命名问卷'}”吗？\n发布后，题目、选项、校验和跳转规则将无法修改。`);
}
function field(label, control) {const n=node('label',null,{class:'field'}); if(control.matches('input,select,textarea'))control.setAttribute('aria-label',label); n.append(node('span',label),control); return n;}
function input(value, fn, type='text', attrs={}) {
  const n=node('input',null,{type,...attrs}); n.value=value??''; n.addEventListener('input',()=>fn(n.value)); return n;
}
function area(value, fn) {const n=node('textarea');n.value=value??'';n.addEventListener('input',()=>fn(n.value));return n;}
function select(options, value, fn) {
  const n=node('select');for(const [v,t] of options) n.append(node('option',t,{value:v}));
  n.value=value;n.addEventListener('change',()=>fn(n.value));return n;
}
function check(label, value, fn) {
  const i=node('input',null,{type:'checkbox'});i.checked=value;i.addEventListener('change',()=>fn(i.checked));
  const n=node('label',null,{class:'check'});n.append(i,document.createTextNode(label));return n;
}
function clearDeviceDrafts(userId) {
  try {for(const key of Object.keys(localStorage))if(key.startsWith(`survey_draft:v1:${userId}:`))localStorage.removeItem(key);} catch (_) {}
}
function newAttemptId() {
  if(crypto.randomUUID)return crypto.randomUUID();
  const b=crypto.getRandomValues(new Uint8Array(16));b[6]=(b[6]&15)|64;b[8]=(b[8]&63)|128;
  const s=Array.from(b,x=>x.toString(16).padStart(2,'0')).join('');return `${s.slice(0,8)}-${s.slice(8,12)}-${s.slice(12,16)}-${s.slice(16,20)}-${s.slice(20)}`;
}
function account() {
  const n=document.querySelector('#account');n.replaceChildren();
  if(user) n.append(node('span',user.username+' '),action('退出登录',async()=>{await api('/auth/logout','POST');clearDeviceDrafts(user.id);clearLogin();await route();},'secondary'));
}
function surveyPath() {return location.pathname.match(/^\/survey\/([a-fA-F0-9]{24})$/);}
function surveyState(state) {
  clear();
  const labels={closed:'问卷已关闭',expired:'问卷已截止',incomplete:'问卷暂不可填写'};
  const card=box('card',node('h1',state.title),node('p',state.description),
    node('h2',labels[state.availability]||'问卷暂不可填写'),node('p',state.message));
  if(state.created_at)card.append(node('p','创建时间：'+new Date(state.created_at).toLocaleString()));
  card.append(node('p','截止时间：'+(state.deadline?new Date(state.deadline).toLocaleString():'未设置')));
  card.append(box('row actions',node('a','返回工作台',{href:'/'}),action('重新检查状态',route,'secondary')));
  app.append(card);
  const questions=state.questions||[];
  app.append(node('h2',`问卷题目（只读，共 ${questions.length} 题）`),node('p','以下为问卷内容，当前不能填写或提交。',{class:'muted'}));
  questions.forEach((q,index)=>{
    const detail=box('card survey-detail',node('h3',`第 ${index+1} 题：${q.title}`),
      node('p',`${types[q.type]||q.type} · ${q.required?'必答':'选答'}`,{class:'muted'}));
    if(q.type==='single'||q.type==='multiple'){
      const options=node('div');
      (q.options||[]).forEach((option,oi)=>options.append(node('p',optionDisplay(option,oi),{class:'readonly-option'})));
      detail.append(options);
    }
    const v=q.validation||{};
    if(q.type==='multiple')detail.append(node('p',`选择数量：${v.min_choices??0}–${v.max_choices??q.options?.length??0} 项`));
    if(q.type==='text')detail.append(node('p',`文本长度：${v.min_length??0}–${v.max_length??10000} 个字符`));
    if(q.type==='number')detail.append(node('p',`数字范围：${v.minimum??'不限'} 至 ${v.maximum??'不限'}；${v.integer_only?'只允许整数':'允许小数'}`));
    app.append(detail);
  });
}
function surveyProblem(message) {
  clear();app.append(box('card',node('h1','问卷暂不可访问'),node('p',message),
    box('row actions',node('a','返回工作台',{href:'/'}),action('重试',route,'secondary'))));
}
async function handleError(error) {
  if(error.name==='SessionChangedError')return;
  if(error.status===401){clearLogin();await route();tell('登录已失效，请重新登录');}
  else if(surveyPath()&&[404,410].includes(error.status))await route();
  else tell(error.message);
}
function auth(register=false) {
  clear();let username='',password='';const f=node('form',null,{class:'card auth'});
  const u=input('',v=>username=v,'text',{autocomplete:'username',required:'',minlength:'3',maxlength:'32'});
  const p=input('',v=>password=v,'password',{autocomplete:register?'new-password':'current-password',required:'',minlength:'8',maxlength:'128'});
  f.append(node('h1',register?'注册账号':'登录账号'),node('p','用户名为 3–32 位字母、数字或下划线；密码为 8–128 位。'),field('用户名',u),field('密码',p));
  const submit=node('button',register?'注册':'登录',{type:'submit'});
  f.append(box('row actions',submit,action(register?'已有账号，去登录':'没有账号，去注册',()=>auth(!register),'secondary')));
  f.lastChild.lastChild.type='button';
  if(surveyPath())f.append(node('a','返回工作台',{href:'/'}));
  f.addEventListener('submit',async e=>{
    e.preventDefault();submit.disabled=true;tell('');
    try {
      if(register) {await api('/auth/register','POST',{username,password});auth();tell('注册成功，请登录',true);}
      else {const result=await api('/auth/login','POST',{username,password});saveToken(result.access_token);if(await restoreLogin())await route();}
    }catch(err){if(err.name!=='SessionChangedError')tell(err.message);}finally{submit.disabled=false;}
  });app.append(f);
}
async function dashboard() {
  const loginToken=token;
  const data=await api(`/surveys?skip=${dashboardSkip}&limit=10`);if(token!==loginToken||!user)return;clear();
  app.append(box('row spread',box('',node('h1','我的问卷'),node('p','创建、发布问卷，并查看实时统计。')),box('row',action('题目与题库',()=>libraryHome(),'secondary'),action('创建问卷',()=>editor()))));
  if(!data.items.length) app.append(box('card',node('p','暂无问卷，点击“创建问卷”开始。')));
  for(const s of data.items) {
    const controls=box('row',action(s.status==='draft'?'编辑问卷':'查看设置',()=>editor(s._id),'secondary'),action('查看统计',()=>statistics(s._id),'secondary'),action('问卷版本',()=>showSurveyVersions(s._id),'secondary'));
    if(s.status==='draft') controls.append(action('发布',async()=>{if(!confirmPublication(s.title))return;const r=await api(`/surveys/${s._id}/publish`,'POST');await dashboard();tell('发布成功：'+location.origin+r.share_url,true);}));
    if(s.status==='published') controls.append(action('关闭问卷',async()=>{await api(`/surveys/${s._id}/close`,'POST');await dashboard();},'danger'));
    const card=box('card',box('row spread',node('h2',s.title),box('row',node('span',statuses[s.status],{class:'badge'}),node('span','v'+s.survey_version,{class:'badge'}))),node('p',s.description),node('p',s.deadline?'截止时间：'+new Date(s.deadline).toLocaleString():'未完成：必须设置截止时间'),controls);
    if(s.status!=='draft') card.append(node('p',null,{class:'share'}));
    if(s.status!=='draft') card.lastChild.append(node('a',location.origin+'/survey/'+s._id,{href:'/survey/'+s._id,target:'_blank',rel:'noopener'}));
    app.append(card);
  }
  app.append(box('row',node('span',`共 ${data.total} 份问卷`),action('上一页',async()=>{dashboardSkip=Math.max(0,dashboardSkip-10);await dashboard();},'secondary'),action('下一页',async()=>{if(dashboardSkip+10<data.total) {dashboardSkip+=10;await dashboard();}},'secondary')));
}
function localTime(iso) {if(!iso)return '';const d=new Date(iso);return new Date(d-d.getTimezoneOffset()*60000).toISOString().slice(0,16);}
function optionCode(index) {
  let code='';
  for(let n=index+1;n>0;n=Math.floor((n-1)/26))code=String.fromCharCode(65+(n-1)%26)+code;
  return code;
}
function optionDisplay(option,index) {return `${optionCode(index)}. ${option.label}`;}
function singleOptionRules(question) {
  // Expand legacy ordered eq/ne conditions into one effective route per option.
  return question.options.flatMap(option=>{
    const rule=question.rules.find(r=>(r.op==='eq'&&r.value===option.id)||(r.op==='ne'&&r.value!==option.id));
    return rule?[{op:'eq',value:option.id,target:rule.target}]:[];
  });
}
async function editor(id) {
  const loginToken=token;
  let s=id?await api('/surveys/'+id):await api('/surveys/drafts','POST');
  if(token!==loginToken||!user)return;
  id=s._id;
  let questionCounter=Math.max(0,...s.questions.map(q=>Number(q.id.match(/^q(\d+)$/)?.[1]||0)))+1;
  const editable=!id||s.status==='draft';
  let versionSets=new Map();
  async function loadVersions(){
    const ids=[...new Set(s.questions.map(q=>q.question_id).filter(Boolean))];
    versionSets=new Map(await Promise.all(ids.map(async key=>{
      try{return [key,{items:(await questionModule.versions(key)).items}];}
      catch(error){if(error.status===401)throw error;return [key,{items:[],error:error.status===404?null:error.message}];}
    })));
  }
  await loadVersions();
  if(token!==loginToken||!user)return;
  if(editable)for(const question of s.questions){
    question.jump_enabled ??= question.rules.length>0;
    if(question.type==='single')question.rules=singleOptionRules(question);
  }
  function jumpTargetName(id) {
    if(id==='END')return '结束问卷';
    const index=s.questions.findIndex(question=>question.id===id);
    return index<0?'目标题目不存在':`第${index+1}题：${s.questions[index].title.trim()||'未命名'}`;
  }
  function jumpTargetSelect(index,value,fn,withDefault=false) {
    const choices=s.questions.slice(index+1).map(question=>[question.id,jumpTargetName(question.id)]);
    choices.push(['END','结束问卷']);
    if(withDefault)choices.unshift(['',index===s.questions.length-1?'按顺序结束（最后一题）':'下一题（按顺序）']);
    const control=select(choices,value,fn);control.classList.add('jump-target');return control;
  }
  function legacyRuleLabel(question,rule,index) {
    const value=question.type==='multiple'?(Array.isArray(rule.value)?rule.value:[]).map(id=>{
      const oi=question.options.findIndex(option=>option.id===id);
      return oi<0?'已移除选项':optionDisplay(question.options[oi],oi);
    }).join('、'):JSON.stringify(rule.value);
    return `${index+1}. ${rule.op} ${value} → ${jumpTargetName(rule.target)}`;
  }
  function updateQuestionTitle(question,title) {
    question.title=title;
    // Updating option text preserves focus and the selected target's stable ID.
    app.querySelectorAll('select.jump-target option').forEach(option=>{
      if(option.value===question.id)option.textContent=jumpTargetName(question.id);
    });
    app.querySelectorAll('select.legacy-rule-choice').forEach(control=>{
      const source=s.questions.find(item=>item.id===control.dataset.questionId);
      Array.from(control.options).forEach((option,index)=>{
        if(source.rules[index].target===question.id)option.textContent=legacyRuleLabel(source,source.rules[index],index);
      });
    });
  }
  function editorPayload() {
    return {title:s.title,description:s.description,allow_anonymous:s.allow_anonymous,
      allow_multiple:s.allow_multiple,deadline:s.deadline,questions:s.questions,revision:s.revision};
  }
  async function createFromQuestion(version,localId=null){
    // Keep the current editor until both requests succeed. Failure never navigates away.
    s=await surveyModule.saveDraft(id,editorPayload());
    try{
      const selected=localId?s.questions.find(q=>q.id===localId):version;
      const next=await surveyModule.nextVersion(id,{revision:s.revision,...(localId?{local_id:localId}:{selected_question:questionRef(selected)})});
      await editor(next._id);tell(`原问卷已保存，已打开问卷 v${next.survey_version}；全部题目已保留，两份草稿均可继续编辑。`,true);
    }catch(error){render();throw error;}
  }
  function versionControls(q){
    const controls=box('row');
    if(q.question_id)controls.append(node('span','当前题目 v'+q.question_version,{class:'muted'}));
    const set=versionSets.get(q.question_id);
    if(set?.items.some(v=>v.question_version!==q.question_version))controls.append(action('切换题目版本',()=>chooseQuestionVersion(q),'secondary'));
    if(set?.error)controls.append(action('重试检查题目版本',async()=>{await loadVersions();render();},'secondary'));
    return controls;
  }
  async function chooseQuestionVersion(q){
    versionSets.set(q.question_id,{items:(await questionModule.versions(q.question_id)).items});
    const {d,content,button}=libraryDialog('选择题目版本');let clearRules=false,switched=false;
    content.append(node('p',`当前使用 v${q.question_version}。${editable?'确认后保存当前草稿并只切换这一题。':'发布版本保持原内容；选择后创建下一问卷版本的草稿。'}`));
    if(q.rules.length)content.append(check('清除本题跳转规则',false,value=>clearRules=value),node('small','题型或选项变化可能使原跳转规则失效；其他题跳到本题的规则会保留。'));
    for(const version of versionSets.get(q.question_id).items){
      const card=questionPreview(version.content,version.question_version);
      card.append(node('p',version.question_version===q.question_version?'当前版本':'可切换版本',{class:'muted'}));
      const choose=button(editable?'使用 v'+version.question_version:'使用 v'+version.question_version+' 新建草稿',async()=>{
        const choice={...questionRef(version),clear_rules:clearRules};
        if(editable){
          s=await surveyModule.saveDraft(id,editorPayload());
          s=await surveyModule.switchVersion(id,q.id,{...choice,revision:s.revision});
          await loadVersions();
          switched=true;d.close();render();tell('题目版本已切换，草稿已保存。',true);
        }else{
          const next=await surveyModule.nextVersion(id,{...choice,local_id:q.id});
          d.close();await editor(next._id);tell('已打开使用所选题目版本的新草稿，原问卷保留。',true);
        }
      });
      choose.disabled=version.question_version===q.question_version;
      card.append(choose);content.append(card);
    }
    d.addEventListener('close',()=>{if(editable&&!switched)render();});
  }
  function render() {
    clear();app.append(box('row spread',node('h1','问卷设置'),box('row',action('问卷版本',()=>showSurveyVersions(id),'secondary'),action('刷新可用版本',async()=>{await loadVersions();render();},'secondary'),action('返回列表',dashboard,'secondary'))));
    app.append(node('p','问卷版本 v'+s.survey_version+' · 编辑修订 '+s.revision,{class:'muted'}));
    app.append(node('p','创建时间：'+new Date(s.created_at).toLocaleString()+'（服务器记录，不能修改）',{class:'muted'}));
    if(!editable) {
      app.append(box('card',node('h2',s.title),node('p','发布后题目与填写策略冻结。可修改问卷标题、说明和截止时间。')));
      app.append(box('card',field('问卷标题',input(s.title,v=>s.title=v,'text',{maxlength:'200'})),field('问卷说明',area(s.description,v=>s.description=v)),action('保存标题和说明',async()=>{s=await api(`/surveys/${id}/metadata`,'PATCH',{title:s.title,description:s.description,revision:s.revision});render();tell('标题和说明已更新，题目与规则保持不变',true);})));
      if(s.status==='closed')app.append(node('p','问卷已关闭，不能修改截止时间。'));
      else{
        const d=input(localTime(s.deadline),()=>{},'datetime-local',{min:localTime(s.created_at),required:''});
        app.append(box('card',field('截止时间（发布前必填，本地时间）',d),action('保存截止时间',async()=>{if(!d.value)throw new Error('截止时间必须填写');await api(`/surveys/${id}/deadline`,'PATCH',{deadline:d.value?new Date(d.value).toISOString():null});await dashboard();tell('截止时间已更新',true);})));
      }
      s.questions.forEach((q,index)=>app.append(box('card question',box('row spread',node('h2',`第 ${index+1} 题`),versionControls(q)),questionPreview(q,q.question_version))));
      return;
    }
    app.append(box('card',field('问卷标题',input(s.title,v=>s.title=v,'text',{maxlength:'200'})),field('问卷说明',area(s.description,v=>s.description=v)),
      check('允许匿名填写（仍须登录）',s.allow_anonymous,v=>s.allow_anonymous=v),check('允许同一账号多次提交',s.allow_multiple,v=>s.allow_multiple=v),
      field('截止时间（发布前必填，本地时间）',input(localTime(s.deadline),v=>{const date=v?new Date(v):null;if(date&&Number.isNaN(date.getTime())){tell('截止时间格式不正确');return;}s.deadline=date?date.toISOString():null;},'datetime-local',{min:localTime(s.created_at),required:''}))));
    s.questions.forEach((q,index)=>app.append(questionEditor(q,index)));
    app.append(box('row actions',action('添加题目',()=>{s.questions.push({id:'q'+questionCounter++,title:'',type:'single',required:true,options:[{id:'o1',label:'选项一'},{id:'o2',label:'选项二'}],validation:{},jump_enabled:false,rules:[]});render();},'secondary'),
      action('从题目或题库选题',async()=>chooseLibraryQuestion(async version=>{if(s.questions.length>=200)throw new Error('每份问卷最多200题');s.questions.push({id:'q'+questionCounter++,source_version_id:version._id,question_id:version.question_id,question_version:version.question_version,...structuredClone(version.content),jump_enabled:false,rules:[]});await loadVersions();render();},createFromQuestion),'secondary'),
      action('保存草稿',async()=>{s=await surveyModule.saveDraft(id,editorPayload());await loadVersions();render();tell('草稿已保存。返回列表后可发布。',true);},'secondary'),
      action('直接发布',async()=>{
        if(!confirmPublication(s.title))return;
        if(!s.deadline)throw new Error('截止时间必须填写');
        const result=await api(`/surveys/${id}/publish-direct`,'POST',editorPayload());
        await dashboard();tell('发布成功：'+location.origin+result.share_url,true);
      })));
  }
  function questionEditor(q,index) {
    const card=box('card question',box('row spread',node('h2',`第 ${index+1} 题 · ${q.id}`),box('row',versionControls(q),action('基于此题创建下一版',()=>createFromQuestion(null,q.id),'secondary'),action('删除题目',()=>{if(s.questions.some(x=>x.rules.some(r=>r.target===q.id)))throw new Error('此题被跳转规则引用，请先修改相关规则');s.questions.splice(index,1);render();},'danger'))));
    card.append(field('题目标题',input(q.title,v=>updateQuestionTitle(q,v),'text',{maxlength:'500'})),box('row',field('题型',select(Object.entries(types),q.type,v=>{q.type=v;q.validation={};q.rules=[];q.jump_enabled=false;q.options=['single','multiple'].includes(v)?[{id:'o1',label:'选项一'},{id:'o2',label:'选项二'}]:[];render();})),check('必答题',q.required,v=>q.required=v)));
    if(['single','multiple'].includes(q.type)){
      card.append(node('p','在输入框中填写选项内容；字母按当前顺序自动更新。',{class:'muted'}));
      q.options.forEach((o,oi)=>card.append(box('row',field(`选项 ${optionCode(oi)}`,input(o.label,v=>{
        o.label=v;
        const route=card.querySelector(`.single-jump[data-option-id="${o.id}"]`);
        if(route){const label=`选项 ${optionCode(oi)}「${v||'未命名'}」跳转到`;route.querySelector('span').textContent=label;route.querySelector('select').setAttribute('aria-label',label);}
        const comparison=card.querySelector(`.comparison-option[data-option-id="${o.id}"]`);
        if(comparison)comparison.lastChild.textContent=optionDisplay(o,oi);
      },'text',{maxlength:'200',placeholder:'请输入选项内容'})),action('上移',()=>{if(oi>0){[q.options[oi-1],q.options[oi]]=[q.options[oi],q.options[oi-1]];render();}},'secondary'),action('删除选项',()=>{if(q.rules.some(r=>Array.isArray(r.value)?r.value.includes(o.id):r.value===o.id))throw new Error('此选项被跳转规则引用，请先修改规则');q.options.splice(oi,1);render();},'danger'))));
      card.append(action('添加选项',()=>{if(q.options.length>=100)throw new Error('每题最多100个选项');q.options.push({id:'o'+newAttemptId().replaceAll('-',''),label:'新选项'});render();},'secondary'));
    }
    const v=q.validation;
    function numberField(label,key,nullable=false,integer=true) {return field(label,input(v[key],x=>v[key]=x===''?(nullable?null:0):Number(x),'number',{step:integer?'1':'any'}));}
    if(q.type==='multiple')card.append(box('grid',numberField('最少选择数（可不答题仅在作答时检查）','min_choices'),numberField('最多选择数（留空为全部）','max_choices',true)));
    if(q.type==='text')card.append(box('grid',numberField('最短文本长度','min_length'),numberField('最长文本长度（默认 10000）','max_length')));
    if(q.type==='number')card.append(box('grid',numberField('数字最小值（可留空）','minimum',true,false),numberField('数字最大值（可留空）','maximum',true,false)),check('只允许整数',v.integer_only||false,x=>v.integer_only=x));
    if(q.source_version_id){
      card.append(node('p',`基于题目 v${q.question_version}。可以直接修改；保存时生成新题目版本，其他问卷保留各自内容。`,{class:'muted'}));
    }
    card.append(action('放入题库',async()=>{
      let version;
      if(q.question_id){
        const base=await api('/questions/by-key/'+q.question_id+'/'+q.question_version);
        if(JSON.stringify(libraryContent(q))===JSON.stringify(base.content))version=base;
        else version=await api('/questions/'+base._id+'/versions','POST',libraryContent(q));
      }else version=await api('/questions','POST',libraryContent(q));
      await api('/usual-questions','POST',questionRef(version));
      Object.assign(q,{source_version_id:version._id,question_id:version.question_id,question_version:version.question_version});
      Object.assign(q,version.content);await loadVersions();render();tell('此题目版本已放入题库；保存草稿后记录问卷引用。',true);
    },'secondary'));
    if(q.type!=='text')card.append(check(q.type==='single'?'启用选项跳转':'启用条件跳转',q.jump_enabled,x=>{q.jump_enabled=x;render();}),node('small','关闭时按题目顺序填写；已配置的规则会保留，重新启用后生效。'));
    if(q.type==='single'&&q.jump_enabled){
      card.append(node('h3','选项跳转'),node('p','每个选项单独选择跳转目标；默认进入下一题。请先添加后续题目，再选择目标。',{class:'muted'}));
      const routes=box('single-jumps');
      q.options.forEach((option,oi)=>{
        const current=q.rules.find(r=>r.op==='eq'&&r.value===option.id)?.target||'';
        const row=box('rule single-jump',field(`选项 ${optionCode(oi)}「${option.label||'未命名'}」跳转到`,jumpTargetSelect(index,current,target=>{
          q.rules=q.rules.filter(r=>r.value!==option.id);
          if(target)q.rules.push({op:'eq',value:option.id,target});
        },true)));
        row.dataset.optionId=option.id;routes.append(row);
      });
      card.append(routes);
    }
    if((q.type==='multiple'||q.type==='number')&&q.jump_enabled){
      card.append(node('h3','条件跳转'),node('p','每题最多一条跳转规则；未命中进入下一题。修改条件可直接编辑，替换规则也可先删除再添加。',{class:'muted'}));
      if(q.rules.length>1){
        let keep=0;
        const keepRule=select(q.rules.map((rule,i)=>[String(i),legacyRuleLabel(q,rule,i)]),'0',value=>keep=Number(value));
        keepRule.classList.add('legacy-rule-choice');keepRule.dataset.questionId=q.id;
        card.append(node('p',`旧配置包含${q.rules.length}条规则，请先选择保留哪一条，其余规则将在保存时移除。`),
          field('选择保留的跳转规则',keepRule),
          action('只保留所选规则',()=>{q.rules=[q.rules[keep]];render();},'secondary'));
        return card;
      }
      q.rules.forEach((r,ri)=>{
        const operators=q.type==='multiple'?[['contains_any','包含任意一个'],['contains_all','包含全部'],['contains_none','全都不包含']]:[['eq','等于'],['ne','不等于'],['gt','大于'],['gte','大于等于'],['lt','小于'],['lte','小于等于'],['between','在闭区间内']];
        const row=box('rule',field('判断条件',select(operators,r.op,x=>{r.op=x;r.value=q.type==='number'?(x==='between'?[0,0]:0):q.type==='multiple'?[]:(q.options[0]?.id||'');render();})));
        if(q.type==='multiple') {const opts=box('');q.options.forEach((o,oi)=>{const comparison=check(optionDisplay(o,oi),r.value.includes(o.id),x=>{r.value=x?[...r.value,o.id]:r.value.filter(k=>k!==o.id);});comparison.classList.add('comparison-option');comparison.dataset.optionId=o.id;opts.append(comparison);});row.append(field('比较选项（至少选择一个）',opts));}
        if(q.type==='multiple'&&r.op==='contains_none')row.append(node('small','已作答且未选中上述任何选项时触发；可选题未作答时不触发。'));
        if(q.type==='number') {
          if(r.op==='between')row.append(box('grid',field('下限',input(r.value[0],x=>r.value[0]=Number(x),'number',{step:'any'})),field('上限',input(r.value[1],x=>r.value[1]=Number(x),'number',{step:'any'}))));
          else row.append(field('比较数字',input(r.value,x=>r.value=Number(x),'number',{step:'any'})));
        }
        row.append(field('跳转到',jumpTargetSelect(index,r.target,x=>r.target=x)),action('删除规则',()=>{q.rules.splice(ri,1);render();},'danger'));card.append(row);
      });
      const addRule=action('添加跳转规则',()=>{if(q.rules.length)throw new Error('最多只能配置一条跳转规则，请先删除已有规则');q.rules.push({op:q.type==='multiple'?'contains_any':'eq',value:q.type==='multiple'?[]:0,target:'END'});render();},'secondary');
      addRule.disabled=q.rules.length>0;
      if(q.rules.length)addRule.title='已有一条规则，删除后才能添加';
      card.append(addRule);
    }
    return card;
  }
  render();
}
async function fill(id,state) {
  const loginToken=token;
  const s=await api('/public/surveys/'+id);let answers={},history=[],current=s.questions[0]?.id,complete=false,anonymous=false;
  if(token!==loginToken||!user)return;
  const serverOffset=Date.parse(state?.server_time||new Date().toISOString())-Date.now();
  let activeValue=null,submissionId=newAttemptId(),saveOnDevice=true,restored=false,storageFailed=false,recoveryFailed=false;
  const draftKey=`survey_draft:v1:${user.id}:${id}`;
  function removeDraft(){try{localStorage.removeItem(draftKey);}catch(_){}}
  function remember(){
    if(!saveOnDevice){removeDraft();return;}
    try{localStorage.setItem(draftKey,JSON.stringify({answers,anonymous,submission_id:submissionId,active_value:activeValue,current,updated_at:Date.now()}));}
    catch(_){if(!storageFailed){storageFailed=true;tell('浏览器无法暂存进度，请保持页面打开直至提交');}}
  }
  function changed(value){activeValue=value;remember();}
  try{
    const saved=JSON.parse(localStorage.getItem(draftKey)||'null');
    if(saved&&Date.now()-saved.updated_at<7*24*60*60*1000&&/^[0-9a-f-]{36}$/i.test(saved.submission_id)){
      const result=await api(`/public/surveys/${id}/next`,'POST',{answers:saved.answers,anonymous:saved.anonymous});
      answers=saved.answers;anonymous=saved.anonymous;submissionId=saved.submission_id;complete=result.complete;current=result.next_question_id;
      history=complete?result.path:result.path.slice(0,-1);activeValue=saved.current===current?saved.active_value:null;restored=true;
    }else if(saved)removeDraft();
  }catch(error){if([401,404,410].includes(error.status))throw error;recoveryFailed=true;}
  function render() {
    clear();app.append(node('h1',s.title),node('p',s.description),node('p',s.deadline?'截止：'+new Date(s.deadline).toLocaleString():'未完成：必须设置截止时间',{class:'muted'}));
    app.append(node('a','返回工作台',{href:'/'}));
    if(s.deadline){const remaining=Date.parse(s.deadline)-(Date.now()+serverOffset);deadlineTimer=setTimeout(()=>route().catch(handleError),Math.min(2147483647,Math.max(0,remaining)+20));}
    if(restored)app.append(node('p','已恢复本设备暂存的填写进度。',{class:'muted'}));
    if(recoveryFailed)app.append(node('p','暂存进度暂时无法恢复，原记录仍保留。请先刷新重试；开始新的作答会覆盖旧记录。',{class:'muted'}));
    app.append(check('在本设备暂存进度（7天；退出登录会清除，共享设备请取消）',saveOnDevice,x=>{saveOnDevice=x;remember();}),action('清除进度并重新填写',()=>{removeDraft();answers={};history=[];current=s.questions[0]?.id;complete=false;activeValue=null;submissionId=newAttemptId();restored=false;render();},'secondary'));
    if(s.allow_anonymous)app.append(check('匿名提交（答卷不保存账号身份）',anonymous,x=>{anonymous=x;remember();}));
    if(!s.allow_multiple)app.append(node('p','本问卷每个账号仅可提交一次；匿名提交也受此限制。',{class:'muted'}));
    if(complete) {
      const card=box('card',node('h2','确认提交'));
      for(const [qid,value] of Object.entries(answers)){const q=s.questions.find(q=>q.id===qid);let display=value;if(q.options.length)display=(Array.isArray(value)?value:[value]).map(v=>{const oi=q.options.findIndex(o=>o.id===v);return oi<0?'已移除选项':optionDisplay(q.options[oi],oi);}).join('、');card.append(node('h3',q.title),node('p',value===null?'未作答':String(display)));}
      card.append(box('row actions',action('返回修改',back,'secondary'),action('提交答卷',async()=>{remember();await api(`/public/surveys/${id}/responses`,'POST',{answers,anonymous,submission_id:submissionId});removeDraft();clear();const success=box('card',node('h1','提交成功'),node('p','答卷已保存。'));if(s.allow_multiple)success.append(action('再次填写',route,'secondary'));success.append(node('a','返回工作台',{href:'/'}));app.append(success);})));app.append(card);return;
    }
    const q=s.questions.find(q=>q.id===current);const card=box('card question',node('p',`第 ${s.questions.indexOf(q)+1} / ${s.questions.length} 题 · ${types[q.type]}`,{class:'muted'}),node('h2',q.title+(q.required?' *':'')));
    let value=activeValue??null;
    if(['single','multiple'].includes(q.type))q.options.forEach((o,oi)=>{
      const i=node('input',null,{type:q.type==='single'?'radio':'checkbox',name:q.id,value:o.id});i.checked=q.type==='single'?value===o.id:Array.isArray(value)&&value.includes(o.id);
      i.addEventListener('change',()=>{if(q.type==='single')value=o.id;else{const values=Array.isArray(value)?value:[];value=i.checked?[...values,o.id]:values.filter(x=>x!==o.id);}changed(value);});
      const label=node('label',null,{class:'option'});label.append(i,document.createTextNode(optionDisplay(o,oi)));card.append(label);
    });
    if(q.type==='text')card.append(field('填写文本',area(value??'',x=>{value=x;changed(value);})),node('small',`长度 ${q.validation.min_length}–${q.validation.max_length} 个字符`));
    if(q.type==='number')card.append(field('填写数字',input(value,x=>{value=x===''?null:Number(x);changed(value);},'number',{step:q.validation.integer_only?'1':'any'})),node('small',`${q.validation.integer_only?'整数；':''}范围 ${q.validation.minimum??'不限'} 至 ${q.validation.maximum??'不限'}`));
    if(!q.required)card.append(action('清空本题答案',()=>{activeValue=null;remember();render();},'secondary'));
    if(q.type==='multiple')card.append(node('small',`选择 ${q.validation.min_choices}–${q.validation.max_choices??q.options.length} 项`));
    const controls=box('row actions');if(history.length)controls.append(action('上一题',back,'secondary'));
    controls.append(action('下一步',async()=>{const candidate={...answers,[q.id]:value};const result=await api(`/public/surveys/${id}/next`,'POST',{answers:candidate,anonymous});history.push(current);answers=candidate;current=result.next_question_id;complete=result.complete;activeValue=null;remember();render();}));card.append(controls);app.append(card);
  }
  function back(){const prior=history.pop();if(!prior)return;current=prior;activeValue=answers[prior]??null;const pos=s.questions.findIndex(q=>q.id===prior);answers=Object.fromEntries(Object.entries(answers).filter(([qid])=>s.questions.findIndex(q=>q.id===qid)<pos));complete=false;remember();render();}
  if(token===loginToken&&user)render();
}
async function statistics(id) {
  const loginToken=token;
  const s=await api('/surveys/'+id);if(token!==loginToken||!user)return;clear();app.append(box('row spread',node('h1',s.title+' · 统计'),action('返回列表',dashboard,'secondary')));
  const out=box('');let chosen='';
  async function load(){const data=await api(`/surveys/${id}/statistics${chosen?'/'+chosen:''}`);out.replaceChildren();if(!chosen)out.append(node('p',`已提交 ${data.response_count} 份答卷。重复提交按答卷次数计数。`));for(const q of chosen?[data]:data.questions){const c=box('card',node('h2',q.title),node('p',`回答总数：${q.answer_count}`));if(q.options)q.options.forEach((o,oi)=>c.append(node('p',`${optionDisplay(o,oi)}：${o.count} ${q.type==='single'?'人次':'次'}`)));if(q.type==='number')c.append(node('p','平均值：'+(q.average??'暂无回答')));if(q.contents){c.append(node('h3','全部回答内容'));q.contents.forEach(v=>c.append(node('div',String(v),{class:'contents'})));}out.append(c);}}
  app.append(box('row',field('统计范围',select([['','整份问卷'],...s.questions.map(q=>[q.id,q.title])],'',async x=>{chosen=x;try{await load();}catch(e){tell(e.message);}})),action('刷新统计',load,'secondary')),out);await load();
}
async function route(){
  if(!user){auth();return;}
  const loginToken=token;
  const match=surveyPath();
  if(match){
    clear();app.append(node('p','正在检查问卷状态…'));
    try{
      const state=await api(`/public/surveys/${match[1]}/status`);
      if(token!==loginToken||!user)return;
      if(!state.can_fill){surveyState(state);return;}
      await fill(match[1],state);
    }catch(error){
      if(token!==loginToken)return;
      if(error.status===401){clearLogin();auth();tell('登录已失效，请重新登录');}
      else if(error.status===410){try{const state=await api(`/public/surveys/${match[1]}/status`);if(state.can_fill)surveyProblem('问卷状态已变化，请重试后填写。');else surveyState(state);}catch(_){surveyProblem(error.message);}}
      else surveyProblem(error.message);
    }
    return;
  }
  await dashboard();
}
async function showSurveyVersions(id){
  const data=await api('/surveys/'+id+'/versions');
  const {d,content,button}=libraryDialog('问卷版本历史');
  content.append(node('p','每个问卷版本独立保留题目快照和答卷，旧分享链接保持不变。'));
  data.items.forEach(v=>{
    const row=box('card',node('h3',`问卷 v${v.survey_version} · ${v.title}`),node('p',`${statuses[v.status]} · ${v.parent_version?'基于 v'+v.parent_version:'初始版本'}`),
      button('查看 v'+v.survey_version,async()=>{d.close();await editor(v._id);},'secondary'));
    row.append(button('基于 v'+v.survey_version+' 新建草稿',async()=>{
      const next=await api('/surveys/'+v._id+'/versions','POST');d.close();await editor(next._id);
    }));
    content.append(row);
  });
}
function libraryContent(q){return Object.fromEntries(['title','type','required','options','validation'].map(k=>[k,structuredClone(q[k])]));}
async function libraryItems(path){
  const items=[];let data;
  do{data=await api(`${path}?skip=${items.length}&limit=100`);items.push(...data.items);}while(items.length<data.total&&data.items.length);
  return items;
}
function libraryDialog(title){
  const d=node('dialog',null,{class:'library-dialog','aria-label':title});
  const content=box('',box('row spread',node('h2',title),action('取消',()=>d.close(),'secondary')));
  const error=node('p','',{class:'dialog-error',role:'alert'});content.append(error);d.append(content);document.body.append(d);
  d.addEventListener('close',()=>d.remove());d.showModal();
  function button(label,fn,cls=''){
    return action(label,async()=>{error.textContent='';try{await fn();}catch(e){if(e.status===401){d.close();throw e;}error.textContent=e.message;}},cls);
  }
  return {d,content,button};
}
function editLibraryQuestion(source,onSaved){
  let q=source?structuredClone(source.content):{title:'',type:'single',required:true,options:[{id:'a',label:'选项一'},{id:'b',label:'选项二'}],validation:{}};
  const {d,content,button}=libraryDialog(source?'创建题目新版本':'创建常用题目');
  const form=box('');content.append(form);
  function render(){
    form.replaceChildren(field('常用题标题',input(q.title,v=>q.title=v,'text',{maxlength:'500'})),
      field('常用题型',select(Object.entries(types),q.type,v=>{q.type=v;q.validation={};q.options=['single','multiple'].includes(v)?[{id:'a',label:'选项一'},{id:'b',label:'选项二'}]:[];render();})),
      check('常用题必答',q.required,v=>q.required=v));
    if(['single','multiple'].includes(q.type)){
      form.append(node('p','每行一个选项，内部标识按位置保留；新版本与旧版本分别统计。',{class:'muted'}),
        field('常用题选项',area(q.options.map(o=>o.label).join('\n'),text=>{q.options=text.split('\n').map((label,i)=>({id:q.options[i]?.id||'o'+newAttemptId().replaceAll('-',''),label}));})));
    }
    const v=q.validation;
    function numeric(label,key,nullable=false){return field(label,input(v[key],x=>v[key]=x===''?(nullable?null:0):Number(x),'number',{step:['minimum','maximum'].includes(key)?'any':'1'}));}
    if(q.type==='multiple')form.append(numeric('常用题最少选择数','min_choices'),numeric('常用题最多选择数','max_choices',true));
    if(q.type==='text')form.append(numeric('常用题最短长度','min_length'),numeric('常用题最长长度','max_length'));
    if(q.type==='number')form.append(numeric('常用题最小值','minimum',true),numeric('常用题最大值','maximum',true),check('常用题只允许整数',v.integer_only||false,x=>v.integer_only=x));
  }
  render();
  if(source)content.append(node('p',`来源：v${source.version} · ${source._id}。保存会生成新记录，已有问卷保持原内容。`));
  async function save(collect){
    const result=await api(source?`/questions/${source._id}/versions`:'/questions','POST',q);
    if(collect)await api('/usual-questions','POST',questionRef(result));
    d.close();await onSaved(result);
  }
  if(source)content.append(button('保存新版本',()=>save(false),'secondary'));
  content.append(button('保存并放入题库',()=>save(true)));
}
function questionPreview(q,version){
  const card=box('card version-preview',node('h3',q.title+(version?' · v'+version:'')),
    node('p',`${types[q.type]} · ${q.required?'必答':'选答'}`));
  q.options.forEach((o,i)=>card.append(node('p',optionDisplay(o,i))));
  const limits=q.validation;
  if(q.type==='multiple')card.append(node('p',`选择数量：${limits.min_choices??0}–${limits.max_choices??q.options.length} 项`));
  if(q.type==='text')card.append(node('p',`文本长度：${limits.min_length??0}–${limits.max_length??10000} 个字符`));
  if(q.type==='number')card.append(node('p',`数字范围：${limits.minimum??'不限'} 至 ${limits.maximum??'不限'}；${limits.integer_only?'只允许整数':'允许小数'}`));
  return card;
}
async function openSurveyFromQuestions(versions){
  const next=await surveyModule.fromQuestions(versions.map(questionRef));
  await editor(next._id);tell('已打开新问卷草稿。',true);
}
async function chooseLibraryQuestion(onChosen,onCreate=null){
  const [collected,accessible]=await Promise.all([libraryItems('/usual-questions'),libraryItems('/questions')]);
  const ids=new Set(collected.map(v=>v._id));
  const versions=[...collected.filter(v=>!v.unavailable),...accessible.filter(v=>!ids.has(v._id))];
  const {d,content,button}=libraryDialog('选择已有题目');
  const rows=box('');
  const sources=new Map();versions.forEach(v=>{if(!sources.has(v.question_id))sources.set(v.question_id,v.content.title);});
  function show(source){
    rows.replaceChildren();
    const groups=new Map();versions.filter(v=>!source||v.question_id===source).forEach(v=>{if(!groups.has(v.question_id))groups.set(v.question_id,[]);groups.get(v.question_id).push(v);});
    if(!groups.size)rows.append(node('p','暂无已入库或收到共享的题目。'));
    for(const [questionId,items] of groups){
      const group=box('question-source',node('h3','同源题目'));group.dataset.questionId=questionId;
      items.sort((a,b)=>b.question_version-a.question_version).forEach(v=>{
        const card=questionPreview(v.content,v.question_version);
        card.append(node('p',`${types[v.content.type]} · ${v.owner_id===user.id?'我的题目':'共享题目'}`),node('p',ids.has(v._id)?'已入库':'未入库',{class:'muted'}),box('row',
          button('选用此版本',async()=>{await onChosen(v);d.close();}),
          button(onCreate?'选用此题创建下一版':'用此题新建问卷',async()=>{if(onCreate)await onCreate(v);else await openSurveyFromQuestions([v]);d.close();},'secondary')));
        group.append(card);
      });rows.append(group);
    }
  }
  content.append(field('按题目源筛选',select([['','全部题目源'],...sources],'',show)),rows);show('');
}
function questionRef(v){return {question_id:v.question_id,question_version:v.question_version};}
function moreMenu(label,entries){
  const details=node('details',null,{class:'more-menu'});
  details.append(node('summary','⋯',{'aria-label':label,'aria-haspopup':'menu'}));
  const menu=box('more-options');menu.setAttribute('role','menu');
  entries.forEach(([title,fn,enabled=true])=>{
    const button=action(title,async()=>{details.open=false;await fn();},'secondary');
    button.setAttribute('role','menuitem');button.disabled=!enabled;
    if(!enabled)button.title='只有题目作者可以转发';
    menu.append(button);
  });
  details.append(menu);return details;
}
function forwardDialog(versions,onDone){
  if(!versions.length)throw new Error('请先选择题目');
  if(versions.some(v=>v.owner_id!==user.id))throw new Error('只有题目作者可以转发，共享题目只能使用');
  const {d,content,button}=libraryDialog('转发题目');let username='';
  content.append(node('p',`已选择 ${versions.length} 个题目版本`));
  const names=node('ul');versions.slice(0,6).forEach(v=>names.append(node('li',`${v.content.title} · v${v.question_version}`)));
  if(versions.length>6)names.append(node('li',`另有 ${versions.length-6} 个版本`));content.append(names,
    field('转发给用户名',input('',value=>username=value,'text',{autocomplete:'off',maxlength:'32',placeholder:'请输入已注册的用户名'})),
    node('p','只转发选定版本。对方需点击“放入题库”才会收录。',{class:'muted'}),button('确认转发',async()=>{
      if(!/^[A-Za-z0-9_]{3,32}$/.test(username))throw new Error('用户名应为3–32位字母、数字或下划线');
      const result=await api('/questions/forward','POST',{username,items:versions.map(questionRef)});
      d.close();if(onDone)await onDone();tell(`已向 ${result.recipient} 转发 ${result.forwarded_count} 个题目版本`,true);
    }));
}
async function libraryHome(startSelecting=false){
  clear();app.append(node('p','正在加载题库…'));
  const [collected,accessible]=await Promise.all([libraryItems('/usual-questions'),libraryItems('/questions')]);
  const collectedIds=new Set(collected.map(v=>v._id));
  const pending=accessible.filter(v=>!collectedIds.has(v._id));
  let selecting=startSelecting===true;const selected=new Set();
  async function removeVersion(v){
    if(!window.confirm(`从你的题库删除 v${v.question_version}？已有问卷、历史版本及其他用户的收录将保留。`))return;
    await api(`/usual-questions/${v.question_id}/${v.question_version}`,'DELETE');await libraryHome();
  }
  function versionRow(v,isPending){
    if(v.unavailable)return box('version-entry',node('h3','不可用版本 v'+v.question_version),node('p','此版本已失去使用权限。'),action('删除此版本',()=>removeVersion(v),'danger'));
    const owned=v.owner_id===user.id;
    const entries=isPending?[]:[['多选',()=>{selecting=true;selected.add(v._id);render();}]];
    entries.push(['转发',()=>forwardDialog([v],()=>libraryHome()),owned]);
    const row=box('version-entry',box('row spread',node('h3',`${v.content.title} · v${v.question_version}`),moreMenu('题目操作：'+v.content.title,entries)),
      node('p',`${types[v.content.type]} · ${owned?'我的题目':'共享题目 · 仅可使用'}`));
    const controls=box('row',action('查看题目详情',()=>libraryDetail(v._id),'secondary'),action('创建新版本',()=>editLibraryQuestion(v,result=>libraryDetail(result._id)),'secondary'));
    if(isPending)controls.append(action('放入题库',async()=>{await api('/usual-questions','POST',questionRef(v));await libraryHome();},'secondary'));
    else controls.append(action('删除此版本',()=>removeVersion(v),'danger'));
    row.append(controls);
    if(selecting&&!isPending)row.prepend(check('选择题目：'+v.content.title+' v'+v.question_version,selected.has(v._id),yes=>{yes?selected.add(v._id):selected.delete(v._id);render();}));
    return row;
  }
  function groups(items,isPending=false){
    const families=new Map();items.forEach(v=>{if(!families.has(v.question_id))families.set(v.question_id,[]);families.get(v.question_id).push(v);});
    for(const [id,versions] of families){
      versions.sort((a,b)=>b.question_version-a.question_version);
      const title=versions.find(v=>v.content)?.content.title||'不可用题目';
      const group=box('card '+(isPending?'pending-group':'question-group'),node('h2','题目：'+title),node('p',`${versions.length} 个${isPending?'待入库':'收录'}版本 · 同一题目源`,{class:'muted'}));
      group.dataset.questionId=id;versions.forEach(v=>group.append(versionRow(v,isPending)));
      if(!isPending)group.append(action('删除此题的所有收录版本',async()=>{
        if(!window.confirm('从你的题库移除这道题的全部收录版本？已有问卷及其他用户收录将保留。'))return;
        await api('/usual-questions/'+id,'DELETE');await libraryHome();
      },'danger'));app.append(group);
    }
  }
  function render(){
    clear();const available=collected.filter(v=>!v.unavailable),mine=available.filter(v=>v.owner_id===user.id);
    app.append(box('row spread',node('h1','题目与题库'),box('row',action('返回问卷列表',dashboard,'secondary'),
      moreMenu('常用题目操作',[
        ['多选',()=>{selecting=true;render();}],
        ['转发我的全部题目',()=>forwardDialog(mine,()=>libraryHome()),mine.length>0]
      ]))));
    app.append(node('p','一题一版本独立收录；同源版本在同一题目下展示。保存新版本不会自动入库。',{class:'muted'}),
      action('创建常用题目',()=>editLibraryQuestion(null,()=>libraryHome())),node('h2','已入库题目'));
    if(selecting)app.append(box('row selection-toolbar',node('span',`已选择 ${selected.size} 题`,{'aria-live':'polite'}),
      action('全选我的题目',()=>{mine.forEach(v=>selected.add(v._id));render();},'secondary'),action('清空选择',()=>{selected.clear();render();},'secondary'),
      action('用选中题目新建问卷',()=>openSurveyFromQuestions(available.filter(v=>selected.has(v._id)))),
      action('转发选中题目',()=>forwardDialog(available.filter(v=>selected.has(v._id)),()=>libraryHome())),
      action('取消多选',()=>{selecting=false;selected.clear();render();},'secondary')));
    if(!collected.length)app.append(node('p','暂无已入库题目。'));
    groups(collected);
    if(pending.length){app.append(node('h2','待入库题目'),node('p','共享和历史版本不会自动入库，可查看选用或明确点击“放入题库”。'));groups(pending,true);}
  }
  render();
}
async function libraryDetail(id){
  const [v,history,usage,stats]=await Promise.all([api('/questions/'+id),api('/questions/'+id+'/history'),api('/questions/'+id+'/usages'),api('/questions/'+id+'/statistics')]);
  clear();app.append(box('row spread',node('h1',v.content.title+' · v'+v.version),action('返回题目与题库',libraryHome,'secondary')));
  const collected=(await libraryItems('/usual-questions')).some(item=>item._id===v._id);
  const details=box('card',node('h2','题目内容'),node('p',`${types[v.content.type]} · ${v.content.required?'必答':'选答'}`),
    collected?node('p','此版本已入库',{class:'muted'}):action('放入题库',async()=>{await api('/usual-questions','POST',questionRef(v));await libraryDetail(id);tell('此版本已放入题库',true);}));
  v.content.options.forEach((o,i)=>details.append(node('p',optionDisplay(o,i))));
  const limits=v.content.validation;
  if(v.content.type==='multiple')details.append(node('p',`选择数量：${limits.min_choices}–${limits.max_choices??v.content.options.length} 项`));
  if(v.content.type==='text')details.append(node('p',`文本长度：${limits.min_length}–${limits.max_length} 个字符`));
  if(v.content.type==='number')details.append(node('p',`数字范围：${limits.minimum??'不限'} 至 ${limits.maximum??'不限'}；${limits.integer_only?'只允许整数':'允许小数'}`));
  details.append(box('row',
    action('用此题新建问卷',()=>openSurveyFromQuestions([v])),
    action('基于此版本修改',()=>editLibraryQuestion(v,result=>libraryDetail(result._id))),
    action('恢复为新版本',async()=>{
      if(!window.confirm('将此版本内容恢复为一个新版本？已有版本和问卷将保留。'))return;
      const restored=await api('/questions/'+id+'/restore','POST');await libraryDetail(restored._id);
    },'secondary')));app.append(details);
  if(v.owner_id===user.id){
    const shares=await api('/questions/'+id+'/shares');let username='';
    const sharing=box('card',node('h2','共享题目'),node('p','仅共享当前题目版本；接收者可以使用，不能继续转发。'),
      field('共享给用户名',input('',x=>username=x)),action('共享给用户',async()=>{await api('/questions/'+id+'/shares','POST',{username});await libraryDetail(id);}));
    shares.items.forEach(s=>sharing.append(box('row',node('span',s.username),action('撤销共享',async()=>{await api('/questions/'+id+'/shares/'+encodeURIComponent(s.username),'DELETE');await libraryDetail(id);},'danger'))));
    app.append(sharing);
  }
  const hist=box('card',node('h2','版本历史'),node('p','来源指向实际派生版本；分支和恢复都保留。'));
  history.items.forEach(h=>{
    const parent=history.items.find(item=>item._id===h.parent_id);
    const relation=h.parent_id?(parent?`来源 v${parent.version}`:'来源：共享题目的其他版本'):'初始版本';
    hist.append(box('row',node('p',`v${h.version} ${h.content.title} · ${relation}${h.restored_from?'（恢复）':''}`),action('查看此版本',()=>libraryDetail(h._id),'secondary')));
  });app.append(hist);
  const usages=box('card',node('h2','问卷引用'),node('p',`总引用问卷：${usage.total_survey_count}；可查看详情：${usage.visible_survey_count}。他人问卷内容保持私有。`));
  usage.items.forEach(s=>usages.append(box('row',node('p',`${s.title} · ${statuses[s.status]} · 本地题号 ${s.local_ids.join('、')}`),action('查看引用问卷',()=>editor(s.survey_id),'secondary'))));app.append(usages);
  const result=box('card',node('h2','跨问卷统计'),node('p','范围：我拥有的所有问卷，只汇总当前确切版本；不同版本分别统计。'),
    node('p',`涉及 ${stats.survey_count} 份问卷，${stats.response_count} 份有回答的答卷，${stats.answer_count} 次回答。同卷重复引用按出现次数计算。`));
  if(stats.options)stats.options.forEach((o,i)=>result.append(node('p',`${optionDisplay(o,i)}：${o.count} 次`)));
  if(stats.type==='number'){
    result.append(node('p','平均值：'+(stats.average??'暂无回答')),node('h3','数字分布'));
    stats.distribution.forEach(x=>result.append(node('p',`${x.value}：${x.count} 次`)));
  }
  if(stats.contents){result.append(node('h3','全部回答内容'));stats.contents.forEach(value=>result.append(node('div',String(value),{class:'contents'})));}
  app.append(result);
}
function entryError(error) {if(error.name==='SessionChangedError')return;if(surveyPath())surveyProblem(error.message);else{if(!user)auth();tell(error.message);}}
window.addEventListener('storage',async event=>{
  if(event.key!==AUTH_KEY&&event.key!==null)return;
  let next;
  try{if(event.storageArea!==localStorage)return;next=localStorage.getItem(AUTH_KEY);}catch(_){return;}
  if(next===token)return;
  token=next;user=null;
  try{sessionStorage.removeItem(AUTH_KEY);}catch(_){}
  account();clear();app.append(node('p','正在检查登录状态…'));
  try{if(await restoreLogin())await route();}catch(error){entryError(error);}
});
(async()=>{try{if(await restoreLogin())await route();}catch(error){entryError(error);}})();
