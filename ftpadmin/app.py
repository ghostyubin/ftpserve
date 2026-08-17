#!/usr/bin/env python3
"""ftpadmin - vsftpd 网页管理后台
- 登录后管理 FTP 用户 (增/删/改)，改动写入 users.json（与 ftpserve 共享的配置卷）
- 每次改动后自动重启 ftpserve 容器使配置生效
- 首页展示 ftpserve 服务详细状态（地址/进程/数据盘/目录/登录探测），移动端友好
"""
import os, json, secrets
from flask import (Flask, request, redirect, url_for, session,
                   render_template_string, jsonify)

APP = Flask(__name__)
APP.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(16))
ADMIN_PASS = os.environ.get("ADMIN_PASS", "admin")
FTPSERVE = os.environ.get("FTPSERVE_CONTAINER", "ftpserve")
USERS_FILE = os.environ.get("USERS_FILE", "/data/users.json")
FTP_HOST = os.environ.get("FTP_PROBE_HOST", FTPSERVE)  # 同 compose 网络内用容器名
FTP_CTRL_PORT = int(os.environ.get("FTP_PROBE_PORT", "21"))

# ---------------- 数据读写 ----------------
def load_users():
    if not os.path.exists(USERS_FILE):
        return []
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []

def save_users(users):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2, ensure_ascii=False)

def restart_ftpserve():
    import docker
    c = docker.DockerClient(base_url="unix:///var/run/docker.sock")
    c.containers.get(FTPSERVE).restart()
    return True

def restart_ftpserve_async():
    """后台线程重启，避免阻塞 HTTP 请求"""
    import threading
    def _run():
        try:
            restart_ftpserve()
        except Exception:
            pass
    t = threading.Thread(target=_run, daemon=True)
    t.start()

def commit_users(users, label):
    """保存并重启：XHR 请求走异步重启+返回 JSON（前端显示进度），
    普通表单提交走同步重启+flash 重定向（兼容无 JS）。"""
    save_users(users)
    is_xhr = request.headers.get("X-Requested-With") == "fetch"
    if is_xhr:
        t0 = ""
        try:
            t0 = get_status().get("startedAt", "")
        except Exception:
            pass
        restart_ftpserve_async()
        return jsonify(ok=True, startedAt=t0, msg=label)
    try:
        restart_ftpserve()
        flash(f"{label}，FTP 服务已重启")
    except Exception as e:
        flash(f"{label}，但重启失败：{e}")
    return redirect(url_for("index"))

# ---------------- 服务状态采集 ----------------
def docker_client():
    try:
        import docker
        return docker.DockerClient(base_url="unix:///var/run/docker.sock")
    except Exception:
        return None

def ftp_probe(user, pwd, host=FTP_HOST, port=FTP_CTRL_PORT):
    """仅走控制通道：连接+登录+退出，验证凭据与 21 端口可达（不触发 PASV 数据通道）"""
    import ftplib
    try:
        f = ftplib.FTP(timeout=8)
        f.connect(host, port)
        f.login(user, pwd)
        f.quit()
        return True, ""
    except Exception as e:
        return False, str(e)

