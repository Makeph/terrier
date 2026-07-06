"""Export the knowledge base as a self-contained interactive HTML graph.

Nodes are projects, sessions, and the vocabulary that binds them —
rendered with a tiny force layout on a <canvas>, no CDN, no build step.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from html import escape

_WORD = re.compile(r"[a-zA-Zà-ÿÀ-Ÿ][a-zA-Zà-ÿÀ-Ÿ0-9_\-]{3,24}")

# English + French glue words and coding-agent boilerplate.
_STOP = frozenset("""
about after again all also and any are because been before being between both
but can could did does doing down during each few for from further had has have
having her here hers him his how into its itself just more most not now off once
only other our ours out over own same she should some such than that the their
them then there these they this those through too under until very was were what
when where which while who whom why will with your yours
alors ainsi aussi autre avant avec bien cela cette ceux chaque comme dans des
donc elle elles encore entre être fait faire ils leur leurs mais même mes moins
mon notre nous par pas peut plus pour quand que quel quelle qui sans ses son
sont sur tous tout toute toutes très une vous votre vos
file files line lines code using used use user assistant message messages
function return true false null none self this that then else import class
should would tool tools call calls result string value error errors need needs
create created update updated make made run running work working
""".split())


def _terms(text: str, n: int = 30) -> Counter:
    counts = Counter(
        w.lower() for w in _WORD.findall(text) if w.lower() not in _STOP
    )
    return Counter(dict(counts.most_common(n)))


def build_graph(con: sqlite3.Connection, max_terms_per_project: int = 8) -> dict:
    nodes: list[dict] = []
    edges: list[dict] = []
    node_index: dict[str, int] = {}

    def add_node(key: str, label: str, kind: str, size: float) -> int:
        if key in node_index:
            return node_index[key]
        node_index[key] = len(nodes)
        nodes.append({"label": label, "kind": kind, "size": size})
        return node_index[key]

    rows = con.execute(
        """
        SELECT s.project, d.session_id, MAX(d.title) AS title,
               COUNT(*) AS turns, GROUP_CONCAT(substr(d.body, 1, 2000), ' ') AS blob
        FROM docs d JOIN sources s ON s.id = d.source_id
        WHERE d.session_id IS NOT NULL
        GROUP BY s.project, d.session_id
        """
    ).fetchall()

    project_text: dict[str, list[str]] = {}
    for r in rows:
        p = add_node(f"p:{r['project']}", r["project"].rsplit("/", 1)[-1], "project", 16)
        label = (r["title"] or r["session_id"] or "?")[:60]
        s = add_node(f"s:{r['session_id']}", label, "session", 6 + min(r["turns"], 40) * 0.2)
        edges.append({"a": p, "b": s, "w": 1})
        project_text.setdefault(r["project"], []).append(r["blob"] or "")

    for note in con.execute(
        "SELECT s.project, d.title, d.body FROM docs d"
        " JOIN sources s ON s.id = d.source_id WHERE d.role = 'note'"
    ):
        p = add_node(f"p:{note['project']}", note["project"], "project", 16)
        n = add_node(f"n:{note['project']}/{note['title']}", note["title"][:60], "note", 7)
        edges.append({"a": p, "b": n, "w": 1})
        project_text.setdefault(note["project"], []).append(note["body"][:4000])

    # Shared vocabulary pulls related projects together.
    for project, blobs in project_text.items():
        for term, count in _terms(" ".join(blobs)).most_common(max_terms_per_project):
            t = add_node(f"t:{term}", term, "term", 5 + min(count, 60) * 0.1)
            edges.append({"a": node_index[f"p:{project}"], "b": t, "w": min(count, 20)})

    return {"nodes": nodes, "edges": edges}


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Terrier — knowledge graph</title>
<style>
  :root { --bg:#0d1117; --fg:#e6edf3; --dim:#8b949e; }
  html,body { margin:0; height:100%; background:var(--bg); color:var(--fg);
              font:14px/1.4 system-ui, sans-serif; overflow:hidden; }
  canvas { display:block; cursor:grab; }
  #hud { position:fixed; top:12px; left:14px; pointer-events:none; }
  #hud h1 { margin:0; font-size:15px; letter-spacing:.04em; }
  #hud p { margin:2px 0 0; color:var(--dim); font-size:12px; }
  #legend { position:fixed; bottom:12px; left:14px; color:var(--dim); font-size:12px; }
  #legend span { display:inline-block; width:10px; height:10px; border-radius:50%;
                 margin:0 4px 0 12px; vertical-align:-1px; }
  #tip { position:fixed; padding:4px 8px; background:#161b22; border:1px solid #30363d;
         border-radius:6px; font-size:12px; pointer-events:none; display:none; max-width:340px; }
</style></head><body>
<div id="hud"><h1>🐕 TERRIER</h1><p>__SUBTITLE__ · drag to pan · wheel to zoom</p></div>
<div id="legend">
  <span style="background:#f97316"></span>project
  <span style="background:#3b82f6"></span>session
  <span style="background:#22c55e"></span>note
  <span style="background:#8b949e"></span>term
</div>
<div id="tip"></div>
<canvas id="c"></canvas>
<script>
const DATA = __DATA__;
const COLORS = {project:"#f97316", session:"#3b82f6", note:"#22c55e", term:"#8b949e"};
const cv = document.getElementById("c"), cx = cv.getContext("2d");
const tip = document.getElementById("tip");
let W, H, view = {x:0, y:0, k:1};
function resize(){ W = cv.width = innerWidth; H = cv.height = innerHeight; }
addEventListener("resize", resize); resize();

const N = DATA.nodes.map((n,i)=>({...n,
  x: Math.cos(i*2.4)* (120+ i%7*60) + (Math.random()-.5)*40,
  y: Math.sin(i*2.4)* (120+ i%7*60) + (Math.random()-.5)*40,
  vx:0, vy:0 }));
const E = DATA.edges;
const deg = new Array(N.length).fill(0);
E.forEach(e=>{ deg[e.a]++; deg[e.b]++; });

let alpha = 1;
function tick(){
  if (alpha < 0.005) return;
  alpha *= 0.985;
  // repulsion (sampled for big graphs)
  for (let i=0;i<N.length;i++){
    const a=N[i], step = N.length>800 ? 7 : 1;
    for (let j=(i+1)%step; j<N.length; j+=step){
      if (i===j) continue;
      const b=N[j];
      let dx=a.x-b.x, dy=a.y-b.y, d2=dx*dx+dy*dy+0.01;
      if (d2>90000) continue;
      const f = 900*alpha/d2;
      dx*=f; dy*=f; a.vx+=dx; a.vy+=dy; b.vx-=dx; b.vy-=dy;
    }
  }
  for (const e of E){
    const a=N[e.a], b=N[e.b];
    let dx=b.x-a.x, dy=b.y-a.y;
    const d=Math.sqrt(dx*dx+dy*dy)+0.01, want=60+8*Math.min(deg[e.a],12);
    const f=(d-want)/d*0.02*alpha*Math.min(e.w,4);
    dx*=f; dy*=f; a.vx+=dx; a.vy+=dy; b.vx-=dx; b.vy-=dy;
  }
  for (const n of N){
    n.vx -= n.x*0.0004*alpha; n.vy -= n.y*0.0004*alpha; // gentle gravity
    n.x += n.vx = n.vx*0.85; n.y += n.vy = n.vy*0.85;
  }
}

let hover = -1;
function draw(){
  tick();
  cx.clearRect(0,0,W,H);
  cx.save();
  cx.translate(W/2+view.x, H/2+view.y); cx.scale(view.k, view.k);
  cx.strokeStyle = "rgba(139,148,158,0.18)";
  cx.beginPath();
  for (const e of E){ cx.moveTo(N[e.a].x,N[e.a].y); cx.lineTo(N[e.b].x,N[e.b].y); }
  cx.stroke();
  for (let i=0;i<N.length;i++){
    const n=N[i];
    cx.beginPath(); cx.arc(n.x,n.y,n.size,0,7);
    cx.fillStyle = COLORS[n.kind] || "#888";
    cx.globalAlpha = (hover===-1 || hover===i) ? 1 : 0.35;
    cx.fill();
    if (n.kind==="project" || view.k>1.4){
      cx.globalAlpha = 0.9;
      cx.fillStyle = "#e6edf3";
      cx.font = (n.kind==="project" ? "bold 12px" : "10px") + " system-ui";
      cx.fillText(n.label, n.x+n.size+4, n.y+3);
    }
  }
  cx.restore();
  requestAnimationFrame(draw);
}
draw();

function pick(mx,my){
  const gx=(mx-W/2-view.x)/view.k, gy=(my-H/2-view.y)/view.k;
  for (let i=N.length-1;i>=0;i--){
    const n=N[i], dx=gx-n.x, dy=gy-n.y;
    if (dx*dx+dy*dy < (n.size+3)*(n.size+3)) return i;
  }
  return -1;
}
let dragging=false, lx=0, ly=0;
cv.onmousedown = e=>{ dragging=true; lx=e.clientX; ly=e.clientY; };
addEventListener("mouseup", ()=>dragging=false);
cv.onmousemove = e=>{
  if (dragging){ view.x+=e.clientX-lx; view.y+=e.clientY-ly; lx=e.clientX; ly=e.clientY; return; }
  hover = pick(e.clientX, e.clientY);
  if (hover>=0){
    tip.style.display="block";
    tip.style.left=(e.clientX+14)+"px"; tip.style.top=(e.clientY+10)+"px";
    tip.textContent = N[hover].kind + " · " + N[hover].label;
  } else tip.style.display="none";
};
cv.onwheel = e=>{
  e.preventDefault();
  const k = Math.min(6, Math.max(0.15, view.k * (e.deltaY<0 ? 1.12 : 0.89)));
  view.x = (view.x)*(k/view.k); view.y = (view.y)*(k/view.k); view.k = k;
};
</script></body></html>
"""


def render_html(con: sqlite3.Connection) -> str:
    g = build_graph(con)
    subtitle = f"{len(g['nodes'])} nodes · {len(g['edges'])} edges"
    return _PAGE.replace("__DATA__", json.dumps(g, ensure_ascii=False)).replace(
        "__SUBTITLE__", escape(subtitle)
    )
