/* Global account administration; personal password flow is shared with the account menu. */
(function(){
'use strict';
const ROLE_TEXT={viewer:'只读用户',admin:'管理员',superadmin:'超级管理员'};
const ROLE_HELP={viewer:'只读访问知识内容，不可修改数据。',admin:'维护知识内容，管理非超级管理员账号。',superadmin:'管理工作空间和全部账号，账号受系统保护。'};
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const el=(tag,cls,html)=>{const node=document.createElement(tag);node.className=cls||'';if(html!=null)node.innerHTML=html;return node;};
const currentUser=()=>window.Auth?.user()||null;
const isSuper=()=>currentUser()?.role==='superadmin';
async function api(path,options){const response=await window.fetch(path,options);let data;try{data=await response.json();}catch{data={};}return {ok:response.ok,status:response.status,data};}
const failure=(result,fallback)=>typeof result?.data?.detail==='string'?result.data.detail:fallback+'（HTTP '+result.status+'）';
let drawer=null,rowsHost=null,flashHost=null,users=[],loadSerial=0;
const flash=(message,tone='')=>{if(flashHost){flashHost.textContent=message;flashHost.className='ua-flash '+(tone?'is-'+tone:'');}};
function open(){
 if(!['admin','superadmin'].includes(currentUser()?.role)&&document.body.classList.contains('is-viewer'))return;
 window.KnowledgeTabs?.open('useradmin',{trigger:false});window.WorkspaceContext?.enter('useradmin');
 drawer=document.getElementById('tab-useradmin');if(!drawer)return;
 drawer.innerHTML=`<section class="ua-page">
 <header class="ua-head"><div><h2>人员管理</h2><p class="ua-sub">全局登录账号与系统角色</p></div><button type="button" id="ua-add">添加人员</button></header>
 <p class="ua-description">查看谁可以登录、拥有什么权限，以及账号是否启用。</p>
 <p class="ua-flash" data-ua-flash role="status" aria-live="polite"></p>
 <div class="ua-search"><label class="ua-search-field" for="ua-search">搜索人员<input id="ua-search" type="search" placeholder="展示名或登录账号"></label><label class="ua-state-field" for="ua-state">账号状态<select id="ua-state"><option value="">全部状态</option><option value="on">启用</option><option value="off">停用</option></select></label><button type="button" id="ua-refresh" class="secondary">刷新</button></div>
 <div class="ua-list-head"><h3>账号列表</h3><span id="ua-result-count" role="status" aria-live="polite">正在读取账号…</span></div>
 <div class="ua-table-wrap"><table class="ua-table"><thead><tr><th scope="col">人员 / 登录账号</th><th scope="col">系统角色</th><th scope="col">状态</th><th scope="col">操作</th></tr></thead><tbody data-ua-rows></tbody></table></div>
 <p class="ua-scope-note">系统角色适用于整个工作空间。项目成员权限尚未提供。</p></section>`;
 rowsHost=drawer.querySelector('[data-ua-rows]');flashHost=drawer.querySelector('[data-ua-flash]');
 drawer.querySelector('#ua-add').onclick=()=>edit();drawer.querySelector('#ua-refresh').onclick=load;
 drawer.querySelector('#ua-search').oninput=render;drawer.querySelector('#ua-state').onchange=render;load();
}
function close(){}
async function load(){const serial=++loadSerial;drawer.querySelector('#ua-result-count').textContent='正在读取账号…';rowsHost.innerHTML='<tr><td class="ua-empty" colspan="4">读取中…</td></tr>';try{const result=await api('/api/users');if(serial!==loadSerial)return;if(!result.ok)throw Error(failure(result,'无法读取人员清单'));users=result.data.items||[];if(flashHost.classList.contains('is-error'))flash('');render();}catch(error){if(serial===loadSerial){rowsHost.innerHTML='<tr><td class="ua-empty" colspan="4">暂时无法读取人员清单，请点击刷新重试。</td></tr>';drawer.querySelector('#ua-result-count').textContent='读取失败';flash(error.message,'error');}}}
function render(){
 const query=drawer.querySelector('#ua-search').value.trim().toLowerCase(),state=drawer.querySelector('#ua-state').value;
 const found=users.filter(item=>(!query||(item.username+' '+item.display_name).toLowerCase().includes(query))&&(!state||Boolean(item.is_active)===(state==='on')));
 drawer.querySelector('#ua-result-count').textContent=`显示 ${found.length} / ${users.length} 个账号`;
 rowsHost.innerHTML=found.length?found.map(item=>{const policy=window.WorkspaceContext.accountPolicy(currentUser(),item);return `<tr${item.id===currentUser()?.id?' class="is-self"':''}><td><strong>${esc(item.display_name||item.username)}</strong>${item.id===currentUser()?.id?'<span class="ua-self-tag">当前账号</span>':''}<small class="ua-login">${esc(item.username)}</small></td><td><span class="ua-role ${item.role==='viewer'?'is-viewer':'is-admin'}">${esc(ROLE_TEXT[item.role]||item.role)}</span><small class="ua-role-description">${esc(ROLE_HELP[item.role]||'系统分配的访问权限。')}</small></td><td><span class="ua-state ${item.is_active?'is-on':'is-off'}">${item.is_active?'启用':'已停用'}</span></td><td>${policy.edit?`<button type="button" class="secondary" data-edit-user="${esc(item.id)}">编辑</button>`:'<span class="ua-protected" title="超级管理员不可降级、停用或删除">受保护账号</span>'}</td></tr>`;}).join(''):'<tr><td class="ua-empty" colspan="4">没有符合条件的人员</td></tr>';
 rowsHost.querySelectorAll('[data-edit-user]').forEach(button=>button.onclick=()=>edit(users.find(item=>item.id===button.dataset.editUser)));
}
function edit(item=null){
 const policy=item?window.WorkspaceContext.accountPolicy(currentUser(),item):{role:true,status:false,password:true,delete:false};if(item&&!policy.edit)return;
 const overlay=el('div','ua-overlay ua-management-overlay');
 const roles=isSuper()?['viewer','admin','superadmin']:['viewer','admin'];
 overlay.innerHTML=`<section class="ua-card ua-edit-card" role="dialog" aria-modal="true" aria-label="${item?'编辑人员':'添加人员'}">
 <header class="ua-head"><h2>${item?'编辑人员':'添加人员'}</h2><button type="button" class="ua-close" data-close aria-label="关闭">×</button></header>
 <p class="ua-dialog-intro">登录账号用于访问整个工作空间，角色决定可使用的功能。</p><p class="ua-flash" data-edit-flash role="status"></p>
 <form class="ua-edit-form">
 <div class="ua-field"><label>登录账号<input name="username" autocomplete="off" aria-describedby="ua-login-help" ${item?'readonly':''}></label><small id="ua-login-help">${item?'登录账号创建后不可修改。':'登录时使用的账号，创建后不可修改。'}</small></div>
 <div class="ua-field"><label>展示名<input name="display_name" aria-describedby="ua-name-help"></label><small id="ua-name-help">列表中显示的姓名或昵称，可使用中文。</small></div>
 <div class="ua-field"><label>系统角色<select name="role" aria-describedby="ua-role-help" ${policy.role?'':'disabled'}>${roles.map(role=>`<option value="${role}">${ROLE_TEXT[role]}</option>`).join('')}</select></label><small id="ua-role-help" data-role-help></small></div>
 ${!item?'<div class="ua-field"><label>初始口令<input name="password" type="password" autocomplete="new-password" aria-describedby="ua-password-help"></label><small id="ua-password-help">至少6位，请通过安全方式告知使用者。</small></div>':''}
 <button type="submit">${item?'保存更改':'创建登录账号'}</button></form>
 ${item?`<section class="ua-edit-secondary"><h3>账号安全</h3>${policy.status?`<button type="button" data-toggle class="secondary">${item.is_active?'停用账号':'启用账号'}</button>`:''}${policy.password?'<details><summary>重置口令</summary><form class="ua-reset-form"><label>新口令<input name="password" type="password" autocomplete="new-password"></label><label>确认新口令<input name="again" type="password" autocomplete="new-password"></label><button type="submit" class="secondary">确认重置口令</button></form></details>':'<p class="subtle">自己的口令请使用个人账号菜单中的「改口令」。</p>'}${policy.delete?'<div class="ua-delete-section"><p>删除会永久移除账号，且无法恢复。</p><button type="button" data-delete class="danger">删除账号</button></div>':''}${item.role==='superadmin'?'<p class="subtle">超级管理员不可降级、停用或删除。</p>':''}</section>`:''}<div data-confirm class="ua-confirm" hidden></div></section>`;
 document.body.append(overlay);const form=overlay.querySelector('.ua-edit-form');
 const field=name=>form.elements.namedItem(name);field('username').value=item?.username||'';field('display_name').value=item?.display_name||'';field('role').value=item?.role||'viewer';
 const explainRole=()=>{overlay.querySelector('[data-role-help]').textContent=ROLE_HELP[field('role').value]||'系统分配的访问权限。';};
 field('role').addEventListener('change',explainRole);explainRole();
 const previous=document.activeElement;
 const dismiss=()=>{overlay.remove();document.removeEventListener('keydown',onKey);previous?.focus();};
 const onKey=event=>{if(event.key==='Escape')dismiss();};document.addEventListener('keydown',onKey);overlay.querySelector('[data-close]').onclick=dismiss;overlay.onclick=event=>{if(event.target===overlay)dismiss();};
 const localFlash=message=>{overlay.querySelector('[data-edit-flash]').textContent=message;};
 async function submit(path,method,payload,message){
  const buttons=[...overlay.querySelectorAll('button')];buttons.forEach(button=>button.disabled=true);localFlash('提交中…');
  try{const result=await api(path,{method,headers:{'Content-Type':'application/json'},...(payload?{body:JSON.stringify(payload)}:{})});if(!result.ok)throw Error(failure(result,'操作失败'));flash(message,'good');dismiss();await load();}
  catch(error){localFlash(error.message);buttons.forEach(button=>button.disabled=false);}
 }
 function confirm(message,action){const host=overlay.querySelector('[data-confirm]');host.hidden=false;host.innerHTML='<p></p><button data-yes class="danger">确认</button><button data-no class="secondary">取消</button>';host.querySelector('p').textContent=message;host.querySelector('[data-yes]').onclick=action;host.querySelector('[data-no]').onclick=()=>host.hidden=true;}
 const url=item?'/api/users/'+encodeURIComponent(item.id):'/api/users';
 form.onsubmit=event=>{
  event.preventDefault();const payload={display_name:field('display_name').value.trim()};
  if(policy.role)payload.role=field('role').value;
  if(!item){payload.username=field('username').value.trim();payload.password=field('password').value;if(!payload.username){localFlash('请填写登录账号');return;}if(payload.password.length<6){localFlash('口令至少6位');return;}}
  const action=()=>submit(url,item?'PATCH':'POST',payload,item?'人员信息已保存':'登录账号已创建');
  if(item&&payload.role&&payload.role!==item.role)confirm('角色更改会立即影响已登录会话，确认保存？',action);else action();
 };
 overlay.querySelector('[data-toggle]')?.addEventListener('click',()=>confirm(item.is_active?'停用后该账号无法登录，现有会话立即失效。':'确认重新启用此账号？',()=>submit(url,'PATCH',{is_active:!item.is_active},'账号状态已更新')));
 overlay.querySelector('[data-delete]')?.addEventListener('click',()=>confirm('删除会永久移除账号并释放登录名，无法恢复。确认删除「'+item.username+'」？',()=>submit(url,'DELETE',null,'账号已删除')));
 const reset=overlay.querySelector('.ua-reset-form');if(reset)reset.onsubmit=event=>{event.preventDefault();const password=reset.elements.namedItem('password').value,again=reset.elements.namedItem('again').value;if(password.length<6){localFlash('口令至少6位');return;}if(password!==again){localFlash('两次输入的口令不一致');return;}submit(url,'PATCH',{password},'口令已重置，旧会话已失效');};
 field(item?'display_name':'username').focus();
}
    // ── 修改我的口令 ──────────────────────────────────────────────────────────
    var pwDialog = null;

    function buildPasswordDialog() {
        pwDialog = el('div', 'ua-overlay');
        pwDialog.innerHTML =
            '<section class="ua-card ua-card-narrow" role="dialog" aria-modal="true" aria-labelledby="ua-pw-title">' +
            '  <header class="ua-head"><div>' +
            '    <h2 id="ua-pw-title">修改我的口令</h2>' +
            '    <p class="ua-sub">只改<b>自己</b>的口令。改完：这台设备会换上新令牌继续用，' +
            '      <b>其他设备上的登录立刻失效</b>（口令是"当前登录凭据"的一部分，这是故意的）。</p>' +
            '  </div><button type="button" class="ua-close" data-ua-pw-close aria-label="关闭">×</button></header>' +
            '  <p class="ua-flash" data-ua-pw-flash></p>' +
            '  <div class="ua-form ua-form-column">' +
            '    <label>当前口令<input data-ua-pw-current type="password" autocomplete="current-password"></label>' +
            '    <label>新口令（至少 6 位）<input data-ua-pw-new type="password" autocomplete="new-password"></label>' +
            '    <label>再输一次新口令<input data-ua-pw-again type="password" autocomplete="new-password"></label>' +
            '    <div class="ua-pw-actions">' +
            '      <button type="button" class="ua-primary" data-ua-pw-save>保存新口令</button>' +
            '      <button type="button" class="ua-action" data-ua-pw-close>取消</button>' +
            '    </div>' +
            '  </div>' +
            '</section>';
        document.body.appendChild(pwDialog);
        pwDialog.querySelectorAll('[data-ua-pw-close]').forEach(function (node) {
            node.addEventListener('click', function () { pwDialog.hidden = true; });
        });
        pwDialog.addEventListener('click', function (event) {
            if (event.target === pwDialog) pwDialog.hidden = true;
        });
        pwDialog.querySelector('[data-ua-pw-save]').addEventListener('click', savePassword);
        pwDialog.querySelector('[data-ua-pw-again]').addEventListener('keydown', function (event) {
            if (event.key === 'Enter') savePassword();
        });
    }

    function pwFlash(text, tone) {
        var host = pwDialog.querySelector('[data-ua-pw-flash]');
        host.textContent = text || '';
        host.className = 'ua-flash' + (tone ? ' is-' + tone : '');
    }

    function openPasswordChange() {
        if (!pwDialog) buildPasswordDialog();
        pwDialog.hidden = false;
        pwFlash('');
        ['[data-ua-pw-current]', '[data-ua-pw-new]', '[data-ua-pw-again]'].forEach(function (selector) {
            pwDialog.querySelector(selector).value = '';
        });
        pwDialog.querySelector('[data-ua-pw-current]').focus();
    }

    function savePassword() {
        var me = currentUser() || {};
        var currentPassword = pwDialog.querySelector('[data-ua-pw-current]').value;
        var next = pwDialog.querySelector('[data-ua-pw-new]').value;
        var again = pwDialog.querySelector('[data-ua-pw-again]').value;
        if (!currentPassword) { pwFlash('请填写当前口令', 'error'); return; }
        if (next.length < 6) { pwFlash('新口令至少 6 位', 'error'); return; }
        if (next !== again) { pwFlash('两次输入的新口令不一致', 'error'); return; }
        var button = pwDialog.querySelector('[data-ua-pw-save]');
        button.disabled = true;
        pwFlash('提交中…');
        api('/api/auth/password', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ current_password: currentPassword, new_password: next })
        }).then(function (result) {
            if (!result.ok) { pwFlash(failure(result, '修改没成功'), 'error'); return null; }
            // 旧令牌因口令指纹变化失效：用新口令换一枚新令牌，避免"改完就被踢下线"。
            return api('/api/auth/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username: me.username, password: next })
            }).then(function (login) {
                if (login.ok && window.Auth && window.Auth.adoptSession) {
                    window.Auth.adoptSession(login.data);
                    pwFlash('口令已更新：其他设备上的登录已失效。', 'good');
                } else {
                    pwFlash('口令已更新，请重新登录。', 'good');
                    if (window.Auth && window.Auth.logout) window.Auth.logout();
                }
            });
        }).catch(function () { pwFlash('网络异常，请稍后重试', 'error'); })
          .finally(function () { button.disabled = false; });
    }

    window.UserAdmin = {
        open: open,
        close: close,
        openPasswordChange: openPasswordChange
    };
})();
