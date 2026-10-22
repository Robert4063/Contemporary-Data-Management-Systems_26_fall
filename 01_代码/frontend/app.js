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
  app.append(box('row spread',box('',node('h1','我的问卷'),node('p','创建、发布问卷，并查看实时统计。')),action('创建问卷',()=>editor())));
  if(!data.items.length) app.append(box('card',node('p','暂无问卷，点击“创建问卷”开始。')));
  for(const s of data.items) {
    const controls=box('row',action(s.status==='draft'?'编辑问卷':'查看设置',()=>editor(s._id),'secondary'),action('查看统计',()=>statistics(s._id),'secondary'));
    if(s.status==='draft') controls.append(action('发布',async()=>{if(!confirmPublication(s.title))return;const r=await api(`/surveys/${s._id}/publish`,'POST');await dashboard();tell('发布成功：'+location.origin+r.share_url,true);}));
    if(s.status==='published') controls.append(action('关闭问卷',async()=>{await api(`/surveys/${s._id}/close`,'POST');await dashboard();},'danger'));
    const card=box('card',box('row spread',node('h2',s.title),node('span',statuses[s.status],{class:'badge'})),node('p',s.description),node('p',s.deadline?'截止时间：'+new Date(s.deadline).toLocaleString():'未完成：必须设置截止时间'),controls);
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
  function render() {
    clear();app.append(box('row spread',node('h1','问卷设置'),action('返回列表',dashboard,'secondary')));
    app.append(node('p','创建时间：'+new Date(s.created_at).toLocaleString()+'（服务器记录，不能修改）',{class:'muted'}));
    if(!editable) {
      app.append(box('card',node('h2',s.title),node('p','发布后题目与填写策略冻结。可修改问卷标题、说明和截止时间。')));
      app.append(box('card',field('问卷标题',input(s.title,v=>s.title=v,'text',{maxlength:'200'})),field('问卷说明',area(s.description,v=>s.description=v)),action('保存标题和说明',async()=>{s=await api(`/surveys/${id}/metadata`,'PATCH',{title:s.title,description:s.description,revision:s.revision});render();tell('标题和说明已更新，题目与规则保持不变',true);})));
      if(s.status==='closed'){app.append(node('p','问卷已关闭，不能修改截止时间。'));return;}
      const d=input(localTime(s.deadline),()=>{},'datetime-local',{min:localTime(s.created_at),required:''});
      app.append(box('card',field('截止时间（必填，本地时间）',d),action('保存截止时间',async()=>{if(!d.value)throw new Error('截止时间必须填写');await api(`/surveys/${id}/deadline`,'PATCH',{deadline:d.value?new Date(d.value).toISOString():null});await dashboard();tell('截止时间已更新',true);})));
      return;
    }
    app.append(box('card',field('问卷标题',input(s.title,v=>s.title=v,'text',{maxlength:'200'})),field('问卷说明',area(s.description,v=>s.description=v)),
      check('允许匿名填写（仍须登录）',s.allow_anonymous,v=>s.allow_anonymous=v),check('允许同一账号多次提交',s.allow_multiple,v=>s.allow_multiple=v),
      field('截止时间（必填，本地时间）',input(localTime(s.deadline),v=>{const date=v?new Date(v):null;if(date&&Number.isNaN(date.getTime())){tell('截止时间格式不正确');return;}s.deadline=date?date.toISOString():null;},'datetime-local',{min:localTime(s.created_at),required:''}))));
    s.questions.forEach((q,index)=>app.append(questionEditor(q,index)));
    app.append(box('row actions',action('添加题目',()=>{s.questions.push({id:'q'+questionCounter++,title:'',type:'single',required:true,options:[{id:'o1',label:'选项一'},{id:'o2',label:'选项二'}],validation:{},jump_enabled:false,rules:[]});render();},'secondary'),
      action('保存草稿',async()=>{if(!s.deadline)throw new Error('截止时间必须填写');s=await api('/surveys/'+id,'PUT',editorPayload());render();tell('草稿已保存。返回列表后可发布。',true);},'secondary'),
      action('直接发布',async()=>{
        if(!confirmPublication(s.title))return;
        if(!s.deadline)throw new Error('截止时间必须填写');
        const result=await api(`/surveys/${id}/publish-direct`,'POST',editorPayload());
        await dashboard();tell('发布成功：'+location.origin+result.share_url,true);
      })));
  }
  function questionEditor(q,index) {
    const card=box('card question',box('row spread',node('h2',`第 ${index+1} 题 · ${q.id}`),action('删除题目',()=>{if(s.questions.some(x=>x.rules.some(r=>r.target===q.id)))throw new Error('此题被跳转规则引用，请先修改相关规则');s.questions.splice(index,1);render();},'danger')));
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
