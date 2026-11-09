"""Platform choice (after vCenter login) and CLI setup pages.

Placeholders are literal strings swapped with str.replace by the caller
(never .format — the CSS braces make .format unusable), same convention
as pages.py:

  PLATFORM_PAGE  __HOST__  __USER__
  AUTH_PAGE      __PLATFORM__ (display name)
                 __INSTRUCTIONS__ (server-provided HTML, escaped before
                 insertion by the caller)
"""

from pages import _BASE_CSS, _THEME_JS

PLATFORM_PAGE = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ChatOps VM — Choose your AI client</title>
<style>{_BASE_CSS}
#w{{max-width:64ch;margin:0 auto;padding:0 16px}}
header{{display:flex;align-items:baseline;gap:10px;padding:6px 2px 14px;
  border-bottom:1px solid var(--line);margin-bottom:22px}}
header b{{font-size:1.0625rem;letter-spacing:-0.015em}}
.who{{color:var(--muted);font-size:.8125rem;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
h1{{font-size:1.25rem;margin-bottom:4px}}
.sub{{color:var(--muted);font-size:.8125rem;margin:0 0 18px}}
.opts{{display:flex;flex-direction:column;gap:12px}}
.card{{background:var(--surface);border:1px solid var(--line);border-radius:14px;
  padding:18px 20px;display:flex;align-items:center;gap:16px}}
.card .info{{flex:1;min-width:0}}
.card h2{{font-size:1rem;font-weight:600;margin:0}}
.card .tagline{{color:var(--muted);font-size:.8125rem;margin:3px 0 0}}
.card .status{{display:block;color:var(--muted);font-size:.75rem;margin-top:8px}}
.card button{{padding:10px 14px;white-space:nowrap}}
.foot{{color:var(--muted);font-size:.8125rem;margin:22px 2px 0}}</style></head>
<body>
<div id="frame">
<div class="topbar"><button class="theme" onclick="toggleTheme()">Theme</button></div>
<div id="w">
  <header>
    <b>ChatOps VM</b>
    <span class="who">__USER__ @ __HOST__</span>
  </header>
  <h1>Choose your AI client</h1>
  <p class="sub">Pick which command-line client this session should run. The client starts after you choose; none is running yet.</p>
  <div class="opts" role="group" aria-label="AI client choices">
    <form class="card" method="post" action="/session/start">
      <input type="hidden" name="platform" value="claude">
      <div class="info">
        <h2>Claude Code</h2>
        <p class="tagline">Command-line AI assistant from Anthropic; MCP-native.</p>
        <span class="status">not connected yet</span>
      </div>
      <button type="submit">Start with Claude Code</button>
    </form>
    <form class="card" method="post" action="/session/start">
      <input type="hidden" name="platform" value="gemini">
      <div class="info">
        <h2>Gemini CLI</h2>
        <p class="tagline">Google&rsquo;s command-line assistant; reads a GEMINI.md context file and uses MCP.</p>
        <span class="status">not connected yet</span>
      </div>
      <button type="submit">Start with Gemini CLI</button>
    </form>
    <form class="card" method="post" action="/session/start">
      <input type="hidden" name="platform" value="codex">
      <div class="info">
        <h2>OpenAI Codex CLI</h2>
        <p class="tagline">OpenAI&rsquo;s command-line assistant for ChatGPT; uses skills and an AGENTS.md file.</p>
        <span class="status">not connected yet</span>
      </div>
      <button type="submit">Start with Codex CLI</button>
    </form>
  </div>
  <p class="foot">Each choice starts an isolated pod with the client installed; your session ends on sign-out or after 4 idle hours.</p>
</div>
<script>{_THEME_JS}</script>
<div class="vfoot">vmware-aiops __AIOPS_VERSION__</div>
</div>
</body></html>"""

AUTH_PAGE = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ChatOps VM — Finish setup</title>
<style>{_BASE_CSS}
#w{{max-width:64ch;margin:0 auto;padding:0 16px}}
h1{{font-size:1.25rem;margin-bottom:14px}}
#status{{display:flex;align-items:center;gap:8px;color:var(--muted);
  font-size:.8125rem;margin:0 0 18px}}
.spin{{width:14px;height:14px;border:2px solid var(--line);border-top-color:var(--accent);
  border-radius:50%;display:inline-block;animation:spin 900ms linear infinite}}
@keyframes spin{{to{{transform:rotate(1turn)}}}}
@media (prefers-reduced-motion:reduce){{.spin{{display:none}}}}
section{{border-left:3px solid var(--line);padding:2px 0 2px 16px;
  color:var(--text);font-size:.9375rem}}
section :first-child{{margin-top:0}}
section :last-child{{margin-bottom:0}}
section code{{font:500 .875rem/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;
  background:var(--field);border:1px solid var(--line);border-radius:5px;padding:1px 5px}}
section pre{{background:var(--field);border:1px solid var(--line);border-radius:9px;
  padding:10px 12px;overflow-x:auto}}
section pre code{{border:0;background:none;padding:0}}
section label{{display:block;font-size:.75rem;color:var(--muted);margin:16px 0 6px}}
section input[type=password],section input[type=text]{{width:100%;padding:11px 13px;
  font-size:.9375rem}}
section input[type=password]{{letter-spacing:.02em}}
section button{{margin-top:14px;padding:10px 16px}}
.cancel{{display:inline-block;margin-top:22px;font-size:.8125rem}}</style></head>
<body>
<div id="frame">
<div class="topbar"><button class="theme" onclick="toggleTheme()">Theme</button></div>
<div id="w">
  <h1>Finish setup: __PLATFORM__</h1>
  __ERROR__
  <p id="status" aria-live="polite"><span class="spin" aria-hidden="true"></span><span id="status-text">Starting&hellip;</span></p>
  <section aria-label="Authentication steps">__INSTRUCTIONS__</section>
  <a class="cancel" href="/logout">Cancel setup (sign out)</a>
</div>
<script>
{_THEME_JS}
(function(){{
  var done=false;
  var text=document.getElementById('status-text');
  function poll(){{
    if(done)return;
    fetch('/session/status').then(function(r){{
      if(!r.ok)return null;
      return r.json();
    }}).then(function(d){{
      if(done||!d)return;
      if(d.ready){{
        done=true;
        text.textContent='Ready — opening chat…';
        window.location.replace('/chat');
      }}
    }}).catch(function(){{}});
  }}
  setInterval(poll,2500);
}})();
</script>
<div class="vfoot">vmware-aiops __AIOPS_VERSION__</div>
</div>
</body></html>"""