def parse_uptime(started_at):
    try:
        from datetime import datetime, timezone
        t = datetime.strptime(started_at[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        d = datetime.now(timezone.utc) - t
        s = int(d.total_seconds())
        h, rem = divmod(s, 3600); m, _ = divmod(rem, 60)
        if h >= 24:
            days, h = divmod(h, 24)
            return f"{days}天{h}小时{m}分"
        return f"{h}小时{m}分"
    except Exception:
        return started_at or "未知"

def human(n):
    try:
        n = int(n)
    except Exception:
        return str(n)
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024.0
        i += 1
    return f"{n:.0f}{units[i]}" if units[i] == "B" else f"{n:.1f}{units[i]}"

def get_status():
    st = {"ok": False, "err": ""}
    try:
        c = docker_client()
        if c is None:
            st["err"] = "无法连接 Docker（docker.sock 未挂载？）"
            return st
        cont = c.containers.get(FTPSERVE)
        attrs = cont.attrs
        state = attrs.get("State", {})
        st["ok"] = True
        st["status"] = state.get("Status", "?")
        st["running"] = bool(state.get("Running", False))
        st["restarting"] = bool(state.get("Restarting", False))
        st["uptime"] = parse_uptime(state.get("StartedAt", ""))
        st["startedAt"] = state.get("StartedAt", "")
        st["id"] = cont.short_id
        st["image"] = (cont.image.tags[0] if cont.image.tags else attrs.get("Image", "")[:20])
        st["created"] = (attrs.get("Created", "") or "")[:19].replace("T", " ")
        st["restartCount"] = attrs.get("RestartCount", 0)
        # vsftpd 进程
        r = cont.exec_run("pgrep -a vsftpd")
        proc = r.output.decode(errors="replace").strip()
        st["vsftpd_running"] = bool(proc)
        # vsftpd.conf
        r = cont.exec_run("cat /etc/ftpserve/vsftpd.conf 2>/dev/null")
        conf = r.output.decode(errors="replace")
        keys = {}
        for line in conf.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            keys[k.strip()] = v.strip()
        st["conf_keys"] = keys
        # FTP 连接地址/端口（客户端实际连接用）
        st["ftp_addr"] = keys.get("pasv_address") or "?"
        st["ftp_port"] = keys.get("listen_port") or "21"
        # 数据盘用量
        r = cont.exec_run("sh -c 'df -h /ftp'")
        for l in r.output.decode(errors="replace").splitlines():
            if "/ftp" in l:
                p = l.split()
                st["disk"] = {
                    "total": p[-5], "used": p[-4], "avail": p[-3],
                    "pct": int(p[-2].rstrip("%") or 0),
                }
        # 各用户目录占用（字节 -> 进度条）
        r = cont.exec_run("sh -c 'for d in /ftp/*; do [ -d \"$d\" ] && du -sb \"$d\"; done'")
        raw = []
        for l in r.output.decode(errors="replace").splitlines():
            if "\t" in l:
                b, p = l.split("\t", 1)
                try: raw.append((os.path.basename(p.rstrip("/")), int(b)))
                except Exception: pass
        maxb = max((b for _, b in raw), default=1) or 1
        st["dirs_bars"] = [(name, human(b), round(b / maxb * 100)) for name, b in raw]
        st["dirs_sizes"] = [(name, human(b)) for name, b in raw]
        # 实时登录探测（仅 users.json 中的用户）
        st["probes"] = [{"name": u["name"], **dict(zip(["ok", "err"], ftp_probe(u["name"], u["pass"])))}
                        for u in load_users()]
    except Exception as e:
        st["err"] = str(e)
    return st

# ---------------- 模板 ----------------
HEAD = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>FTP 服务管理</title>
<style>
  :root{--bg:#f4f6fb;--card:#fff;--ink:#1f2933;--muted:#6b7280;--pri:#2563eb;--pri2:#1d4ed8;--ok:#16a34a;--warn:#d97706;--del:#dc2626;--bd:#e6e9f0;}
  *{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
  body{margin:0;font-family:-apple-system,Segoe UI,Roboto,'PingFang SC','Microsoft YaHei',sans-serif;background:var(--bg);color:var(--ink);-webkit-text-size-adjust:100%}
  .wrap{max-width:880px;margin:14px auto;padding:0 14px}
  .card{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:10px 12px;margin-bottom:10px;box-shadow:0 1px 2px rgba(0,0,0,.04)}
  .card.top-card{padding:9px 14px;margin-bottom:10px}
  h1{font-size:18px;margin:0}
  h2{font-size:12.5px;margin:12px 0 6px;color:var(--muted);font-weight:600}
  h2:first-of-type{margin-top:0}
  .top{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px}
  table{width:100%;border-collapse:collapse;font-size:14px}
  th,td{text-align:left;padding:10px 8px;border-bottom:1px solid var(--bd);vertical-align:middle}
  th{color:var(--muted);font-weight:600;font-size:12.5px}
  input{font-size:16px;padding:9px 10px;border:1px solid var(--bd);border-radius:9px;width:100%;background:#fff}
  .row{display:flex;gap:8px;flex-wrap:wrap}
  .row>div{flex:1;min-width:120px}
  button{cursor:pointer;border:none;border-radius:9px;padding:10px 16px;font-size:15px;font-weight:600;color:#fff;background:var(--pri);touch-action:manipulation}
  button:hover{background:var(--pri2)}
  .b-del{background:var(--del)} .b-del:hover{background:#b91c1c}
  .b-ghost{background:#fff;color:var(--pri);border:1px solid var(--pri)}
  .flash{padding:10px 14px;border-radius:9px;margin-bottom:12px;background:#ecfdf5;color:#065f46;border:1px solid #a7f3d0;font-size:14px}
  .muted{color:var(--muted);font-size:13px}
  a{color:var(--pri);text-decoration:none}
  .login{max-width:340px;margin:64px auto}
  .badge{display:inline-block;background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe;border-radius:999px;padding:3px 11px;font-size:12px}
  .dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px;vertical-align:middle}
  .ok{background:var(--ok)} .warn{background:var(--warn)} .del{background:var(--del)}
  .tag{display:inline-block;padding:2px 9px;border-radius:7px;font-size:12px;font-weight:700}
  .tag.ok{background:#dcfce7;color:#166534} .tag.err{background:#fee2e2;color:#991b1b}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px}
  .kpi-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:6px}
  .kpi{background:#f8fafc;border:1px solid var(--bd);border-radius:8px;padding:5px 8px;line-height:1.25}
  .kpi .lbl{font-size:10.5px;color:var(--muted)}
  .kpi .val{font-size:12px;font-weight:500;word-break:break-all}
  .storage-line{display:flex;align-items:center;gap:10px;margin-top:4px}
  .storage-pct{font-size:15px;font-weight:700;color:var(--ink);white-space:nowrap}
  .storage-bar{flex:1;height:7px;background:#eef2f7;border-radius:999px;overflow:hidden}
  .storage-bar>i{display:block;height:100%;border-radius:999px;transition:width .4s;background:linear-gradient(90deg,#3b82f6,#1d4ed8)}
  .storage-meta{font-size:12px;color:var(--muted);margin-top:4px}
  .user-sizes{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:6px;margin-top:6px}
  .user-size{display:flex;justify-content:space-between;align-items:center;background:#f8fafc;border:1px solid var(--bd);border-radius:8px;padding:5px 8px;font-size:12px}
  .user-size .sz{color:var(--muted);font-weight:500}
  .progress-mask{position:fixed;inset:0;background:rgba(244,246,251,.72);display:none;align-items:flex-start;justify-content:center;z-index:50;padding-top:90px;backdrop-filter:blur(1px)}
  .progress-mask.show{display:flex}
  .progress-box{background:#fff;border:1px solid var(--bd);border-radius:14px;padding:20px 26px;box-shadow:0 8px 30px rgba(0,0,0,.14);min-width:240px;max-width:88vw;text-align:center}
  .spinner{width:28px;height:28px;border:3px solid #dbe3f0;border-top-color:var(--pri);border-radius:50%;animation:spin .8s linear infinite;margin:0 auto 12px}
  @keyframes spin{to{transform:rotate(360deg)}}
  .progress-step{font-size:13.5px;color:var(--ink);line-height:1.6}
  .progress-step b{color:var(--pri)}
  .tablewrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
  details summary{cursor:pointer;color:var(--pri);font-size:13px;margin-top:8px}
  @media (max-width:480px){
    .wrap{margin:10px auto;padding:0 10px}
    .card{padding:9px 11px}
    .card.top-card{padding:8px 11px}
    .top a,button{padding:9px 12px}
    th,td{padding:8px 5px}
    .kpi-grid{grid-template-columns:1fr 1fr;gap:5px}
    .kpi{padding:5px 7px}
    .kpi .val{font-size:11.5px}
    .storage-line{gap:8px}
    .storage-pct{font-size:14px}
    .storage-meta{font-size:11.5px}
    .user-sizes{grid-template-columns:1fr 1fr;gap:5px}
    .user-size{padding:5px 7px;font-size:11.5px}
  }
</style>
</head>
<body>
"""

FOOT = "</body></html>"

LOGIN = HEAD + """
<div class="wrap login">
  <div class="card">
    <h1>FTP 管理后台</h1>
    <p class="muted">请输入管理员密码</p>
    {% with m = get_flashed_messages() %}{% if m %}<div class="flash">{{ m[0] }}</div>{% endif %}{% endwith %}
    <form method="post" action="/login">
      <input name="password" type="password" placeholder="密码" autofocus>
      <div style="height:12px"></div>
      <button style="width:100%">登 录</button>
    </form>
  </div>
</div>
""" + FOOT

INDEX = HEAD + """
<div class="wrap">
  <div class="card top-card top">
    <div><h1>FTP 服务管理</h1></div>
    <div style="display:flex;align-items:center;gap:10px">
      {% if st.ok %}
        {% if st.running and not st.restarting %}
          <span class="dot ok"></span><b style="color:var(--ok);font-size:13px">运行中</b>
        {% elif st.restarting %}
          <span class="dot warn"></span><b style="color:var(--warn);font-size:13px">重启中…</b>
        {% else %}
          <span class="dot del"></span><b style="color:var(--del);font-size:13px">已停止</b>
        {% endif %}
      {% else %}
        <span class="dot del"></span><b style="color:var(--del);font-size:13px">状态未知</b>
      {% endif %}
      <a class="b-ghost" style="padding:8px 12px;border-radius:8px;font-size:13px" href="/">刷新</a>
      <a class="b-ghost" style="padding:8px 12px;border-radius:8px;font-size:13px" href="/logout">退出</a>
    </div>
  </div>
  {% with m = get_flashed_messages() %}{% if m %}<div class="flash">{{ m[0] }}</div>{% endif %}{% endwith %}

  {% if not st.ok %}
  <div class="card"><div class="flash" style="background:#fef2f2;color:#991b1b;border-color:#fecaca">
    无法获取服务状态：{{ st.err }}</div></div>
  {% else %}

  <div class="card">
    <h2>服务详细状态</h2>
    <div class="kpi-grid">
      <div class="kpi"><div class="lbl">IP 地址</div><div class="val">{{ st.ftp_addr }}</div></div>
      <div class="kpi"><div class="lbl">端口</div><div class="val">{{ st.ftp_port }}</div></div>
      <div class="kpi"><div class="lbl">已运行时间</div><div class="val">{{ st.uptime }}</div></div>
      <div class="kpi"><div class="lbl">vsftpd 进程</div><div class="val">{% if st.vsftpd_running %}<span class="tag ok">运行中</span>{% else %}<span class="tag err">未运行</span>{% endif %}</div></div>
    </div>

    <h2>数据盘用量 (/ftp)</h2>
    {% if st.disk %}
    <div class="storage-line">
      <div class="storage-pct">{{ st.disk.pct }}%</div>
      <div class="storage-bar"><i style="width:{{ st.disk.pct }}%;background:{% if st.disk.pct>=90 %}var(--del){% elif st.disk.pct>=70 %}var(--warn){% else %}linear-gradient(90deg,#3b82f6,#1d4ed8){% endif %}"></i></div>
    </div>
    <div class="storage-meta">{{ st.disk.used }} / {{ st.disk.total }} · 剩余 {{ st.disk.avail }}</div>
    {% endif %}

    <h2>各用户目录占用</h2>
    {% if st.dirs_sizes %}
    <div class="user-sizes">
      {% for name, sz in st.dirs_sizes %}
      <div class="user-size"><span>/ftp/{{ name }}</span><span class="sz">{{ sz }}</span></div>
      {% endfor %}
    </div>
    {% else %}
    <div class="muted">无子目录</div>
    {% endif %}
  </div>
  {% endif %}

  <div class="card">
    <h2>当前用户</h2>
    <div class="tablewrap">
    <table>
      <tr><th>用户名</th><th>目录</th><th>登录</th><th>操作</th></tr>
      {% for u in users %}
      <tr>
        <td>{{ u.name }}</td>
        <td class="muted">{{ u.dir }}</td>
        <td>{% set p = probe_map.get(u.name) %}{% if p and p.ok %}<span class="tag ok">✓</span>{% elif p %}<span class="tag err">✗</span>{% else %}<span class="muted">—</span>{% endif %}</td>
        <td>
          <form class="op-form" method="post" action="/user/edit" style="display:inline-flex;gap:6px;flex-wrap:wrap">
            <input type="hidden" name="name" value="{{ u.name }}">
            <input name="pass" placeholder="新密码" style="width:96px">
            <input name="dir" placeholder="新目录" value="{{ u.dir }}" style="width:130px">
            <button type="submit">改</button>
          </form>
          <form class="op-form" method="post" action="/user/delete" style="display:inline">
            <input type="hidden" name="name" value="{{ u.name }}">
            <button class="b-del" type="submit">删</button>
          </form>
        </td>
      </tr>
      {% else %}
      <tr><td colspan="4" class="muted">暂无额外用户（默认账户在 compose 环境变量中配置）</td></tr>
      {% endfor %}
    </table>
    </div>
  </div>

  <div class="card">
    <h2>新增用户</h2>
    <form class="op-form" method="post" action="/user/add">
      <div class="row">
        <div><div class="muted" style="margin-bottom:4px">用户名</div><input name="name" placeholder="如 ftpm5" required></div>
        <div><div class="muted" style="margin-bottom:4px">密码</div><input name="pass" placeholder="密码" required></div>
        <div><div class="muted" style="margin-bottom:4px">目录</div><input name="dir" placeholder="/ftp/用户名" value=""></div>
        <div style="flex:0 0 auto;align-self:flex-end"><button type="submit">添加并重启</button></div>
      </div>
      <p class="muted" style="margin-top:8px">目录留空则默认 /ftp/&lt;用户名&gt;；提交后写入 users.json 并自动重启 FTP 服务。</p>
    </form>
  </div>
</div>

<div class="progress-mask" id="pmask">
  <div class="progress-box">
    <div class="spinner"></div>
    <div class="progress-step" id="pstep">处理中…</div>
  </div>
</div>
<script>
(function(){
  function showProgress(t){document.getElementById('pstep').textContent=t;document.getElementById('pmask').classList.add('show');}
  function setStep(t){document.getElementById('pstep').innerHTML=t;}
  function hideProgress(){document.getElementById('pmask').classList.remove('show');}
  function getStartedAt(){return fetch('/api/status').then(function(r){return r.json();}).then(function(s){return s.startedAt||'';}).catch(function(){return '';});}
  document.querySelectorAll('form.op-form').forEach(function(f){
    f.addEventListener('submit', function(e){
      e.preventDefault();
      var btn=f.querySelector('button[type=submit]')||f.querySelector('button');
      var orig=btn?btn.textContent:'';
      if(btn){btn.disabled=true;btn.textContent='处理中…';}
      showProgress('① 正在保存配置…');
      var fd=new FormData(f);
      getStartedAt().then(function(t0){
        fetch(f.action,{method:'POST',body:fd,headers:{'X-Requested-With':'fetch'}}).then(function(){
          setStep('② 已保存，正在重启 FTP 服务 (<b>约 10 秒</b>)…');
          var done=false, i=0;
          (function poll(){
            if(i>=60){ setStep('⚠ 重启超时，请手动刷新页面'); setTimeout(function(){location.reload();},2500); return; }
            i++;
            setTimeout(function(){
              getStartedAt().then(function(t1){
                if(t1 && t1!==t0){ done=true; setStep('③ 完成 ✓ FTP 服务已就绪'); setTimeout(function(){location.reload();},1200); }
                else { poll(); }
              }).catch(function(){ poll(); });
            },1000);
          })();
        }).catch(function(err){
          hideProgress(); if(btn){btn.disabled=false;btn.textContent=orig;} alert('请求失败：'+err);
        });
      });
    });
  });
})();
</script>
""" + FOOT

# ---------------- 路由 ----------------
@APP.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASS:
            session["auth"] = True
            return redirect(url_for("index"))
        flash("密码错误")
    return render_template_string(LOGIN)

@APP.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@APP.route("/", methods=["GET"])
def index():
    if not session.get("auth"):
        return redirect(url_for("login"))
    st = get_status()
    users = load_users()
    probe_map = {p["name"]: p for p in st.get("probes", [])}
    return render_template_string(INDEX, users=users, st=st, probe_map=probe_map)

@APP.route("/api/status")
def api_status():
    return jsonify(get_status())

@APP.route("/user/add", methods=["POST"])
def user_add():
    if not session.get("auth"):
        return redirect(url_for("login"))
    name = (request.form.get("name") or "").strip()
    pwd = (request.form.get("pass") or "").strip()
    d = (request.form.get("dir") or "").strip() or f"/ftp/{name}"
    if not name or not pwd:
        flash("用户名和密码不能为空"); return redirect(url_for("index"))
    users = load_users()
    if any(u.get("name") == name for u in users):
        flash(f"用户 {name} 已存在"); return redirect(url_for("index"))
    users.append({"name": name, "pass": pwd, "dir": d})
    return commit_users(users, f"已添加 {name}")

@APP.route("/user/delete", methods=["POST"])
def user_delete():
    if not session.get("auth"):
        return redirect(url_for("login"))
    name = request.form.get("name", "")
    users = [u for u in load_users() if u.get("name") != name]
    return commit_users(users, f"已删除 {name}")

@APP.route("/user/edit", methods=["POST"])
def user_edit():
    if not session.get("auth"):
        return redirect(url_for("login"))
    name = request.form.get("name", "")
    pwd = (request.form.get("pass") or "").strip()
    d = (request.form.get("dir") or "").strip()
    users = load_users()
    for u in users:
        if u.get("name") == name:
            if pwd: u["pass"] = pwd
            if d: u["dir"] = d
    return commit_users(users, f"已更新 {name}")

if __name__ == "__main__":
    if not os.path.exists(USERS_FILE):
        save_users([])
    APP.run(host="0.0.0.0", port=8080)
