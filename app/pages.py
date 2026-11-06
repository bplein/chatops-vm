"""HTML pages as Python constants — no template engine dependency.

Placeholders __HOST__/__USER__/__ERROR__ are literal strings swapped with str.replace
(never .format — the CSS braces make .format unusable).
"""

# Design tokens. Dark default; light via :root:not([data-theme="dark"])@media dark override.
# Manual choice persists in localStorage and wins over the OS setting.
_BASE_CSS = """
:root{
  color-scheme:dark;
  --bg:#101216;--surface:#171A21;--line:#262C38;--field:#0D1015;
  --text:#E7EAF0;--muted:#8A93A6;--u-log:#A5CBFF;--accent:#5B9DFF;
  --accent-ink:#0C1322;--danger:#F08080;--float:rgba(16,18,22,.72);--line-soft:rgba(255,255,255,.06);
}
:root[data-theme="light"]{
  color-scheme:light;
  --bg:#F4F5F8;--surface:#FFFFFF;--line:#E2E5EC;--field:#FFFFFF;
  --text:#1B1F2A;--muted:#67707F;--u-log:#2458C9;--accent:#2F6FED;
  --accent-ink:#FFFFFF;--danger:#C93636;--float:rgba(244,245,248,.78);--line-soft:rgba(20,24,40,.08);
}
@media (prefers-color-scheme:light){:root:not([data-theme]){
  color-scheme:light;
  --bg:#F4F5F8;--surface:#FFFFFF;--line:#E2E5EC;--field:#FFFFFF;
  --text:#1B1F2A;--muted:#67707F;--u-log:#2458C9;--accent:#2F6FED;
  --accent-ink:#FFFFFF;--danger:#C93636;--float:rgba(244,245,248,.78);--line-soft:rgba(20,24,40,.08);
}}
*,*::before,*::after{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
  font:400 15px/1.55 -apple-system,BlinkMacSystemFont,"SF Pro Text",system-ui,sans-serif;
  -webkit-font-smoothing:antialiased}
h1{font-weight:600;letter-spacing:-0.02em;line-height:1.1;margin:0}
.muted{color:var(--muted)}
input[type=text],input[type=password],textarea{
  border-radius:9px;border:1px solid var(--line);background:var(--field);color:var(--text);
  font:inherit;outline:none;transition:border-color 120ms ease}
input:focus-visible,textarea:focus-visible{border-color:var(--accent);outline:none}
button{border:0;border-radius:9px;background:var(--accent);color:var(--accent-ink);
  font:600 .9375rem/1 inherit;cursor:pointer}
button:not(:disabled):active{transform:scale(.97);transition:transform 100ms ease-out}
a{color:var(--danger);text-decoration:none}a:hover{text-decoration:underline}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.theme{position:fixed;top:14px;right:16px;background:var(--surface);border:1px solid var(--line);
  color:var(--muted);border-radius:8px;padding:6px 10px;font:400 .8125rem/1 inherit}
.vfoot{position:fixed;bottom:10px;left:16px;color:var(--muted);font-size:.75rem;opacity:.65;pointer-events:none}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
"""

_THEME_JS = """
(function(){
  const KEY='aiops-theme';
  const cur=localStorage.getItem(KEY);
  if(cur)document.documentElement.dataset.theme=cur;
  window.toggleTheme=function(){
    const now=document.documentElement.dataset.theme ||
      (matchMedia('(prefers-color-scheme: light)').matches?'light':'dark');
    const next=now==='light'?'dark':'light';
    document.documentElement.dataset.theme=next;
    try{localStorage.setItem(KEY,next);}catch(e){}
    const b=document.querySelector('.theme');if(b)b.textContent=next==='light'?'Dark':'Light';
  };
})();
"""

LOGIN_PAGE = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ChatOps VM — Sign in</title>
<style>{_BASE_CSS}
body{{min-height:100vh;display:grid;place-items:center;padding:24px}}
.card{{width:min(380px,100%);background:var(--surface);border:1px solid var(--line);
  border-radius:14px;padding:28px;display:flex;flex-direction:column;gap:4px}}
h1{{font-size:1.25rem}}
.sub{{color:var(--muted);font-size:.8125rem;margin:6px 0 14px}}
.card label{{display:block;font-size:.75rem;color:var(--muted);margin:12px 0 4px}}
.card input[type=text],.card input[type=password]{{width:100%;padding:9px 11px;font-size:.9375rem}}
.card button{{margin-top:18px;width:100%;padding:10px 12px}}
.row{{display:flex;gap:8px;align-items:center;margin-top:14px}}
.row input{{width:auto;margin:0}}
.row label{{margin:0;font-size:.8125rem;color:var(--text)}}
.err{{color:var(--danger);font-size:.8125rem;margin:14px 0 0}}</style></head>
<body>
<button class="theme" onclick="toggleTheme()">Theme</button>
<form class="card" method="post" action="/login">
  <h1>ChatOps VM</h1>
  <p class="sub">Chat with your vCenter. Credentials are verified once against the vCenter and held in server memory for this session only — never written to disk, never shared between users.</p>
  <label for="host">vCenter host</label>
  <input id="host" name="host" type="text" placeholder="vcenter01.example.com" autocomplete="off" value="__HOSTVALUE__" required>
  <label for="username">Username</label>
  <input id="username" name="username" type="text" placeholder="you@vsphere.local" autocomplete="username" value="__USERVALUE__" required>
  <label for="password">Password</label>
  <input id="password" name="password" type="password" autocomplete="current-password" required>
  <div class="row">
    <input type="checkbox" id="verify_ssl" name="verify_ssl">
    <label for="verify_ssl">Verify TLS certificate (off for self-signed labs)</label>
  </div>
  <button type="submit">Sign in</button>
  __ERROR__
</form>
<script>{_THEME_JS}</script>
<div class="vfoot">vmware-aiops __AIOPS_VERSION__</div>
</body></html>"""

CHAT_PAGE = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ChatOps VM</title>
<style>{_BASE_CSS}
#w{{max-width:72ch;margin:0 auto;padding:20px 16px 140px}}
header{{display:flex;align-items:baseline;gap:10px;padding:6px 2px 12px;border-bottom:1px solid var(--line);margin-bottom:18px}}
header b{{font-size:1.0625rem;letter-spacing:-0.015em}}
.who{{color:var(--muted);font-size:.8125rem;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.out{{font-size:.8125rem}}
#log{{display:flex;flex-direction:column;gap:20px}}
.msg .tag{{font-size:.75rem;color:var(--muted);display:block;margin-bottom:3px}}
.msg p{{margin:0;white-space:pre-wrap}}
.msg.user p{{color:var(--u-log)}}
.msg .thinking{{display:inline-block;color:var(--muted);font-size:.8125rem}}
.msg .thinking::after{{content:"";animation:el 1.2s steps(3,end) infinite}}
@keyframes el{{0%{{content:""}}25%{{content:"."}}50%{{content:".."}}75%{{content:"..."}}}}
.note{{color:var(--muted);font-size:.8125rem;font-style:italic}}
.msg.error p{{color:var(--danger)}}
#dock{{position:fixed;inset:auto 0 0 0;background:var(--float);
  -webkit-backdrop-filter:blur(20px) saturate(160%);backdrop-filter:blur(20px) saturate(160%);
  border-top:1px solid var(--line-soft)}}
#dock form{{max-width:72ch;margin:0 auto;padding:12px 16px;display:flex;gap:8px;align-items:flex-end}}
#q{{flex:1;resize:none;max-height:140px;min-height:42px;padding:10px 12px}}
#send{{padding:11px 16px;border-radius:10px}}
#send:disabled{{background:var(--line);color:var(--muted);cursor:default}}
@media (prefers-reduced-transparency:reduce){{#dock{{background:var(--bg);backdrop-filter:none}}}}</style></head>
<body>
<button class="theme" onclick="toggleTheme()">Theme</button>
<div id="w">
  <header>
    <b>ChatOps VM</b>
    <span class="who">__USER__ @ __HOST__</span>
    <a class="out" href="/logout">Sign out</a>
  </header>
  <div id="log"></div>
</div>
<div id="dock"><form id="f" onsubmit="event.preventDefault();ask();">
  <textarea id="q" rows="1" placeholder="Ask about your vSphere environment…" autofocus></textarea>
  <button id="send" type="submit">Send</button>
</form></div>
<script>
{_THEME_JS}
const log=document.getElementById('log'),q=document.getElementById('q'),sendBtn=document.getElementById('send');
let pinned=true;
window.addEventListener('scroll',()=>{{
  pinned=(document.documentElement.scrollHeight-(window.scrollY+window.innerHeight))<24;
}},{{passive:true}});
function follow(){{if(pinned)window.scrollTo(0,document.documentElement.scrollHeight);}}
q.addEventListener('keydown',e=>{{if(e.key==='Enter'&&!e.shiftKey){{e.preventDefault();ask();}}}});
q.addEventListener('input',()=>{{q.style.height='auto';q.style.height=Math.min(q.scrollHeight,140)+'px';}});
function add(cls,tag){{
  const m=document.createElement('div');m.className='msg '+cls;
  if(tag){{const t=document.createElement('span');t.className='tag';t.textContent=tag;m.appendChild(t);}}
  const p=document.createElement('p');m.appendChild(p);log.appendChild(m);follow();return m;
}}
async function ask(){{
  const text=q.value.trim();if(!text)return;
  q.value='';q.style.height='auto';sendBtn.disabled=true;
  add('user','You').querySelector('p').textContent=text;
  const box=add('assistant','ChatOps VM');const p=box.querySelector('p');
  p.innerHTML='<span class="thinking"></span>';
  try{{
    const r=await fetch('/api/chat',{{method:'POST',
      headers:{{'content-type':'application/json'}},body:JSON.stringify({{message:text}})}});
    const d=await r.json();
    if(r.status===401){{p.textContent='Session expired — sign in again.';return;}}
    if(!r.ok||d.error){{box.classList.add('error');p.textContent=d.error||('HTTP '+r.status);return;}}
    p.textContent=d.text||'(no output)';
    follow();
  }}catch(e){{box.classList.add('error');p.textContent='Connection lost mid-answer. Try again.';}}
  finally{{sendBtn.disabled=false;q.focus();}}
}}
</script>
<div class="vfoot">vmware-aiops __AIOPS_VERSION__</div>
</body></html>"""
